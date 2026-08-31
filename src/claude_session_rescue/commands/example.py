"""``example`` -- a worked walkthrough built from the machine it is run on.

Documentation that shows one person's session store has two problems: it leaks
whoever wrote it, and it never quite matches what the reader is looking at. This
command replaces that. It runs the same analysis ``scan`` and ``doctor`` use,
then narrates the result as a walkthrough, using the reader's own projects and
printing the exact commands for their situation.

If nothing is wrong on this machine it says so plainly and still demonstrates
the commands, because "healthy" is a useful thing to have seen too.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from claude_session_rescue import store
from claude_session_rescue.chain import build_chain
from claude_session_rescue.session import SessionAnalysis
from claude_session_rescue.textfmt import RULE, heading, paragraph
from claude_session_rescue.commands.scan import human_bytes


def _short(session: SessionAnalysis) -> str:
    """A short, quotable reference for a session: the first uuid group."""
    return session.path.stem.split("-")[0]


def _biggest(projects: List[store.ProjectDir]) -> Optional[SessionAnalysis]:
    sessions = [s for p in projects for s in p.sessions if not s.error and not s.is_empty]
    return max(sessions, key=lambda s: s.size_bytes) if sessions else None


def _describe_store(projects: List[store.ProjectDir], projects_dir: Path) -> List[str]:
    total_sessions = sum(len(p.session_files) for p in projects)
    total_bytes = sum(p.size_bytes for p in projects)
    worktrees = [p for p in projects if p.is_worktree]

    lines = [heading(1, "What is on this machine"), ""]
    lines.append(paragraph(
        "Claude Code keeps one directory per project under {0}, and one file per "
        "session inside it. Yours holds {1} project director{2} and {3} session{4}, "
        "totalling {5}.".format(
            projects_dir, len(projects), "y" if len(projects) == 1 else "ies",
            total_sessions, "" if total_sessions == 1 else "s", human_bytes(total_bytes))))
    if worktrees:
        lines.append("")
        lines.append(paragraph(
            "{0} of those director{1} came from a git worktree. Worktree sessions are "
            "filed separately because the working directory is different -- they are "
            "not missing, just listed apart. `scan` groups them under the project they "
            "branched from.".format(
                len(worktrees), "y is" if len(worktrees) == 1 else "ies are")))
    lines.extend(["", "  claude-session-rescue scan", ""])
    return lines


def _describe_orphans(orphans: List[store.ProjectDir], number: int) -> List[str]:
    lines = [heading(number, "Folders that moved"), ""]
    if not orphans:
        lines.append(paragraph(
            "None here. Every project directory still points at a folder that exists "
            "on this machine, so nothing has been stranded by a move or rename."))
        lines.append("")
        lines.append(paragraph(
            "For reference, this is what it would look like if one had: the sessions "
            "stay on disk under the old folder's name, while Claude Code starts filing "
            "new ones under a new name derived from the new path. Nothing is deleted; "
            "nothing points at the old history either."))
        lines.append("")
        return lines

    lines.append(paragraph(
        "{0} project director{1} recorded work in a folder that is no longer there. "
        "That is what a moved or renamed project looks like from the store's side: the "
        "history is intact, but Claude Code has started filing new sessions under a "
        "different name, so nothing links back to the old ones.".format(
            len(orphans), "y" if len(orphans) == 1 else "ies")))
    lines.append("")
    for project in orphans[:5]:
        lines.append("  {0} session{1} recorded in:".format(
            len(project.session_files), "" if len(project.session_files) == 1 else "s"))
        lines.append("    {0}".format(project.original_path or "(unknown)"))
        lines.append("  which does not exist now. To keep that history:")
        lines.append("    claude-session-rescue export --project \"{0}\" --out ./rescued".format(
            project.original_path or project.slug))
        lines.append("")
    if len(orphans) > 5:
        lines.append("  ... and {0} more; `doctor` lists them all.".format(len(orphans) - 5))
        lines.append("")
    return lines


def _describe_splits(splits: List[SessionAnalysis], number: int) -> List[str]:
    lines = [heading(number, "Conversations the app cannot fully draw"), ""]
    if not splits:
        lines.append(paragraph(
            "None here. Every session on this machine is a single message tree, which "
            "is what the transcript view expects, so all of it should be visible."))
        lines.append("")
        lines.append(paragraph(
            "The failure to watch for: when a conversation gets long, Claude Code "
            "compacts it, and the record marking that has a null parent. A null parent "
            "means 'start of a conversation', so the file ends up holding two message "
            "trees. The transcript view walks parent links from the first tree and "
            "stops at the seam; the link across it is a different field, "
            "logicalParentUuid. The messages are still on disk, and `claude --resume` "
            "still works, because resume follows the newest leaf rather than the root. "
            "If you ever see a chat that stops part-way while Claude clearly still "
            "remembers what happened, that is this."))
        lines.append("")
        return lines

    lines.append(paragraph(
        "{0} session{1} on this machine {2} split into more than one message tree. "
        "This happens when a conversation is compacted: the marker record has a null "
        "parent, which reads as 'start of a conversation', so the file ends up holding "
        "two trees. The transcript view walks parent links from the first one and stops "
        "at the seam -- the link across it is a different field, logicalParentUuid. "
        "Nothing is missing from disk.".format(
            len(splits), "" if len(splits) == 1 else "s",
            "is" if len(splits) == 1 else "are")))
    lines.append("")

    for session in splits[:3]:
        chain = build_chain(session)
        stranded = sum(s.record_count for s in session.segments[1:])
        share = stranded / max(session.read.records, 1)
        lines.append("  \"{0}\"".format(session.display_title))
        lines.append("    {0} segments, {1:,} of {2:,} records ({3:.0%}) after the first "
                     "break".format(len(session.segments), stranded,
                                    session.read.records, share))
        for i, boundary in enumerate(session.boundaries, start=1):
            dropped = boundary.dropped_tokens
            lines.append("    break {0}: line {1}, trigger '{2}'{3}".format(
                i, boundary.root_line,
                boundary.compact_metadata.get("trigger", "unknown"),
                ", {0:,} tokens of context dropped".format(dropped)
                if isinstance(dropped, int) else ""))
        for note in chain.notes[:2]:
            lines.append("    note: {0}".format(note))
        lines.append("    To read the whole thing, both sides of the seam:")
        lines.append("      claude-session-rescue export {0} --out ./rescued".format(
            _short(session)))
        lines.append("")
    if len(splits) > 3:
        lines.append("  ... and {0} more; `doctor` explains each one.".format(len(splits) - 3))
        lines.append("")

    lines.append(paragraph(
        "To be clear about the limit: this tool cannot patch the desktop app, so the "
        "hidden half will not reappear in the transcript pane. Exporting is the "
        "workaround, not a repair."))
    lines.append("")
    return lines


def _describe_export(projects: List[store.ProjectDir], number: int) -> List[str]:
    lines = [heading(number, "Getting a conversation out"), ""]
    biggest = _biggest(projects)
    if biggest is None:
        lines.append(paragraph(
            "There are no readable sessions here to demonstrate with."))
        lines.append("")
        return lines

    lines.append(paragraph(
        "Any session can be written out as Markdown, plain text or JSON. Tool calls are "
        "summarised rather than dumped, long tool output is truncated with a marker "
        "saying how much was cut, and secret-shaped strings are masked -- though read "
        "the README on what that does and does not guarantee before sharing an export."))
    lines.append("")
    lines.append("  The largest session here is \"{0}\" ({1}, {2:,} records):".format(
        biggest.display_title, human_bytes(biggest.size_bytes), biggest.read.records))
    lines.append("")
    lines.append("    claude-session-rescue export {0} --out ./rescued".format(_short(biggest)))
    lines.append("    claude-session-rescue export {0} --out ./rescued --format txt".format(
        _short(biggest)))
    lines.append("    claude-session-rescue export {0} --out ./rescued "
                 "--split-bytes 800000".format(_short(biggest)))
    lines.append("")
    lines.append(paragraph(
        "Without --out it runs as a dry run and writes nothing, so you can see what it "
        "would produce first."))
    lines.append("")
    return lines


def _describe_backup(projects_dir: Path, projects: List[store.ProjectDir],
                     number: int) -> List[str]:
    total_bytes = sum(p.size_bytes for p in projects)
    lines = [heading(number, "Before you reinstall"), ""]
    lines.append(paragraph(
        "A reinstall, an upgrade or a hopeful bit of folder-deleting is where history "
        "usually goes missing. Zipping the store first takes a couple of seconds -- "
        "yours is {0} -- and the archive carries a SHA-256 for every file so a restore "
        "can be verified rather than hoped at.".format(human_bytes(total_bytes))))
    lines.extend([
        "",
        "  claude-session-rescue backup --out ./claude-backup.zip",
        "",
    ])
    return lines


def _describe_next(number: int, problems: bool) -> List[str]:
    lines = [heading(number, "If something still is not right"), ""]
    lines.append(paragraph(
        "`doctor` goes through every finding in more detail, and can write a report "
        "that is safe to attach to a bug report -- counts, version strings and field "
        "names only, with no transcript text, paths, project names or uuids in it."))
    lines.extend([
        "",
        "  claude-session-rescue doctor",
        "  claude-session-rescue doctor --report ./session-report.json",
        "",
    ])
    if not problems:
        lines.append(paragraph(
            "Nothing on this machine currently needs rescuing. Worth knowing where "
            "this lives anyway, for the day it does."))
        lines.append("")
    return lines


def run(args) -> int:
    projects_dir = Path(args.projects_dir)
    usable, message = store.store_status(projects_dir)
    if not usable:
        print(message)
        print()
        print(paragraph(
            "There is nothing on this machine to build a walkthrough from. If Claude "
            "Code keeps its data somewhere else here, point at it with --projects-dir "
            "and run this again."))
        return 1

    projects = store.load_store(projects_dir, deep=True, probe=not args.no_probe)
    orphans = [p for p in projects if p.is_orphaned]
    splits = [s for p in projects for s in p.sessions if s.is_split]

    print(RULE)
    print("A worked example, using this machine's own session store")
    print(RULE)
    print()
    print(paragraph(
        "Everything below was measured just now from your own files. Nothing was "
        "modified: this tool only ever reads the session store."))
    print()

    section = 1
    for block in (
        _describe_store(projects, projects_dir),
        _describe_orphans(orphans, section + 1),
        _describe_splits(splits, section + 2),
        _describe_export(projects, section + 3),
        _describe_backup(projects_dir, projects, section + 4),
        _describe_next(section + 5, bool(orphans or splits)),
    ):
        for line in block:
            print(line)

    return 0
