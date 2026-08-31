"""Reading files that are not perfect, and the facts we derive from them."""

from __future__ import annotations

import json

import pytest

from claude_session_rescue.jsonl import ReadStats, iter_records
from claude_session_rescue.session import analyze, is_human_turn, sorted_versions


def test_reads_a_clean_file(simple):
    stats = ReadStats()
    records = list(iter_records(simple, stats))
    assert stats.lines_malformed == 0
    assert stats.records == len(records) == 7


def test_settings_records_are_not_tree_roots(simple):
    """A missing parentUuid is not the same as a null one.

    ``custom-title`` and friends have neither uuid nor parentUuid. Counting
    them as roots would report a handful of real sessions as hundreds of
    fragments.
    """
    records = list(iter_records(simple))
    settings = [r for r in records if r.type in ("custom-title", "last-prompt", "queue-operation")]
    assert settings, "fixture should contain settings records"
    assert all(not r.has_lineage for r in settings)
    assert all(not r.is_root for r in settings)
    assert sum(1 for r in records if r.is_root) == 1


def test_malformed_lines_are_skipped_and_counted(malformed):
    stats = ReadStats()
    records = list(iter_records(malformed, stats))
    assert stats.lines_malformed == 3  # junk line, bare string, truncated tail
    assert stats.lines_blank == 1
    assert [r.uuid for r in records] == ["u1", "a1", "u2"]


def test_analyze_survives_a_malformed_file(malformed):
    analysis = analyze(malformed)
    assert analysis.error is None
    assert analysis.read.lines_malformed == 3
    assert analysis.human_turns == 2


def test_empty_file_is_reported_not_crashed(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    analysis = analyze(path)
    assert analysis.error is None
    assert analysis.is_empty
    assert analysis.segments == []


def test_missing_file_is_reported_not_raised(tmp_path):
    analysis = analyze(tmp_path / "nope.jsonl")
    assert analysis.error is not None
    assert "no longer exists" in analysis.error


def test_byte_order_mark_does_not_break_the_first_line(tmp_path):
    path = tmp_path / "bom.jsonl"
    record = {"type": "user", "uuid": "u1", "parentUuid": None,
              "message": {"role": "user", "content": "hi"}}
    path.write_text("﻿" + json.dumps(record) + "\n", encoding="utf-8")
    assert [r.uuid for r in iter_records(path)] == ["u1"]


def test_invalid_utf8_bytes_do_not_crash(tmp_path):
    path = tmp_path / "bytes.jsonl"
    record = {"type": "user", "uuid": "u1", "parentUuid": None,
              "message": {"role": "user", "content": "hi"}}
    path.write_bytes(json.dumps(record).encode("utf-8") + b"\n" + b"\xff\xfe not utf8\n")
    stats = ReadStats()
    records = list(iter_records(path, stats))
    assert [r.uuid for r in records] == ["u1"]
    assert stats.lines_malformed == 1


def test_records_missing_expected_fields_are_tolerated(tmp_path):
    """Old Claude Code versions did not write every field we use."""
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({
        "type": "user", "uuid": "u1", "parentUuid": None,
        "message": {"role": "user", "content": "hello from an old version"},
    }) + "\n", encoding="utf-8")
    analysis = analyze(path)
    assert analysis.error is None
    assert analysis.human_turns == 1
    assert analysis.versions == []
    assert analysis.first_timestamp is None
    assert analysis.display_title == "hello from an old version"


def test_tool_results_are_not_counted_as_human_turns(compacted):
    records = list(iter_records(compacted))
    results = [r for r in records if r.type == "user" and not is_human_turn(r)]
    assert len(results) == 1
    assert analyze(compacted).human_turns == 3  # 2 typed + the injected summary


def test_duplicate_uuids_are_detected(duplicates):
    analysis = analyze(duplicates)
    assert analysis.duplicate_uuids == 2
    assert analysis.duplicate_uuid_occurrences == 2


def test_non_monotonic_timestamps_are_detected(duplicates):
    analysis = analyze(duplicates)
    assert analysis.timestamp_regressions >= 1


def test_version_sorting_is_numeric_not_lexicographic():
    assert sorted_versions(["2.1.229", "2.1.9", "2.1.170"]) == ["2.1.9", "2.1.170", "2.1.229"]


def test_title_falls_back_to_the_first_prompt(tmp_path, duplicates):
    analysis = analyze(duplicates)
    assert analysis.title is None
    assert analysis.display_title == "hello"
