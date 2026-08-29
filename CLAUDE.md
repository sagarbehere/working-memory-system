# Working Memory System — orientation for coding agents

Read this first. It tells you what this repo is, which files matter for which
task, and the handful of rules that are load-bearing. It is deliberately about
*orientation*, not behaviour — **the code is the source of truth, and you
should read the current code before changing it.** Anything here can go stale;
a file you have opened cannot.

## What this is

A personal "second brain" that runs as a skill inside **Hermes**, a
self-hosted personal-agent gateway on the user's VPS. The user sends a thought
from any chat client; the system files it, and later answers questions about
it and fires reminders.

Two halves, and the split is the main thing to understand:

- **A deterministic layer** — plain Python CLIs and cron scripts in this repo.
  Storage, locking, scheduling, backup. No LLM involved. Must be correct.
- **An agent layer** — `SKILL.md`, the policy the LLM follows for
  classification, routing, and retrieval. Judgment lives here.

The boundary is the design: anything that must be exactly right is code the
agent *calls*, not behaviour the agent is asked to perform. When you are
tempted to put a rule in `SKILL.md`, first ask whether it belongs in a CLI.

## Where things live

This repo is the **package**. The user's data lives in a separate directory
(`WM_ROOT`, default `~/working-memory`), which is its own git repo pushed to a
private remote. Never commit data into this repo.

```
wmlib.py                  env parsing, timezone, logging, atomic writes, locking
rawlog.py                 the raw capture log (CLI) — owns the on-disk entry format
records.py                SQLite store for structured records (CLI)
reminders.py              reminder store (CLI) — owns reminders.json
todoist.py                Todoist API client + CLI; importable as a library
reminder-check.py         cron: fires due reminders (every 5 min)
wm-consolidation-gate.py  cron: nightly; prints work or stays SILENT
wm-backup-push.py         cron: nightly backup push (watchdog)
cron-session-prune.py     cron: monthly session cleanup
hooks/working-memory-debounce/handler.py
                          the capture gate — monkey-patches Hermes' inbound seam
SKILL.md                  the agent's policy (installed as a Hermes skill)
setup.sh / export.sh      installer / machine-to-machine migration
verify-on-vps.sh          full verification against the live install
tests/                    12 suites; tests/run_all.py runs them all
```

Design docs, in the order worth reading:

1. `second-brain-schema.md` — the type/tag/status model. Start here; it
   explains *why* captures are classified the way they are.
2. `working-memory-system-spec-v3.md` — the plumbing: storage layout, capture
   flow, reminder scheduler, error handling.
3. `second-brain-implementation-guide.md` — decisions and rejected
   alternatives: why things are as they are, and what is absent on purpose.
   Deliberately not a description of the system.

## Where to look, by task

| You need to… | Read |
|---|---|
| change how a capture is classified or routed | `SKILL.md`, `second-brain-schema.md` |
| touch reminders | `reminders.py` first, then `reminder-check.py` |
| touch structured records | `records.py` |
| change the raw entry format, ids, or dedup | `rawlog.py` — and the spec §5 contract |
| change what triggers the nightly agent run | `wm-consolidation-gate.py` |
| change capture/buffering/lanes | `hooks/working-memory-debounce/handler.py` |
| add or change a Todoist call | `todoist.py`, then check the call budget (below) |
| change timezone or env handling | `wmlib.py` — do not reimplement it locally |
| change install/wiring | `setup.sh`, then `verify-on-vps.sh` |

## Rules that are load-bearing

These exist because breaking them caused real bugs. Each is enforced by a test.

1. **Never hand-write `raw/`, `reminders.json`, or `records.db`.** Use
   `rawlog.py` / `reminders.py` / `records.py`. Each closes a *silent* failure
   mode: the reminder store has two concurrent writers (the agent and the cron
   tick) and the CLI takes the lock a direct edit does not, so an unlocked
   capture is erased by the tick's write-back; a malformed raw header makes an
   entry invisible to consolidation forever; a colliding raw id breaks the
   `raw_entry_id` links back from reminders and records. None of these report
   anything when they go wrong.
2. **The live `records.db` is never copied, moved, or replaced.** It is
   WAL-mode. The committed artifact is `records-snapshot.db`, produced by
   SQLite's backup API while the live file is only read. Replacing the file
   under open connections corrupts it — measurably, not theoretically.
3. **Timestamps: store UTC, display local.** `records.occurred_at` is stored
   normalised to UTC so string comparison matches chronological order.
   Everything shown to a user goes through `wmlib.local_iso()`. Never hardcode
   an offset or a zone; `wmlib.tz()` resolves `WM_TZ` or the system zone.
