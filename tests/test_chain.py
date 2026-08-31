"""Segment detection and stitching -- the core of the tool."""

from __future__ import annotations

import json

from claude_session_rescue.chain import build_chain
from claude_session_rescue.session import analyze

from tests import fixtures


def test_a_normal_session_is_one_segment(simple):
    analysis = analyze(simple)
    assert len(analysis.segments) == 1
    assert not analysis.is_split
    chain = build_chain(analysis)
    assert chain.seam_count == 0
    assert chain.notes == []


def test_compaction_creates_a_second_root(compacted):
    analysis = analyze(compacted)
    assert analysis.is_split
    assert len(analysis.segments) == 2

    first, second = analysis.segments
    assert first.root_line == 3
    assert first.end_line == 6
    assert not first.started_by_compaction

    assert second.started_by_compaction
    assert second.root_subtype == "compact_boundary"
    # The break itself: a null parentUuid with a logicalParentUuid beside it.
    assert second.logical_parent_uuid == "t1"
    assert second.logical_parent_segment == 0
    assert second.dropped_tokens == 900000 - 18000


def test_chain_orders_segments_and_reports_no_problems(compacted):
    chain = build_chain(analyze(compacted))
    assert [s.index for s in chain.segments] == [0, 1]
    assert chain.fully_linked
    assert chain.notes == []
    assert chain.total_dropped_tokens == 882000


def test_three_segments_from_two_compactions(twice_compacted):
    analysis = analyze(twice_compacted)
    assert len(analysis.segments) == 3
    chain = build_chain(analysis)
    assert [s.index for s in chain.segments] == [0, 1, 2]
    assert chain.seam_count == 2
    assert chain.total_dropped_tokens == 2 * (900000 - 18000)


def test_boundary_without_a_logical_parent_falls_back_and_says_so(tmp_path):
    """Older versions did not write logicalParentUuid; nothing may be dropped."""
    path = tmp_path / "old-boundary.jsonl"
    boundary = fixtures.compact_boundary("b1", "a1", "2026-01-01T11:00:00.000Z")
    del boundary["logicalParentUuid"]
    fixtures.write_jsonl(path, [
        fixtures.user_turn("u1", None, "first", "2026-01-01T10:00:00.000Z"),
        fixtures.assistant_turn("a1", "u1", "reply", "2026-01-01T10:00:01.000Z"),
        boundary,
        fixtures.user_turn("u2", "b1", "second", "2026-01-01T11:00:01.000Z"),
    ])
    chain = build_chain(analyze(path))
    assert [s.index for s in chain.segments] == [0, 1]
    assert not chain.fully_linked
    assert any("no logicalParentUuid" in note for note in chain.notes)


def test_dangling_logical_parent_is_reported(tmp_path):
    path = tmp_path / "dangling.jsonl"
    fixtures.write_jsonl(path, [
        fixtures.user_turn("u1", None, "first", "2026-01-01T10:00:00.000Z"),
        fixtures.compact_boundary("b1", "not-in-this-file", "2026-01-01T11:00:00.000Z"),
        fixtures.user_turn("u2", "b1", "second", "2026-01-01T11:00:01.000Z"),
    ])
    analysis = analyze(path)
    assert analysis.segments[1].logical_parent_segment is None
    chain = build_chain(analysis)
    assert not chain.fully_linked
    assert len(chain.segments) == 2  # still exported, nothing lost


def test_an_unrelated_second_tree_is_kept_at_the_end(tmp_path):
    path = tmp_path / "two-trees.jsonl"
    fixtures.write_jsonl(path, [
        fixtures.user_turn("u1", None, "first tree", "2026-01-01T10:00:00.000Z"),
        fixtures.user_turn("u2", None, "second tree, no boundary record",
                           "2026-01-01T11:00:00.000Z"),
    ])
    chain = build_chain(analyze(path))
    assert len(chain.segments) == 2
    assert not chain.fully_linked
    assert any("unrelated" in note for note in chain.notes)


def test_line_order_is_used_not_timestamp_order(duplicates):
    """Timestamps go backwards in this fixture; segments must not reorder."""
    analysis = analyze(duplicates)
    chain = build_chain(analysis)
    lines = [s.root_line for s in chain.segments]
    assert lines == sorted(lines)
