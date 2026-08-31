"""Finding and describing a Claude Code session store.

A "store" is the ``projects`` directory: one subdirectory per project slug, each
holding ``<sessionUuid>.jsonl`` files.

Nothing in here assumes a particular operating system, username, drive letter or
project.  The store is discovered, the slugs are read off disk, and the original
working directory of each project is recovered from the ``cwd`` field recorded
*inside* the transcripts -- which is ground truth on every machine, regardless
of how that version of Claude Code happened to compute the slug.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from claude_session_rescue import slugs
from claude_session_rescue.jsonl import iter_records
from claude_session_rescue.session import SessionAnalysis, analyze, explain_os_error


def candidate_store_roots() -> List[Path]:
    """Places a Claude Code session store may live, most likely first.

    ``CLAUDE_CONFIG_DIR`` is honoured because Claude Code itself honours it, so
    users who relocated their config are not left out.
    """
    roots: List[Path] = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        roots.append(Path(env).expanduser() / "projects")
    home = Path.home()
    roots.append(home / ".claude" / "projects")
    # XDG-style location used by some installs.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        roots.append(Path(xdg).expanduser() / "claude" / "projects")
    roots.append(home / ".config" / "claude" / "projects")
    # De-duplicate while preserving order.
    seen, unique = set(), []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def default_projects_dir() -> Path:
    """The store we will use when the user gives no ``--projects-dir``."""
    for root in candidate_store_roots():
        if root.is_dir():
            return root
    return candidate_store_roots()[-1] if candidate_store_roots() else Path.home() / ".claude" / "projects"


def store_status(projects_dir: Path) -> Tuple[bool, str]:
    """Return ``(usable, message)`` describing the store at *projects_dir*."""
    if not projects_dir.exists():
        tried = "\n".join("  - {0}".format(p) for p in candidate_store_roots())
        return False, (
            "No session store found at {0}.\n"
            "Looked in:\n{1}\n"
            "If your Claude Code data lives elsewhere, pass --projects-dir "
            "<path>. If you have never run Claude Code on this machine, there "
            "is nothing to rescue here.".format(projects_dir, tried)
        )
    if not projects_dir.is_dir():
        return False, "{0} exists but is not a directory.".format(projects_dir)
    try:
        entries = list(projects_dir.iterdir())
    except OSError as exc:
        return False, explain_os_error(exc, projects_dir)
    if not entries:
        return False, (
            "{0} exists but is empty. Claude Code creates a subdirectory per "
            "project the first time you use it in that folder.".format(projects_dir)
        )
    return True, "{0} ({1} project directories)".format(projects_dir, len(entries))


@dataclass
class ProjectDir:
    """One ``<slug>/`` directory inside the store."""

    slug: str
    path: Path
    session_files: List[Path] = field(default_factory=list)
    sessions: List[SessionAnalysis] = field(default_factory=list)
    #: Recovered original working directory, and how we recovered it.
    original_path: Optional[str] = None
    #: "recorded" (read from the transcripts), "probed" (found on disk) or
    #: "guess" (decoded from the slug with no confirmation).
    origin_confidence: str = "unknown"
    path_exists: Optional[bool] = None
    error: Optional[str] = None

    @property
    def is_worktree(self) -> bool:
        return slugs.looks_like_worktree(self.slug)

    @property
    def parent_slug(self) -> Optional[str]:
        return slugs.worktree_parent_slug(self.slug)

    @property
    def size_bytes(self) -> int:
        return sum(s.size_bytes for s in self.sessions)

    @property
    def is_orphaned(self) -> bool:
        """The directory the sessions came from is no longer on disk.

        This is what a folder move or rename looks like from the store's point
        of view: the sessions are intact, but nothing points at them any more
        because a new slug was created for the new location.
        """
        return self.path_exists is False

    @property
    def date_range(self) -> Tuple[Optional[str], Optional[str]]:
        firsts = [s.first_timestamp for s in self.sessions if s.first_timestamp]
        lasts = [s.last_timestamp for s in self.sessions if s.last_timestamp]
        return (min(firsts) if firsts else None, max(lasts) if lasts else None)

    @property
    def git_branches(self) -> List[str]:
        out: List[str] = []
        for session in self.sessions:
            for branch in session.git_branches:
                if branch and branch not in out:
                    out.append(branch)
        return out


def list_project_dirs(projects_dir: Path) -> List[Path]:
    """All slug directories in the store, sorted, skipping unreadable ones."""
    try:
        entries = sorted(p for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    return entries


def list_session_files(project_dir: Path) -> List[Path]:
    """``*.jsonl`` files in a slug directory, newest last.

    Symlinks are followed (``is_file`` resolves them) but a broken symlink is
    silently skipped rather than raising.
    """
    try:
        files = [p for p in project_dir.iterdir() if p.suffix == ".jsonl"]
    except OSError:
        return []
    readable = []
    for path in files:
        try:
            if path.is_file():
                readable.append(path)
        except OSError:
            continue
    return sorted(readable)


def peek_origin(path: Path, max_records: int = 200) -> Dict[str, Optional[str]]:
    """Cheaply read the identifying fields from the head of a session file.

    Used for reverse lookups where a full analysis would be wasteful.  Reads at
    most *max_records* records, so it is O(1) regardless of file size.
    """
    out: Dict[str, Optional[str]] = {"cwd": None, "sessionId": None, "version": None}
    try:
        for i, record in enumerate(iter_records(path)):
            if out["cwd"] is None and record.cwd:
                out["cwd"] = record.cwd
            if out["sessionId"] is None and record.session_id:
                out["sessionId"] = record.session_id
            if out["version"] is None and record.version:
                out["version"] = record.version
            if all(out.values()) or i >= max_records:
                break
    except OSError:
        pass
    return out


def recover_original_path(slug: str, cwds: Iterable[str], probe: bool = True) -> Tuple[Optional[str], str]:
    """Work out which directory a project slug refers to.

    Order of trust:

    1. ``cwd`` values recorded inside the transcripts, walked up until one of
       them slugifies to this directory name.  Exact, version-independent.
    2. The shortest recorded cwd, if no ancestor matched.  This still beats
       decoding, because it came from the machine that wrote the file -- it just
       means that version computed slugs differently from our rule, which is
       worth knowing and is reported as ``"recorded-unverified"``.
    3. Decoding the slug against the live filesystem.
    4. A naive decode, clearly labelled a guess.
    """
    cwd_list = [c for c in cwds if c]
    exact = slugs.path_from_sessions(cwd_list, slug)
    if exact:
        return exact, "recorded"
    if cwd_list:
        shortest = min(cwd_list, key=len)
        return shortest, "recorded-unverified"
    decoded, confidence = slugs.decode_slug(slug, probe=probe)
    return decoded, confidence


def path_exists(path: Optional[str]) -> Optional[bool]:
    """Whether a recovered path is present on this machine (None if unknown)."""
    if not path:
        return None
    try:
        return Path(path).exists()
    except OSError:
        return None


def load_project(project_dir: Path, deep: bool = True, probe: bool = True) -> ProjectDir:
    """Build a :class:`ProjectDir`, analysing its sessions.

    ``deep=False`` skips the full streaming analysis and only peeks at each
    file's head, which makes ``scan`` fast on very large stores at the cost of
    session-level detail.
    """
    project = ProjectDir(slug=project_dir.name, path=project_dir)
    project.session_files = list_session_files(project_dir)

    cwds: List[str] = []
    if deep:
        for path in project.session_files:
            analysis = analyze(path)
            project.sessions.append(analysis)
            for cwd in analysis.cwds:
                if cwd not in cwds:
                    cwds.append(cwd)
    else:
        for path in project.session_files:
            info = peek_origin(path)
            if info["cwd"] and info["cwd"] not in cwds:
                cwds.append(info["cwd"])

    project.original_path, project.origin_confidence = recover_original_path(
        project.slug, cwds, probe=probe
    )
    project.path_exists = path_exists(project.original_path)
    return project


def load_store(projects_dir: Path, deep: bool = True, probe: bool = True) -> List[ProjectDir]:
    """Load every project in the store."""
    return [load_project(d, deep=deep, probe=probe) for d in list_project_dirs(projects_dir)]


def group_worktrees(projects: List[ProjectDir]) -> List[Tuple[ProjectDir, List[ProjectDir]]]:
    """Group worktree project dirs under the project they branched from.

    Worktree sessions get their own slug, so a project's history can look
    scattered across several directories.  Grouping restores the mental model.
    Orphan worktrees whose parent is not in the store are returned as their own
    top-level entries rather than dropped.
    """
    by_slug = {p.slug: p for p in projects}
    children: Dict[str, List[ProjectDir]] = {}
    tops: List[ProjectDir] = []

    for project in projects:
        parent = project.parent_slug
        if parent and parent in by_slug:
            children.setdefault(parent, []).append(project)
        else:
            tops.append(project)

    return [(top, children.get(top.slug, [])) for top in tops]


def find_projects_for_directory(projects: List[ProjectDir], target: str) -> List[ProjectDir]:
    """All project dirs whose sessions were recorded in *target*.

    Matching is done on the recovered original path -- i.e. on ``cwd`` values
    read out of the transcripts -- and not on string-slugifying the folder name.
    That is deliberate: it keeps working on machines where the slug rule differs
    from the one we verified, and it catches the case where a folder was moved
    and now has two slugs pointing at two different past locations.
    """
    target_norm = os.path.normcase(os.path.abspath(os.path.expanduser(target))).rstrip("\\/")
    matches = []
    for project in projects:
        if not project.original_path:
            continue
        candidate = os.path.normcase(os.path.abspath(project.original_path)).rstrip("\\/")
        if candidate == target_norm:
            matches.append(project)
            continue
        # Also match a worktree that lives underneath the target directory.
        if candidate.startswith(target_norm + os.sep) or candidate.startswith(target_norm + "/"):
            matches.append(project)
    return matches


def find_session(projects_dir: Path, needle: str) -> List[Path]:
    """Resolve a user-supplied session reference to concrete files.

    Accepts a full path, a full session uuid, or any unambiguous prefix of one.
    Returning a list (rather than one path) lets the caller report ambiguity
    instead of silently picking one.
    """
    direct = Path(needle).expanduser()
    if direct.is_file():
        return [direct]

    needle_lower = needle.lower()
    hits: List[Path] = []
    for project_dir in list_project_dirs(projects_dir):
        for path in list_session_files(project_dir):
            if path.stem.lower() == needle_lower:
                return [path]
            if path.stem.lower().startswith(needle_lower):
                hits.append(path)
    return hits
