"""Stitching a split session back into one conversation.

When Claude Code compacts a conversation it writes a ``system`` record with
``subtype: "compact_boundary"`` and, crucially, ``parentUuid: null``.  That null
starts a second message tree in the same file.  The link across the seam is a
*different* field, ``logicalParentUuid``, which points at the last message
before the compaction.

Anything that walks only ``parentUuid`` therefore stops at the seam.  This
module walks ``logicalParentUuid`` as a first-class edge, which is the whole
trick.

Two ordering facts drive the implementation, both verified on real data:

* Line order is reliable.  Segments appear in the file in the order they
  happened.
* Timestamps are **not** reliable -- in the reference file 471 of 5,094
  timestamped records go backwards relative to the previous line.  Timestamps
  are for display only; never sort by them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from claude_session_rescue.session import Segment, SessionAnalysis


@dataclass
class Chain:
    """The ordered segments of a session, plus how we arrived at that order."""

    segments: List[Segment] = field(default_factory=list)
    #: True when every non-first segment was attached via logicalParentUuid.
    fully_linked: bool = True
    notes: List[str] = field(default_factory=list)

    @property
    def seam_count(self) -> int:
        return max(0, len(self.segments) - 1)

    @property
    def total_dropped_tokens(self) -> Optional[int]:
        values = [s.dropped_tokens for s in self.segments if s.dropped_tokens is not None]
        return sum(values) if values else None


def build_chain(analysis: SessionAnalysis) -> Chain:
    """Order a session's segments into a single conversation.

    Strategy: start from the segment nothing links into, then repeatedly append
    whichever segment declares its ``logicalParentUuid`` inside the segment we
    just placed.  If the links run out -- old Claude Code versions did not write
    ``logicalParentUuid`` at all, and a corrupt file may lose it -- fall back to
    line order for the remainder and say so, rather than dropping content.
    """
    chain = Chain()
    segments = list(analysis.segments)
    if not segments:
        chain.notes.append("No message-tree roots found; the file has no renderable conversation.")
        return chain
    if len(segments) == 1:
        chain.segments = segments
        return chain

    # Which segment does each one claim to continue from?
    linked_from = {}
    for segment in segments:
        if segment.index == 0:
            # The first segment in the file is where the conversation started;
            # it is not supposed to link back to anything.
            continue
        if segment.logical_parent_segment is not None:
            linked_from[segment.index] = segment.logical_parent_segment
        elif segment.logical_parent_uuid:
            chain.fully_linked = False
            chain.notes.append(
                "Segment {0} (line {1}) has logicalParentUuid {2} but that uuid is "
                "not in this file, so the link could not be confirmed.".format(
                    segment.index + 1, segment.root_line, segment.logical_parent_uuid
                )
            )
        elif segment.started_by_compaction:
            chain.fully_linked = False
            chain.notes.append(
                "Segment {0} (line {1}) is a compaction boundary but carries no "
                "logicalParentUuid field. This happens on older Claude Code "
                "versions; falling back to file order.".format(
                    segment.index + 1, segment.root_line
                )
            )
        else:
            chain.fully_linked = False
            chain.notes.append(
                "Segment {0} (line {1}) starts a new tree with a {2} record and no "
                "link back. It may be an unrelated conversation stored in the same "
                "file; it is included at the end so nothing is lost.".format(
                    segment.index + 1, segment.root_line, segment.root_type or "unknown"
                )
            )

    children = {}
    for child, parent in linked_from.items():
        children.setdefault(parent, []).append(child)

    by_index = {s.index: s for s in segments}
    placed = set()
    ordered: List[Segment] = []

    # Roots of the link graph, in file order.
    starts = [s.index for s in segments if s.index not in linked_from]
    for start in starts:
        cursor: Optional[int] = start
        while cursor is not None and cursor not in placed:
            placed.add(cursor)
            ordered.append(by_index[cursor])
            following = sorted(children.get(cursor, []))
            cursor = following[0] if following else None
            # A segment with several claimed children would mean a genuine fork;
            # we have never observed one, so extra children are handled by the
            # leftover sweep below rather than guessed at.

    leftovers = [s for s in segments if s.index not in placed]
    if leftovers:
        chain.fully_linked = False
        ordered.extend(sorted(leftovers, key=lambda s: s.root_line))

    chain.segments = ordered
    return chain
