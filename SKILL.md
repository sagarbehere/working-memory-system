---
name: working-memory
description: "Use for the working-memory system v3 (second brain): input in a reserved lane or starting with 'Hey memory'/'note'. Classify each capture (reminder/record/project/reference/idea), route to the vault or Todoist, retrieve, and set reminders."
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
(vault notes, Todoist tasks) are the primary curated artifacts;
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

## Capture: transcript first, then classify & route

**Append the capture verbatim FIRST — one command:**

```
python3 ~/.hermes/scripts/rawlog.py add --text "<the thought, marker stripped>"
```

That is the whole call: a timestamp and your words, nothing else. It prints
`{"ts": …, "duplicate": false}`; `"duplicate": true` means an identical
capture landed within 24h, so it is already filed — do not route it again.

The transcript exists because **you are the unreliable part of this system**:
if you mis-file a thought, or judge a real one to be chit-chat, the verbatim
text is the only thing that survives your mistake. It carries no
classification — the destination note does — and nothing links back to it.

Then classify the item and route it:

- **`type`** — `reminder | record | project | reference | idea`. A capture
  with a due date splits into *two items*: a `record` for the event plus a
  `reminder` for the next due (schema §3.1's habit model).
- **`domain`** — 1+ tags from the canonical list at `<vault>/_meta/tags.md`
  (`<vault>` = `WM_VAULT_PATH`, default `~/wiki`). Classify against it first;
  coin a new tag only when nothing fits, adding it to that list in the same
  operation (a policy change → refinement log).
- Heuristics (schema §8): due-date language → `reminder`; dated/factual/no
  action → `record`; open question/decision → `project`; "how do I"/stable
  entity → `reference`; musing/quote → `idea`; decision-time analysis →
  `reference`/concept if worth rereading, else project support material;
  puzzle → `reference` with difficulty/subject as domain tags.
  **Low confidence → `record`.**

| `type` | Destination | Mechanism |
|---|---|---|
| `reminder` | **Todoist** — the only reminder mechanism | `python3 ~/.hermes/scripts/todoist.py create --content <text> --due <ISO-8601 with offset>` (or `--due-string "friday 9am"`). Todoist notifies on every device; nothing fires locally. If the call fails, say so plainly — the capture is safe in the transcript but **the reminder does not exist**, so do not claim it was set. |
| `record` — one-off | vault `records/` dated note | `records/YYYY-MM-DD-<slug>.md`, frontmatter + prose |
| `record` — recurring series | **append to the series note** | A measurement or repeated observation (BP, headaches, weight) goes as ONE line appended to a single topical note, e.g. `records/blood-pressure.md` — never a note per reading. Keep the line consistently formatted (date first, then values) so the whole history reads as a table. |
| `project` | vault `projects/` note | `status: active` frontmatter (+ `target_date`, `last_touched` if applicable) |
| `reference` | vault `references/` | `subtype: entity` → `references/entities/`; `concept` → `references/concepts/`; `procedure` → `references/procedures/` |
| `idea` | vault `ideas/` atomic note | freely linked, no status |
| **undated task** | Todoist **or** vault — one home only | quick one-off errand → Todoist task ONLY (no vault note); **project-scoped to-do → checklist line in that project's note** (`## Checklist` at the bottom, `- [ ] item`; append on capture, tick on "mark X done" or an Obsidian edit); substantial/multi-step → project note body |

**Vault write discipline (all vault destinations):**
- v3 notes **ARE wiki pages**: add an index.md entry under the page's type
  section + a log.md line; frontmatter per the vault SCHEMA; link related notes
  when natural (no forced minimum).
- Frontmatter: `type`, `domain`, `status` (where applicable), `subtype`
  (references), `created`/`updated`.
- **Commit AND push in the vault (`WM_VAULT_PATH`, default `~/wiki`) after every write** — a local-only commit in a
  sync repo isn't backed up.

**Confirm** with ONE short line after each flush — **showing the destination**:
`✅ → Todoist: buy stamps` · `✅ → wiki (project): renew passport` ·
`✅ → wiki (checklist): WM — X` · `✅ → wiki (series): BP 128/82` ·
`✅ → wiki (concept): …`. If the user says
"No, that should be a project note / a Todoist task", re-route on the spot.

## Retrieve (pick the store by what's asked)

- **Reminders / what's due** → `todoist.py list` (add `--notes` for comments).
  Todoist holds every reminder; there is no local reminder store.
- **Completion history** ("what did I finish last month") →
  `todoist.py completed --since YYYY-MM-DD --until YYYY-MM-DD [--project NAME]`
  (`--by due` for the due-date view). Answer conversationally, grouped by project.
- **Measurements and series** ("BP last month", "when did the headaches
  cluster") → read the series note in the vault and reason over it directly.
  It is a small file; you are the query engine, so correlate and summarise
  rather than just quoting lines.
- **Undated tasks** → Todoist for errands, vault `projects/` for project notes.
- **Vault content** (project/reference/idea/record) → search the vault by
  title/backlink/domain tag. Exclude `status: archived|superseded` from
  default answers (surface if explicitly asked).
- **Fallback — "did I ever say…"** → `rawlog.py search --text "…" [--since …]`.
  This is the transcript of what you actually said, so it answers questions
  the filed notes cannot. Never read `raw/` by hand.

## Command (run immediately)

- Mis-filed → move it: edit or relocate the vault note; for a reminder,
  `todoist.py delete --id <id>` and recreate it correctly.
- Merge/split vault notes → edit them directly; the transcript is untouched.
- Forget X → **confirm first** (the one destructive action), then remove the
  derived artifacts: the vault note, and any Todoist task (find it by content
  — `todoist.py list`, then `delete --id`). The transcript is append-only and
  is NOT edited; say so plainly rather than implying the words are gone.
- Ambiguous target → ask, never guess.

## Consolidation (v3, nightly job + size triggers)

- Reference-flavored vault notes: condense like v2 topic files (derived,
  regenerable).
- **Series notes are never collapsed** — a measurement history is the point;
  summarising it destroys what it exists for.
- Supersession: newer replaces older; `status: superseded` suppresses from
  defaults.
- Expiry: resolved reminders / time-bound lines drop from derived notes; raw
  untouched. Rotate raw >60-90d to `raw/archive/`; delete `logs/` >30d.
- The nightly gate (`wm-consolidation-gate.py`) prints a digest only when
  there's work; a silent night is normal.

## Reminders (Todoist only)

- **Todoist is the reminder mechanism, not a mirror.** There is no local
  reminder store and nothing fires from this box; Todoist notifies on every
  device. If `TODOIST_API_TOKEN` is unset, reminders are unavailable — say so
  rather than pretending to set one.
- Create: `todoist.py create --content <text> --due <ISO-8601 with offset>`,
  or `--due-string "friday 9am"` when the user's phrasing is natural language
  and unambiguous. Report failure plainly; the transcript keeps the words but
  the reminder will not exist.
- "Mark X done" → `todoist.py list` to find the id, then
  `todoist.py close --id <id>`. Abandoned → `todoist.py delete --id <id>`.
- "What's due" → `todoist.py list`, soonest first.
- Recurring reminders: use Todoist's own recurrence via `--due-string`
  ("every monday 9am"). Do not hand-roll regeneration.

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
  together (docs describe the actual system), run `tests/run_all.py`,
  commit + push. Nothing goes live without your go.
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
