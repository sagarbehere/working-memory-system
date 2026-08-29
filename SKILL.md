---
name: working-memory
description: "Use for the working-memory system v3 (second brain): input in a reserved lane or starting with 'Hey memory'/'note'. Classify each capture (reminder/record/project/reference/idea), route to SQLite/vault/Todoist, retrieve from all stores, manage reminders."
version: 3.0.0
author: Sagar Behere
license: MIT
metadata:
  hermes:
    tags: [memory, second-brain, capture, retrieval, reminders]
    related_skills: [hermes-agent]
---

# Working Memory v3 (Second Brain)

Personal second-brain system: the user captures thoughts via any connected
client; you classify, file, retrieve, and remind. **The raw log is the
immutable, full-text capture record and audit trail** (`$WM_ROOT/raw/`)
— every capture is written there first, then routed to its store. Destinations
(vault notes, SQLite rows, Todoist tasks) are the primary curated artifacts;
**recovery is the backups' job** (vault git + nightly private-remote push + Todoist
exports), not a rebuild from the log.

**Read before operating:** `second-brain-schema.md` (type/tag/status model —
read this first), `second-brain-implementation-guide.md` (routing/backup
rationale), `working-memory-system-spec-v3.md` (capture plumbing), then
`logs/` (what actually happened). Same three-tier escalation as v2, extended.

## Scope guard (unchanged from v2)

Working-memory input is any message that (a) arrives in a **reserved lane**
(`meta/lanes.json`, or the legacy env lane), or (b) starts with a marker:
`Hey memory` or `note` (case-insensitive, word boundary). The capture-gate
hook already buffered and stamped it; **strip the marker token before filing**.
Everything else is ordinary conversation — answer normally, file nothing.
(In a reserved lane, chit-chat reaches the extraction pass, classifies as
neither capture/question/command, and is answered normally — nothing filed.)

## Every incoming message: route it

1. Reservation phrases (`reserve for memory` / `release for memory`) — the
   hook has updated `meta/lanes.json`; confirm in one line, file nothing.
2. Split into items — conservative: one coherent thought = one item.
3. Classify each item: `capture` / `question` / `command`.
4. Capture → **Capture & classify** below. Question → **Retrieve**. Command →
   **Command**.
5. Chit-chat → answer normally.

## Capture: raw entry first, then classify & route

**Write the raw entry BEFORE anything else** — append-only, never edited:

```
## 2026-08-28T16:03:00+05:30 [id: 20260828-1603-01]
tags: health, vitamin-d
type: reminder
domain: health, vitamin-d
supersedes: 20260817-1610-01

<text>
---
```

- id = deterministic timestamp; `-01`, `-02`… per flush. Dedup first (check
  current month raw + pending buffer; a duplicate re-send is NOT re-filed).