4. **Watchdogs are silent when healthy.** `wm-consolidation-gate.py` printing
   nothing is how the scheduler knows to skip the AI call entirely — that is
   the whole point of the gate. `wm-backup-push.py` prints only problems. An
   alert that fires on a healthy night trains the user to ignore it, so gate
   every alert on the relevant feature actually being configured.
5. **No hardcoded paths.** `WM_ROOT`, `WM_VAULT_PATH`, `WM_TZ` are config.
   Resolve them with `wmlib`, which honours both the process env and
   `~/.hermes/working-memory.env`.
6. **The Todoist call budget is deliberate.** See below.
7. **The raw log is append-only and is the audit trail.** Everything else is
   derived and regenerable. Never rewrite `raw/`.

## The Todoist call budget

The user's concern is a rate-limited or blocked account, so call counts are
asserted in `tests/test_todoist_budget.py`. Current steady state:

| Event | Calls |
|---|---|
| capture a reminder (project id cached) | 1 (`POST /tasks`) |
| capture, cold cache | 2 (+ `GET /projects`, cached afterwards) |
| 5-min tick, nothing pending | **0** |
| 5-min tick, reconcile window open | 1 (`GET /tasks`, all open ids at once) |
| nightly backup export | 2 |

≈48 calls/day at rest, because completion reconciliation is rate-limited to
`TODOIST_RECONCILE_MINUTES` (default 30) rather than running every tick.
**If you add a call inside the tick, you are adding 288 requests/day.** Check
the budget test before and after.

## Cron and the wrapper model

Hermes' cron scheduler refuses to execute scripts that resolve outside
`~/.hermes/scripts/`, so `setup.sh` writes **wrapper** files there that `exec`
the package copy. The package stays the single source of truth and edits take
effect immediately — no refresh step.

**If you add a script that cron must run, add it to `WRAPPED_SCRIPTS` in
`setup.sh`.** A script invoked from that directory without a wrapper is a
stale copy waiting to happen; that exact situation put pre-fix
`reminder-check.py` code in the scheduler's path once already.

Current cron jobs (no new ones were added by the recent work):
`reminder-check.py` (OS crontab, 5 min), `wm-consolidation-gate.py` (nightly,
Hermes job), `wm-backup-push.py` (nightly, Hermes no_agent job),
`cron-session-prune.py` (monthly).

## Testing

```bash
python3 tests/run_all.py          # everything that runs without Hermes
./verify-on-vps.sh                # on the VPS: adds live checks, read-only
```

`tests/stubs/` provides a minimal `gateway` package so the capture-gate suites
run on a dev machine; the real Hermes package is used automatically when
importable. `tests/test_patch_install.py` is excluded from the runner on
purpose — it asserts against the *real* gateway classes, which a stub would
satisfy, so it only means something on the VPS.

Every suite uses temporary directories. **No test may touch a real `WM_ROOT`.**

## Development workflow

The user edits on a Mac and deploys to the VPS by pushing to GitHub and
pulling there. You will usually not have access to the live environment, so:

- Verify locally with `tests/run_all.py`; prefer a test that reproduces a bug
  over an argument that it exists.
- Anything needing the live gateway, real Todoist, or cron goes in
  `verify-on-vps.sh` for the user to run.
- After changing `handler.py` or `SKILL.md`, the user must
  `hermes gateway restart` and `/reload-skills` — both are symlinked from the
  package, so a `git pull` changes them on disk but not in the running process.

## Gotchas that have bitten before

- `git rm --cached a b c` is **all-or-nothing**: one non-matching pathspec
  aborts the whole command. Do not bundle paths that may not exist.
- Fixtures must not assume `init.defaultBranch`. Set the bare repo's HEAD and
  clone with `-b main` explicitly.
- `todoist.py` shells out to `curl`, not urllib — Cloudflare resets urllib's
  TLS handshake. That is also what makes the fake-curl budget test possible.
- The hook is imported into the live gateway process. An exception at import
  time silently disables capture on every platform, which is why `wmlib` is
  imported there behind a `try` with a local fallback.
- `install_patches()` must stay idempotent; the hook directory is symlinked,
  which makes a double import under a second module name easy, and that would
  capture the already-patched function as "original" and recurse.

## Refinement loop

`meta/refinement-log.md` in the *data* repo is a decision record and an async
mailbox, not a place to make decisions. Numeric threshold tweaks may be
auto-applied; policy changes need the user's sign-off with a before/after
diff. **Deterministic code is never self-edited outside that sanctioned flow.**
