"""``scan`` -- inventory of everything in the session store.

Answers, for a user who knows nothing about the on-disk format: what projects
does Claude Code think I have, where did each one come from, is that folder
still there, how much history is in it, and is anything wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_session_rescue import store
from claude_session_rescue.session import SessionAnalysis


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "{0:.0f} {1}".format(value, unit) if unit == "B" else "{0:.1f} {1}".format(value, unit)
        value /= 1024
    return "{0:.1f} GB".format(value)


def _date(ts: Optional[str]) -> str:
    return ts[:10] if ts else "?"


def _session_line(session: SessionAnalysis, indent: str) -> List[str]:
    if session.error:
        return ["{0}! {1}  --  {2}".format(indent, session.path.name, session.error)]
    if session.is_empty:
        return ["{0}. {1}  --  empty file (0 records)".format(indent, session.path.name)]

    flags = []
    if session.is_split:
        flags.append("SPLIT into {0} segments".format(len(session.segments)))
    if session.duplicate_uuids:
        flags.append("{0} duplicate uuids".format(session.duplicate_uuids))
    if session.read.lines_malformed:
        flags.append("{0} unreadable lines".format(session.read.lines_malformed))
    if session.straddles_versions:
        flags.append("versions {0}".format(", ".join(session.versions)))

    head = "{0}- {1}  [{2}]".format(indent, session.display_title, session.path.stem[:8])
    detail = "{0}  {1} -> {2}  ·  {3} human turns  ·  {4}".format(
        indent,
        _date(session.first_timestamp),
        _date(session.last_timestamp),
        session.human_turns,
        human_bytes(session.size_bytes),
    )
    lines = [head, detail]
    if flags:
        lines.append("{0}  ** {1}".format(indent, "; ".join(flags)))
    return lines


def _project_block(project: store.ProjectDir, indent: str = "") -> List[str]:
    lines: List[str] = []
    origin = project.original_path or "(could not determine)"
    if project.origin_confidence == "recorded":
        origin_note = "from transcripts"
    elif project.origin_confidence == "recorded-unverified":
        origin_note = "from transcripts; slug rule differs on this machine"
    elif project.origin_confidence == "probed":
        origin_note = "decoded from slug, found on disk"
    else:
        origin_note = "decoded from slug, UNCONFIRMED guess"

    status = "missing" if project.is_orphaned else ("present" if project.path_exists else "unknown")
    first, last = project.date_range

    lines.append("{0}{1}".format(indent, project.slug))
    lines.append("{0}  folder : {1}  ({2})".format(indent, origin, origin_note))
    lines.append(
        "{0}  status : folder {1}  ·  {2} sessions  ·  {3}  ·  {4} -> {5}".format(
            indent,
            status,
            len(project.session_files),
            human_bytes(project.size_bytes),
            _date(first),
            _date(last),
        )
    )
    if project.git_branches:
        lines.append("{0}  branch : {1}".format(indent, ", ".join(project.git_branches)))
    if project.is_orphaned:
        lines.append(
            "{0}  ORPHANED: that folder no longer exists, so these sessions do not "
            "show up for any project you currently have open.".format(indent)
        )
    for session in project.sessions:
        lines.extend(_session_line(session, indent + "    "))
    if not project.sessions and project.session_files:
        lines.append("{0}    ({1} session files, not analysed - run without "
                     "--quick for detail)".format(indent, len(project.session_files)))
    return lines


def to_dict(projects: List[store.ProjectDir], projects_dir: Path) -> Dict[str, Any]:
    """Machine-readable form of the scan, for ``--json``."""
    return {
        "projectsDir": str(projects_dir),
        "projects": [
            {
                "slug": p.slug,
                "originalPath": p.original_path,
                "originConfidence": p.origin_confidence,
                "folderExists": p.path_exists,
                "isWorktree": p.is_worktree,
                "parentSlug": p.parent_slug,
                "sessionCount": len(p.session_files),
                "sizeBytes": p.size_bytes,
                "firstTimestamp": p.date_range[0],
                "lastTimestamp": p.date_range[1],
                "gitBranches": p.git_branches,
                "sessions": [
                    {
                        "file": s.path.name,
                        "sessionId": s.session_id,
                        "title": s.display_title,
                        "sizeBytes": s.size_bytes,
                        "records": s.read.records,
                        "malformedLines": s.read.lines_malformed,
                        "segments": len(s.segments),
                        "isSplit": s.is_split,
                        "duplicateUuids": s.duplicate_uuids,
                        "versions": s.versions,
                        "firstTimestamp": s.first_timestamp,
                        "lastTimestamp": s.last_timestamp,
                        "humanTurns": s.human_turns,
                        "error": s.error,
                    }
                    for s in p.sessions
                ],
            }
            for p in projects
        ],
    }


def run(args) -> int:
    projects_dir = Path(args.projects_dir)
    usable, message = store.store_status(projects_dir)
    if not usable:
        print(message)
        return 1

    projects = store.load_store(projects_dir, deep=not args.quick, probe=not args.no_probe)
    if args.json:
        print(json.dumps(to_dict(projects, projects_dir), indent=2))
        return 0

    grouped = store.group_worktrees(projects)
    total_sessions = sum(len(p.session_files) for p in projects)
    total_bytes = sum(p.size_bytes for p in projects)

    print("Session store: {0}".format(projects_dir))
    print("{0} project directories, {1} sessions, {2}".format(
        len(projects), total_sessions, human_bytes(total_bytes)))
    print()

    for parent, worktrees in grouped:
        for line in _project_block(parent):
            print(line)
        for worktree in worktrees:
            print("    -- git worktree of the above --")
            for line in _project_block(worktree, indent="    "):
                print(line)
        print()

    orphans = [p for p in projects if p.is_orphaned]
    split = [(p, s) for p in projects for s in p.sessions if s.is_split]

    print("Summary")
    print("  orphaned project directories : {0}".format(len(orphans)))
    print("  sessions split by compaction : {0}".format(len(split)))
    if orphans or split:
        print()
        print("Next step: run `claude-session-rescue doctor` for an explanation of "
              "each problem and the command that recovers it.")
    else:
        print()
        print("Nothing looks broken. `doctor` will confirm in more detail.")
    return 0
