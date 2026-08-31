# claude-session-rescue

[![tests](https://github.com/Bhuvan-Chandra-Sekhar/claude-session-rescue/actions/workflows/test.yml/badge.svg)](https://github.com/Bhuvan-Chandra-Sekhar/claude-session-rescue/actions/workflows/test.yml)

Find, diagnose and rescue Claude Code session history.

**It never writes anything into your Claude data directory.** That is enforced in
code, not just promised — see [Read-only](#read-only-really).

---

## Does one of these sound like you?

| What you are seeing | What is probably going on | What to run |
|---|---|---|
| "A long chat is blank, or stops part-way — but Claude clearly still remembers what we did." | The conversation was **compacted**. That writes a record which starts a second message tree in the same file, and the transcript view only draws the first one. Nothing is lost on disk. | `claude-session-rescue doctor` |
| "My sessions vanished after I moved / renamed the project folder." | Claude Code files sessions under a name derived from the folder path. Move the folder and new sessions go somewhere new; the old history is intact but **orphaned**. | `claude-session-rescue doctor` |
| "My history is gone after reinstalling / upgrading." | Either the store moved, or it is still there and nothing is pointing at it. | `claude-session-rescue scan` |
| "Some sessions are missing and I've been using git worktrees." | Worktree sessions get their **own** project directory, because the working directory is different. | `claude-session-rescue scan` (groups them under the parent project) |
| "I want a copy of a conversation I can keep, search or paste elsewhere." | — | `claude-session-rescue export <session> --out ./rescued` |
| "I'm about to reinstall and I don't trust it." | — | `claude-session-rescue backup --out ./backup.zip` |
| "Just show me what all of this means for my machine." | — | `claude-session-rescue example` |

Not sure? Run it with no arguments:

```
claude-session-rescue
```

It finds your store, tells you what is in it in plain language, and suggests the
next command. No flags, no knowledge of the file format required.

---

## What this tool can and cannot do

**It can**: read your session files, tell you exactly what is on disk and why the
app might not be showing it, and write the complete conversation — including the
part the app will not draw — into a Markdown, text or JSON file you own.

**It cannot** patch the Claude Code desktop app. If a session is split by a
compaction, this tool will not make the missing half appear in the transcript
pane. `export` is the workaround, not a repair. That boundary is deliberate: the
fix for the renderer belongs upstream, and this tool refuses to modify your data
in order to work around it.

---

## Install

```bash
git clone https://github.com/Bhuvan-Chandra-Sekhar/claude-session-rescue
cd claude-session-rescue
pip install -e .
```

or, to keep it isolated:

```bash
pipx install .
```

Python 3.9 or newer. **No runtime dependencies** — everything is standard
library, so nothing else can break it and it installs anywhere Python does.
`pytest` is the only development dependency.

---

## Commands

### `scan` — what is on this machine

```bash
claude-session-rescue scan
claude-session-rescue scan --json          # machine-readable
claude-session-rescue scan --quick         # faster, less detail, for huge stores
```

Lists every project directory: the folder it came from, whether that folder still
exists, how many sessions, how big, what date range, which git branch, and a
title for each session. Git worktrees are grouped under their parent project.
Directories whose folder has gone are flagged **ORPHANED**.

### `doctor` — why can't I see my history

```bash
claude-session-rescue doctor                      # everything
claude-session-rescue doctor <session-id>         # one session (a prefix is fine)
claude-session-rescue doctor --project ~/code/app # one folder
claude-session-rescue doctor --report report.json # + a sanitized bug report
```

Every finding says three things: what was found, why the app cannot show it, and
the exact command that helps. It detects compaction splits, orphaned projects,
worktree sessions, duplicate message ids, version straddling, unparseable lines
and non-monotonic timestamps.

`--report` writes a file that is **safe to attach to a GitHub issue**: counts,
Claude Code version strings and field *names* only. No transcript text, no file
paths, no project names, no uuids — slugs and session ids are replaced with short
hashes so a maintainer can talk about "session 4f2a…" without knowing what it is.

### `export` — get the whole conversation out

```bash
claude-session-rescue export <session-id> --out ./rescued
claude-session-rescue export <session-id> --out ./rescued --format txt
claude-session-rescue export <session-id> --out ./rescued --split-bytes 800000
claude-session-rescue export --project ~/code/app --out ./rescued
```

Writes the conversation in order, **crossing every compaction boundary**, with a
header saying how many tokens the compaction dropped. Human and assistant turns
are rendered readably; tool calls are summarised as `Read(file_path=...)` rather
than dumped; long tool output is truncated with a marker saying how much was cut.
`--split-bytes` produces numbered parts plus an `INDEX.md`.

Useful flags: `--format md|txt|json`, `--include-thinking`, `--tool-output-limit
N` (`0` disables truncation), `--no-redact`.

### `example` — the walkthrough, on your own data

```bash
claude-session-rescue example
```

Runs the same analysis as `scan` and `doctor`, then narrates it: what is in your
store, which folders have moved, which conversations are split and by how much,
and the exact commands for your situation. If nothing is wrong it says so, and
still shows you what each failure would look like.

This exists so the documentation does not have to show anybody else's sessions.

### `backup` — before you reinstall

```bash
claude-session-rescue backup --out ./claude-backup.zip
```

Zips the entire store with a `MANIFEST.json` listing a SHA-256 for every file, so
a restore can be verified rather than hoped at. Restoring is a manual step on
purpose — this tool does not write into Claude directories, not even to help you.

---

## Read-only, really

Two rules, enforced in `safety.py` rather than by good intentions:

1. Every write in the entire tool goes through one function.
2. That function refuses any target inside `~/.claude`, inside the store you
   pointed at, or inside that store's parent.

`test_the_store_is_never_modified` in the test suite hashes every file in a test
store, runs every command against it, and asserts the bytes are identical
afterwards.

Additionally, **dry-run is the default whenever you do not name a destination.**
`export` and `backup` with no `--out` tell you what they would write and write
nothing.

---

## Redaction: what it does and does not guarantee

Exports pass every rendered string through a redactor before writing.

**It does** mask well-shaped credentials: Anthropic / OpenAI keys, GitHub
tokens, AWS access key ids, Google API keys, Slack tokens, Groq and Stripe keys,
JWTs, PEM private key blocks, `Bearer <token>` headers, credentials embedded in
URLs, and assignments like `password = ...` / `api_key: ...` where the value
looks credential-shaped. Obvious placeholders (`your-password-here`,
`os.getenv("KEY")`, `${DB_PASSWORD}`) are left alone so your code still reads
correctly.

**It does not** guarantee an export is safe to publish. It cannot recognise a
password with no label, a secret pasted as prose, an internal hostname, a
customer name, or anything else that is sensitive without being *shaped* like a
secret. **Read an export before you share it.** `--no-redact` exists for when you
are keeping the file yourself and want it verbatim.

---

## Worked example

**Run it against your own machine:**

```bash
claude-session-rescue example
```

That prints this walkthrough using *your* projects — what was found, what is
orphaned or split, and the exact commands for your situation. Nothing below is
required reading; it is the same shape, with invented names, for anyone who
wants to see it before installing.

---

A user — call her `alex` — has one project in her head, `my-project`, and four
project directories on disk. She moved the folder off a cloud-synced Desktop into
`~/code` a while back, and she has used two git worktrees:

```
-home-alex-code-my-project                         <- current
-home-alex-Desktop-my-project                      <- orphaned by the move
-home-alex-code-my-project--claude-worktrees-<name>-<hash>
-home-alex-code-my-project--claude-worktrees-<name>-<hash>
```

On Windows the same four would read `C--Users-alex-code-my-project` and so on.

`scan` groups the two worktree directories under the project they branched from,
flags the Desktop one as **ORPHANED** — its folder is gone, so nothing points at
that history any more — and marks one long session `SPLIT`. `doctor` on that
session says roughly this:

```
[!] "Refactoring the importer" is split into 2 parts and the app only draws
    the first
    ...
    Your conversation is not lost. It is all in the file. About 1,600 records
    (roughly a quarter of the file) sit after the first break and are not
    being drawn.

    break 1: line ~4,500, trigger 'auto', ~900,000 tokens dropped, links back
      to the message before the break
```

A large session file, a couple of days of work stranded after the break,
invisible in the transcript pane but present on disk — and `claude --resume`
still continues it, because resume follows the newest leaf rather than the root.
`export` writes both sides of the seam into one Markdown file in a couple of
seconds.

Figures here are illustrative round numbers. The real ones are whatever
`claude-session-rescue example` measures on your machine.

For the mechanism, see [docs/compaction-boundary.md](docs/compaction-boundary.md).

---

## How it works, briefly

* **Streaming.** Session files reach tens of megabytes. Nothing is ever loaded
  whole; records are yielded one line at a time.
* **Line order, never timestamp order.** Timestamps in real files are not
  monotonic — in the transcripts this was developed against, roughly one
  timestamp in ten was earlier than the line before it. Ordering by time
  scrambles the transcript. Line number is reliable.
* **`(sessionId, uuid, lineNumber)` as the key.** `uuid` is *not* unique within a
  file; replays produce duplicates. Keying on uuid alone silently merges
  messages.
* **`cwd` from inside the transcripts is ground truth.** The directory name is a
  lossy encoding of a path (`:`, `\`, `/`, space, `_` and `.` all become `-`), so
  it cannot be decoded with certainty. Instead the tool reads the working
  directory recorded inside the records themselves — which works regardless of
  how that version of Claude Code computed the name. Decoding is only a fallback,
  and is labelled as a guess when used.
* **Malformed input is data, not an error.** Unparseable lines are skipped,
  counted and reported. A file truncated by a crash still exports.

Details, with each claim marked verified or inferred, in
[docs/on-disk-format.md](docs/on-disk-format.md).

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against small synthetic fixtures generated by `tests/fixtures.py` —
including one reproducing a compaction split and one with duplicate uuids. **No
real transcripts are committed**; they contain private work.

CI runs the suite on Python 3.9–3.13 across Linux, macOS and Windows, and
separately asserts two things the README claims: that the package has no runtime
dependencies, and that the session store is never modified.

Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).
