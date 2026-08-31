"""Project-directory slugs.

Claude Code files a session under::

    ~/.claude/projects/<slug>/<sessionUuid>.jsonl

where ``<slug>`` is derived from the working directory the session started in.

Encoding (verified empirically -- see docs/on-disk-format.md)::

    C:\\Users\\alex\\code\\my_project  ->  C--Users-alex-code-my-project
    /home/alex/code/my_project         ->  -home-alex-code-my-project

Every one of ``: \\ / space _ .`` becomes a single ``-``.  ``C:`` contributes one
dash and the following ``\\`` contributes another, which is where the ``--``
comes from.  A POSIX path leads with ``/`` so its slug leads with ``-``.

The encoding is lossy: five different characters all collapse to ``-``, so a
slug cannot be decoded back to a path with certainty.  This module therefore
offers three levels of confidence, in the order you should trust them:

1. :func:`path_from_sessions` -- read the ``cwd`` field recorded *inside* the
   session files.  This is ground truth and needs no guessing.
2. :func:`decode_slug` with ``probe=True`` -- walk the real filesystem trying
   each ambiguous separator, and return a path that actually exists.
3. :func:`decode_slug` with ``probe=False`` -- a naive best guess, clearly
   labelled as such.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# Characters that Claude Code replaces with "-" when building a slug.
SEPARATOR_CHARS = ":\\/ _."

#: Marks a slug that looks like it came from a git worktree checkout.
WORKTREE_MARKER = "-claude-worktrees-"


def slug_for_path(path) -> str:
    """Encode a filesystem path into a Claude Code project slug.

    This direction is deterministic, so it is the part we can test exactly.
    """
    text = str(path)
    # Trailing separators would produce a trailing dash; Claude Code does not.
    text = text.rstrip("\\/")
    return "".join("-" if ch in SEPARATOR_CHARS else ch for ch in text)


def looks_like_worktree(slug: str) -> bool:
    """True if the slug appears to be a git worktree session directory.

    Worktrees live under ``<project>/.claude-worktrees/<name>-<hash>``.  The
    ``\\.`` in that path encodes to ``--``, giving the distinctive
    ``--claude-worktrees-`` run inside the slug.
    """
    return WORKTREE_MARKER in slug


def worktree_parent_slug(slug: str) -> Optional[str]:
    """For a worktree slug, return the slug of the project that owns it.

    ``A-B--claude-worktrees-feat-abc123`` -> ``A-B``.  Returns ``None`` when the
    slug is not a worktree slug.
    """
    if not looks_like_worktree(slug):
        return None
    head = slug.split(WORKTREE_MARKER, 1)[0]
    # The marker starts with the dash produced by the "." of ".claude-worktrees",
    # and the dash before that came from the path separator, so strip it.
    return head.rstrip("-") or None


def worktree_name(slug: str) -> Optional[str]:
    """The ``<name>-<hash>`` tail of a worktree slug, or ``None``."""
    if not looks_like_worktree(slug):
        return None
    return slug.split(WORKTREE_MARKER, 1)[1] or None


def _windows_drive_prefix(slug: str) -> Optional[Tuple[str, str]]:
    """Split ``C--Users-...`` into ``("C:\\\\", "Users-...")``.

    A single uppercase or lowercase letter followed by ``--`` is the signature
    of ``<drive>:\\`` at the start of a Windows path.
    """
    if len(slug) >= 3 and slug[1:3] == "--" and slug[0].isalpha():
        return slug[0] + ":" + os.sep if os.name == "nt" else slug[0] + ":\\", slug[3:]
    return None


def naive_decode(slug: str) -> str:
    """Best-guess decode with no filesystem access.

    Every remaining ``-`` becomes a path separator.  This is wrong wherever the
    original path contained a literal ``-``, a space or an underscore, which is
    why callers must present the result as a guess.
    """
    drive = _windows_drive_prefix(slug)
    if drive is not None:
        prefix, rest = drive
        return prefix + rest.replace("-", "\\" if os.name != "nt" else os.sep)
    return "/" + slug.lstrip("-").replace("-", "/")


def _probe(base: Path, tokens: Sequence[str], budget: List[int]) -> Optional[Path]:
    """Recursively rebuild a path from dash-separated *tokens* using the disk.

    At each step the next directory component may be one token, or several
    tokens re-joined with one of the collapsed characters.  We try the longest
    plausible component first, so a directory literally named ``my project``
    wins over a directory named ``my`` that happens to sit beside it.

    ``budget`` is a one-element list used as a mutable counter so a pathological
    slug cannot make this run forever.
    """
    if not tokens:
        return base
    if budget[0] <= 0:
        return None
    budget[0] -= 1

    if not base.is_dir():
        return None

    try:
        entries = {entry.name: entry for entry in base.iterdir() if entry.is_dir()}
    except (PermissionError, OSError):
        return None

    # Try to consume as many tokens as possible into one directory name.
    for take in range(len(tokens), 0, -1):
        chunk = tokens[:take]
        for joiner in SEPARATOR_CHARS.replace(":", "").replace("\\", "").replace("/", "") + "-":
            candidate = joiner.join(chunk)
            entry = entries.get(candidate)
            if entry is None:
                continue
            found = _probe(entry, tokens[take:], budget)
            if found is not None:
                return found
    return None


def decode_slug(slug: str, probe: bool = True, max_steps: int = 5000) -> Tuple[str, str]:
    """Decode a slug to a path.

    Returns ``(path, confidence)`` where confidence is one of
    ``"probed"`` (the path exists on this machine) or ``"guess"``.
    """
    if probe:
        drive = _windows_drive_prefix(slug)
        if drive is not None:
            root, rest = drive
            base = Path(root)
            tokens = rest.split("-")
        else:
            base = Path("/")
            tokens = slug.lstrip("-").split("-")
        if base.exists():
            found = _probe(base, [t for t in tokens if t != ""], [max_steps])
            if found is not None:
                return str(found), "probed"
    return naive_decode(slug), "guess"


def path_from_sessions(cwds: Sequence[str], slug: str) -> Optional[str]:
    """Recover the original path from ``cwd`` values found inside the sessions.

    Records carry the cwd at the moment they were written, and that can be a
    *subdirectory* of the session root (verified: several real files contain
    two or three different cwds).  So we walk each cwd upwards until its slug
    matches the directory name.  That match is exact, which makes this the only
    non-guessing decoder in the module.
    """
    for cwd in cwds:
        if not cwd:
            continue
        current = cwd.rstrip("\\/")
        seen = set()
        while current and current not in seen:
            seen.add(current)
            if slug_for_path(current) == slug:
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return None
