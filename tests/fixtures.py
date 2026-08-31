"""Synthetic session files for the tests.

Written by hand rather than copied from a real machine. Real transcripts contain
private work, so none are committed here; these fixtures reproduce the *shapes*
that matter -- a compaction boundary, duplicate uuids, non-monotonic timestamps,
a truncated final line -- at a size you can read in one screen.

The field names and nesting mirror what was observed in real files. See
docs/on-disk-format.md for which of those observations are verified and which
are inferred.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SESSION_ID = "11111111-2222-3333-4444-555555555555"
DEFAULT_CWD = "/home/example/projects/demo app"
DEFAULT_SLUG = "-home-example-projects-demo-app"


def _base(uuid: str, parent: Optional[str], kind: str, timestamp: str,
          cwd: str = DEFAULT_CWD, version: str = "2.1.229",
          session_id: str = SESSION_ID) -> Dict[str, Any]:
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": kind,
        "uuid": uuid,
        "timestamp": timestamp,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session_id,
        "version": version,
        "gitBranch": "main",
    }


def user_turn(uuid: str, parent: Optional[str], text: str, timestamp: str, **kw) -> Dict[str, Any]:
    record = _base(uuid, parent, "user", timestamp, **kw)
    record["message"] = {"role": "user", "content": text}
    record["promptSource"] = "typed"
    record["origin"] = {"kind": "human"}
    return record


def assistant_turn(uuid: str, parent: str, text: str, timestamp: str, **kw) -> Dict[str, Any]:
    record = _base(uuid, parent, "assistant", timestamp, **kw)
    record["message"] = {
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": text}],
    }
    return record


def tool_call(uuid: str, parent: str, name: str, args: Dict[str, Any], timestamp: str,
              tool_id: str = "toolu_1", **kw) -> Dict[str, Any]:
    record = _base(uuid, parent, "assistant", timestamp, **kw)
    record["message"] = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}],
    }
    return record


def tool_result(uuid: str, parent: str, output: str, timestamp: str,
                tool_id: str = "toolu_1", **kw) -> Dict[str, Any]:
    record = _base(uuid, parent, "user", timestamp, **kw)
    record["message"] = {
        "role": "user",
        "content": [{"tool_use_id": tool_id, "type": "tool_result", "content": output}],
    }
    return record


def compact_boundary(uuid: str, logical_parent: str, timestamp: str,
                     pre_tokens: int = 900000, post_tokens: int = 18000,
                     trigger: str = "auto", **kw) -> Dict[str, Any]:
    """The record at the heart of this tool.

    Note ``parentUuid: null`` alongside ``logicalParentUuid``: that combination
    is what makes a renderer that only knows ``parentUuid`` stop dead here.
    """
    record = _base(uuid, None, "system", timestamp, **kw)
    record["subtype"] = "compact_boundary"
    record["content"] = "Conversation compacted"
    record["level"] = "info"
    record["logicalParentUuid"] = logical_parent
    record["compactMetadata"] = {
        "trigger": trigger,
        "preTokens": pre_tokens,
        "postTokens": post_tokens,
        "durationMs": 172005,
        "cumulativeDroppedTokens": pre_tokens - post_tokens,
        "preservedSegment": {
            "headUuid": logical_parent,
            "anchorUuid": logical_parent,
            "tailUuid": logical_parent,
        },
    }
    return record


def compact_summary(uuid: str, parent: str, timestamp: str, **kw) -> Dict[str, Any]:
    record = user_turn(uuid, parent, "This session is being continued from a previous "
                                     "conversation.\n\nSummary: the earlier work.", timestamp, **kw)
    record["isCompactSummary"] = True
    return record


def settings_record(kind: str, **fields: Any) -> Dict[str, Any]:
    """A record with no uuid/parentUuid at all.

    These exist in every real file (``mode``, ``last-prompt``, ``custom-title``,
    ``queue-operation``). A parser that treats their absent parentUuid as null
    will report hundreds of fake tree roots, so the fixtures include them.
    """
    record: Dict[str, Any] = {"type": kind, "sessionId": SESSION_ID}
    record.update(fields)
    return record


def write_jsonl(path: Path, records: List[Dict[str, Any]], truncate_last: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    text = "\n".join(lines)
    if truncate_last:
        text = text[: -max(10, len(lines[-1]) // 2)]  # chop the tail of the last line
    path.write_text(text + ("\n" if not truncate_last else ""), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# Whole-file fixtures
# ----------------------------------------------------------------------


def simple_session(path: Path) -> Path:
    """One tree, one exchange, no problems."""
    records = [
        settings_record("queue-operation", operation="enqueue"),
        settings_record("custom-title", customTitle="A simple session"),
        user_turn("u1", None, "hello there", "2026-01-01T10:00:00.000Z"),
        assistant_turn("a1", "u1", "Hi. What are we building?", "2026-01-01T10:00:01.000Z"),
        user_turn("u2", "a1", "a small parser", "2026-01-01T10:00:09.000Z"),
        assistant_turn("a2", "u2", "Sounds good.", "2026-01-01T10:00:10.000Z"),
        settings_record("last-prompt", leafUuid="a2", lastPrompt="a small parser"),
    ]
    return write_jsonl(path, records)


def compacted_session(path: Path) -> Path:
    """The headline case: two message trees in one file.

    Segment 1 is lines 3-6; the boundary at line 7 has ``parentUuid: null`` and
    a ``logicalParentUuid`` pointing back at the last message of segment 1.
    """
    records = [
        settings_record("queue-operation", operation="enqueue"),
        settings_record("custom-title", customTitle="Compacted session"),
        user_turn("u1", None, "start the long job", "2026-01-01T10:00:00.000Z"),
        assistant_turn("a1", "u1", "Working on it.", "2026-01-01T10:00:01.000Z"),
        tool_call("a2", "a1", "Read", {"file_path": "/tmp/x.txt"}, "2026-01-01T10:00:02.000Z"),
        tool_result("t1", "a2", "line one\nline two", "2026-01-01T10:00:03.000Z"),
        compact_boundary("b1", "t1", "2026-01-01T11:00:00.000Z"),
        compact_summary("s1", "b1", "2026-01-01T11:00:01.000Z"),
        user_turn("u3", "s1", "carry on from where we were", "2026-01-01T11:05:00.000Z",
                  version="2.1.241"),
        assistant_turn("a3", "u3", "Continuing. Here is the rest.", "2026-01-01T11:05:02.000Z",
                       version="2.1.241"),
        settings_record("last-prompt", leafUuid="a3", lastPrompt="carry on"),
    ]
    return write_jsonl(path, records)


def twice_compacted_session(path: Path) -> Path:
    """Three trees: a case we have not seen in the wild but must handle."""
    records = [
        user_turn("u1", None, "first", "2026-01-01T10:00:00.000Z"),
        assistant_turn("a1", "u1", "first reply", "2026-01-01T10:00:01.000Z"),
        compact_boundary("b1", "a1", "2026-01-01T11:00:00.000Z"),
        user_turn("u2", "b1", "second", "2026-01-01T11:00:01.000Z"),
        assistant_turn("a2", "u2", "second reply", "2026-01-01T11:00:02.000Z"),
        compact_boundary("b2", "a2", "2026-01-01T12:00:00.000Z", trigger="manual"),
        user_turn("u3", "b2", "third", "2026-01-01T12:00:01.000Z"),
        assistant_turn("a3", "u3", "third reply", "2026-01-01T12:00:02.000Z"),
    ]
    return write_jsonl(path, records)


def duplicate_uuid_session(path: Path) -> Path:
    """A replayed section: the same uuids written twice, and time going backwards."""
    records = [
        user_turn("u1", None, "hello", "2026-01-01T10:00:05.000Z"),
        assistant_turn("a1", "u1", "hi", "2026-01-01T10:00:06.000Z"),
        # replay of the same two records, with earlier timestamps
        user_turn("u1", None, "hello", "2026-01-01T10:00:01.000Z"),
        assistant_turn("a1", "u1", "hi", "2026-01-01T10:00:02.000Z"),
        user_turn("u2", "a1", "still here?", "2026-01-01T10:00:09.000Z"),
    ]
    # The replayed "u1" has parentUuid null, which would make it a third root;
    # give it the real shape instead: replays keep their original parent.
    records[2]["parentUuid"] = None
    return write_jsonl(path, records)


def malformed_session(path: Path) -> Path:
    """Valid records with junk in the middle and a truncated final line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    good = [
        json.dumps(user_turn("u1", None, "hello", "2026-01-01T10:00:00.000Z")),
        "{not json at all",
        json.dumps(assistant_turn("a1", "u1", "hi", "2026-01-01T10:00:01.000Z")),
        '"a bare string, valid json but not a record"',
        "",
        json.dumps(user_turn("u2", "a1", "bye", "2026-01-01T10:00:02.000Z")),
    ]
    truncated = json.dumps(assistant_turn("a2", "u2", "goodb", "2026-01-01T10:00:03.000Z"))[:40]
    path.write_text("\n".join(good + [truncated]), encoding="utf-8")
    return path


