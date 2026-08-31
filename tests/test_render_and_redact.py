"""Rendering and redaction."""

from __future__ import annotations

import pytest

from claude_session_rescue.jsonl import Record
from claude_session_rescue.redact import redact
from claude_session_rescue.render import (
    RenderOptions,
    RenderStats,
    markdown_to_text,
    render_record,
    summarise_tool_call,
)

from tests import fixtures


def render(record_dict, options=None, stats=None):
    stats = stats or RenderStats()
    return render_record(Record(1, record_dict), options or RenderOptions(), stats), stats


def test_human_turn_is_rendered_with_a_timestamp():
    text, stats = render(fixtures.user_turn("u1", None, "hello world", "2026-01-01T10:00:00.000Z"))
    assert "### User" in text
    assert "2026-01-01 10:00:00 UTC" in text
    assert "hello world" in text
    assert stats.human_turns == 1


def test_tool_calls_are_summarised_not_dumped():
    block = {"type": "tool_use", "name": "Read",
             "input": {"file_path": "/tmp/x.txt", "limit": 200}}
    summary = summarise_tool_call(block, RenderOptions())
    assert summary == "Read(file_path=/tmp/x.txt, limit=200)"


def test_long_tool_arguments_are_shortened():
    block = {"type": "tool_use", "name": "Write", "input": {"content": "x" * 500}}
    summary = summarise_tool_call(block, RenderOptions(tool_arg_limit=20))
    assert len(summary) < 60
    assert summary.endswith("...)")


def test_many_tool_arguments_are_capped():
    block = {"type": "tool_use", "name": "T", "input": {str(i): i for i in range(9)}}
    assert "+5 more" in summarise_tool_call(block, RenderOptions())


def test_large_tool_output_is_truncated_with_a_marker():
    record = fixtures.tool_result("t1", "a1", "y" * 5000, "2026-01-01T10:00:00.000Z")
    text, stats = render(record, RenderOptions(tool_output_limit=100))
    assert "[truncated 4,900 characters]" in text
    assert stats.truncated_blocks == 1
    assert stats.truncated_chars == 4900


def test_thinking_is_excluded_by_default():
    record = fixtures.assistant_turn("a1", "u1", "visible", "2026-01-01T10:00:00.000Z")
    record["message"]["content"].insert(0, {"type": "thinking", "thinking": "private reasoning"})

    text, _ = render(record)
    assert "private reasoning" not in text

    text, _ = render(record, RenderOptions(include_thinking=True))
    assert "private reasoning" in text


def test_compaction_seam_is_rendered_loudly():
    record = fixtures.compact_boundary("b1", "a1", "2026-01-01T11:00:00.000Z")
    text, _ = render(record)
    assert "Context compaction (auto)" in text
    assert "dropped: 882,000 tokens" in text
    assert "continues from message `a1`" in text


def test_records_with_no_content_are_skipped_and_counted():
    text, stats = render(fixtures.settings_record("mode", mode="auto"))
    assert text is None
    assert stats.skipped_records == 1


def test_markdown_to_text_strips_markup():
    plain = markdown_to_text("# Title\n\n**bold** text\n```\ncode\n```")
    assert "#" not in plain
    assert "**" not in plain
    assert "code" in plain


# These are shaped like credentials so the rules match them, and spelled so that
# no human or scanner can mistake them for live keys. Filler is the word EXAMPLE
# rather than a run of A's: repository secret-scanners flag high-entropy-looking
# blobs, and a test file is a bad place to argue with one. Nothing here is or
# ever was a real credential.
@pytest.mark.parametrize("secret", [
    "sk-ant-api03-EXAMPLENOTAREALKEY000000",
    "ghp_EXAMPLENOTAREALTOKEN00000000000000",
    "AKIAEXAMPLENOTREAL00",
    "gsk_EXAMPLENOTAREALKEY0000000000",
    "eyJFWEFNUExFTk9UUkVBTA.eyJFWEFNUExFTk9UUkVBTA.EXAMPLENOTAREALSIGNATURE",
])
def test_known_credential_shapes_are_masked(secret):
    out, count = redact("the value is {0} ok".format(secret))
    assert count == 1
    assert secret not in out
    assert "[REDACTED:" in out


def test_assigned_credentials_keep_their_label():
    out, count = redact("password = hunter2SuperSecret")
    assert count == 1
    assert out.startswith("password = [REDACTED:")


def test_placeholders_and_prose_survive():
    for harmless in [
        "password = your-password-here",
        "api_key = os.getenv('API_KEY')",
        "the old passwords were rejected",
        "password = ${DB_PASSWORD}",
    ]:
        out, count = redact(harmless)
        assert count == 0, harmless
        assert out == harmless


def test_redaction_can_be_turned_off():
    record = fixtures.user_turn("u1", None, "key sk-ant-api03-EXAMPLENOTAREALKEY0",
                               "2026-01-01T10:00:00.000Z")
    text, _ = render(record, RenderOptions(redact_secrets=False))
    assert "EXAMPLENOTAREALKEY0" in text
