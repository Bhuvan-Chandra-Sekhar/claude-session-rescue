"""Command line entry point.

Two things matter more here than anywhere else in the package:

* Running the bare command with no arguments must be useful. Somebody who has
  just lost their history should not be met with an argparse usage error.
* Every message should assume no knowledge of the file format.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claude_session_rescue import __version__, store
from claude_session_rescue.commands import backup as backup_cmd
from claude_session_rescue.commands import doctor as doctor_cmd
from claude_session_rescue.commands import example as example_cmd
from claude_session_rescue.commands import export as export_cmd
from claude_session_rescue.commands import scan as scan_cmd
from claude_session_rescue.safety import WriteRefused, describe_protection

DESCRIPTION = """\
Find, diagnose and rescue Claude Code session history.

Read-only: this tool never writes anything into your Claude data directory.
"""

EPILOG = """\
Common situations:

  history is gone after a reinstall     claude-session-rescue scan
  a chat is blank but Claude remembers  claude-session-rescue doctor
  sessions vanished after moving a folder
                                        claude-session-rescue doctor
  I want a copy I can keep              claude-session-rescue export <session> --out ./rescued
  I am about to reinstall               claude-session-rescue backup --out ./backup.zip
  show me all of this on my own data    claude-session-rescue example

Run with no arguments for an overview of what is on this machine.
"""


def common_options() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand name.

    Every default is ``SUPPRESS`` on purpose. argparse lets a subparser's
    defaults overwrite a value the top-level parser already set, so
    ``tool --projects-dir X scan`` would otherwise lose the directory. With
    SUPPRESS the attribute is simply absent unless somebody passed the flag, and
    :func:`apply_common_defaults` fills the gaps once, at the end.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--projects-dir",
        default=argparse.SUPPRESS,
        help="Session store to read (default: auto-detected, usually ~/.claude/projects).",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Do not touch the filesystem while working out where a project came from.",
    )
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="Show findings with no problems too.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Report what would be written without writing it. On by default when no "
             "output destination is given.",
    )
    return parser


def apply_common_defaults(args) -> None:
    for name, default in (("projects_dir", None), ("no_probe", False),
                          ("verbose", False), ("dry_run", None)):
        if not hasattr(args, name):
            setattr(args, name, default)


def build_parser() -> argparse.ArgumentParser:
    common = common_options()
    parser = argparse.ArgumentParser(
        prog="claude-session-rescue",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    parser.add_argument("--version", action="version", version="claude-session-rescue " + __version__)
    subparsers = parser.add_subparsers(dest="command")

    scan_p = subparsers.add_parser(
        "scan",
        help="List every project and session on this machine.",
        description="Inventory the session store: which folder each project came from, "
                    "whether that folder still exists, how much history there is, and "
                    "whether anything looks wrong.",
        parents=[common],
    )
    scan_p.add_argument("--json", action="store_true", help="Machine-readable output.")
    scan_p.add_argument("--quick", action="store_true",
                        help="Skip full analysis of each session; much faster on large stores.")
    scan_p.set_defaults(func=scan_cmd.run)

    doctor_p = subparsers.add_parser(
        "doctor",
        help="Explain why history looks missing, and what to do about it.",
        description="Diagnose a session, a project folder, or the whole store.",
        parents=[common],
    )
    doctor_p.add_argument("session", nargs="?", help="Session id (or a unique prefix), or a file path.")
    doctor_p.add_argument("--project", help="Diagnose the sessions recorded in this folder.")
    doctor_p.add_argument("--report", nargs="?", const="session-report.json",
                          help="Also write a sanitized report safe to attach to a bug report.")
    doctor_p.set_defaults(func=doctor_cmd.run)

    export_p = subparsers.add_parser(
        "export",
        help="Write a full transcript to a file, including parts the app cannot draw.",
        description="Export a session in conversation order, following compaction "
                    "boundaries so nothing is left out.",
        parents=[common],
    )
    export_p.add_argument("session", nargs="?", help="Session id (or a unique prefix), or a file path.")
    export_p.add_argument("--project", help="Export every session recorded in this folder.")
    export_p.add_argument("--out", default=None, help="Output directory (default: ./rescued-sessions).")
    export_p.add_argument("--format", choices=("md", "txt", "json"), default="md")
    export_p.add_argument("--split-bytes", type=int, default=0,
                          help="Split into numbered parts of about this size, with an INDEX file.")
    export_p.add_argument("--tool-output-limit", type=int, default=2000,
                          help="Truncate tool output longer than this many characters (0 = never).")
    export_p.add_argument("--include-thinking", action="store_true",
                          help="Include the model's internal reasoning blocks.")
    export_p.add_argument("--no-redact", action="store_true",
                          help="Do not mask secret-shaped strings. Think before using this.")
    export_p.set_defaults(func=export_cmd.run)

    example_p = subparsers.add_parser(
        "example",
        help="A worked walkthrough, built from this machine's own sessions.",
        description="Narrate what is in your session store, what (if anything) is "
                    "wrong with it, and the exact commands for your situation. The "
                    "documentation's worked example, generated from your data instead "
                    "of somebody else's.",
        parents=[common],
    )
    example_p.set_defaults(func=example_cmd.run)

    backup_p = subparsers.add_parser(
        "backup",
        help="Zip the whole session store with checksums, before a reinstall.",
        parents=[common],
    )
    backup_p.add_argument("--out", default=None, help="Archive path or directory.")
    backup_p.set_defaults(func=backup_cmd.run)

    return parser


def resolve_projects_dir(args) -> Path:
    if getattr(args, "projects_dir", None):
        return Path(args.projects_dir).expanduser()
    return store.default_projects_dir()


def overview(args) -> int:
    """What the bare command prints. No flags, no jargon, no usage error."""
    projects_dir = resolve_projects_dir(args)
    print("claude-session-rescue {0}".format(__version__))
    print()
    usable, message = store.store_status(projects_dir)
    if not usable:
        print(message)
        print()
        print("Nothing else can run until a session store is found.")
        return 1

    projects = store.load_store(projects_dir, deep=False, probe=not args.no_probe)
    sessions = sum(len(p.session_files) for p in projects)
    orphaned = [p for p in projects if p.is_orphaned]
    worktrees = [p for p in projects if p.is_worktree]

    print("Session store : {0}".format(projects_dir))
    print("Projects      : {0}{1}".format(
        len(projects),
        " ({0} of them git worktrees)".format(len(worktrees)) if worktrees else ""))
    print("Sessions      : {0}".format(sessions))
    if orphaned:
        print()
        print("{0} project directory(ies) point at a folder that is no longer on this "
              "machine.".format(len(orphaned)))
        print("That is what a moved or renamed project looks like: the history is still "
              "here,")
        print("but Claude Code is now filing new sessions under a different name.")

    print()
    print("Next steps")
    print("  claude-session-rescue example   a walkthrough using your own sessions")
    print("  claude-session-rescue scan      what is on disk, project by project")
    print("  claude-session-rescue doctor    what is wrong and how to get it back")
    print("  claude-session-rescue backup    a checksummed zip before you reinstall")
    print()
    print("This tool never writes into: {0}".format(
        ", ".join(describe_protection(projects_dir))))
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    apply_common_defaults(args)
    args.projects_dir = str(resolve_projects_dir(args))

    if not getattr(args, "command", None):
        return overview(args)

    # Dry-run defaults to on whenever the user did not name a destination, so a
    # first run can never surprise anybody by creating files.
    if args.command in ("export", "backup"):
        if args.dry_run is None:
            args.dry_run = args.out is None
        if args.command == "export" and args.out is None:
            args.out = "./rescued-sessions"
    else:
        args.dry_run = bool(args.dry_run)

    try:
        code = args.func(args)
    except WriteRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing was modified.", file=sys.stderr)
        return 130

    if args.command in ("export", "backup") and args.dry_run:
        print("\nThis was a dry run because no --out was given. Re-run with "
              "--out <path> to write files.")
    return code