def make_store(root: Path) -> Path:
    """A whole store with several projects, including the awkward ones."""
    projects = root / "projects"

    live = projects / DEFAULT_SLUG
    simple_session(live / "aaaaaaaa-0000-0000-0000-000000000001.jsonl")
    compacted_session(live / "bbbbbbbb-0000-0000-0000-000000000002.jsonl")

    # A project whose folder was moved: nothing at the recorded cwd any more.
    moved_cwd = "/home/example/old_place/demo app"
    moved = projects / "-home-example-old-place-demo-app"
    records = [
        user_turn("m1", None, "work in the old place", "2026-01-01T09:00:00.000Z", cwd=moved_cwd),
        assistant_turn("m2", "m1", "sure", "2026-01-01T09:00:01.000Z", cwd=moved_cwd),
    ]
    write_jsonl(moved / "cccccccc-0000-0000-0000-000000000003.jsonl", records)

    # A git worktree session, filed under its own slug.
    wt_cwd = "/home/example/projects/demo app/.claude-worktrees/feature-a1b2c3"
    worktree = projects / (DEFAULT_SLUG + "--claude-worktrees-feature-a1b2c3")
    write_jsonl(
        worktree / "dddddddd-0000-0000-0000-000000000004.jsonl",
        [user_turn("w1", None, "on a branch", "2026-01-02T09:00:00.000Z", cwd=wt_cwd)],
    )

    # An empty file and a directory with nothing in it: both must not crash.
    (live / "eeeeeeee-0000-0000-0000-000000000005.jsonl").write_text("", encoding="utf-8")
    (projects / "-home-example-empty-project").mkdir(parents=True, exist_ok=True)

    return projects
