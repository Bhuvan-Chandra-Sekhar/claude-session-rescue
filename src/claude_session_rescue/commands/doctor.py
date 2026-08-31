"""``doctor`` -- explain, in plain language, why history looks missing.

The output is written for somebody who has never heard of JSONL, ``parentUuid``
or compaction. Every finding says three things:

    what was found  ->  why the app cannot show it  ->  the command that helps

``doctor --report`` additionally emits a sanitized bug report: counts, version
strings and field *names* only. No transcript text, no file paths, no
usernames, no project names, no uuids -- slugs and session ids are replaced by
short hashes so the same file can be talked about without revealing what it is.
"""

from __future__ import annotations

import hashlib
import json
import platform
import textwrap
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_session_rescue import __version__, store
from claude_session_rescue.chain import build_chain
from claude_session_rescue.jsonl import iter_records
from claude_session_rescue.session import SessionAnalysis, analyze, sorted_versions
from claude_session_rescue.textfmt import RULE, wrap_block
from claude_session_rescue.commands.scan import human_bytes


def _hash(text: str) -> str:
    """Stable short pseudonym, so a report can reference a file safely."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:10]


#: Wrapping lives in textfmt so the walkthrough in `example` can reuse it.
_wrap_block = wrap_block




# ----------------------------------------------------------------------
# Findings
# ----------------------------------------------------------------------


class Finding:
    """One diagnosed problem, with an explanation and a suggested command."""

    def __init__(self, code: str, headline: str, detail: str, action: Optional[str] = None,
                 severity: str = "problem") -> None:
        self.code = code
        self.headline = headline
        self.detail = detail
        self.action = action
        self.severity = severity  # "problem" | "note"

    def render(self, width: int = 78) -> str:
        marker = "[!]" if self.severity == "problem" else "[i]"
        out = [textwrap.fill(
            "{0} {1}".format(marker, self.headline), width=width, subsequent_indent="    "), ""]
        out.extend(_wrap_block(self.detail, indent="    ", width=width))
        if self.action:
            out.append("")
            out.append("    What to do:")
            out.extend(_wrap_block(self.action, indent="      ", width=width, keep_breaks=True))
        return "\n".join(out)


def diagnose_session(session: SessionAnalysis) -> List[Finding]:
    """All findings for one session file."""
    findings: List[Finding] = []

    if session.error:
        return [Finding(
            "unreadable",
            "A session file could not be read: {0}".format(session.path.name),
            session.error,
            "Fix the permission or restore the file, then run doctor again.",
        )]

    if session.is_empty:
        return [Finding(
            "empty",
            "{0} contains no records".format(session.path.name),
            "The file exists but has no usable lines. Claude Code creates the file "
            "when a session starts, so a zero-record file usually means the session "
            "was abandoned immediately, or the write was interrupted.",
            "Nothing to recover here. It is safe to ignore.",
            severity="note",
        )]

    if session.is_split:
        chain = build_chain(session)
        boundaries = session.boundaries
        first_visible = session.segments[0]
        hidden = sum(s.record_count for s in session.segments[1:])
        dropped = chain.total_dropped_tokens

        detail = [
            "This session file contains {0} separate message trees, not one.".format(
                len(session.segments)),
            "",
            "Claude Code compacts a conversation when it runs out of context. When it "
            "does, it writes a marker record whose parentUuid is null -- and a null "
            "parentUuid is exactly what 'start of a new conversation' looks like. So "
            "the file now holds a second tree.",
            "",
            "The transcript pane in the desktop app follows parentUuid links downward "
            "from the first record. It reaches the end of tree 1 and stops. The link "
            "across the gap is stored in a different field, logicalParentUuid, which "
            "that walk does not read.",
            "",
            "Your conversation is not lost. It is all in the file. About {0:,} records "
            "({1} of the file) sit after the first break and are not being drawn."
            .format(hidden, "{0:.0%}".format(hidden / max(session.read.records, 1))),
        ]
        for i, boundary in enumerate(boundaries, start=1):
            meta = boundary.compact_metadata
            trigger = meta.get("trigger", "unknown")
            drop = boundary.dropped_tokens
            detail.append("")
            detail.append(
                "  break {0}: line {1}, trigger '{2}'{3}{4}".format(
                    i,
                    boundary.root_line,
                    trigger,
                    ", {0:,} tokens dropped".format(drop) if isinstance(drop, int) else "",
                    ", links back to line {0}".format(boundary.logical_parent_line)
                    if boundary.logical_parent_line else ", link target not found in this file",
                )
            )
        non_compaction = [s for s in session.segments[1:] if not s.started_by_compaction]
        if non_compaction:
            detail.append("")
            detail.append(
                "  {0} of the extra trees did not come from a compaction. They may be "
                "unrelated content appended to the same file; the export includes them "
                "at the end so nothing is lost.".format(len(non_compaction))
            )

        findings.append(Finding(
            "split-session",
            "\"{0}\" is split into {1} parts and the app only draws the first".format(
                session.display_title, len(session.segments)),
            "\n".join(detail),
            "This tool cannot patch the desktop app's renderer. The workaround is to\n"
            "export the whole thing to a file you can read and search:\n"
            "  claude-session-rescue export {0} --out ./rescued\n"
            "`claude --resume` also still works: it follows the newest leaf, which is\n"
            "inside the part you cannot see.".format(session.path.stem),
        ))

    if session.duplicate_uuids:
        findings.append(Finding(
            "duplicate-uuids",
            "{0} message ids appear more than once in {1}".format(
                session.duplicate_uuids, session.path.name),
            "Message uuids are not unique inside a session file. This happens when "
            "Claude Code replays or re-anchors part of a conversation. It is not "
            "corruption and it does not cost you any content.\n\n"
            "It matters only if you write your own tooling: keying on uuid alone will "
            "silently merge or drop messages. Key on (sessionId, uuid, line number).",
            "No action needed. This tool already orders by line number.",
            severity="note",
        ))

    if session.straddles_versions:
        findings.append(Finding(
            "version-straddle",
            "{0} was written by {1} different Claude Code versions".format(
                session.path.name, len(session.versions)),
            "Versions seen: {0}\n"
            "\n"
            "A long session survives app upgrades, so the record format can change "
            "part-way through the same file. Fields present at the end may be absent "
            "at the start. That is normal and is not a sign of damage.".format(
                ", ".join(sorted_versions(session.versions))),
            "No action needed. It is worth mentioning in a bug report.",
            severity="note",
        ))

    if session.read.lines_malformed:
        findings.append(Finding(
            "malformed-lines",
            "{0} lines in {1} could not be parsed".format(
                session.read.lines_malformed, session.path.name),
            "Lines: {0}{1}\n\n"
            "A single bad line at the very end usually means the app was killed "
            "mid-write and the last record was truncated -- harmless. Bad lines in the "
            "middle suggest the file was edited or partially overwritten.".format(
                ", ".join(str(n) for n in session.read.malformed_line_numbers),
                " (first 20 shown)" if session.read.lines_malformed > 20 else ""),
            "Export still works; the bad lines are skipped and counted:\n"
            "  claude-session-rescue export {0} --out ./rescued".format(session.path.stem),
        ))

    if session.timestamp_regressions:
        findings.append(Finding(
            "non-monotonic-timestamps",
            "{0} of {1} timestamps in {2} go backwards".format(
                session.timestamp_regressions, session.timestamps_seen, session.path.name),
            "Records are not written in timestamp order -- attachments and their parent "
            "messages interleave. Sorting a transcript by timestamp will scramble it.",
            "No action needed. This tool orders by line number, which is reliable.",
            severity="note",
        ))

    if session.bridge_sessions:
        findings.append(Finding(
            "bridge-session-records",
            "{0} bridge-session records present in {1}".format(
                session.bridge_sessions, session.path.name),
            "These records appeared in newer Claude Code builds. This tool reports "
            "them but does not interpret them, because their meaning is not "
            "documented and guessing would be worse than saying nothing.",
            None,
            severity="note",
        ))

    return findings


def diagnose_project(project: store.ProjectDir, all_projects: List[store.ProjectDir]) -> List[Finding]:
    """Findings about a project directory as a whole."""
    findings: List[Finding] = []

    if project.is_orphaned:
        siblings = [
            p for p in all_projects
            if p is not project and p.path_exists and p.original_path
            and Path(p.original_path).name == Path(project.original_path or "").name
        ]
        detail = [
            "Claude Code files sessions under a directory name derived from the folder "
            "you were working in. Move or rename that folder and the next session gets "
            "a brand new directory -- the old one keeps your history but nothing points "
            "at it any more.",
            "",
            "This directory's sessions were recorded in:",
            "  {0}".format(project.original_path or "(unknown)"),
            "and that path does not exist on this machine.",
        ]
        if siblings:
            detail.append("")
            detail.append("A folder with the same name does exist at:")
            for sibling in siblings:
                detail.append("  {0}".format(sibling.original_path))
            detail.append("which is consistent with the folder having been moved.")
        findings.append(Finding(
            "orphaned-project",
            "{0} sessions belong to a folder that is no longer there".format(
                len(project.session_files)),
            "\n".join(detail),
            "The history is intact and readable. Export it somewhere permanent:\n"
            "  claude-session-rescue export --project \"{0}\" --out ./rescued\n"
            "Moving the folder back to its original path would also make Claude Code\n"
            "find these sessions again, but this tool will not move anything for you."
            .format(project.original_path or project.slug),
        ))

    if project.is_worktree:
        findings.append(Finding(
            "worktree-project",
            "{0} is a git worktree and is filed separately".format(project.slug[:40] + "..."),
            "Sessions started inside a git worktree get their own project directory, "
            "because the working directory is different. They are not missing -- they "
            "are simply listed under the worktree rather than the main project.",
            "`claude-session-rescue scan` groups worktrees under their parent project "
            "so you can see them together.",
            severity="note",
        ))

    if project.origin_confidence in ("guess", "recorded-unverified"):
        findings.append(Finding(
            "slug-uncertain",
            "Could not confirm which folder {0} came from".format(project.slug[:40]),
            "The directory name is a lossy encoding of a path: several different "
            "characters all become '-'. Normally the cwd recorded inside the "
            "transcripts settles it, but that did not resolve here"
            + (" (the recorded cwd does not re-encode to this directory name, which "
               "means this version of Claude Code computes the name differently from "
               "the rule this tool knows)." if project.origin_confidence == "recorded-unverified"
               else " (no cwd was recorded in any session)."),
            "Everything else still works -- the tool reads the files directly. Please "
            "include this in a bug report via `doctor --report` so the rule can be "
            "widened.",
            severity="note",
        ))

    return findings


# ----------------------------------------------------------------------
# Sanitized report
# ----------------------------------------------------------------------


def key_census(path: Path, limit_records: int = 200000) -> Counter:
    """Count top-level field *names* in a session file. Never reads values."""
    census: Counter = Counter()
    try:
        for i, record in enumerate(iter_records(path)):
            for key in record.data:
                census[key] += 1
            if i >= limit_records:
                break
    except OSError:
        pass
    return census


def build_report(projects: List[store.ProjectDir], projects_dir: Path,
                 include_keys: bool = True) -> Dict[str, Any]:
    """A shareable, content-free description of what is on disk."""
    report: Dict[str, Any] = {
        "tool": "claude-session-rescue",
        "toolVersion": __version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "storePresent": projects_dir.exists(),
        "projectCount": len(projects),
        "projects": [],
        "note": "Counts, version strings and field names only. No transcript text, "
                "paths, project names or uuids are included.",
    }
    versions: Counter = Counter()
    keys: Counter = Counter()

    for project in projects:
        entry = {
            "projectRef": _hash(project.slug),
            "slugLength": len(project.slug),
            "isWorktree": project.is_worktree,
            "originConfidence": project.origin_confidence,
            "folderExists": project.path_exists,
            "sessionCount": len(project.session_files),
            "sizeBytes": project.size_bytes,
            "sessions": [],
        }
        for session in project.sessions:
            for version in session.versions:
                versions[version] += 1
            if include_keys:
                keys.update(key_census(session.path))
            entry["sessions"].append({
                "sessionRef": _hash(session.path.stem),
                "sizeBytes": session.size_bytes,
                "records": session.read.records,
                "malformedLines": session.read.lines_malformed,
                "typeCounts": dict(session.type_counts),
                "segmentCount": len(session.segments),
                "compactBoundaries": len(session.boundaries),
                "compactTriggers": [s.compact_metadata.get("trigger") for s in session.boundaries],
                "duplicateUuids": session.duplicate_uuids,
                "timestampRegressions": session.timestamp_regressions,
                "versions": session.versions,
                "bridgeSessionRecords": session.bridge_sessions,
                # The category, never session.error -- that message embeds the
                # absolute path, and this file is meant to be pasteable into a
                # public issue.
                "errorKind": session.error_kind,
            })
        report["projects"].append(entry)

    report["versionsSeen"] = dict(versions)
    if include_keys:
        report["topLevelFieldNames"] = dict(keys)
    return report


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def _print_findings(title: str, findings: List[Finding]) -> int:
    problems = [f for f in findings if f.severity == "problem"]
    print(RULE)
    print(title)
    print(RULE)
    if not findings:
        print("No problems found.")
        print()
        return 0
    for finding in findings:
        print(finding.render())
        print()
    return len(problems)


def run(args) -> int:
    projects_dir = Path(args.projects_dir)
    usable, message = store.store_status(projects_dir)
    if not usable:
        print(message)
        return 1

    # Work out what the user asked about.
    if getattr(args, "session", None):
        matches = store.find_session(projects_dir, args.session)
        if not matches:
            print("No session matches '{0}'.\nRun `claude-session-rescue scan` to see "
                  "what is available.".format(args.session))
            return 1
        if len(matches) > 1:
            print("'{0}' is ambiguous; it matches {1} sessions:".format(args.session, len(matches)))
            for path in matches[:20]:
                print("  {0}".format(path.stem))
            return 1
        session = analyze(matches[0])
        findings = diagnose_session(session)
        problems = _print_findings(
            "Diagnosis: {0}  ({1}, {2})".format(
                session.display_title, matches[0].name, human_bytes(session.size_bytes)),
            findings,
        )
        _print_footer(problems)
        return 0

    projects = store.load_store(projects_dir, deep=True, probe=not args.no_probe)

    if getattr(args, "project", None):
        matched = store.find_projects_for_directory(projects, args.project)
        if not matched:
            print("No sessions in {0} were recorded in '{1}'.".format(projects_dir, args.project))
            print()
            print("This tool matches on the working directory recorded inside each "
                  "transcript, so a folder that was never used with Claude Code will "
                  "not appear. Run `claude-session-rescue scan` to see every folder "
                  "that does.")
            return 1
        projects = matched

    total_problems = 0
    for project in projects:
        findings = diagnose_project(project, projects)
        for session in project.sessions:
            findings.extend(diagnose_session(session))
        if not findings and not args.verbose:
            continue
        label = project.original_path or project.slug
        total_problems += _print_findings(
            "Project: {0}\n  store directory: {1}\n  {2} sessions, {3}".format(
                label, project.slug, len(project.session_files),
                human_bytes(project.size_bytes)),
            findings,
        )

    if total_problems == 0:
        print(RULE)
        print("Checked {0} project directories and {1} sessions. Nothing is broken.".format(
            len(projects), sum(len(p.session_files) for p in projects)))
        print(RULE)

    if getattr(args, "report", None):
        report = build_report(projects, projects_dir)
        target = Path(args.report)
        from claude_session_rescue.safety import open_for_write
        with open_for_write(target, projects_dir, dry_run=args.dry_run) as handle:
            handle.write(json.dumps(report, indent=2, default=str))
        if args.dry_run:
            print("\n[dry run] would write sanitized report to {0}".format(target))
        else:
            print("\nSanitized report written to {0}".format(target))
            print("It contains counts, version strings and field names only -- no "
                  "transcript text, paths or names. Safe to attach to a GitHub issue.")
    else:
        _print_footer(total_problems)
    return 0


def _print_footer(problems: int) -> None:
    print()
    print("If this did not explain your problem")
    print(RULE)
    print("Generate a report that is safe to paste into a bug report:")
    print("  claude-session-rescue doctor --report ./session-report.json")
    print("It contains counts, Claude Code version strings and field names only -")
    print("no transcript text, no file paths, no project names, no uuids.")
