"""Streaming JSONL reader.

Session files reach tens of megabytes (the reference file is 20 MB, the whole
store 58 MB), so nothing here ever holds a whole file in memory.  We yield one
:class:`Record` at a time and let callers keep only what they need.

Malformed lines are counted and skipped, never raised.  A transcript that was
truncated by a crash is exactly the case where you most want the tool to keep
working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class Record:
    """One JSONL line, plus the line number that gives it its identity.

    ``uuid`` is **not** unique inside a file -- the reference session has 114
    uuids that occur twice, from a mid-session message replay.  The only safe
    key is ``(session_id, uuid, line_no)``, which is why ``line_no`` travels
    with every record instead of being thrown away after parsing.
    """

    line_no: int
    data: Dict[str, Any]

    # --- thin accessors, so the rest of the code never does data.get("...") ---

    @property
    def type(self) -> Optional[str]:
        return self.data.get("type")

    @property
    def subtype(self) -> Optional[str]:
        return self.data.get("subtype")

    @property
    def uuid(self) -> Optional[str]:
        return self.data.get("uuid")

    @property
    def parent_uuid(self) -> Optional[str]:
        return self.data.get("parentUuid")

    @property
    def has_lineage(self) -> bool:
        """True for records that participate in the message tree.

        Settings records (``mode``, ``last-prompt``, ``custom-title`` ...) have
        no ``uuid``/``parentUuid`` at all.  Treating their missing parentUuid as
        ``None`` would invent hundreds of fake tree roots -- a real bug that a
        naive implementation hits immediately.
        """
        return "uuid" in self.data and "parentUuid" in self.data

    @property
    def is_root(self) -> bool:
        return self.has_lineage and self.data.get("parentUuid") is None

    @property
    def timestamp(self) -> Optional[str]:
        return self.data.get("timestamp")

    @property
    def session_id(self) -> Optional[str]:
        return self.data.get("sessionId")

    @property
    def version(self) -> Optional[str]:
        return self.data.get("version")

    @property
    def cwd(self) -> Optional[str]:
        return self.data.get("cwd")

    @property
    def git_branch(self) -> Optional[str]:
        return self.data.get("gitBranch")

    @property
    def is_compact_boundary(self) -> bool:
        return self.data.get("type") == "system" and self.data.get("subtype") == "compact_boundary"

    @property
    def logical_parent_uuid(self) -> Optional[str]:
        """The link across a compaction seam.

        Verified: this field appears *only* on ``compact_boundary`` records.
        It is the field the desktop transcript renderer does not follow.
        """
        return self.data.get("logicalParentUuid")

    @property
    def compact_metadata(self) -> Dict[str, Any]:
        meta = self.data.get("compactMetadata")
        return meta if isinstance(meta, dict) else {}

    @property
    def message(self) -> Dict[str, Any]:
        msg = self.data.get("message")
        return msg if isinstance(msg, dict) else {}


@dataclass
class ReadStats:
    """Counters filled in as a file is streamed."""

    path: Optional[Path] = None
    lines_total: int = 0
    lines_blank: int = 0
    lines_malformed: int = 0
    malformed_line_numbers: List[int] = field(default_factory=list)
    records: int = 0

    def note_malformed(self, line_no: int) -> None:
        self.lines_malformed += 1
        if len(self.malformed_line_numbers) < 20:
            self.malformed_line_numbers.append(line_no)


def iter_records(path, stats: Optional[ReadStats] = None) -> Iterator[Record]:
    """Yield :class:`Record` objects from a JSONL file, one line at a time.

    ``utf-8-sig`` strips a byte-order mark if one is present, which otherwise
    makes the very first line unparseable on Windows-written files.
    """
    path = Path(path)
    if stats is not None:
        stats.path = path
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if stats is not None:
                stats.lines_total = line_no
            stripped = raw.strip()
            if not stripped:
                if stats is not None:
                    stats.lines_blank += 1
                continue
            try:
                data = json.loads(stripped)
            except ValueError:
                if stats is not None:
                    stats.note_malformed(line_no)
                continue
            if not isinstance(data, dict):
                # A bare string or list is structurally valid JSON but is not a
                # session record; treat it the same as a parse failure.
                if stats is not None:
                    stats.note_malformed(line_no)
                continue
            if stats is not None:
                stats.records += 1
            yield Record(line_no=line_no, data=data)
