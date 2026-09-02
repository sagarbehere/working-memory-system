# Working Memory System

A personal second brain you talk to, built on top of
[Hermes](https://hermes-agent.nousresearch.com/). Send a thought from any
Hermes chat client; it gets filed. Ask later in plain language; it comes back.
Anything with a deadline turns into a reminder that reaches every device you
own.

---

## Why it exists

Every note-taking system asks you to decide *where something goes* at the exact
moment you least want to think about it — when you have a thought and want it
out of your head. So you either stop capturing, or you accumulate an inbox you
never process.

This inverts that. **Capture is instant and thoughtless; the organising is done
for you.** You send the thought and Hermes decides what kind of thing it is,
files it, and confirms in one line. You never choose a folder, a tag, or a
format. If it files something somewhere you disagree with, you say so and it
moves it — a five-second correction rather than a decision you had to make in
advance.

The way notes are organised is deliberately *not* folders-by-subject. Instead
there is a highly opinionated — and, we'd argue, unusual — model based on **how
a note will be retrieved and what its content's lifecycle looks like**, rather
than which knowledge domain it belongs to. A recipe, a blood-pressure reading
and a passport renewal are filed by the shape of the question you will later
ask about them, not by whether they are "health" or "admin". That model is
written up in [`second-brain-schema.md`](second-brain-schema.md), which stands
on its own and may be useful even if you never run this code.

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

That last answer is the point of the whole design. The readings live in one
small markdown file, so the agent reads the whole file and *reasons over it*.
There is no query language that can express "the two highest were after evening
coffee" — but a model reading forty lines can simply notice it.

Anywhere else — any chat Hermes is connected to — prefix with `Hey memory`:

```
you   Hey memory the plumber's number is 555-0134
bot   ✅ → wiki (reference/entity): plumber
```

---

## How it works

Three moving parts:

**1. A capture gate** (a Hermes hook) decides whether a message is memory input
at all — because it is in a chat you reserved, or because it starts with
`Hey memory`. Everything else falls through as ordinary conversation. Matching
messages are buffered for a few seconds, so three thoughts typed in a row
become one turn rather than three.

**2. The agent** classifies and routes, following the policy in
[`SKILL.md`](SKILL.md). This is where the judgment lives: what kind of thing is
this, and where does it belong.

**3. A small set of tools** handles the mechanics — talking to Todoist,
timezones, backups. The agent calls them rather than reinventing each step, so
every Todoist task comes out the same shape.

Where things end up:

| You said | It lands in |
|---|---|
| Something with a deadline | Todoist — which notifies your phone, watch, laptop |
| A repeated measurement | One growing note per series, a line per entry |
| A fact, decision, procedure, person, idea | A typed note in your Obsidian vault |
| A quick errand | A Todoist task, and nowhere else |

**There is no fourth part holding a copy of what you said.** A capture goes to
one of those destinations or nowhere; this system keeps no log of the raw
message. Until 2026-08-31 it did, and [the reasoning for removing
it](decisions.md) is worth reading before you add one back. Your words are not
gone, though — they are in the chat, and Hermes indexes its own conversation
history, which is what "did I ever say…" searches.

---

## Requirements

This is a personal system, published because a few people asked. It assumes:

- **[Hermes](https://hermes-agent.nousresearch.com/)** running on a machine of
  yours, with at least one chat platform connected. Install it first — this
  plugs into it.
- **A Todoist account.** Reminders are Todoist tasks; there is no local
  reminder store and nothing fires from your machine. Without a token, capture
  and notes work fine but reminders are unavailable.
- **An Obsidian vault that is a git repo** with a remote. Notes are written
  there and pushed after every write.
- Nothing else. The data directory (`~/working-memory/`) needs no remote: it
  holds coordination state and diagnostics, not your notes, and nothing backs
  it up on purpose.
- Python 3.9+. Standard library only — no dependencies to install.

If you want a version that works without Todoist, this is not it. One existed
and was deliberately removed; [the reasoning](decisions.md) is worth reading
before you rebuild it.

---

## Install

```bash
git clone https://github.com/sagarbehere/working-memory-system
cd working-memory-system
./setup.sh
```

`setup.sh` is idempotent — safe to re-run after every update. It creates the
data directory and its git repo, installs the skill and capture hook as
symlinks, writes wrapper scripts into `~/.hermes/scripts/`, and creates
`~/.hermes/working-memory.env` from the shipped example. It never overwrites an
existing config.

Then:

1. **Edit** `~/.hermes/working-memory.env` — at minimum `WM_VAULT_PATH` (your
   vault), `TODOIST_ENABLED=true`, and `WM_TZ` if it differs from the
   machine's zone. Optional: `WM_VAULT_SKILL=<skill-name>` layers a personal
   vault-operation skill (loaded via `skill_view` when a capture routes to
   the vault — e.g. your own wiki skill with device/sync preferences);
   without it, the generic vault discipline in SKILL.md applies (pull
   `--ff-only` before writes, follow `<vault>/SCHEMA.md`, commit AND push).
2. **Add your Todoist token** as `TODOIST_API_TOKEN` in `~/.hermes/.env` —
   Hermes' own secrets file, where your bot tokens already live. Secrets go
   there; everything else in the working-memory env file above.
3. **Make sure your vault has a remote and you can push to it.** This is the
   one backup that matters — it is where your notes are, and the watchdog
   below exists to tell you when a note has not reached it.
4. **Register one Hermes cron job**: `wm-watchdog.py`, nightly, `no_agent`.
   That is the only scheduled work — nothing invokes the agent on a timer, and
   there is no OS crontab entry.
5. **Restart the gateway** so the hook loads: `hermes gateway restart` (from a
   shell, not from inside an agent session).
6. **`/reload-skills`** in your chat client, then **start a new
   conversation** — the skill is injected when a session begins, so a chat you
   already have open keeps the version it started with.

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
| `review NDA of client and tag it as work` | Todoist task with the `work` label |
| `what's due this week?` | Answered from Todoist, soonest first |
| `what did I decide about the printer?` | Answered from the note |
| `did I ever mention the taxi driver?` | Searches your notes, then your chat history |
| `that should be a project, not an idea` | Re-filed |
| `mark the plumber task done` | Closed in Todoist |
| `forget what I said about X` | Confirms, then removes the note and task |
| `.` | Flush the buffer now instead of waiting for the debounce |

Two things to know. **"forget X" reaches only what was filed** — the note and
the task. The original message stays in your chat history like any other
message; this system does not reach in there. And **you should never see an
approval prompt** during a capture; if you do, a tool is missing and that is
the bug.

---

## What is where

```
<your vault>/              # your memories — notes, synced by your own setup
  records/ projects/ references/ ideas/

~/working-memory/          # bookkeeping — not backed up, nothing of yours here
  meta/lanes.json          #   which chats you reserved
  logs/                    #   operational log, pruned after 30 days
```

---

## Health

`wm-watchdog.py` runs nightly and prints nothing when all is well. Anything it
does say is a real problem: an unpushed vault commit, a vault pull that failed,
or something that has been failing quietly.

It backs nothing up. Its whole job is to notice when **your vault** — the only
place your notes exist — has commits that never reached its remote. That is the
failure this system is actually exposed to, and it is silent without a
watchdog.

```bash
python3 tests/run_all.py    # no network, no live data touched
```

---

## Design notes

- [`second-brain-schema.md`](second-brain-schema.md) — the information model.
  Tool-independent; the most reusable thing here.
- [`working-memory-system-spec.md`](working-memory-system-spec.md) — how
  this system works, in English.
- [`decisions.md`](decisions.md) — decisions made, alternatives rejected, and
  things deliberately *not* built. Read this before adding anything.
- [`CLAUDE.md`](CLAUDE.md) — orientation for a coding agent working on the
  repo.

MIT licensed. Built for one person; you are welcome to it.
