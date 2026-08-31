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
todoist.py                Todoist client + CLI — the reminder layer
wm-backup-push.py         cron: nightly backup + health watchdog (silent when well)
hooks/working-memory-debounce/handler.py
                          the capture gate — monkey-patches Hermes' inbound seam
SKILL.md                  the agent's policy (installed as a Hermes skill)
setup.sh / export.sh      installer / machine-to-machine migration
verify-on-vps.sh          full verification against the live install
tests/                    tests/run_all.py runs every offline suite
```

Design docs, in the order worth reading:

1. `second-brain-schema.md` — the type/tag/status model. Start here; it
   explains *why* captures are classified the way they are.
2. `working-memory-system-spec.md` — the plumbing: storage layout, capture
   flow, reminder scheduler, error handling.
3. `decisions.md` — decisions and rejected
   alternatives: why things are as they are, and what is absent on purpose.
   Deliberately not a description of the system.

## Where to look, by task

| You need to… | Read |
|---|---|
| change how a capture is classified or routed | `SKILL.md`, `second-brain-schema.md` |
| touch reminders | `todoist.py` — there is no local reminder store |
| change capture/buffering/lanes | `hooks/working-memory-debounce/handler.py` |
| add or change a Todoist call | `todoist.py`, then check the call budget (below) |
| change timezone or env handling | `wmlib.py` — do not reimplement it locally |
| change install/wiring | `setup.sh`, then `verify-on-vps.sh` |

## Rules that are load-bearing

These exist because breaking them caused real bugs. Each is enforced by a test.

1. **This system keeps no copy of a capture.** Since the 2026-08-31 cut
   (`decisions.md`) a capture goes straight to the vault or Todoist, and if a
   write fails nothing else is holding it. Docs and skill text must not
   promise otherwise — "nothing is lost" was true of the transcript and is not
   true now. The words survive in Hermes' session history (`session_search`),
   which this repo depends on but does not own or test.
2. **Timestamps: store aware, display local.** Everything shown to a user goes
   through `wmlib.local_iso()`. Never hardcode an offset or a zone;
   `wmlib.tz()` resolves `WM_TZ` or the system zone.
3. **Watchdogs are silent when healthy.** `wm-backup-push.py` prints only
   problems; the no_agent scheduler delivers its stdout verbatim, so anything
   it prints reaches the user. An alert that fires on a healthy night trains
   the user to ignore it, so gate every alert on the relevant feature actually
   being configured.
4. **Nothing runs the agent on a schedule.** Since the 2026-08-29 cut the only
   cron jobs are `no_agent` watchdogs. The agent is alive only while the user
   is talking to it — which is why the refinement log is a decision record and
   not a mailbox. **Do not add a scheduled agent job** without a concrete
   reason; the last one cost tokens nightly to report work that had already
   been done at capture time.
5. **No hardcoded paths.** `WM_ROOT`, `WM_VAULT_PATH`, `WM_TZ` are config.
   Resolve them with `wmlib`, which honours both the process env and
   `~/.hermes/working-memory.env`.
6. **The Todoist call budget is deliberate.** See below.

## The Todoist call budget

The user's concern is a rate-limited or blocked account, so call counts are
asserted in `tests/test_todoist_budget.py`. Current steady state:

| Event | Calls |
|---|---|
| create a reminder (project id cached) | 1 (`POST /tasks`) |
| create, cold cache | 2 (+ `GET /projects`, cached afterwards) |
| "what's due" | 2 (`GET /projects`, `GET /tasks`) |
| nightly backup export | 2 |
| **anything else, including idling** | **0** |

There is no polling loop: since the 2026-08-29 cut nothing contacts Todoist
unless the agent is acting. Cost is proportional to use, not to time —
roughly 2 calls a day at rest. **Do not reintroduce a periodic poll**; that
is how this got to 288 requests/day before.

## Cron and the wrapper model

Hermes' cron scheduler refuses to execute scripts that resolve outside
`~/.hermes/scripts/`, so `setup.sh` writes **wrapper** files there that `exec`
the package copy. The package stays the single source of truth and edits take
effect immediately — no refresh step.

**If you add a script that cron must run, add it to `WRAPPED_SCRIPTS` in
`setup.sh`.** A script invoked from that directory without a wrapper is a
stale copy waiting to happen — that exact situation once put a superseded
script in the scheduler's path.

Current scheduled work: exactly one job — `wm-backup-push.py`, a nightly
Hermes `no_agent` entry. **No OS crontab entry, and nothing that invokes the
agent.**

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

Two things the suite deliberately does not cover, and where they live instead:

- **"Is this message memory input?"** — `tests/gate-cases.txt`, one line per
  real phrasing (`<expected> | <message>`). Adding a case is one line; that is
  the point. It found a real bug on its first run.
- **"Did the agent file it sensibly?"** — `tests/scenarios.md`, a hand-run
  checklist. This is an LLM judgment and there is no cheap deterministic test
  for it. **When the agent gets something wrong, prefer a constraint in a CLI
  over a test**: a test tells you it was wrong last Tuesday, a validation
  stops it being wrong at all. That is why `todoist.py` owns the API call
  shape and due-date handling rather than `SKILL.md` describing them.

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
