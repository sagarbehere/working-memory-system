---
name: working-memory
description: "Use for the working-memory system (second brain): input in a reserved lane or starting with 'Hey memory'. Classify each capture (reminder/record/project/reference/idea), route to the vault or Todoist, retrieve, and set reminders."
version: 4.0.0
author: Sagar Behere
license: MIT
metadata:
  hermes:
    tags: [memory, second-brain, capture, retrieval, reminders]
    related_skills: [hermes-agent]
---

# Working Memory System

Personal second-brain system: the user captures thoughts via any connected
client; you classify, file, retrieve, and remind.

**The transcript (`$WM_ROOT/raw/`) is an append-only record of what the user
actually said** — every capture goes there first, before you decide anything
about it. The curated artifacts are the destinations (vault notes, Todoist
tasks); nothing is ever rebuilt from the transcript, and recovery is the
backups' job (the vault's own remote, the nightly push, the Todoist export).

**Read before operating:** `second-brain-schema.md` (the type/tag/status model
— read this first), `working-memory-system-spec.md` (how this system files
those types, §5a), `decisions.md` (why, and what is deliberately absent), then
`logs/` (what actually happened).

## Scope guard

Working-memory input is any message that (a) arrives in a **reserved lane**
(`meta/lanes.json`, or the legacy env lane), or (b) starts with a marker:
`Hey memory` (case-insensitive, word boundary). The capture-gate
hook already buffered and stamped it; **strip the marker token before filing**.
Everything else is ordinary conversation — answer normally, file nothing.
(In a reserved lane, chit-chat reaches the extraction pass, classifies as
neither capture/question/command, and is answered normally — nothing filed.)

## Every incoming message: route it

1. Reservation phrases (`reserve for memory` / `release for memory`) — the
   hook has updated `meta/lanes.json`; confirm in one line, file nothing.
2. Split into items — conservative: one coherent thought = one item.
3. Classify each item: `capture` / `question` / `command`.
4. Capture → **Capture** below. Question → **Retrieve**. Command → **Command**.
5. Chit-chat → answer normally.

## Capture: transcript first, then classify & route

**Append the capture verbatim FIRST — one command:**

```
python3 ~/.hermes/scripts/rawlog.py add --text "<the thought, marker stripped>"
```

That is the whole call — a timestamp and the user's words, nothing else. It
prints `{"ts": …, "duplicate": false}`; `"duplicate": true` means an identical
capture already landed within 24h, so it is filed: do not route it again.

The transcript exists because **your judgment is the fallible part of this system**: if
you mis-file something, or decide a real thought was chit-chat, the user's
words still survive that mistake. The transcript carries no classification —
the destination note does — and nothing links back to it.

Then classify the item and route it:

