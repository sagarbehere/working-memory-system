# Working Memory

A personal second brain you talk to. Send a thought from any chat client; it
gets filed. Ask later in plain language; it comes back. Anything with a
deadline turns into a reminder that reaches every device you own.

It runs as a skill inside [Hermes](https://github.com/sagarbehere/hermes), a
self-hosted personal-agent gateway, on your own machine. There is no app, no
web UI, and nothing to log into.

---

## Why it exists

Every note-taking system asks you to decide *where something goes* at the exact
moment you least want to think about it — when you have a thought and want it
out of your head. So you either stop capturing, or you accumulate an inbox you
never process.

This inverts that. **Capture is dumb and instant; organisation happens
afterwards, and is done for you.** You send the thought; the agent decides what
kind of thing it is, files it, and confirms in one line. If it decides wrong,
you say so and it moves it. Nothing is lost either way, because everything you
said is also appended verbatim to an append-only transcript before any
judgment is applied to it.

The filing model itself is deliberately not folders-by-subject. Things are
classified by **how you will get them back**, not what they are about — see
[`second-brain-schema.md`](second-brain-schema.md), which is written to stand
alone and may be useful even if you never run this code.

---

## What it looks like

In a chat you have reserved for it, you just talk:

```
you   printer is out of ink
bot   ✅ → wiki (record): printer out of ink

you   remind me Tuesday at 8am to call the plumber
bot   ✅ → Todoist: call the plumber (Tue 08:00)

you   BP 128/82 this morning
bot   ✅ → wiki (series): blood-pressure

you   what's my BP been doing?
bot   Six readings since the 12th, trending slightly up — 128/82 this
      morning versus 121/79 two weeks ago. The two highest were both
      after evening coffee.
```

Anywhere else — any chat, any platform — prefix with `Hey memory`:

```
you   Hey memory the plumber's number is 555-0134
bot   ✅ → wiki (reference/entity): plumber
```

That last answer is the point of the whole design. The readings live in one
small markdown file, so the agent reads the file and *reasons over it*. There
is no query language to express "the two highest were after evening coffee."

---

## How it works

Four moving parts, and the split between them is the design:

**1. A capture gate** (a Hermes hook) decides whether a message is memory input
at all — because it is in a chat you reserved, or because it starts with
`Hey memory`. Everything else falls through as ordinary conversation. Matching
messages are buffered for a few seconds so three thoughts typed in a row become
one turn, not three.

**2. A transcript.** Every capture is appended verbatim to a monthly markdown
file before anything else happens. Nothing links to it and nothing is rebuilt
from it. It exists for one reason: the agent's judgment is the only unreliable
part of this system, and the transcript is the only thing upstream of it. If a
thought is mis-filed — or judged to be chit-chat and not filed at all — the
words are still there.

**3. The agent** classifies and routes, following the policy in
[`SKILL.md`](SKILL.md). This is where judgment lives, and only judgment.

**4. Deterministic tools** do everything that must be exactly right — writing
the transcript, talking to Todoist, locking, backups. The agent *calls* these;
it never improvises the mechanics.

That boundary is the rule the project is built on: **anything that must be
correct is code the agent calls, not behaviour the agent is asked to perform.**
When something goes wrong, the fix is usually a new constraint in a tool rather
than a new instruction in the prompt.

Where things end up:

| You said | It lands in |
|---|---|
| Something with a deadline | Todoist — which notifies your phone, watch, laptop |
| A repeated measurement | One growing note per series, a line per entry |
| A fact, decision, procedure, person, idea | A typed note in your Obsidian vault |
| A quick errand | A Todoist task, and nowhere else |
| Anything at all | The transcript, verbatim |

---

## Requirements

This is a personal system, published because a few people asked. It assumes:

- **[Hermes](https://github.com/sagarbehere/hermes)** running on a machine of
  yours, with at least one chat platform connected.
- **A Todoist account.** Reminders are Todoist tasks; there is no local
  reminder store and nothing fires from your machine. Without a token,
  capture and notes work fine but reminders are unavailable.
- **An Obsidian vault that is a git repo** with a remote. Notes are written
  there and pushed after every write.
- **A private git remote** for the data directory, so an off-box copy exists.
- Python 3.9+. Standard library only — no dependencies to install.

If you want a version that works without Todoist, this is not it. One existed
and was deliberately removed;
[the reasoning](second-brain-implementation-guide.md) is worth reading before
you rebuild it.

---

## Install

```bash
git clone https://github.com/sagarbehere/working-memory-system
cd working-memory-system
./setup.sh
```

`setup.sh` is idempotent — safe to re-run after every update. It creates the
data directory, installs the skill and capture hook as symlinks, writes wrapper
scripts into `~/.hermes/scripts/`, and writes a config file it will never
overwrite.

Then:

1. **Configure** `~/.hermes/working-memory.env` — at minimum `WM_VAULT_PATH`
   (your vault) and, if it is not your machine's zone, `WM_TZ`.
2. **Add your Todoist token** as `TODOIST_API_TOKEN` in `~/.hermes/.env`, and
   set `TODOIST_MIRROR_ENABLED=true` in the working-memory env file.
3. **Give the data directory a remote:**
   `git -C ~/working-memory remote add origin <your-private-repo>`
4. **Register two Hermes cron jobs**, both `no_agent` — a nightly
   `wm-backup-push.py` and a monthly `cron-session-prune.py`. Neither invokes
   the agent; neither costs tokens. There is no OS crontab entry.
5. **Restart the gateway** so the hook loads: `hermes gateway restart` (from a
   shell, not from inside an agent session).
6. **`/reload-skills`** in your chat client.

Verify the whole install at any time:

```bash
./verify-on-vps.sh
```

Read-only against your data, and it reports what is missing rather than
guessing.

---

## Using it

**Reserve a chat** so you can skip the marker: say `reserve for memory` in it.
`release for memory` undoes that. Everywhere else, start with `Hey memory`.

| Say | What happens |
|---|---|
| `printer is out of ink` | Filed as a record; one-line confirmation |
| `remind me Friday 9am to renew the passport` | Todoist task, due Friday 09:00 |
| `every monday 9am water the plants` | Recurring Todoist task |
| `buy stamps` | Todoist task only — no note |
| `what's due this week?` | Answered from Todoist, soonest first |
| `what did I decide about the printer?` | Answered from the note |
| `did I ever mention the taxi driver?` | Searches the transcript |
| `that should be a project, not an idea` | Re-filed |
| `mark the plumber task done` | Closed in Todoist |
| `forget what I said about X` | Confirms, then removes the note and task |
| `.` | Flush the buffer now instead of waiting for the debounce |

Two things to know. **The transcript is never edited** — "forget X" removes
what was *derived*, and the agent will tell you the words remain. And **you
should never see an approval prompt** during a capture; if you do, a tool is
missing and that is the bug.

---

## What is where

```
~/working-memory/          # your data — its own git repo, pushed nightly
  raw/2026-08.md           #   the transcript, one file per month
  logs/                    #   operational log, pruned after 30 days
  meta/lanes.json          #   which chats you reserved
  todoist-export.jsonl     #   nightly export of open tasks

<your vault>/              # notes, synced by your own setup
  records/ projects/ references/ ideas/
```

Everything durable is in those two directories, and both are git repos. A full
backup is a `git clone`.

---

## Health

`wm-backup-push.py` runs nightly and **prints nothing when all is well.**
Anything it does say is a real problem: a failed push, an unpushed vault
commit, something that has been failing quietly. That silence is deliberate —
a watchdog that speaks every day is one you stop reading.

```bash
python3 tests/run_all.py    # 8 suites, no network, no live data touched
```

---

## Design notes

The interesting parts are written down, mostly because they were mistakes
first:

- [`second-brain-schema.md`](second-brain-schema.md) — the information model.
  Tool-independent; the most reusable thing here.
- [`working-memory-system-spec-v3.md`](working-memory-system-spec-v3.md) — how
  this system actually works, in English.
- [`second-brain-implementation-guide.md`](second-brain-implementation-guide.md)
  — decisions, and things deliberately *not* built. Read this before adding
  anything.
- [`CLAUDE.md`](CLAUDE.md) — orientation for a coding agent working on the
  repo.

This system used to be roughly twice its current size. It had a local reminder
store that duplicated Todoist, a SQLite database for structured records, and a
nightly job to consolidate everything. All three were removed in one pass. The
short version: the reminder store was built for hypothetical users who never
asked; the database was built because it seemed like it would be useful, and
held zero rows; and the nightly job reported work that had already been done at
capture time. The full reasoning is in the decisions document, and the deleted
code is one command away at the tag `v3.0.0-full`.

MIT licensed. Built for one person; you are welcome to it.
