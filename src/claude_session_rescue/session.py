"""Analyse a single session file.

Everything the other commands need about a session is produced here, by
streaming the file rather than loading it.  The output is a plain dataclass so
it can be printed, turned into JSON, or asserted against in tests.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_session_rescue.jsonl import ReadStats, Record, iter_records


@dataclass
class Segment:
    """A contiguous run of lines that hangs off one ``parentUuid == null`` root.

    A file with two segments is the bug this tool exists for: the desktop
    transcript renderer walks ``parentUuid`` from the first root and therefore
    never reaches the second segment, even though its content is right there in
    the same file.
    """

    index: int
    root_line: int
    root_type: Optional[str]
    root_subtype: Optional[str]
    root_uuid: Optional[str]
    end_line: Optional[int] = None  # inclusive; None until the file is finished
    logical_parent_uuid: Optional[str] = None
    compact_metadata: Dict[str, Any] = field(default_factory=dict)
    record_count: int = 0
    human_turns: int = 0
    assistant_turns: int = 0
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    #: Filled in by the second pass: which segment the logical parent lives in.
    logical_parent_segment: Optional[int] = None
    logical_parent_line: Optional[int] = None

    @property
    def started_by_compaction(self) -> bool:
        return self.root_subtype == "compact_boundary"

    @property
    def dropped_tokens(self) -> Optional[int]:
        """How much context the compaction threw away, if it said so."""
        meta = self.compact_metadata
        if "cumulativeDroppedTokens" in meta:
            return meta.get("cumulativeDroppedTokens")
        pre, post = meta.get("preTokens"), meta.get("postTokens")
        if isinstance(pre, int) and isinstance(post, int):
            return pre - post
        return None


@dataclass
class SessionAnalysis:
    """Everything one streaming pass can tell us about a session file."""

    path: Path
    session_id: Optional[str] = None
    size_bytes: int = 0
    read: ReadStats = field(default_factory=ReadStats)

    type_counts: Counter = field(default_factory=Counter)
    versions: List[str] = field(default_factory=list)
    cwds: List[str] = field(default_factory=list)
    git_branches: List[str] = field(default_factory=list)
    title: Optional[str] = None
    first_prompt: Optional[str] = None
    leaf_uuid: Optional[str] = None

    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    timestamp_regressions: int = 0
    timestamps_seen: int = 0

    duplicate_uuids: int = 0
    duplicate_uuid_occurrences: int = 0
    duplicate_examples: List[str] = field(default_factory=list)

    segments: List[Segment] = field(default_factory=list)
    bridge_sessions: int = 0
    sidechain_records: int = 0

    #: Set when the file could not be read at all (permissions, vanished,
    #: unreadable device).  Callers must check this before trusting anything
    #: else; it is never a reason to raise.
    #:
    #: This message contains the file path, because it is written for the user's
    #: own terminal.  Anything shareable must use ``error_kind`` instead.
    error: Optional[str] = None

    #: Path-free category for the same failure: "permission-denied",
    #: "not-found", "is-a-directory", "symlink-loop", "name-too-long", "other".
    error_kind: Optional[str] = None

    # ------------------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return self.error is None and self.read.records == 0

    @property
    def session_uuid_from_filename(self) -> str:
        return self.path.stem

    @property
    def boundaries(self) -> List[Segment]:
        return [s for s in self.segments if s.started_by_compaction]

    @property
    def is_split(self) -> bool:
        return len(self.segments) > 1

    @property
    def straddles_versions(self) -> bool:
        return len(self.versions) > 1

    @property
    def human_turns(self) -> int:
        return sum(s.human_turns for s in self.segments)

    @property
    def display_title(self) -> str:
        """Best available human label for the session.

        Preference order: the UI's own ``custom-title`` record, then the first
        human prompt trimmed to one line, then the filename.
        """
        if self.title:
            return self.title
        if self.first_prompt:
            one_line = " ".join(self.first_prompt.split())
            return (one_line[:70] + "...") if len(one_line) > 70 else one_line
        return self.session_uuid_from_filename


def sorted_versions(versions) -> List[str]:
    """Sort Claude Code version strings numerically, tolerating odd values."""
    def key(value: str):
        parts = [int(chunk) if chunk.isdigit() else 0 for chunk in str(value).split(".")]
        return (parts, str(value))

    return sorted(versions, key=key)


def classify_os_error(exc: OSError) -> str:
    """A short, path-free category for an I/O failure.

    ``explain_os_error`` produces a message containing the file path, which is
    right for the terminal and wrong for anything shareable.  This gives the
    same information with nothing identifying in it, so ``doctor --report`` can
    describe what went wrong without disclosing where.
    """
    import errno

    code = getattr(exc, "errno", None)
    if code == errno.EACCES or isinstance(exc, PermissionError):
        return "permission-denied"
    if code == errno.ENOENT or isinstance(exc, FileNotFoundError):
        return "not-found"
    if code == errno.EISDIR or isinstance(exc, IsADirectoryError):
        return "is-a-directory"
    if code == errno.ELOOP:
        return "symlink-loop"
    if code == errno.ENAMETOOLONG:
        return "name-too-long"
    return "other"


def explain_os_error(exc: OSError, path) -> str:
    """Turn an OSError into something a non-expert can act on.

    The message embeds the path on purpose -- it is printed to the user's own
    terminal, where the path is the most useful part.  Never put this string in
    a report; use :func:`classify_os_error` there instead.
    """
    import errno

    code = getattr(exc, "errno", None)
    if code == errno.EACCES or isinstance(exc, PermissionError):
        return (
            "permission denied reading {0}. Try running as the user who owns "
            "the Claude data directory, or copy the file somewhere readable "
            "first.".format(path)
        )
    if code == errno.ENOENT or isinstance(exc, FileNotFoundError):
        return "{0} no longer exists (it may have been deleted mid-scan).".format(path)
    if code == errno.EISDIR or isinstance(exc, IsADirectoryError):
        return "{0} is a directory, not a session file.".format(path)
    return "could not read {0}: {1}: {2}".format(path, type(exc).__name__, exc)


def _first_text(content: Any) -> Optional[str]:
    """Pull plain text out of a message ``content`` field.

    Content is either a bare string (typed prompts) or a list of blocks
    (everything else).  Tool results are also ``user`` records, so callers that
    want *human* turns must filter those out first.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return None