- **`type`** — `reminder | record | project | reference | idea`. A capture
  with a due date splits into *two items*: a `record` for the event plus a
  `reminder` for the next due (schema §3.1's habit model).
- **`tags`** — 1+ tags from the canonical list at `<vault>/_meta/tags.md`
  (`<vault>` = `WM_VAULT_PATH`, default `~/wiki`). The field is `tags:`,
  Obsidian's built-in tag property; every value is a plain string. Classify
  against the list first and prefer the closest existing tag. When genuinely nothing fits, coin one:
  add it to the list in the same operation and **tell the user you did** —
  this is do-then-inform (schema §6), not something to wait on. They can veto,
  and then the tag comes back out and the entry is re-tagged.
- Heuristics (schema §8): due-date language → `reminder`; dated/factual/no
  action → `record`; open question/decision → `project`; "how do I"/stable
  entity → `reference`; musing/quote → `idea`; decision-time analysis →
  `reference`/concept if worth rereading, else project support material;
  puzzle → `reference` with difficulty/subject as domain tags.
  **Low confidence → `record`.**

| `type` | Destination | Mechanism |
|---|---|---|
| `reminder` | **Todoist** — the only reminder mechanism | See **Reminders** below for the command and the failure rule. |
| `record` — one-off | vault `records/` dated note | `records/YYYY-MM-DD-<slug>.md`, frontmatter + prose |
| `record` — recurring series | **append to the series note** | A measurement or repeated observation (BP, headaches, weight) goes as ONE line appended to a single topical note, e.g. `records/blood-pressure.md` — never a note per reading. Keep the line consistently formatted (date first, then values) so the whole history reads as a table. |
| `project` | vault `projects/` note | `status: active` frontmatter (+ `target_date`, `last_touched` if applicable) |
| `reference` | vault `references/` | `subtype: entity` → `references/entities/`; `concept` → `references/concepts/`; `procedure` → `references/procedures/` |
| `idea` | vault `ideas/` atomic note | freely linked, no status |
| **artifact** (photo, PDF, scan) | leave the file where it already syncs; file a `record` note with `file_ref:` | Never copy the file into the vault. `file_ref` must be a **stable** location, never a path the user might reorganise (schema §9). If you only have a chat attachment and no durable path, say so and ask where it lives. |
| **undated task** | Todoist **or** vault — one home only | quick one-off errand → Todoist task ONLY (no vault note); **project-scoped to-do → checklist line in that project's note** (`## Checklist` at the bottom, `- [ ] item`; append on capture, tick on "mark X done" or an Obsidian edit); substantial/multi-step → project note body |

**Vault write discipline (all vault destinations):**

- **READ `<vault>/SCHEMA.md` BEFORE WRITING, AND FOLLOW IT.** It is the vault's
  own constitution and it governs every page in there, whoever wrote it —
  frontmatter, folder, filename, the index entry, the log line, the page-size
  and archiving rules. A capture routed here becomes an ordinary wiki page and
  is indistinguishable from one written in a wiki session; it does not get a
  dialect of its own.
- Do **not** rely on this file for those details. They are deliberately not
  repeated here: two copies of the vault's rules is exactly how they drift,
  and the vault's copy is the one its own linter enforces.
- Two things that file asks for and that are easy to skip under time pressure:
  **search for an existing page first** (extending beats a near-duplicate),
  and **update `index.md` and `log.md`** — a page missing from the index is
  invisible to retrieval, since the index is where lookups start.
- **Commit AND push in the vault after every write** — a local-only commit in
  a syncing repo is not backed up.

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
  The transcript holds the user's own words, so it answers questions the filed
  notes cannot — including about things that were never filed. Never read
  `raw/` by hand.

## Command (run immediately)

- Mis-filed → move it: edit or relocate the vault note; for a reminder,
  `todoist.py delete --id <id>` and recreate it correctly.
- Merge/split vault notes → edit them directly; the transcript is untouched.
- Forget X → **confirm first** (the one destructive action), then remove the
  derived artifacts: the vault note, and any Todoist task (find it by content
  — `todoist.py list`, then `delete --id`). The transcript is append-only and
  is NOT edited; say so plainly rather than implying the words are gone.
- Ambiguous target → ask, never guess.

## Upkeep

There is **no scheduled agent job**. Nothing wakes you on a timer, so upkeep
happens only when it comes up in conversation:

- Series notes stay itemised — never collapse a measurement history.
- Supersession: a newer fact replaces an older one; mark the old
  `status: superseded` rather than deleting it.
- Condense a reference note when it has visibly sprawled and the user asks,
  or when you are already editing it. Do not go looking for work.
- The transcript is never edited, rotated, or pruned by you.

The nightly backup watchdog reports anything that failed quietly and prunes
old logs. It is not an agent job and costs no tokens.

## Reminders (Todoist only)

Todoist **is** the reminder mechanism — not a mirror of one. Nothing fires
from this machine, and there is no local reminder store.

```
python3 ~/.hermes/scripts/todoist.py create --content <text> --due <ISO-8601 with offset>
```

Use `--due-string "friday 9am"` instead when the user's own phrasing is
natural language and unambiguous, and `--due-string "every monday 9am"` for
anything recurring — Todoist owns recurrence, so never hand-roll the next
occurrence yourself.

- **If the call fails, or `TODOIST_API_TOKEN` is unset, the reminder does not
  exist.** The transcript still has the words. Say that plainly; never report
  a reminder you did not manage to set.
- "Mark X done" → `todoist.py list` for the id, then `close --id <id>`.
  Abandoned → `delete --id <id>`.
- "What's due" → `todoist.py list`, soonest first.

## Refinement loop

Append to `meta/refinement-log.md` (never rewrite) on repeated corrections,
recurring `unfiled` fallbacks, missed retrievals, or rules that don't fit.
Entries carry `STATUS: PENDING APPROVAL` or `STATUS: INFO`. Categories include
`POLICY` (classification/routing/tag rules) and **`CODE IMPROVEMENT`**
(proposals for the deterministic layer, with before/after + why).
- **Role:** the log is a *decision record*, not the decision channel —
  rulings happen in conversation (present a before/after and WAIT), and the
  log records the outcome. It is no longer a mailbox: with no scheduled agent
  job, you only ever run while the user is present, so raise a proposal in
  the conversation rather than filing it for a job that will never read it.
  File-change history lives in git; the log carries the decisions git can't.
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

- **Can't classify it?** The transcript already has the text, so nothing is
  lost. Say you filed it but couldn't place it, and ask — do not guess a
  destination to look decisive. Log the fallback (`outcome: unfiled-fallback`)
  so the nightly watchdog can surface a pattern of them.
- **A vault write fails?** Say so. The transcript has the words; the note does
  not exist. Never report a destination you did not reach.
- **A reply fails to send?** Retry once, then stop. Do not claim success.

## Escalation

Not clearly covered? Consult in order: `second-brain-schema.md` (what kind of
thing is this?) → `working-memory-system-spec.md` §5a (where does that kind
of thing go here?) → `decisions.md` (why is it this way, and what was
deliberately not built?) → `logs/` (what happened on a past run). SKILL.md is
git-versioned on `main`, so every accepted refinement is diffable and
revertible.
