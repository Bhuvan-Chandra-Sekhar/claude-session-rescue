"""Read-only enforcement.

The whole point of this tool is that it can be pointed at a session store you
care about without any risk.  Two rules, enforced in code rather than by
convention:

1. Nothing under the Claude home directory (``~/.claude`` by default, or the
   parent of whatever ``--projects-dir`` was given) may ever be opened for
   writing.
2. Every write goes through :func:`open_for_write`, which checks rule 1 and
   also honours dry-run mode.

If you add a new command, write through this module and the invariant holds
for free.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Iterable, Optional


class WriteRefused(Exception):
    """Raised when a write target is inside a protected directory."""


def _resolve(path: Path) -> Path:
    """Resolve without requiring the path to exist (Python 3.9 friendly)."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def is_inside(child: Path, parent: Path) -> bool:
    """True if *child* is *parent* or lives underneath it.

    Uses string-free comparison via ``Path.parts`` so that ``/a/bc`` is not
    mistaken for a child of ``/a/b``.
    """
    child_r = _resolve(child)
    parent_r = _resolve(parent)
    if os.name == "nt":
        # Windows paths are case-insensitive in practice.
        child_parts = tuple(p.lower() for p in child_r.parts)
        parent_parts = tuple(p.lower() for p in parent_r.parts)
    else:
        child_parts = child_r.parts
        parent_parts = parent_r.parts
    return child_parts[: len(parent_parts)] == parent_parts


def protected_roots(projects_dir: Optional[Path] = None) -> list:
    """Directories this tool must never write into.

    Always includes ``~/.claude``.  If a custom ``--projects-dir`` was given we
    protect that too, plus its parent, because a custom store deserves the same
    treatment as the default one.
    """
    roots = [_resolve(Path.home() / ".claude")]
    if projects_dir is not None:
        pd = _resolve(projects_dir)
        roots.append(pd)
        if pd.parent != pd:
            roots.append(pd.parent)
    return roots


def assert_write_allowed(target: Path, projects_dir: Optional[Path] = None) -> None:
    """Raise :class:`WriteRefused` if *target* is inside a protected root."""
    target_r = _resolve(target)
    for root in protected_roots(projects_dir):
        if is_inside(target_r, root):
            raise WriteRefused(
                "refusing to write to {0}: it is inside the protected session "
                "store {1}. claude-session-rescue is read-only with respect to "
                "your Claude data.".format(target_r, root)
            )


class DryRunFile(io.StringIO):
    """A file-like object that swallows writes and reports the byte count."""

    def __init__(self, target: Path) -> None:
        super().__init__()
        self.target = target
        self.bytes_written = 0

    def write(self, s):  # type: ignore[override]
        self.bytes_written += len(s.encode("utf-8"))
        return len(s)


def open_for_write(
    target: Path,
    projects_dir: Optional[Path] = None,
    dry_run: bool = False,
    binary: bool = False,
):
    """Open *target* for writing after the safety check.

    In dry-run mode a :class:`DryRunFile` is returned instead, so calling code
    is identical either way and we still get an accurate byte count to report.
    """
    assert_write_allowed(target, projects_dir)
    if dry_run:
        return DryRunFile(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        return open(target, "wb")
    return open(target, "w", encoding="utf-8", newline="\n")


def describe_protection(projects_dir: Optional[Path] = None) -> Iterable[str]:
    """Human-readable list of protected roots, for `--help`-style output."""
    for root in protected_roots(projects_dir):
        yield str(root)
