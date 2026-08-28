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
immutable, full-text capture record and audit trail** (`~/working-memory/raw/`)
— every capture is written there first, then routed to its store. Destinations
(vault notes, SQLite rows, Todoist tasks) are the primary curated artifacts;
**recovery is the backups' job** (vault git + ops-repo snapshots + Todoist
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
- `domain`: 1+ tags from the canonical list at `~/wiki/_meta/tags.md` —
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
and commit `~/working-memory` after the batch):

| `type` | Destination | Mechanism |
|---|---|---|
| `reminder` | local `reminders.json` (+ Todoist mirror) | v2 format: `{id, due_at, message, raw_entry_id, status, origin}`; set `mirrored: true` when the Todoist mirror succeeds |
| `record` `structured` | SQLite `records` table | `python3 ~/.hermes/scripts/records.py --root ~/working-memory add --type … --domain … --occurred-at <event date ISO-8601; now if unknown> --entity … --json '{…}' --notes …` |
| `record` `narrative` | vault `records/` dated note | `records/YYYY-MM-DD-<slug>.md`, frontmatter + prose |
| `project` | vault `projects/` note | `status: active` frontmatter (+ `target_date`, `last_touched` if applicable — see schema §11, digest is out of scope for now) |
| `reference` | vault `references/` | `subtype: entity` → `references/entities/`; `concept` → `references/concepts/`; `procedure` → `references/procedures/` |
| `idea` | vault `ideas/` atomic note | freely linked, no status |
| **undated task** | Todoist **or** vault — one home only | quick one-off errand → Todoist task ONLY (`todoist_only: true`, no vault note) — **config-gated: until `TODOIST_API_TOKEN` is set (stage 3), route to a vault project instead**; substantial/multi-step → vault project note ONLY (no Todoist mirror unless it later acquires a due date → then reminder rules apply) |

**Vault write discipline (all vault destinations):**
- v3 notes **ARE wiki pages**: add an index.md entry under the page's type
  section + a log.md line; frontmatter per the vault SCHEMA; link related notes
  when natural (no forced minimum).
- Frontmatter: `type`, `domain`, `status` (where applicable), `subtype`
  (references), `record_kind` (records), `created`/`updated`.
- **Commit AND push in `~/wiki` after every write** — a local-only commit in a
  sync repo isn't backed up.
- `records.py` is deterministic — for structured records ALWAYS use it (it
  handles JSON escaping and indexing); never hand-edit `records.db`.

**Confirm** with ONE short line after each flush — **showing the destination**:
`✅ → Todoist: buy stamps` · `✅ → wiki (project): renew passport` ·
`✅ record (SQLite): BP 128/82` · `✅ → wiki (concept): …`. If the user says
"No, that should be a project note / a Todoist task", re-route on the spot.

## Retrieve (spec §12 — pick the store by what's asked)

- **Reminder queries** ("what's due this week", "did I take it?") → local
  `reminders.json`, `status: pending`, soonest-first. "Did it get done" →
  cross-check completion in Todoist. Manual Todoist tasks → answer from Todoist.
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

- Mis-filed → re-route to the correct store (fix the SQLite row / vault note /
  Todoist task / raw classification fields).
- Merge/split vault notes → regenerate the named notes from raw.
- Forget X → **confirm first** (the one destructive action), then strike the
  raw entry AND deprecate/remove derived artifacts (vault note, rows, task).
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

- **Local `reminders.json` is the firing fallback and durable record** — the
  cron (`reminder-check.py`) fires only entries WITHOUT a successful mirror.
- **Todoist (stage 3, config-gated):** every new reminder mirrors there
  (best-effort). `mirrored: true` → **local firing is skipped** — Todoist's
  notification IS the reminder; local fires only when the mirror is absent or
  failed (Todoist down, token missing, degraded). One notification, from the
  healthy layer.
- Completion: user checks off in Todoist → reconciliation marks the matching
  local entry `done` (runs in the reminder-check pass, not the digest — digest
  is out of scope).

## Refinement loop

Append to `meta/refinement-log.md` (never rewrite) on repeated corrections,
recurring `unfiled` fallbacks, missed retrievals, or rules that don't fit.
Entries carry `STATUS: PENDING APPROVAL` or `STATUS: INFO`. Categories include
`POLICY` (classification/routing/tag rules) and **`CODE IMPROVEMENT`**
(proposals for the deterministic layer, with before/after + why).
Approval boundary:
- **Auto-tune:** numeric thresholds already flagged tunable — apply, log why.
- **Sign-off required:** policy changes (classification rules, routing rules,
  canonical-tag edits, SKILL.md) — present a before/after diff and WAIT.
- **Sanctioned code flow (your approval):** on approval of a `CODE
  IMPROVEMENT` entry, implement on the `v3.0.0` branch — spec + skill + code
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
runs). SKILL.md is git-versioned on the `v3.0.0` branch — every accepted
refinement is diffable and revertible.
