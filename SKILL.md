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
client; you classify, file, retrieve, and remind. **Raw log is ground truth**
(`~/working-memory/raw/`) — every capture is written there first, then routed
to its store. Everything derived (vault notes, SQLite rows, Todoist tasks) is
regenerable or correctable; nothing is silently lost.

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
type: log+reminder
second_brain_type: reminder
domain: health, vitamin-d
supersedes: 20260817-1610-01

<text>
---
```

- id = deterministic timestamp; `-01`, `-02`… per flush. Dedup first (check
  current month raw + pending buffer; a duplicate re-send is NOT re-filed).
- `type` stays `log` / `reminder` / `log+reminder` (v2 field).
- **v3 fields** (classification per schema §7/§8): `second_brain_type`
  (`reminder|record|project|reference|idea`), `domain` (1+ tags from the
  canonical list), `status` (project/reference only, default `active`),
  `record_kind` (`structured|narrative` for records), `subtype`
  (`entity|concept|procedure` for references), `file_ref` when a file is
  involved (schema §12: stable location, never a reorganizable path).
- Classification heuristics (schema §8): due-date language → `reminder`;
  dated/factual/no action → `record`; open question/decision → `project`;
  "how do I"/stable entity → `reference`; musing/quote → `idea`; decision-time
  analysis → `reference`/concept if worth rereading, else project support
  material; puzzle → `reference` with difficulty/subject as domain tags.
  **Low confidence → `record`.** No `type: query/comparison/puzzle` exists —
  the schema's §14 test decides their home.
- **Domain tags come from the canonical list** at `~/wiki/_meta/tags.md`
  (vault, git-synced). Classify against it first; coin a new tag only when
  nothing fits, and add it to the list in the same operation. Coining a tag is
  a policy change → log it to the refinement log.

Then route per the table (update `meta/tag-index.json` in the same operation,
and commit `~/working-memory` after the batch):

| `second_brain_type` | Destination | Mechanism |
|---|---|---|
| `reminder` | local `reminders.json` (+ Todoist mirror, §Todoist) | v2 format: `{id, due_at, message, raw_entry_id, status, origin}` |
| `record` `structured` | SQLite `records` table | `python3 ~/.hermes/scripts/records.py --root ~/working-memory add --type … --domain … --occurred-at <event date ISO-8601; now if unknown> --entity … --json '{…}' --notes …` |
| `record` `narrative` | vault `records/` dated note | `records/YYYY-MM-DD-<slug>.md`, frontmatter + prose |
| `project` | vault `projects/` note | `status: active` frontmatter (+ `target_date`, `last_touched` if applicable — see schema §11, digest is out of scope for now) |
| `reference` | vault `entities/` (entity) or `concepts/` (concept, procedure) | `subtype` + `status: active` frontmatter |
| `idea` | vault `ideas/` atomic note | freely linked, no status |

**Vault write discipline (all vault destinations):**
- Frontmatter per schema: `type`, `domain`, `status` (where applicable),
  `subtype` (references), `created`/`updated`, `source_url` where relevant.
- **Commit AND push in `~/wiki` after every write** — a local-only commit in a
  sync repo isn't backed up.
- v3 notes are NOT wiki pages: do not add index.md/log.md entries, do not use
  wiki SCHEMA types, do not force ≥2 wikilinks. Link related notes when
  natural.
- `records.py` is deterministic — for structured records ALWAYS use it (it
  handles JSON escaping and indexing); never hand-edit `records.db`.

**Confirm** with ONE short line after each flush (v2 style): `✅ logged: …` /
`✅ reminder set: …`. Include type when it clarifies (`✅ record (structured):
BP 128/82`).

## Retrieve (spec §12 — pick the store by what's asked)

- **Reminder queries** ("what's due this week", "did I take it?") → local
  `reminders.json`, `status: pending`, soonest-first. "Did it get done" →
  cross-check completion in Todoist (stage 3). Manual Todoist tasks → answer
  from Todoist.
- **Structured records** ("when did I last buy X", "BP last month") →
  `records.py query --domain … --entity … --since … --until …`; answer
  conversationally. Prescription overlap → pull rows, diff `data_json` in
  reasoning.
- **Vault content** (project/reference/idea/narrative record) → search vault
  by title/backlink/domain tag. Exclude `status: archived|superseded` from
  default answers (surface if explicitly asked).
- **Fallback / everything else** → raw log ground truth: `tag-index.json` →
  current month raw → `raw/archive/`. Never make the user guess a tag or type.

## Command (run immediately)

- Mis-filed → re-route to the correct store (fix the SQLite row / vault note /
  Todoist task) AND correct the raw entry's classification fields.
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

- **Local `reminders.json` is the firing source of truth** — same cron
  (`reminder-check.py`), same origin/home-channel delivery, overdue-pending
  fires after downtime. If Todoist is absent or down, reminders work unchanged.
- **Todoist (stage 3, config-gated):** mirror every new reminder there
  (best-effort, retried at next reconciliation). Todoist provides cross-device
  visibility + notifications, never gates firing. Completion: user checks off
  in Todoist → reconciliation marks the matching local entry `done` (runs in
  the reminder-check pass, not the digest — digest is out of scope).
- Write local first, then mirror. Drift: Todoist wins for done-ness, local
  wins for firing.

## Refinement loop

Append to `meta/refinement-log.md` (never rewrite) on repeated corrections,
recurring `unfiled` fallbacks, missed retrievals, or rules that don't fit.
Approval boundary:
- **Auto-tune:** numeric thresholds already flagged tunable — apply, log why.
- **Sign-off required:** classification rules, routing rules, edits to the
  canonical domain-tag list, and any SKILL.md policy change — present a
  before/after diff and WAIT.
- **Never self-patch:** deterministic code (capture gate, `records.py`,
  `reminder-check.py`) — flag it, don't edit unsupervised.

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
