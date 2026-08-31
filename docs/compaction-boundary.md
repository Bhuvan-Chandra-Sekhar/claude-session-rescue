# The compaction boundary, and why half a conversation disappears

## The symptom

A long conversation stops part-way through in the transcript pane. Scrolling
does nothing; the rest is not there. But `claude --resume` on the same session
continues correctly, and the model plainly remembers work that the transcript
does not show.

Nothing has been deleted. The messages are on disk, in the same file, in order.
They are simply not reachable by the walk the renderer performs.

## The mechanism

When Claude Code runs out of context it compacts the conversation: it summarises
what came before, throws the old messages out of the context window, and carries
on. To mark that, it appends a record of this shape — abridged, with identifiers
and figures replaced by illustrative ones:

```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "content": "Conversation compacted",
  "uuid": "<boundary-uuid>",
  "parentUuid": null,
  "logicalParentUuid": "<uuid of the last message before the break>",
  "compactMetadata": {
    "trigger": "auto",
    "preTokens": 900000,
    "postTokens": 18000,
    "durationMs": 172000,
    "cumulativeDroppedTokens": 882000,
    "preservedSegment": { "headUuid": "...", "anchorUuid": "...", "tailUuid": "..." }
  }
}
```

`tests/fixtures.py` generates exactly this record, which is what the test suite
checks the stitching against.

Two fields do all the damage:

**`parentUuid: null`.** In this format, a null parent means "this is the start of
a message tree". So from the point of view of anything walking parent links, the
file now contains **two** conversations, not one.

**`logicalParentUuid`.** This is the actual link across the gap — it points at
the last message before the compaction. It appears on `compact_boundary` records
and, in the data examined, nowhere else.

The next line is a `user` record with `isCompactSummary: true`, whose
`parentUuid` is the boundary's uuid: the "This session is being continued from a
previous conversation…" message. That summary is the only in-band explanation of
what tree 1 contained.

## Why the app shows nothing after the seam

Reconstructed from the shape of the data, not from source:

1. The renderer picks a root and walks *children by `parentUuid`* downward.
2. It reaches the last message of tree 1 and finds no child, because the next
   record's parent is `null`, not that message.
3. It stops. `logicalParentUuid` is a different field name; that walk never
   reads it.
4. The `custom-title` record is file-scoped, so both trees share one title —
   there is no second entry in the session list to click on either.
5. `claude --resume` works because it follows `last-prompt.leafUuid`, which
   points at a leaf *inside tree 2*. Resume follows the leaf; the transcript view
   follows the root. That is the whole reason the two disagree.

## What it looks like in practice

A representative case, with figures rounded to make the shape clear rather than
to report anyone's telemetry:

- A session file of roughly 20 MB and about 6,000 lines.
- Tree 1 starts at the top of the file, on an ordinary record with
  `parentUuid: null`. It runs for about three quarters of the file.
- Tree 2 starts at the `compact_boundary` record, also with `parentUuid: null`,
  and holds the remaining **quarter or so of the records** — in this case
  roughly 1,600 of them, covering about two days of work.
- Context went from around 900,000 tokens before the compaction to under 20,000
  after it, so on the order of **880,000 tokens** of context were dropped.
- The wall-clock gap between the last message of tree 1 and the first of tree 2
  matches `durationMs` exactly. The compaction itself *is* the gap.
- One file can straddle several Claude Code versions across the seam, because a
  long session survives app upgrades.

Everything in that list is on disk and readable. None of it after the seam is
drawn in the transcript pane.

To see the equivalent numbers for your own sessions, run
`claude-session-rescue example`.

## The fix, and this tool's version of it

**The real fix is one line of upstream logic**: when walking the tree, treat
`compact_boundary.logicalParentUuid → uuid` as an edge with the same standing as
`parentUuid`, and render a divider at the seam instead of stopping. A `⧉ 2`
badge that counts trees while the renderer draws only one is the same bug seen
from the other end.

**This tool cannot do that.** It cannot patch the desktop app, and it will not
modify your session files to work around it. What it does instead:

- `doctor` detects multiple roots, resolves each `logicalParentUuid` to the line
  it points at, and explains the situation in plain language.
- `export` walks the boundaries as first-class edges and writes the entire
  conversation — both sides of every seam — to a file you can read, with the
  seam marked and the dropped-token count stated.

That is a workaround, and the README says so. If you want the real fix, the
sanitized output of `doctor --report` is designed to be attached to an upstream
issue.

## Notes for anyone writing their own tooling

1. **Segment on `parentUuid == null`, then stitch on `logicalParentUuid`.** Do
   not assume one file is one tree.
2. **Only test `parentUuid` on records that have the key.** Settings records
   (`mode`, `custom-title`, `last-prompt`, `queue-operation`) have no lineage
   fields at all; treating their absence as null invents hundreds of roots.
3. **Order by line number.** Timestamps are not monotonic.
4. **`sessionId` does not change across a compaction.** Detecting chain
   boundaries by watching for a sessionId change detects nothing.
5. **Older versions may not write `logicalParentUuid`.** Fall back to file order
   and say that you did, rather than dropping the segment.
6. **Show the user the seam.** "Compacted here — 917,156 tokens dropped" is
   information they need; silent truncation is what caused the panic in the
   first place.
