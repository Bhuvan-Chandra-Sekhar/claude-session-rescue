"""``export`` -- write a session out as a readable, complete transcript.

This is the workaround, and it is worth being blunt about the boundary: this
tool cannot fix the desktop app. It cannot make the hidden half of a compacted
session appear in the transcript pane. What it can do is write the whole
conversation -- both sides of every compaction seam -- to a file you can open,
search, diff and keep.

Ordering is by line number within each segment, and segments are ordered by
following ``logicalParentUuid`` across the seams. Timestamps are shown but never
sorted on, because they are not monotonic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from claude_session_rescue import store
from claude_session_rescue.chain import Chain, build_chain
from claude_session_rescue.jsonl import Record, iter_records
from claude_session_rescue.render import (
    RenderOptions,
    RenderStats,
    markdown_to_text,
    record_to_json,
    render_record,
    segment_heading,
    short_timestamp,
)
from claude_session_rescue.safety import open_for_write
from claude_session_rescue.session import SessionAnalysis, analyze, sorted_versions
from claude_session_rescue.commands.scan import human_bytes


class PartWriter:
    """Writes a growing document, rolling over to a new numbered part.

    Rollover happens between rendered blocks, never inside one, so a message is
    never cut in half by the split.
    """

    def __init__(self, out_dir: Path, stem: str, extension: str, split_bytes: int,
                 header: str, projects_dir: Optional[Path], dry_run: bool) -> None:
        self.out_dir = out_dir
        self.stem = stem
        self.extension = extension
        self.split_bytes = split_bytes
        self.header = header
        self.projects_dir = projects_dir
        self.dry_run = dry_run
        self.parts: List[Path] = []
        self.part_bytes: List[int] = []
        self._handle = None
        self._written = 0

    def _filename(self, index: int) -> Path:
        if self.split_bytes <= 0:
            return self.out_dir / "{0}.{1}".format(self.stem, self.extension)
        return self.out_dir / "{0}-part{1:02d}.{2}".format(self.stem, index, self.extension)

    def _open_next(self) -> None:
        self._close()
        path = self._filename(len(self.parts) + 1)
        self.parts.append(path)
        self._handle = open_for_write(path, self.projects_dir, dry_run=self.dry_run)
        self._written = 0
        part_note = ""
        if self.split_bytes > 0:
            part_note = "\n_(part {0} of this export)_\n".format(len(self.parts))
        self._write_raw(self.header + part_note + "\n")

    def _write_raw(self, text: str) -> None:
        if self._handle is None:
            self._open_next()
        assert self._handle is not None
        self._handle.write(text)
        self._written += len(text.encode("utf-8"))

    def write_block(self, text: str) -> None:
        if self._handle is None:
            self._open_next()
        elif self.split_bytes > 0 and self._written + len(text.encode("utf-8")) > self.split_bytes:
            self._open_next()
        self._write_raw(text)

    def _close(self) -> None:
        if self._handle is not None:
            self.part_bytes.append(self._written)
            self._handle.close()
            self._handle = None

    def close(self) -> None:
        self._close()
        # If splitting was requested but everything fitted in one file, drop the
        # "-part01" suffix. A lone "part 1 of 1" is just confusing.
        if self.split_bytes > 0 and len(self.parts) == 1 and not self.dry_run:
            plain = self.out_dir / "{0}.{1}".format(self.stem, self.extension)
            try:
                self.parts[0].replace(plain)
                self.parts[0] = plain
            except OSError:
                pass


def _segment_records(path: Path, start: int, end: Optional[int]) -> Iterable[Record]:
    """Stream just the records whose line numbers fall inside one segment.

    Re-reading the file once per segment keeps memory flat. Sessions have one or
    two segments in practice, so the extra passes are cheap; the alternative --
    buffering a 20 MB file in memory to reorder it -- is not.
    """
    for record in iter_records(path):
        if record.line_no < start:
            continue
        if end is not None and record.line_no > end:
            break
        yield record


def build_header(session: SessionAnalysis, chain: Chain, options: RenderOptions) -> str:
    lines = [
        "# {0}".format(session.display_title),
        "",
        "- session id: `{0}`".format(session.session_id or session.path.stem),
        "- source file: `{0}`".format(session.path.name),
        "- exported by: claude-session-rescue (read-only; the source file was not modified)",
        "- records: {0:,} over {1:,} lines".format(session.read.records, session.read.lines_total),
        "- date range: {0} to {1}".format(
            short_timestamp(session.first_timestamp), short_timestamp(session.last_timestamp)),
    ]
    if session.versions:
        lines.append("- Claude Code version(s): {0}".format(
            ", ".join(sorted_versions(session.versions))))
    if session.git_branches:
        lines.append("- git branch(es): {0}".format(", ".join(session.git_branches)))

    if len(chain.segments) > 1:
        dropped = chain.total_dropped_tokens
        lines.extend([
            "",
            "> **This session was compacted {0} time(s).** The desktop transcript view "
            "shows only the first {1:,} lines of it, because a compaction starts a new "
            "message tree and the view does not follow the link across the break. This "
            "export follows that link, so everything is here."
            .format(len(chain.segments) - 1, chain.segments[0].end_line or 0),
        ])
        if dropped:
            lines.append(">")
            lines.append("> Approximately **{0:,} tokens** of context were dropped by "
                         "the compaction(s). That context is gone from the model's "
                         "memory, but the *messages* below are complete.".format(dropped))
    if session.read.lines_malformed:
        lines.append("")
        lines.append("> {0} line(s) could not be parsed and were skipped.".format(
            session.read.lines_malformed))
    for note in chain.notes:
        lines.append("")
        lines.append("> Note: {0}".format(note))

    lines.extend([
        "",
        "Tool calls are summarised. Tool output longer than {0:,} characters is "
        "truncated with a marker. Secret-shaped strings are masked; see the README "
        "for what that does and does not guarantee.".format(options.tool_output_limit),
        "",
    ])
    return "\n".join(lines) + "\n"


def export_session(session_path: Path, out_dir: Path, args, projects_dir: Optional[Path]) -> Dict[str, Any]:
    """Export one session. Returns a summary dict for the caller to print."""
    session = analyze(session_path)
    if session.error:
        return {"file": session_path.name, "error": session.error}

    chain = build_chain(session)
    options = RenderOptions(
        tool_output_limit=args.tool_output_limit,
        include_thinking=args.include_thinking,
        redact_secrets=not args.no_redact,
    )
    stats = RenderStats()

    stem = session_path.stem
    if args.format == "json":
        return _export_json(session, chain, out_dir, stem, options, stats, args, projects_dir)

    extension = "md" if args.format == "md" else "txt"
    header = build_header(session, chain, options)
    if args.format == "txt":
        header = markdown_to_text(header)

    writer = PartWriter(out_dir, stem, extension, args.split_bytes, header, projects_dir, args.dry_run)

    total = len(chain.segments)
    for position, segment in enumerate(chain.segments, start=1):
        if total > 1:
            heading = segment_heading(segment, position, total)
            writer.write_block(heading if args.format == "md" else markdown_to_text(heading))
        for record in _segment_records(session_path, segment.root_line, segment.end_line):
            block = render_record(record, options, stats)
            if block is None:
                continue
            writer.write_block((block if args.format == "md" else markdown_to_text(block)) + "\n")
    writer.close()

    if len(writer.parts) > 1:
        _write_index(writer, session, chain, out_dir, args, projects_dir)

    return {
        "file": session_path.name,
        "title": session.display_title,
        "parts": [p.name for p in writer.parts],
        "bytes": sum(writer.part_bytes),
        "segments": total,
        "stats": stats,
    }


def _export_json(session: SessionAnalysis, chain: Chain, out_dir: Path, stem: str,
                 options: RenderOptions, stats: RenderStats, args,
                 projects_dir: Optional[Path]) -> Dict[str, Any]:
    """JSON export: one object, streamed as an array of entries.

    Written incrementally rather than built in memory, for the same reason
    everything else here streams.
    """
    path = out_dir / "{0}.json".format(stem)
    handle = open_for_write(path, projects_dir, dry_run=args.dry_run)
    meta = {
        "sessionId": session.session_id,
        "sourceFile": session.path.name,
        "title": session.display_title,
        "records": session.read.records,
        "lines": session.read.lines_total,
        "malformedLines": session.read.lines_malformed,
        "versions": session.versions,
        "gitBranches": session.git_branches,
        "firstTimestamp": session.first_timestamp,
        "lastTimestamp": session.last_timestamp,
        "segments": [
            {
                "index": s.index,
                "rootLine": s.root_line,
                "endLine": s.end_line,
                "startedByCompaction": s.started_by_compaction,
                "logicalParentUuid": s.logical_parent_uuid,
                "logicalParentLine": s.logical_parent_line,
                "compactMetadata": s.compact_metadata or None,
            }
            for s in chain.segments
        ],
        "chainNotes": chain.notes,
        "readOnly": True,
    }
    try:
        handle.write('{\n"meta": ')
        handle.write(json.dumps(meta, indent=2, default=str))
        handle.write(',\n"entries": [\n')
        first = True
        for segment in chain.segments:
            for record in _segment_records(session.path, segment.root_line, segment.end_line):
                entry = record_to_json(record, options, stats)
                if entry is None:
                    continue
                entry["segment"] = segment.index
                if not first:
                    handle.write(",\n")
                handle.write(json.dumps(entry, default=str))
                first = False
        handle.write("\n]\n}\n")
    finally:
        handle.close()

    return {
        "file": session.path.name,
        "title": session.display_title,
        "parts": [path.name],
        "bytes": getattr(handle, "bytes_written", None) or (path.stat().st_size if path.exists() else 0),
        "segments": len(chain.segments),
        "stats": stats,
    }


def _write_index(writer: PartWriter, session: SessionAnalysis, chain: Chain,
                 out_dir: Path, args, projects_dir: Optional[Path]) -> None:
    path = out_dir / "{0}-INDEX.md".format(writer.stem)
    with open_for_write(path, projects_dir, dry_run=args.dry_run) as handle:
        handle.write("# Export index: {0}\n\n".format(session.display_title))
        handle.write("Session `{0}` was written in {1} parts, in conversation order.\n\n"
                     .format(session.session_id or writer.stem, len(writer.parts)))
        for i, (part, size) in enumerate(zip(writer.parts, writer.part_bytes), start=1):
            handle.write("{0}. [{1}]({1}) - {2}\n".format(i, part.name, human_bytes(size)))
        handle.write("\n## Segments\n\n")
        for position, segment in enumerate(chain.segments, start=1):
            handle.write("- Segment {0}: lines {1}-{2}, {3} records{4}\n".format(
                position, segment.root_line, segment.end_line, segment.record_count,
                " (starts at a compaction boundary)" if segment.started_by_compaction else "",
            ))


def run(args) -> int:
    projects_dir = Path(args.projects_dir)
    out_dir = Path(args.out).expanduser()

    targets: List[Path] = []
    if args.session:
        matches = store.find_session(projects_dir, args.session)
        if not matches:
            print("No session matches '{0}'. Run `claude-session-rescue scan` to list "
                  "what is available.".format(args.session))
            return 1
        if len(matches) > 1:
            print("'{0}' matches {1} sessions; be more specific:".format(args.session, len(matches)))
            for path in matches[:20]:
                print("  {0}".format(path.stem))
            return 1
        targets = matches
    elif args.project:
        usable, message = store.store_status(projects_dir)
        if not usable:
            print(message)
            return 1
        projects = store.load_store(projects_dir, deep=False, probe=not args.no_probe)
        matched = store.find_projects_for_directory(projects, args.project)
        if not matched:
            print("No sessions were recorded in '{0}'.".format(args.project))
            return 1
        for project in matched:
            targets.extend(project.session_files)
    else:
        print("Nothing to export. Give a session id, or --project <folder>.")
        print("Run `claude-session-rescue scan` to see both.")
        return 1

    if args.dry_run:
        print("[dry run] no files will be written.\n")

    results = []
    for target in targets:
        result = export_session(target, out_dir, args, projects_dir)
        results.append(result)
        if result.get("error"):
            print("! {0}: {1}".format(result["file"], result["error"]))
            continue
        stats: RenderStats = result["stats"]
        print("{0}{1}".format("[dry run] " if args.dry_run else "", result["title"]))
        print("  from {0} ({1} segment(s))".format(result["file"], result["segments"]))
        print("  wrote {0} -> {1}".format(", ".join(result["parts"]), human_bytes(result["bytes"])))
        print("  {0} human turns, {1} assistant turns, {2} tool calls".format(
            stats.human_turns, stats.assistant_turns, stats.tool_calls))
        if stats.truncated_blocks:
            print("  {0} tool outputs truncated ({1:,} characters omitted)".format(
                stats.truncated_blocks, stats.truncated_chars))
        if stats.redactions:
            print("  {0} secret-shaped strings masked".format(stats.redactions))
        print()

    if not args.dry_run and results:
        print("Output directory: {0}".format(out_dir.resolve()))
    return 0
