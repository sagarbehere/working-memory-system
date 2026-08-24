# Working Memory System

A frictionless personal working-memory system on top of Hermes + Telegram.
Thoughts are captured via Telegram with zero categorization effort; the
Hermes agent handles all classification, filing, reminders, retrieval,
and cleanup. The raw log is ground truth; topic files are derived caches;
everything is reversible.

Full design rationale: [`working-memory-system-spec.md`](working-memory-system-spec.md).

## v2: markers + reserved lanes (implemented)

Since v1, the system is **marker-first** (spec Section 18):

- **Marker mode (zero config, any platform):** a message starting with
  `Hey memory` or `note` (case-insensitive, word boundary) is
  working-memory input — capture, retrieval, or commands — from *any*
  chat or client (Telegram, web UI via the api_server adapter, CLI).
  Markers are stripped before processing; a short 5s debounce merges
  quick follow-ups.
- **Reserved lanes (frictionless):** say `reserve this chat` (or the
  full `reserve this chat for working memory`) in any chat and it
  becomes a marker-free lane (recorded in `$WM_ROOT/meta/lanes.json`);
  `unreserve this chat` undoes it. The v1 env-declared chat
  (`WM_TELEGRAM_CHAT_ID`/`THREAD_ID`) still works as one pre-reserved
  legacy lane.
- **Reminders deliver to the chat where they were captured**, with a
  home-channel fallback (origin recorded per reminder, spec 18.4).

## Scope: a dedicated chat (v1 design, still supported)

Only messages sent to a **dedicated working-memory chat** are captured.
Every other chat with Hermes is completely unaffected and behaves as
normal conversation always has. No classification step guesses whether a
message is "for" the memory system — the chat boundary does that
deterministically.

Two ways to create the dedicated chat (same bot, same token — no new bot):

- **DM topic lane** (lightest): enable Telegram DM topics with the bot
  (`/topic` — see its help for the one-time setup), then use one topic
  lane as the WM chat. Set `WM_TELEGRAM_CHAT_ID` + `WM_TELEGRAM_THREAD_ID`.
- **Private group**: create a group containing only you + the bot, and
  make sure the bot is allowed to respond there (add the group to
  `group_allowed_chats` under the telegram platform config). Set
  `WM_TELEGRAM_CHAT_ID` to the group's id.

Until `WM_TELEGRAM_CHAT_ID` is set, the system is disabled.

## What this package adds (nothing more)

- **SKILL.md** — the operational policy the agent follows on every
  capture/consolidation pass. Installed as a **symlink** into
  `~/.hermes/skills/` pointing at the package, so the package is the
  single source of truth and every edit applies immediately (after
  `/reload-skills` or a new session). Git-tracked here, per spec
  Section 17.
- **The debounce hook** (`hooks/working-memory-debounce/`) — wraps the
  *already-running* Hermes Telegram adapter, **only for the dedicated WM
  chat**: text messages are buffered per chat and flushed as one agent
  turn after a debounce window (default 25s). A lone `.` or `/done`
  flushes immediately. Buffers are persisted to
  `$WM_ROOT/meta/pending-buffer.json` on every message, so a gateway
  restart never loses an in-progress thought. Each buffered event is
  stamped with `auto_skill` (`WM_SKILL`, default `working-memory`), so
  Hermes **deterministically auto-loads the skill** into the lane's
  session instead of relying on the model choosing to load it.
- **`reminder-check.py`** — cron'd script that fires due reminders through
  the *existing* bot into the WM chat. Not a daemon.
- **`setup.sh`** — creates the data skeleton + backup git repos (data +
  package), installs the skill and hook, writes the runtime env.
- **`export.sh`** — one-command bundle of the whole system (package +
  data, both with git history, plus install notes) for copying to another
  machine or an off-box backup. No secrets included.
- **`crontab.example`** — the exact cron line for the reminder check.
- **Nightly consolidation job** — a Hermes cron job (separate from the OS
  crontab) registered via the agent: schedule `30 2 * * *`, loads the
  `working-memory` skill, runs the Consolidation pass (see SKILL.md), and
  reports to the WM chat. Lives in Hermes's cron store — re-create it on a
  new machine by asking the agent to "recreate the working-memory
  consolidation cron job" (the *policy* ships in SKILL.md, the
  *registration* is per-install).