def is_human_turn(record: Record) -> bool:
    """True for a real typed/sent human message, not a tool result.

    Verified: tool results are stored as ``type: "user"`` records whose content
    is a list of ``tool_result`` blocks.  ``promptSource`` / ``origin`` do not
    discriminate reliably across versions, so we look at the content shape.
    """
    if record.type != "user":
        return False
    content = record.message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return False
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def analyze(path) -> SessionAnalysis:
    """Stream *path* once (twice if it contains compaction boundaries).

    Pass 1 collects counts, segments and metadata.  Pass 2 runs only when there
    is at least one ``compact_boundary`` and exists purely to find which segment
    each ``logicalParentUuid`` points into -- that is what proves the seam is a
    real continuation rather than an unrelated tree.
    """
    path = Path(path)
    stats = ReadStats()
    analysis = SessionAnalysis(path=path, read=stats)
    try:
        analysis.size_bytes = path.stat().st_size
    except OSError:
        analysis.size_bytes = 0

    seen_uuids = set()
    duplicate_uuids = set()
    # Note: this set holds one string per lineage-bearing record.  For the real
    # 20 MB reference file that is ~4,900 entries -- negligible.  It is the only
    # part of the pass that is not O(1) in memory, and it is what lets us report
    # the duplicate-uuid problem honestly.

    versions, cwds, branches = [], [], []
    current: Optional[Segment] = None
    last_ts: Optional[str] = None

    def readable_records():
        """Wrap the reader so an I/O failure becomes a message, not a crash.

        The file may be unreadable, may be a directory, or may vanish halfway
        through.  Catching here covers both open-time and mid-read failures
        without indenting the whole loop body.
        """
        try:
            for rec in iter_records(path, stats):
                yield rec
        except OSError as exc:
            analysis.error = explain_os_error(exc, path)
            analysis.error_kind = classify_os_error(exc)

    for record in readable_records():
        analysis.type_counts[record.type] += 1

        if analysis.session_id is None and record.session_id:
            analysis.session_id = record.session_id

        for value, bucket in ((record.version, versions), (record.cwd, cwds),
                              (record.git_branch, branches)):
            if value and value not in bucket:
                bucket.append(value)

        if record.type == "custom-title" and analysis.title is None:
            title = record.data.get("customTitle")
            if isinstance(title, str) and title.strip():
                analysis.title = title.strip()

        if record.type == "last-prompt":
            leaf = record.data.get("leafUuid")
            if isinstance(leaf, str):
                analysis.leaf_uuid = leaf  # keep the last one: it is the newest

        if record.type == "bridge-session":
            analysis.bridge_sessions += 1

        if record.data.get("isSidechain"):
            analysis.sidechain_records += 1

        ts = record.timestamp
        if ts:
            analysis.timestamps_seen += 1
            if analysis.first_timestamp is None:
                analysis.first_timestamp = ts
            analysis.last_timestamp = ts
            if last_ts is not None and ts < last_ts:
                analysis.timestamp_regressions += 1
            last_ts = ts

        if record.has_lineage:
            uid = record.uuid
            if uid in seen_uuids:
                if uid not in duplicate_uuids:
                    duplicate_uuids.add(uid)
                    if len(analysis.duplicate_examples) < 5:
                        analysis.duplicate_examples.append(str(uid))
                analysis.duplicate_uuid_occurrences += 1
            else:
                seen_uuids.add(uid)

            if record.is_root:
                if current is not None:
                    current.end_line = record.line_no - 1
                current = Segment(
                    index=len(analysis.segments),
                    root_line=record.line_no,
                    root_type=record.type,
                    root_subtype=record.subtype,
                    root_uuid=record.uuid,
                    logical_parent_uuid=record.logical_parent_uuid,
                    compact_metadata=record.compact_metadata,
                )
                analysis.segments.append(current)

        if current is not None:
            current.record_count += 1
            if ts:
                if current.first_timestamp is None:
                    current.first_timestamp = ts
                current.last_timestamp = ts
            if is_human_turn(record):
                current.human_turns += 1
                if analysis.first_prompt is None:
                    analysis.first_prompt = _first_text(record.message.get("content"))
            elif record.type == "assistant":
                current.assistant_turns += 1

    if current is not None:
        current.end_line = stats.lines_total

    analysis.duplicate_uuids = len(duplicate_uuids)
    analysis.versions = versions
    analysis.cwds = cwds
    analysis.git_branches = branches

    _resolve_logical_parents(path, analysis)
    return analysis


def _resolve_logical_parents(path: Path, analysis: SessionAnalysis) -> None:
    """Second pass: locate each ``logicalParentUuid`` in the file.

    Only runs when there is something to look for, and only remembers the
    handful of uuids we care about, so it stays O(1) in memory.
    """
    wanted = {
        segment.logical_parent_uuid: segment
        for segment in analysis.segments
        if segment.logical_parent_uuid
    }
    if not wanted:
        return

    line_of: Dict[str, int] = {}
    try:
        for record in iter_records(path):
            uid = record.uuid
            if uid in wanted and uid not in line_of:
                line_of[uid] = record.line_no
                if len(line_of) == len(wanted):
                    break
    except OSError:
        # Pass 1 already succeeded, so we still have a usable analysis; we just
        # cannot confirm where the seam attaches.  Leave the fields unset.
        return

    for uid, segment in wanted.items():
        line = line_of.get(uid)
        if line is None:
            continue
        segment.logical_parent_line = line
        for candidate in analysis.segments:
            end = candidate.end_line if candidate.end_line is not None else line
            if candidate.root_line <= line <= end:
                segment.logical_parent_segment = candidate.index
                break