- **`type` is the v3 classification itself** — `reminder | record | project |
  reference | idea` (v2's `log|reminder|log+reminder` is gone: a capture with
  a due date splits into *two items* — a `record` for the event + a `reminder`
  for the next due, per schema §3.1's habit model).
- `domain`: 1+ tags from the canonical list at `<vault>/_meta/tags.md`
  (`<vault>` = `WM_VAULT_PATH`, default `~/wiki`) —
  classify against it first; coin a new tag only when nothing fits, and add it
  to the list in the same operation (a policy change → refinement log).
- `status` (`active|superseded|archived`) — project/reference only, default
  active. `record_kind: structured|narrative` — records only. `subtype:
  entity|concept|procedure` — references only. `file_ref` when a file is
  involved (schema §12: stable location, never a reorganizable path).
- Classification heuristics (schema §8): due-date language → `reminder`;
  dated/factual/no action → `record`; open question/decision → `project`;
  "how do I"/stable entity → `reference`; musing/quote → `idea`; decision-time
  analysis → `reference`/concept if worth rereading, else project support
  material; puzzle → `reference` with difficulty/subject as domain tags.
  **Low confidence → `record`.**

Then route per the table (update `meta/tag-index.json` in the same operation,
and commit the working-memory repo after the batch):

| `type` | Destination | Mechanism |
|---|---|---|
| `reminder` | local `reminders.json` (+ synchronous Todoist mirror) | **ONE command — never hand-edit `reminders.json`:** `python3 ~/.hermes/scripts/reminders.py add --message <text> --due-at <ISO-8601 with offset> --raw-entry-id <id> --origin-platform <p> --origin-chat <id> [--origin-thread <id>]`. It writes the durable local entry, then calls Todoist synchronously and records `todoist_id`/`mirrored: true`, and prints the finished entry as JSON. It takes the store lock, so it is safe against a concurrent cron tick. A failed mirror is not an error — the entry is durable and the cron catches it up. **Origin:** pass the chat's real `chat_id` and `thread_id` separately and do not guess — in a reserved lane you may omit `--origin-*` entirely and the store adopts the lane from `meta/lanes.json`. A `chat_id` that is actually a thread id is detected and corrected, and the correction is printed; if you see that message, you passed the wrong values. |
| `record` `structured` | SQLite `records` table | `python3 ~/.hermes/scripts/records.py add --type … --domain … --occurred-at <event date ISO-8601; now if unknown> --entity … --json '{…}' --notes …` |
| `record` `narrative` | vault `records/` dated note | `records/YYYY-MM-DD-<slug>.md`, frontmatter + prose |
| `project` | vault `projects/` note | `status: active` frontmatter (+ `target_date`, `last_touched` if applicable — see schema §11, digest is out of scope for now) |
| `reference` | vault `references/` | `subtype: entity` → `references/entities/`; `concept` → `references/concepts/`; `procedure` → `references/procedures/` |
| `idea` | vault `ideas/` atomic note | freely linked, no status |
| **undated task** | Todoist **or** vault — one home only | quick one-off errand → Todoist task ONLY (`todoist_only: true`, no vault note); **project-scoped to-do → checklist line in that project's note** (`## Checklist` section at the bottom, `- [ ] item`; append on capture, tick on "mark X done" or an Obsidian edit); substantial/multi-step → project note body (no Todoist mirror unless it later acquires a due date → then reminder rules apply) |

**Vault write discipline (all vault destinations):**
- v3 notes **ARE wiki pages**: add an index.md entry under the page's type
  section + a log.md line; frontmatter per the vault SCHEMA; link related notes
  when natural (no forced minimum).
- Frontmatter: `type`, `domain`, `status` (where applicable), `subtype`
  (references), `record_kind` (records), `created`/`updated`.
- **Commit AND push in the vault (`WM_VAULT_PATH`, default `~/wiki`) after every write** — a local-only commit in a
  sync repo isn't backed up.
- `records.py` is deterministic — for structured records ALWAYS use it (it
  handles JSON escaping and indexing); never hand-edit `records.db`.

**Confirm** with ONE short line after each flush — **showing the destination**:
`✅ → Todoist: buy stamps` · `✅ → wiki (project): renew passport` ·
`✅ → wiki (checklist): WM — X` · `✅ record (SQLite): BP 128/82` ·
`✅ → wiki (concept): …`. If the user says
"No, that should be a project note / a Todoist task", re-route on the spot.

## Retrieve (spec §12 — pick the store by what's asked)

- **Reminder queries** ("what's due this week", "did I take it?") → local
  `reminders.py list` (`--status all` for history), soonest-first.
  "Did it get done" →
  cross-check completion in Todoist. Manual Todoist tasks → answer from Todoist.
- **Completion history** ("what did I finish last month", "did I get X done?")
  → `todoist.py completed --since YYYY-MM-DD --until YYYY-MM-DD [--project NAME]`
  (completion-date view; `--by due` for the due-date view). Answer
  conversationally, grouped by project.
- **Task details incl. comments** → `todoist.py list --notes` (adds a
  `comments` array per task; note_count is unreliable in v1, so it fetches
  per task).
- **Structured records** ("when did I last buy X", "BP last month") →
  `records.py query --domain … --entity … --since … --until …`; answer
  conversationally. Prescription overlap → pull rows, diff `data_json` in
  reasoning.
- **Undated tasks** ("what errands are pending") → Todoist for `todoist_only`
  items, vault `projects/` for project notes.
- **Vault content** (project/reference/idea/narrative record) → search vault
  by title/backlink/domain tag. Exclude `status: archived|superseded` from
  default answers (surface if explicitly asked).
- **Fallback / everything else** → raw log: `tag-index.json` → current month
  raw → `raw/archive/`. Never make the user guess a tag or type.

## Command (run immediately)

- Mis-filed → re-route to the correct store. Use the CLIs, never hand-edits:
  `records.py update --id N [--type|--domain|--entity|--occurred-at|--notes]
  [--json '{…}' merges | --replace-json '{…}' overwrites]` for a SQLite row;
  edit the vault note in place; `reminders.py`/`todoist.py` for the others.
- Merge/split vault notes → regenerate the named notes from raw.
- Forget X → **confirm first** (the one destructive action), then strike the
  raw entry AND remove derived artifacts: `records.py delete --id N` (or a
  filter — run it with `--dry-run` first and show the user what matches),
  `reminders.py cancel --id`, `todoist.py delete --id`, and the vault note.
  `delete` refuses to run without `--id` or a filter, so it can never
  become "delete everything".
- Ambiguous target → ask, never guess.

## Consolidation (v3, nightly job + size triggers)

- Reference-flavored vault notes: condense like v2 topic files (derived,
  regenerable).
- **Structured records in SQLite are never collapsed** — itemized history.
- Supersession: newer replaces older; `status: superseded` suppresses from
  defaults.
- Expiry: resolved reminders / time-bound lines drop from derived notes; raw
  untouched. Rotate raw >60-90d to `raw/archive/`; delete `logs/` >30d.
- The nightly gate (`wm-consolidation-gate.py`) prints a digest only when
  there's work; a silent night is normal.

## Reminders (v3 — local first, Todoist mirrors)

- **`reminders.py` owns `reminders.json` — never edit that file by hand**
  (the same rule as `records.db`/`records.py`). Two processes write it, you
  and the cron tick; the CLI takes the lock that makes them safe, and a
  hand-written edit does not. Commands: `add`, `list [--status …]`,
  `done --id`, `cancel --id`, `fire-due`.
- **Local `reminders.json` is the firing fallback and durable record** — the
  cron (`reminder-check.py`) fires only entries WITHOUT a successful mirror.
- **Todoist (config-gated):** `reminders.py add` mirrors **synchronously at
  capture time** and records `todoist_id` / `mirrored: true`. If that call
  fails, the entry is still durable and `reminder-check.py`'s tick mirrors it
  on a later run — a catch-up, not the primary path.
  `mirrored: true` → **local firing is skipped** — Todoist's notification IS
  the reminder; local fires only when the mirror is absent or failed
  (Todoist down, token missing, degraded). One notification, from the healthy
  layer.
- Completion: user checks off in Todoist → reconciliation marks the matching
  local entry `done` (in the reminder-check pass). A mirrored reminder never
  checked off is aged out after 30 days so it stops being polled forever.
- "Mark X done" from the user → `reminders.py done --id <id>`; a reminder the
  user abandons → `reminders.py cancel --id <id>`.
- Wrong message, time, or origin on an existing reminder →
  `reminders.py update --id <id> [--message …] [--due-at …] [--repair-origin]`.
  Never recreate it to fix a field: the id is what links it to its Todoist
  task. `--repair-origin` re-checks the stored address against
  `meta/lanes.json`.
- A reminder whose delivery keeps failing escalates to the home channel after
  3 attempts and logs `escalating` — that means its recorded origin is wrong;
  fix it with `update --repair-origin` rather than waiting.

## Refinement loop

Append to `meta/refinement-log.md` (never rewrite) on repeated corrections,
recurring `unfiled` fallbacks, missed retrievals, or rules that don't fit.
Entries carry `STATUS: PENDING APPROVAL` or `STATUS: INFO`. Categories include
`POLICY` (classification/routing/tag rules) and **`CODE IMPROVEMENT`**
(proposals for the deterministic layer, with before/after + why).
- **Role (2026-08-29):** the log is a *decision record + async mailbox*,
  not the decision channel — rulings happen in conversation (agent presents
  a before/after and WAITs), the log records outcomes; `PENDING APPROVAL`
  entries are the mailbox the nightly gate surfaces for decisions not
  settled in chat. File-change history lives in git; the log carries the
  decisions git can't.
Approval boundary:
- **Auto-tune:** numeric thresholds already flagged tunable — apply, log why.
- **Sign-off required:** policy changes (classification rules, routing rules,
  SKILL.md edits; canonical-tag coining at capture time is exempt —
  do-then-inform, schema §6) — present a before/after diff and WAIT.
- **Sanctioned code flow (your approval):** on approval of a `CODE
  IMPROVEMENT` entry, implement on `main` — spec + skill + code
  together (docs describe the actual system), run the records.py round-trip
  test, commit + push. Nothing goes live without your go.
- **Never self-patch:** deterministic code outside that sanctioned flow —
  flag it, don't edit unsupervised.

## Failure handling

- Extraction fails → retry once → single untagged `unfiled` raw entry, never
  drop a capture; log the fallback.
- A structured-record insert fails → log; keep the raw entry; retry at next
  consolidation.
- Telegram reply fails → retry once; don't claim success.

## Escalation

Not clearly covered? Consult in order: `second-brain-implementation-guide.md`
(routing/backup decisions) → `second-brain-schema.md` (type/tag/status model)
→ `working-memory-system-spec-v3.md` (plumbing rationale) → `logs/` (past
runs). SKILL.md is git-versioned on `main` — every accepted
refinement is diffable and revertible.