It deliberately contains **no** Telegram bot token flow, no Telegram
client, and no scheduler daemon — it reuses the infrastructure Hermes
already runs.

## Install

Prereqs (already in place on this VPS): Hermes with the Telegram gateway
running, `python-telegram-bot` installed, a crontab available.

```bash
cd ~/working-memory-system
./setup.sh
```

Then:

1. **Create the dedicated WM chat** (see above) and set
   `WM_TELEGRAM_CHAT_ID` (+ thread id if a topic lane) in
   `~/.hermes/working-memory.env`. For a DM-topic lane, also register the
   skill binding so the working-memory skill auto-loads natively (the
   hook stamps `auto_skill` regardless — this is a second, config-level
   layer):

   ```yaml
   # in ~/.hermes/config.yaml
   platforms:
     telegram:
       extra:
         dm_topics:
           - chat_id: 143386153
             topics:
               - name: Working Memory
                 thread_id: 87471
                 skill: working-memory
   ```
2. **Cron** — `crontab -e` and paste the line from `crontab.example`
   (every 5 minutes).
3. **Restart the gateway** so the hook loads: `hermes gateway restart`
   (from SSH — not from inside an agent session, which deadlocks).
4. **`/reload-skills`** in the chat so the agent sees the
   `working-memory` skill.

## Storage layout

```
~/working-memory/            # WM_ROOT (git repo = point-in-time backup)
  raw/2026-08.md             # append-only raw entries, one file per month
  raw/archive/               # rotated raw files (> WM_RAW_RETENTION_DAYS)
  topics/<tag>.md            # derived topic files (regenerable)
  reminders.json             # pending reminders {id, due_at, message, raw_entry_id, status}
  logs/2026-08.log           # operational trail, JSON lines (~30 day retention)
  meta/tag-index.json        # tag -> entry ids + occurrence counts
  meta/pending-buffer.json   # unflushed capture buffer (hook-managed)
  meta/refinement-log.md     # curated patterns worth reviewing (spec §17)
```

Everything durable lives under `WM_ROOT`; a full backup is archiving that
one folder (or its git history).

## Configuration (`~/.hermes/working-memory.env`)

| Key | Default | Meaning |
|---|---|---|
| `WM_ROOT` | `~/working-memory` | storage root |
| `WM_DEBOUNCE_SECONDS` | `25` | silence window before a buffer flushes |
| `WM_PROMOTE_AFTER` | `2` | tag occurrences before a topic file is created |
| `WM_CONDENSE_SIZE` | `2500` | topic-file bytes that trigger condense-on-write |
| `WM_RAW_RETENTION_DAYS` | `90` | raw files older than this move to `raw/archive/` |
| `WM_CONFIRM` | `1` | brief "logged …" confirmation after each buffer |
| `WM_TELEGRAM_CHAT_ID` | *(required)* | the dedicated WM chat; empty = disabled |
| `WM_TELEGRAM_THREAD_ID` | *(optional)* | topic lane within the WM chat |

## Usage (in the dedicated WM chat)

- **Capture:** just send the thought — typed, or dictated on-device and
  reviewed before sending. Multiple rapid messages merge into one entry.
- **Manual flush:** send `.` or `/done` to skip the debounce wait.
- **Retrieval:** ask naturally ("what printer was I thinking of?",
  "what's due this week?").
- **Corrections:** "that's mis-filed, it's about X" / "merge printer and
  electronics" / "forget what I said about the taxi driver" — run
  immediately, nothing is lost (raw log is untouched).

## Notes & known limits (v1)

- **Recovery timing:** after a gateway restart, an unflushed buffer is
  reloaded on the next message for that chat (never dropped — worst case
  it waits for the next message or `.`/`/done`).
- **Text only:** photos/locations bypass the debounce (stock behavior).
- **Backup:** on-VPS git history only (spec Section 14 open item). To add
  an off-box copy later: push the repos to a private remote, or cron a
  `tar` + `scp`/rclone of `WM_ROOT`.
- **Re-installing after `hermes update`:** the hook is a symlink into
  `~/.hermes/hooks/`, so it survives; only re-run `./setup.sh` if the
  paths changed.
- **Refinement loop (spec §17):** numeric threshold tweaks are auto-applied
  by the agent (logged); policy changes to SKILL.md are proposed to you
  for sign-off; the deterministic code is never self-edited.
