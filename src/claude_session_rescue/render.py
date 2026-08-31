"""Turning records into something a human can read.

Design rules, in priority order:

* Never lose a human turn or an assistant message.
* Summarise tool calls (name + short args) instead of dumping them; a session
  is mostly tool traffic and dumping it buries the conversation.
* Truncate long tool output with an explicit marker saying how much was cut, so
  the reader knows something is missing and by how much.
* Mark the compaction seam loudly, because that is the thing the desktop app
  silently swallows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from claude_session_rescue.jsonl import Record
from claude_session_rescue.redact import redact
from claude_session_rescue.session import Segment, is_human_turn


@dataclass
class RenderOptions:
    """Everything the renderer can be told to do differently."""

    tool_output_limit: int = 2000
    text_limit: int = 0  # 0 = never truncate conversation text
    tool_arg_limit: int = 160
    include_thinking: bool = False
    include_tool_calls: bool = True
    include_tool_results: bool = True
    include_system: bool = True
    redact_secrets: bool = True


@dataclass
class RenderStats:
    human_turns: int = 0
    assistant_turns: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    system_notices: int = 0
    skipped_records: int = 0
    redactions: int = 0
    truncated_blocks: int = 0
    truncated_chars: int = 0


def _clean(text: str, options: RenderOptions, stats: RenderStats) -> str:
    if options.redact_secrets:
        text, hits = redact(text)
        stats.redactions += hits
    return text


def _truncate(text: str, limit: int, stats: RenderStats) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    cut = len(text) - limit
    stats.truncated_blocks += 1
    stats.truncated_chars += cut
    return text[:limit] + "\n... [truncated {0:,} characters]".format(cut)


def short_timestamp(ts: Optional[str]) -> str:
    """``2026-08-17T01:32:21.892Z`` -> ``2026-08-17 01:32:21 UTC``.

    Purely cosmetic. Timestamps are display-only in this tool; ordering always
    comes from line numbers.
    """
    if not ts:
        return "no timestamp"
    text = ts.replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1] + " UTC"
    if "." in text:
        head, tail = text.split(".", 1)
        suffix = " UTC" if tail.endswith(" UTC") else ""
        text = head + suffix
    return text


def summarise_tool_call(block: Dict[str, Any], options: RenderOptions) -> str:
    """``Read(file_path=..., limit=200)`` -- name plus a few short arguments."""
    name = block.get("name") or "unknown-tool"
    args = block.get("input")
    if not isinstance(args, dict) or not args:
        return "{0}()".format(name)
    parts = []
    for key in list(args)[:4]:
        value = args[key]
        if isinstance(value, str):
            text = " ".join(value.split())
        else:
            try:
                text = json.dumps(value)
            except (TypeError, ValueError):
                text = str(value)
        if len(text) > options.tool_arg_limit:
            text = text[: options.tool_arg_limit] + "..."
        parts.append("{0}={1}".format(key, text))
    if len(args) > 4:
        parts.append("... +{0} more".format(len(args) - 4))
    return "{0}({1})".format(name, ", ".join(parts))


def _tool_result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
                elif part.get("type") == "image":
                    pieces.append("[image omitted]")
        return "\n".join(pieces)
    if content is None:
        return ""
    return str(content)


def render_record(record: Record, options: RenderOptions, stats: RenderStats) -> Optional[str]:
    """Render one record to Markdown, or return ``None`` to skip it.

    Records with no conversational content -- attachments, hook summaries, mode
    changes, queue operations -- are skipped and counted, not dropped silently.
    """
    kind = record.type

    if kind == "user":
        content = record.message.get("content")
        if is_human_turn(record):
            stats.human_turns += 1
            text = content if isinstance(content, str) else _first_text_blocks(content)
            body = _clean(text or "", options, stats)
            body = _truncate(body, options.text_limit, stats)
            return "### User  ·  {0}  ·  line {1}\n\n{2}\n".format(
                short_timestamp(record.timestamp), record.line_no, body.strip()
            )
        if not options.include_tool_results:
            stats.skipped_records += 1
            return None
        blocks = content if isinstance(content, list) else []
        rendered = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            stats.tool_results += 1
            text = _clean(_tool_result_text(block), options, stats)
            text = _truncate(text.strip(), options.tool_output_limit, stats)
            marker = "error" if block.get("is_error") else "result"
            rendered.append(
                "> **Tool {0}** (`{1}`)\n\n```\n{2}\n```".format(
                    marker, block.get("tool_use_id", "?"), text
                )
            )
        if not rendered:
            stats.skipped_records += 1
            return None
        return "\n\n".join(rendered) + "\n"

    if kind == "assistant":
        content = record.message.get("content")
        if not isinstance(content, list):
            stats.skipped_records += 1
            return None
        pieces: List[str] = []
        counted_turn = False
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                if not counted_turn:
                    stats.assistant_turns += 1
                    counted_turn = True
                body = _clean(block["text"], options, stats)
                pieces.append(_truncate(body.strip(), options.text_limit, stats))
            elif btype == "thinking" and options.include_thinking:
                thought = block.get("thinking")
                if isinstance(thought, str):
                    body = _clean(thought.strip(), options, stats)
                    pieces.append("_(thinking)_\n\n> " + body.replace("\n", "\n> "))
            elif btype == "tool_use":
                stats.tool_calls += 1
                if options.include_tool_calls:
                    pieces.append("→ **{0}**".format(summarise_tool_call(block, options)))
        if not pieces:
            stats.skipped_records += 1
            return None
        header = "### Assistant  ·  {0}  ·  line {1}".format(
            short_timestamp(record.timestamp), record.line_no
        )
        return header + "\n\n" + "\n\n".join(pieces) + "\n"

    if kind == "system":
        if not options.include_system:
            stats.skipped_records += 1
            return None
        if record.is_compact_boundary:
            return render_seam(record)
        if record.subtype in ("api_error",):
            stats.system_notices += 1
            error = record.data.get("error")
            message = ""
            if isinstance(error, dict):
                message = str(error.get("formatted") or error.get("message") or "")
            return "_[system: {0}{1}]_\n".format(
                record.subtype, (" - " + message) if message else ""
            )
        stats.skipped_records += 1
        return None

    stats.skipped_records += 1
    return None


def _first_text_blocks(content: Any) -> Optional[str]:
    if isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        if texts:
            return "\n\n".join(texts)
    return None


def render_seam(record: Record) -> str:
    """The divider that the desktop transcript pane does not draw."""
    meta = record.compact_metadata
    pre, post = meta.get("preTokens"), meta.get("postTokens")
    dropped = meta.get("cumulativeDroppedTokens")
    if dropped is None and isinstance(pre, int) and isinstance(post, int):
        dropped = pre - post
    trigger = meta.get("trigger") or "unknown"
    duration = meta.get("durationMs")

    lines = [
        "",
        "---",
        "",
        "## Context compaction ({0})  ·  {1}  ·  line {2}".format(
            trigger, short_timestamp(record.timestamp), record.line_no
        ),
        "",
        "Everything below this line is a **second message tree in the same "
        "file**. The desktop transcript view walks `parentUuid` only, so it "
        "stops here and never renders the rest. Nothing is missing from disk.",
        "",
    ]
    facts = []
    if isinstance(pre, int):
        facts.append("context before: {0:,} tokens".format(pre))
    if isinstance(post, int):
        facts.append("context after: {0:,} tokens".format(post))
    if isinstance(dropped, int):
        facts.append("dropped: {0:,} tokens".format(dropped))
    if isinstance(duration, int):
        facts.append("compaction took: {0:.1f}s".format(duration / 1000.0))
    if record.logical_parent_uuid:
        facts.append("continues from message `{0}`".format(record.logical_parent_uuid))
    for fact in facts:
        lines.append("- {0}".format(fact))
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def markdown_to_text(markdown: str) -> str:
    """A crude but predictable Markdown -> plain text conversion.

    Deliberately simple: strip heading hashes, code fences and bold markers.
    Nobody needs a Markdown parser dependency for this.
    """
    out = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if stripped.startswith("#"):
            line = line.lstrip("#").strip()
            out.append(line)
            out.append("-" * min(len(line), 72))
            continue
        line = line.replace("**", "").replace("_(thinking)_", "(thinking)")
        out.append(line)
    return "\n".join(out)


def record_to_json(record: Record, options: RenderOptions, stats: RenderStats) -> Optional[Dict[str, Any]]:
    """A structured, redacted version of a record for ``--format json``.

    Keeps the fields that matter for downstream processing and drops the API
    bookkeeping (usage, cache stats, thinking signatures) that makes raw JSONL
    unpleasant to work with.
    """
    rendered = render_record(record, options, stats)
    if rendered is None:
        return None
    entry: Dict[str, Any] = {
        "line": record.line_no,
        "type": record.type,
        "timestamp": record.timestamp,
        "uuid": record.uuid,
        "parentUuid": record.parent_uuid,
        "text": rendered,
    }
    if record.is_compact_boundary:
        entry["compactMetadata"] = {
            key: record.compact_metadata.get(key)
            for key in ("trigger", "preTokens", "postTokens", "durationMs", "cumulativeDroppedTokens")
        }
        entry["logicalParentUuid"] = record.logical_parent_uuid
    return entry


def segment_heading(segment: Segment, position: int, total: int) -> str:
    """Heading printed at the start of each segment in an export."""
    label = "continuation after compaction" if segment.started_by_compaction else "opening segment"
    span = "lines {0}-{1}".format(segment.root_line, segment.end_line or "?")
    return "## Segment {0} of {1} - {2} ({3})\n".format(position, total, label, span)
