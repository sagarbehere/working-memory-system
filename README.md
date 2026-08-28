# Working Memory System

A capture-anywhere second brain on top of [Hermes Agent](https://hermes-agent.nousresearch.com): dump a thought in plain language from any chat, and the agent files it, organizes it, retrieves it later, and reminds you on schedule. No folders, no categories, no notes app to maintain — organizing is the system's job.

**How it works**

1. **Capture** — blurt a thought into any chat: prefix it with *"Hey memory"* (or *"note"*), or use a chat you've reserved for memory.
2. **Classify** — the agent reads what you sent, splits it into separate thoughts if needed, and labels each one with its kind — a reminder, a dated fact, a project, a reference, or an idea — plus tags saying what it's about.
3. **Store** — each thought is put where its kind belongs: structured facts (health measurements, purchases, prescriptions) → the records database; dated notes, projects, references, and ideas → the wiki (your vault); reminders → the reminder store — optionally mirrored to Todoist so they notify on every device. Without Todoist, everything still works locally.
4. **Ask** — *"when did I last buy vitamins?"*, *"what's due this week?"* — the agent answers from wherever the thought was stored.

**Classify** is the one opinionated step. Each capture gets exactly one type, chosen by retrieval shape and lifecycle — not by topic:

| Type | What it is |
|---|---|
| `reminder` | has a due date; surfaces itself when due |
| `record` | a dated fact — **structured** (health measurement, purchase → SQLite) or **narrative** (journal → the vault); queried by date/entity |
| `project` | an open thread that ends in a decision or completion |
| `reference` | evergreen knowledge (subtypes: entity · concept · procedure) |
| `idea` | an atemporal musing, freely linked |

Topic is carried by **flat tags, never folders** — folders mirror the types, tags carry the subject (a curry recipe gets `cooking, curry, indian`, not a Food folder). The types are deliberately few and extend only for a genuinely new retrieval shape (see [`second-brain-schema.md`](second-brain-schema.md)).

It is, at heart, a searchable copy of the parts of your memory you choose to write down — you talk, it organizes, and it learns from corrections.

Design docs: [`second-brain-schema.md`](second-brain-schema.md) (types/tags/status), [`second-brain-implementation-guide.md`](second-brain-implementation-guide.md) (build & backup), [`working-memory-system-spec-v3.md`](working-memory-system-spec-v3.md) (capture plumbing). The previous v2 line is frozen at tags `v2.0.x`, with its spec preserved there for existing users.

---

## What it does

- **Capture** — send a thought; it's split into atomic notes, tagged, and filed to an append-only, git-versioned raw log.
- **Retrieval** — ask naturally ("what did I decide about X?", "what's due this week?") and get a conversational answer.
- **Reminders** — "remind me Tuesday 8 am to call the plumber" becomes a scheduled message delivered to the chat where you captured it.
- **Typed routing** — captures are classified (record / project / reference / idea / reminder) and routed to the right store: SQLite for structured records, the Obsidian vault for notes, Todoist for reminders.
- **Nightly consolidation** — duplicates collapse, superseded facts replace old ones, archives rotate. Quiet by default.
- **Corrections** — "that's mis-filed, it's about X" / "merge A and B" / "forget Y" — handled immediately, nothing lost.

## How it works (30 seconds)

A capture-gate hook wraps Hermes' message adapter and buffers text messages from **working-memory lanes** (see below), flushing them as one agent turn after a short debounce. The agent follows the policy in [`SKILL.md`](SKILL.md): classify → file → route → confirm. The capture log lives under one folder (`WM_ROOT`), itself a git repo for point-in-time history; curated artifacts are routed to SQLite and the Obsidian vault. Reminders are fired by a tiny cron'd script through the bot you already run (Todoist mirror optional, config-gated).

## Prerequisites

- **Hermes Agent** installed and running with a connected gateway (Telegram recommended; any adapter works for marker capture).
- **python-telegram-bot** installed in the Hermes environment (Telegram mode only).
- **An LLM API key** configured in Hermes (the agent does the filing).
- **crontab** available (for the reminder check; on macOS/Linux `cron` is built in).
- **git** (used for the built-in backup repos).

---

## Install

```bash
git clone https://github.com/sagarbehere/working-memory-system
cd working-memory-system
./setup.sh
```

`setup.sh` (idempotent, safe to re-run):

1. Creates the data skeleton at `~/working-memory` (`WM_ROOT`) and initializes its backup git repo.
2. Symlinks `SKILL.md` into Hermes' skills directory and the hook into `~/.hermes/hooks/`.
3. Copies the two cron helper scripts into `~/.hermes/scripts/` (copies, not symlinks — Hermes' cron scheduler refuses scripts outside `~/.hermes/`).
4. Writes `~/.hermes/working-memory.env` from `.env.example` — **never overwrites** an existing file.

Then:

1. **Reminder delivery** — Telegram users: `crontab -e` and paste the line from [`crontab.example`](crontab.example) (every 5 minutes; adjust paths). **No Telegram?** Skip the crontab and instead register a Hermes no_agent cron job (every 5 minutes, `script=reminder-check.py`, deliver to your home channel) — the script prints only due reminders, which the scheduler delivers verbatim to whatever channel Hermes speaks on.
2. **Restart the gateway** so the hook loads: `hermes gateway restart` (run from SSH/terminal, *not* from inside an agent session — it deadlocks there).
3. **`/reload-skills`** in your chat so the agent picks up the `working-memory` skill.

That's it — **marker capture already works everywhere** (next section). The Telegram lane is an optional frictionless upgrade.

---

## Set up your capture surface

The system has **three input modes**, all active at once. Pick what suits you:

### Option A — Markers: any chat, any platform (zero config) ⭐

Working-memory input is any message that **starts with `Hey memory` or `note`** (case-insensitive, word boundary):

```
note the printer warranty expires in March
Hey memory remind me Tuesday 8 am to call the plumber
Hey memory what did I decide about the printer?
```

Works from *any* chat or client — Telegram, the web UI, the CLI — with no setup at all. The marker is stripped at filing time; a short 5-second debounce merges quick follow-up messages into one entry.

### Option B — Reserve a chat: marker-free lane (any platform)

Turn *any* chat into a dedicated memory lane, no markers needed:

- Send **`reserve for memory`** in the chat → it's recorded in `$WM_ROOT/meta/lanes.json` and **from then on no markers are needed: every message in that chat is memory input until you send `release for memory`**.
- **`release for memory`** undoes it.

This is chat-identity-based (chat + thread), not session-based: `/new`, compression, or restarts don't disconnect the lane.

### Option C — Telegram dedicated lane (the frictionless classic)

The original design: a dedicated chat where *every* message is captured. Same bot, same token — no new bot. Two ways to make it:

**C1. Private group (simplest):**
1. Create a Telegram group containing only you + your Hermes bot.
2. Make sure the bot may respond there: add the group id to `group_allowed_chats` under the telegram platform config in `~/.hermes/config.yaml`.
3. Set `WM_TELEGRAM_CHAT_ID` to the group's id in `~/.hermes/working-memory.env`.

**C2. DM topic lane (no group needed):**
1. Enable DM topics with the bot (`/topic` — see its help for the one-time setup).
2. Use one topic (e.g. "Working Memory") as the lane.
3. Set both `WM_TELEGRAM_CHAT_ID` and `WM_TELEGRAM_THREAD_ID` in `~/.hermes/working-memory.env`.
4. *Optional but nice:* bind the skill to the topic in `~/.hermes/config.yaml` so the working-memory skill auto-loads natively (the hook already stamps it regardless — this is a second, config-level layer):

   ```yaml
   platforms:
     telegram:
       extra:
         dm_topics:
           - chat_id: <CHAT_ID>
             topics:
               - name: Working Memory
                 thread_id: <THREAD_ID>
                 skill: working-memory
   ```

5. Restart the gateway from SSH: `hermes gateway restart`.

> Reminders fire back into the chat where they were captured (origin recorded per reminder), falling back to the home channel when the origin isn't deliverable.

---

## Using it

| You say | What happens |
|---|---|
| `note printer is out of ink` | Captured, tagged, filed |
| `Hey memory what's due this week?` | Pending reminders listed, soonest first |
| `what did I decide about the printer?` | Answered from the topic file / raw log |
| `remind me Tue 8 am to call the plumber` | Reminder scheduled, fires Tue 8 am |
| `.` or `/done` | Flush the buffer immediately (skip the debounce wait) |
| `that's mis-filed, it's about X` | Entry re-tagged, topic files regenerated |
| `merge printer and electronics` | Topics merged (raw log untouched) |
| `forget what I said about the taxi driver` | Fact struck from topic + raw entry (the one destructive action — the agent confirms first) |

**In the Telegram lane**, no markers are needed — just send the thought. **Everywhere else**, prefix with `Hey memory`/`note`, or reserve the chat once with `reserve for memory`.

---

## Storage layout

```
~/working-memory/            # WM_ROOT (git repo = point-in-time backup)
  raw/2026-08.md             # append-only raw entries, one file per month
  raw/archive/               # rotated raw files (> WM_RAW_RETENTION_DAYS)
  topics/<tag>.md            # derived topic files (regenerable)
  reminders.json             # pending reminders {id, due_at, message, ...}
  logs/2026-08.log           # operational trail, JSON lines (~30 day retention)
  meta/tag-index.json        # tag -> entry ids + occurrence counts
  meta/pending-buffer.json   # unflushed capture buffer (hook-managed)
  meta/lanes.json            # reserved chats (reserve/release)
  meta/refinement-log.md     # curated patterns worth reviewing (spec §17)
```

Everything durable lives under `WM_ROOT`; a full backup is archiving that one folder (or its git history).

## Configuration (`~/.hermes/working-memory.env`)

| Key | Default | Meaning |
|---|---|---|
| `WM_ROOT` | `~/working-memory` | storage root |
| `WM_DEBOUNCE_SECONDS` | `5` | silence window before a buffered message flushes as one agent turn |
| `WM_PROMOTE_AFTER` | `2` | tag occurrences before a topic file is created |
| `WM_CONDENSE_SIZE` | `2500` | topic-file bytes that trigger condense-on-write |
| `WM_RAW_RETENTION_DAYS` | `90` | raw files older than this move to `raw/archive/` |
| `WM_CONFIRM` | `1` | brief "logged …" confirmation after each buffer |
| `WM_TELEGRAM_CHAT_ID` | *(optional)* | legacy Telegram lane; empty = no lane (markers still work) |
| `WM_TELEGRAM_THREAD_ID` | *(optional)* | topic lane within the WM chat |

---

## Maintenance

- **Reminder delivery** — two modes, auto-detected by `reminder-check.py`:
  - *Telegram mode* (OS crontab line, every 5 min): sends via the existing bot into the chat where each reminder was captured. If it stops, reminders queue up and fire late; check `~/.hermes/logs/wm-reminders.log`.
  - *stdout mode* (no Telegram configured): the script prints each due reminder to stdout and marks it fired — wire it as a Hermes no_agent cron job (every 5 minutes, `script=reminder-check.py`, deliver to your home channel) and the scheduler delivers the lines verbatim. Diagnostics go to stderr; stdout carries only reminder lines.
- **Nightly consolidation** — a *Hermes* cron job (separate from the OS crontab), schedule `30 2 * * *`, with `wm-consolidation-gate.py` as its context script: the gate emits a work-digest only when there IS work, so quiet nights are normal (no tokens, no delivery). Register it on a new machine by asking your agent: *"recreate the working-memory consolidation cron job"* — the policy ships in SKILL.md, only the registration is per-install.
- **Monthly session prune** — optional watchdog: `cron-session-prune.py` (no-agent cron job, silent unless it pruned something).
- **After a Hermes update** — re-run `./setup.sh` to refresh the copied cron scripts (the skill/hook symlinks survive on their own).
- **Refinement loop (spec §17)** — the agent logs recurring frictions; numeric threshold tweaks are auto-applied (logged), policy changes to SKILL.md are proposed for your sign-off, and the deterministic code is never self-edited.

## Troubleshooting

- **Gateway restart deadlocks** — restart from SSH/terminal, not from inside an agent session (graceful drain waits for active agents).
- **Hook not loading** — `hermes gateway restart` after install; the hook binds at `gateway:startup`.
- **Silent consolidation nights** — normal by design (gated job). Only a *delivery* when there's work.
- **Unflushed buffer after a restart** — reloaded on the next message for that chat; worst case it waits for the next message or `.`/`/done`. Never dropped.

## Uninstall

```bash
rm ~/.hermes/hooks/working-memory-debounce
rm ~/.hermes/skills/note-taking/working-memory/SKILL.md
rm ~/.hermes/scripts/wm-consolidation-gate.py ~/.hermes/scripts/cron-session-prune.py
# remove the crontab line, the Hermes cron jobs, and delete ~/working-memory if you want the data gone
```

## Notes & known limits

- **Text-first captures** — the raw log stores text. Photos and locations are handled as ordinary messages and never become entries on their own; a photo with a caption in a reserved lane captures the caption text. If you want an image remembered, say so in words (e.g. `note the plumber's receipt is in my photos`).
- **Backup** — the on-box git history is the audit trail; a nightly cron (`wm-backup-push.py`, 03:00, no_agent) pushes `WM_ROOT` to a private GitHub remote (off-box copy lags at most 24 h) and alerts on failure. Setup: create an empty private repo, add it as `origin`, widen the PAT to include it.
- The package deliberately contains **no** bot token flow, no Telegram client, no scheduler daemon — it reuses the infrastructure Hermes already runs.

## License

MIT — see [LICENSE](LICENSE).
