# The on-disk format of Claude Code sessions

Everything here was worked out by reading real session files. Nothing is from
documentation, because there is none.

Each claim is tagged:

- **[verified]** — observed directly in real transcripts, and reproduced by a
  test in this repository.
- **[inferred]** — consistent with everything observed, but not proven. Treat as
  a working assumption.
- **[unknown]** — seen but not understood. Reported by the tool, never
  interpreted.

The evidence base was 8 session files (~58 MB, ~15,000 records) from a single
store, spanning Claude Code versions 2.1.170 to 2.1.243. **One machine, one
operating system, one user.** Anything that generalises beyond that is marked
inferred unless the tool derives it at runtime instead of assuming it.

---

## 1. Where sessions live

```
<claude-config>/projects/<slug>/<sessionUuid>.jsonl
```

**[verified]** `<claude-config>` defaults to `~/.claude`.
**[inferred]** `CLAUDE_CONFIG_DIR` relocates it; this tool checks that
environment variable and a couple of other plausible locations before giving up,
and always accepts `--projects-dir`.

**[verified]** The filename stem equals the `sessionId` field inside the file, in
all 8 files examined.

---

## 2. The slug

**[verified]** on Windows:

```
C:\Users\alex\code\my_project        ->   C--Users-alex-code-my-project
C:\Users\alex smith\code\my_project  ->   C--Users-alex-smith-code-my-project
```

Each of `:` `\` ` ` `_` becomes one `-`. `C:` plus the following `\` is where the
leading `--` comes from.

**[inferred]** on POSIX, by the same rule:

```
/home/alex/code/my_project           ->   -home-alex-code-my-project
```

**[inferred]** `/` and `.` behave the same way, which is why a POSIX path leads
with `-` and why a worktree path containing `\.claude-worktrees\` produces the
distinctive `--claude-worktrees-` run.

### The encoding is lossy

Five different characters collapse to `-`, so **a slug cannot be decoded back to
a path with certainty**. `/a/b-c`, `/a/b/c`, `/a/b c` and `/a/b_c` all produce
`-a-b-c`.

This tool therefore does not rely on decoding. In order of trust:

1. **[verified]** Every lineage-bearing record carries a `cwd` field. Walking a
   recorded `cwd` upwards until it re-encodes to the directory name recovers the
   original path exactly. This is what the tool does first, and it is
   version-independent: it does not matter how *that* build computed the slug.
2. If no ancestor matches, the shortest recorded `cwd` is used and flagged
   `recorded-unverified` — which is itself a useful signal that the slug rule
   differs on that machine.
3. Only if there is no `cwd` at all does the tool decode the slug, probing the
   real filesystem to disambiguate, and labelling the result a guess if it cannot
   confirm it.

**[verified]** `cwd` is *not* constant within a file — several real files contain
two or three different `cwd` values, because it records the directory at the
moment each record was written, including subdirectories. Hence the upward walk.

### Worktrees

**[verified]** Sessions started inside a git worktree are filed under their own
slug, derived from the worktree path, containing `--claude-worktrees-`.
**[inferred]** Splitting on that marker yields the parent project's slug, which
is how `scan` groups them.

---

## 3. Record types

One JSON object per line. Types observed, by role:

| `type` | Role |
|---|---|
| `user` | human turns **and** tool results |
| `assistant` | model turns: text, thinking and tool_use blocks |
| `attachment` | out-of-band context: hook output, file contents, tool deltas |
| `system` | hooks, API errors, **compaction boundaries** |
| `last-prompt` | `{sessionId, leafUuid, lastPrompt}` — the resume pointer |
| `custom-title` | `{sessionId, customTitle}` — the title shown in the UI |
| `mode`, `permission-mode`, `atis-latch` | session settings |
| `queue-operation` | the prompt queue |
| `frame-link`, `file-history-snapshot`, `file-history-delta`, `artifact-*` | misc |
| `bridge-session` | **[unknown]** — see §7 |

### Only some records have lineage

**[verified]** `uuid` and `parentUuid` appear only on `user`, `assistant`,
`attachment` and `system` records. In one 6,162-line file, 4,894 records had
them and 1,268 did not.

This matters more than it sounds. A settings record has *no* `parentUuid` key at
all. Code that does `record.get("parentUuid") is None` treats every one of them
as a tree root and reports a handful of sessions as hundreds of fragments. The
correct test is "has both keys **and** `parentUuid` is null".

### Distinguishing a human turn from a tool result

**[verified]** Both are `type: "user"`. A tool result's `message.content` is a
list containing `tool_result` blocks; a human turn's content is either a plain
string or a list of `text` blocks.

**[verified]** `userType` was `"external"` on every record in all 8 files.
`origin` was always `{"kind": "human"}`. `promptSource` was `"sdk"` or `"typed"`.
None of these discriminate anything useful — do not build on them. Look at the
content shape instead.

---

## 4. Ordering

**[verified]** Timestamps are **not** monotonic. In the reference file, 471 of
5,094 timestamped records were earlier than the line before them, because
attachments and their parents interleave, and because replayed sections keep
their original times.

**[verified]** Line order is reliable and matches conversation order.

**Consequence:** order by line number. Use timestamps for display only. Every
part of this tool follows that rule.

---

## 5. `uuid` is not unique

**[verified]** One 20 MB file contained 4,894 lineage-bearing records but only
4,780 distinct uuids: 114 uuids appeared twice, 228 occurrences in total, all
before the compaction boundary. This is a mid-session replay or re-anchor.

**Consequence:** the only safe key is `(sessionId, uuid, lineNumber)`. Keying on
`uuid` alone silently merges or drops messages.

---

## 6. Versions straddle a single file

**[verified]** A long session survives app upgrades. One file spans
`2.1.170` → `2.1.241`; another spans `2.1.227` → `2.1.243`. The record format can
therefore change *within* one file: fields present at the end may be absent at
the start.

**Consequence:** never require a field. This tool treats every field as optional
and reports what is missing rather than crashing on it.

---

## 7. Things reported but not interpreted

**[unknown]** `bridge-session` records, carrying `bridgeSessionId`,
`ownerAccountUuid`, `ownerOrganizationUuid` and `lastSequenceNum`, appear only in
newer builds (observed from 2.1.241). Their purpose is not documented and
guessing would be worse than saying nothing. `doctor` notes their presence and
stops there.

**[unknown]** `isSidechain` was `false` on every record in all 8 files, so
sub-agent transcripts were not exercised. The field is counted and reported.

**[unknown]** `frame-link`, `artifact-comment-monitor`,
`artifact-autoreact-ledger`, `file-history-delta`. Counted, not interpreted.

---

## 8. The compaction boundary

The important one. Full detail in
[compaction-boundary.md](compaction-boundary.md).

---

## 9. What has not been tested

Being explicit about the edges of the evidence:

- Only one operating system's store was examined directly. The slug *rule* is
  Windows-verified; POSIX behaviour is inferred, which is exactly why the tool
  prefers recorded `cwd` values over decoding.
- No sub-agent (`isSidechain: true`) transcripts were available.
- No file with more than two message trees was observed. Three-plus is handled
  and unit-tested against a synthetic fixture, but not against real data.
- No genuinely corrupt file was available; malformed-line handling is tested with
  synthetic junk and a synthetic truncated final line.

If you hit any of these, `claude-session-rescue doctor --report <file>` produces
a sanitized description that is safe to attach to an issue.
