# Working Memory System — Implementation Spec

**Document map:** this file covers *capture, debounce, reminder-delivery, and crash-recovery* — the working-memory-system plumbing. This copy describes **v3.0.0**, forked from the frozen **v2.0.0** tag and evolving independently in its own branch — see the versioning note at the end of this document. For type/tag/status classification, see `second-brain-schema.md`. For build, storage-routing, and backup mechanics, see `decisions.md`.

## Purpose

A frictionless personal working-memory system. Thoughts get captured via any connected client (Telegram, web UI, CLI) — typed, or dictated on-device using the OS's native speech-to-text — with zero categorization effort at capture time. An LLM-backed agent (Hermes) handles all classification, filing, reminders, and cleanup in the background. The user never manually organizes anything and never has to search by scrolling through logs — retrieval is a conversational query to the same agent.

Voice capture is handled entirely on-device (VoiceInk on macOS, native dictation on iOS/iPad) before the message ever reaches Hermes, so Hermes only ever receives text. This also means the user sees and can edit the dictated text before sending, satisfying the "review before it's saved" requirement without any server-side transcription step.

Core principle: **capture is dumb and instant; organization is the agent's job, done after the fact, and is always reversible because the raw log is the source of truth.**

Capture works identically on every client — Telegram, a web UI (via the api_server adapter), CLI, or any future platform — with no per-platform configuration. A message is working-memory input either because it starts with a marker, or because it arrives in a chat the user has explicitly reserved for that purpose (Section 2).

---

## 1. Components

1. **Capture gate** — a small hook on the base adapter's inbound seam (the shared path every platform's message passes through) that detects working-memory input (Section 2) and buffers it (Section 6) before anything else happens.
2. **Extraction/tagging pass** — LLM call that reads a flushed buffer, splits it into items, classifies each as capture or question, and for captures produces: freeform tags, entry type, and (if applicable) a reminder directive and/or supersession flag.
3. **Raw log** — append-only, immutable, timestamped. Ground truth.
4. **Typed destinations** — the Obsidian vault (project/reference/idea/record notes) and Todoist (reminders and errands). Derived and regenerable; not authoritative. *(v3 replaced v2's flat `topics/<tag>.md` files with vault notes — see §4.)*
5. **Promotion/consolidation pass** — background job that routes recurring or important captures into typed notes and periodically condenses the reference-flavoured ones. Gated by `wm-consolidation-gate.py`, which stays silent when there is no work.
6. **Reminders** — created directly in Todoist, which notifies every device (§9). Nothing fires from this machine.
7. **Retrieval handler** — answers a question by searching the right store (§12), not by the user browsing anything.
8. **Deterministic layer** — `wmlib.py` (env, timezone, logging, locking), `rawlog.py`, and `todoist.py`. The agent calls tools; it never hand-writes `raw/`.

---

## 2. Scope boundary: what counts as working-memory input

Working-memory input is defined by a deterministic **message marker**, not by guessing intent on every message the user sends Hermes — this avoids misfiring on ordinary conversation (e.g. "remind me to bring this up with the team" as a passing remark, not a capture instruction).

**The marker:** `Hey memory`, matched case-insensitively at message start with a word-boundary rule (so "hey memories" never matches). Explicit, natural to dictate, and — the point — something nobody types by accident.

A short alias `note` existed until 2026-08-29 and was **removed**: it is an ordinary English sentence-opener, so "Note that the deadline moved", "Note the difference between these", and "Note: I disagree" were all silently filed as captures. A marker's whole job is to be unambiguous; a common word cannot do it. The extra characters cost nothing in practice, because the primary capture surface is a reserved lane where no marker is needed at all.

A message starting with the marker is working-memory input, from any client. The marker is left in the text through the capture gate; the extraction pass strips it before writing, so it's never filed as part of an entry.

**Reserved lanes (optional convenience, not required):** a chat can be reserved for working-memory input with an in-chat phrase (`reserve for memory` / `release for memory`), after which every message in that chat is treated as working-memory input with no marker needed — zero-friction for a primary capture surface. This is stored in `meta/lanes.json`, self-populating from the in-chat declaration, git-backed alongside the rest of the data. Multiple chats, on any platform, can be reserved simultaneously. A reserved chat is a convenience, not a dependency: if it's ever deleted, capture and reminders both degrade gracefully to marker mode and origin/home-channel delivery (Section 9) — nothing breaks, and re-reserving the recreated chat is one phrase.

**Known residual risk:** the user can still send a memory-bound thought without the marker (or forget it's not in a reserved lane) and have it treated as ordinary conversation. Nothing in this system detects that — it's an inherent UX risk of relying on the user to use the marker/lane correctly, a candidate for the refinement loop (Section 17) if it proves to be a recurring problem in practice.

---

## 3. Implementation approach (code vs. agent judgment)

This is not "just a prompt to Hermes," and it's not "a pile of scripts with no agent involved" — it's a deliberate split, placed at whichever side handles each concern more reliably.

**Build as real code (deterministic; can't depend on an LLM choosing to act):**

- The capture gate (Section 6, step 2/3) — marker/lane detection and `auto_skill` stamping, on the base adapter's inbound seam so it works identically across platforms. A single small gate, not a registry.
- The debounce buffer (Section 6) — an exact timer that fires N seconds after the last message with no intervening one.
- The reminder scheduler (Section 9) — firing a message at a specific `due_at`, delivered to the capture's origin chat with a home-channel fallback. Uses the VPS's existing cron, not a new scheduler process.
- Persisting `meta/pending-buffer.json` on every buffered message (Section 11), so a crash doesn't silently drop an in-progress thought.
- Basic file read/write/append/list operations over `/working-memory/`.

**Leave to the agent's own judgment, guided by a written policy — not hardcoded rules:**

- Splitting a flushed buffer into items, classifying each as capture/question/command, choosing tags, detecting supersession (Section 7).
- Consolidation/condensation wording, deciding when to split or merge topic files (Section 8).
- Answering retrieval questions (Section 12).

**How this is wired together:** Hermes gets a skill document (distilled from this spec) plus generic file read/write/list tools, and decides tagging, filing, and consolidation itself each time it's invoked. The capture gate invokes Hermes with the flushed buffer and relevant context (tag list, candidate topic-file excerpts) when it's time to process something; the cron-fired reminder script handles what Hermes shouldn't be trusted to act on purely by its own initiative.

**Escalation path for edge cases:** SKILL.md is terse by design (Section 16) — it won't spell out every judgment call. It ends with a pointer to three things, each answering a different kind of question: this spec's file path (**why** the system works this way), the implementation source location (**how** it currently works), and `logs/` (**what actually happened** on a specific past run — e.g. "why didn't my reminder fire yesterday" needs logs, not the spec or code). This is a lookup capability, not genuine introspection — Hermes decides *whether* to escalate based on its own in-the-moment judgment of "does this look covered," which is inherently imperfect, but the pointer at least makes the right behavior available and instructed.

**Net shape:** a capture gate on the shared adapter seam, a small cron-fired reminder script, and one skill document for the judgment layer — all reusing infrastructure that already runs, with a documented escalation path back to this spec, the source, and the logs.

---

## 4. Storage layout (VPS-only, no cross-device sync required for this component)

```
/working-memory/
  raw/
    2026-08.md              # one file per month, append-only
    2026-09.md
    archive/                 # rotated-out raw files older than the retention window (Section 10)
  todoist-export.jsonl       # nightly export of open Todoist tasks (their only off-box copy)
  logs/
    2026-08.log               # one file per month — diagnostic, not memory (Section 11)
  meta/
    pending-buffer.json      # unflushed per-chat message buffer (Section 11)
    lanes.json               # reserved chats (Section 2)
    rawlog.lock              # flock serialising concurrent transcript appends (§5)
    todoist-state.json       # disposable cache: project id + last reconcile (gitignored)
    refinement-log.md        # curated patterns worth reviewing (Section 17)
```

**v3 change:** `topics/<tag>.md` flat files are left behind (see "Left behind from v2.0.0" in the decisions doc) — derived content now routes to the Obsidian vault as typed notes. The `/working-memory/` folder keeps only the raw transcript, `meta/`, and `logs/`.

- Raw log files are **never edited**, only appended to. Rotate monthly.
- **Transcript search is `rawlog.py search`** — a scan over the monthly files, fast at personal scale. An inverted index existed until 2026-08-29 and was removed: no code maintained it, only the agent did, so it could drift out of step with `raw/` and the only symptom was retrieval quietly missing things. That index is NOT the canonical tag *vocabulary*, which lives at `_meta/tags.md` in the vault and is unaffected.
- `logs/` records operational events — distinct from `raw/`, which records *content*.
- **Everything durable lives under this single `/working-memory/` directory.** A full backup is just archiving this one folder.
- **Backup:** a local git repo over `/working-memory/`, committing on each write, gives point-in-time recovery; `wm-backup-push.py` adds the off-box copy by pushing nightly to a private remote (see "Backup design" in the decisions doc).
- **Timestamps:** every stored timestamp is timezone-aware; everything user-facing is rendered in `WM_TZ`, defaulting to the machine's zone. No component hardcodes an offset.

---

## 5. Raw log entry format

An append-only **verbatim transcript**: a timestamp and exactly what the user
said. `rawlog.py` owns this format and is the only supported writer.

```
## 2026-08-29T16:03:00+05:30

Took vitamin D pill. Next one due in a week.
```

That is the whole entry. It carries no classification — the destination note
does — and no id, because nothing links back to it.

**Why it is verbatim and nothing more (2026-08-29).** The transcript's single
job is to sit *upstream of the agent's judgment*. The vault's git history
records changes to what was filed; only this log records what was said, which
is what makes a misclassification — or a thought wrongly judged to be
chit-chat — recoverable instead of silently lost. It used to carry ids and
typed fields so that entries in other stores could link back to it; those
stores are gone, so the ids and fields went with them.

- Entries are delimited by the header, **never** by the trailing `---`:
  captured text legitimately contains such a line, and treating it as a
  terminator truncated the entry on read.
- Entries written before the cut carry `[id: …]` and field lines. They still
  parse: the transcript is append-only, so formats coexist forever.
- Reading it: `rawlog.py search --text … [--since …]`. Never by hand.

---

## 5a. Where each type is filed

`second-brain-schema.md` says what a capture *is* and how it will be
retrieved; it deliberately names no tools. This is where that model meets this
system.

| Schema type | Filed as | Notes |
|---|---|---|
| Reminder | a Todoist task | Todoist owns due date, recurrence, completion and the cross-device notification (§9). |
| Record — recurring series | ONE note per series in the vault, a line per entry (`records/blood-pressure.md`, `records/headaches.md`) | Never a note per reading. Keep the line shape stable so the file reads as a table. |
| Record — one-off | `records/YYYY-MM-DD-<slug>.md` | Dated note, frontmatter + prose. |
| Project | `projects/<name>.md` | `status: active` in frontmatter. |
| Reference | `references/entities/`, `references/concepts/`, `references/procedures/` | Sub-folder per schema sub-type. |
| Idea / Quote | `ideas/<name>.md` | Atomic, freely linked, no status. |
| Undated task | Todoist **or** a `## Checklist` line in the relevant project note — one home, never both | Quick errand → Todoist only; project-scoped to-do → the project note. |
| Artifact | stays where it already syncs; a Record carries `file_ref` | Per schema §9: a stable location, never a reorganizable path. |

Vault notes carry `type`, `domain`, `status` (where applicable), `subtype`
(references), and `created`/`updated` in frontmatter. Every vault write is
committed **and pushed** — a local-only commit in a syncing repo is not backed
up.

---

## 6. Capture flow

1. Message arrives on any connected client.
2. The capture gate checks: does it start with the marker, or is this chat in a reserved lane? If neither, it's ordinary conversation — untouched, falls through with no effect. If either, `auto_skill: working-memory` is set and the message is buffered.
3. **Buffer, don't process immediately.** A debounce timer (default ~5s for marker input, ~25s for a reserved lane, both tunable) resets on every new buffered message. Only when the timer elapses does the buffer flush: messages concatenated in order into one logical input. A lone `.` or `/done` flushes immediately.
4. Once flushed, the extraction pass (Section 7) runs against the flushed buffer, returning items classified `capture`, `question`, or `command`.
5. Question items go to the retrieval handler (Section 12); command items go to the consolidation pass (Section 8). Neither becomes a raw log entry.
6. Capture items are written as raw log entries (Section 5), tags/type/reminder already resolved by the same extraction call.
7. Any reminder directive updates the local reminder store and mirrors to Todoist (Section 9).
8. A brief confirmation is sent back once processed ("logged 2 items: health/vitamin-d, printer"), especially useful early on (Section 13).

---

## 7. Extraction/tagging pass (LLM call, per flushed buffer)

**Input:** the flushed, concatenated text, plus context for tag reuse and supersession detection: the current canonical domain-tag list, and — for any domain already present in the vault — that note's current content.

This pass does routing (capture/question/command) and, for captures, classification in one call:

- Output is a **list** of items, each with `text`, `kind` (`capture`/`question`/`command`), and — for `capture` items — the classification per `second-brain-schema.md` (the **`type` field itself carries the v3 class**; no separate `second_brain_type`):
  - `type`: `reminder | record | project | reference | idea`
  - if `record`: whether it belongs to an existing series note or is a one-off dated note (schema §3.2 — frequency determines shape)
  - if `reference`: `subtype`: `entity | concept | procedure`
  - `domain`: 1+ flat tags, checked against the canonical list before coining a new one
  - if `project` or `reference`: `status`, defaulting to `active`
  - if a `record` or `reference` involves a file: `file_ref` (schema §9)
  - `reminder` (`{due_at, message}`) when `type: reminder`
- **Habit captures split** per schema §3.1: "took vitamin D, next due Friday" →
  two items — a `record` (the completion) + a `reminder` (the next due).
- Classification heuristics: the structural cues from `second-brain-schema.md` §8 (due-date language → reminder; dated/factual/no action → record; open question/decision → project; "how do I"/stable entity → reference; musing/quote → idea). **Low confidence defaults to `record`** — cheapest to fix later, nothing silently lost.
- `command` items are administrative/corrective instructions ("that's mis-filed," "merge these notes," "forget X") — handed to consolidation (Section 8), never producing a transcript entry. Corrections touch whichever destination the original item was routed to (a vault note or a Todoist task) — same confirm-before-destructive rule. The transcript itself is never edited.
- Splitting is conservative — one coherent thought touching two tags stays one entry; split only for genuinely unrelated content.
- One LLM call per flushed buffer, kept cheap and fast.

---

## 8. Promotion & consolidation policy

**Promotion (v3):** recurring or important captures graduate into the vault as typed notes at routing time (§5a is canonical for routing) instead of `topics/<tag>.md` files — the flat topic files are left behind in v2.0.0. The raw log remains the ground truth from which any note can be regenerated.

**Consolidation (v3):** runs on a schedule or size threshold:
- Reference-flavored content (procedures, entity pages) can be condensed like the old topic files — it's derived and regenerable.
- Series notes (BP, headaches) stay itemized and are never collapsed — a measurement history is the point, and summarising destroys it.
- Supersession: a newer fact replaces the older line (`supersedes`); `status: superseded` on vault notes suppresses them from default answers.
- Expired lines drop (resolved reminders, "due in a week" facts); the raw entry itself is untouched.

Fully reversible — derived content regenerates from the raw log, so "that's mis-filed" or "split/merge these" just triggers a regeneration.

**Handling `command` items:** run immediately, not on the next scheduled pass. "Forget entirely" strikes the underlying raw entry's content too (the one justified exception to "raw log is never edited") and removes or deprecates the derived artifacts — confirm with the user first, since it's the one destructive, hard-to-reverse action in the system. If a command is ambiguous about which entry/topic it means, ask rather than guess.

---

## 9. Reminders

**Todoist is the reminder mechanism.** The agent creates the task directly
(`todoist.py create --content … --due …`); Todoist owns the due date,
recurrence, completion state, and the notification to every device. Nothing
fires from this machine, and there is no local reminder store.

**Why (2026-08-29 cut).** v3 originally kept `reminders.json` plus a
five-minute cron tick as a durable firing layer, with Todoist as a mirror.
That existed to serve deployments without a Todoist account — a configuration
the author does not run. It cost roughly 18% of the codebase and produced most
of the system's concurrency: two processes writing one file, a lost-update
race that silently erased captures, an origin-resolution bug that would have
retried into a nonexistent chat forever, and a polling loop making ~288 API
calls a day. Todoist's own reliability comfortably exceeds that.

- **If Todoist is unconfigured, reminders are unavailable.** The skill must
  say so rather than appear to set one. Captures and notes still work.
- **If the create call fails**, the capture is safe in the transcript but the
  reminder does not exist — report that plainly.
- **Recurrence** uses Todoist's own (`--due-string "every monday 9am"`); do
  not hand-roll regeneration.
- **The nightly backup exports open tasks** to `todoist-export.jsonl`, which
  is their only off-box copy.

---

## 10. Cleanup & aging

1. **Raw log rotation** — files older than ~60-90 days move to `raw/archive/`, still grep-able.
2. **Expiry** — time-bound lines (resolved reminders, "due in a week" facts) drop from derived notes once resolved; the raw entry itself is untouched.
3. **Supersession** — new fact replaces old rather than accumulating (Section 8).
4. **Size-triggered condensation** — a derived note (Reference-flavored) past ~2-3KB gets a full condense-and-rewrite on its next write.
5. **Log rotation** — `logs/` deleted (not archived) after ~30 days; shorter retention than the raw log, since it's diagnostic, not memory.

---

## 11. Error handling & crash recovery

Every event below writes one line to the current month's `logs/` file — timestamp, component, event, outcome. Minimum logged events: every extraction pass invocation and result, every reminder fire attempt and result, every `command` item executed, and each failure scenario below.

- **Buffer durability** — the per-chat buffer persists to `meta/pending-buffer.json` so a Hermes restart doesn't silently drop an in-progress thought.
- **Extraction pass failures** — retry once; on continued failure, fall back to writing the raw text as a single untagged (`unfiled`) entry rather than losing the capture.
- **Reminder delivery during downtime** — if the VPS/Hermes was down when a reminder's `due_at` passed, the next cron run fires it as soon as it's back up (check for any `due_at` in the past with `status: pending`, not just entries due since the last check).
- **Delivery failures** — retry with backoff; don't let a failed send silently mark a reminder as fired.

---

## 12. Retrieval flow

Questions are diverted here by the extraction pass — question items never produce a raw log entry. Which store gets searched depends on what is being asked:

**A. Reminder queries** ("what's due this week", "did I take it?") — `todoist.py list`, soonest-first; `todoist.py completed --since … --until …` for history. Todoist holds every reminder, including ones the user created there by hand.

**B. Series and measurements** ("when did I last buy X", "BP last month") — read the relevant vault series note and reason over it directly. These files are small; the agent is the query engine, so correlate and summarise rather than quoting lines.

**C. Vault content (project / reference / idea / narrative record)** — search the vault by title, backlink, or domain tag; exclude `status: archived` / `superseded` from default answers (surface them if explicitly asked). Reference pages look up by name (entity/concept/procedure); Projects by open status.

**D. Everything else, and fallback** — the raw log is the ground truth: `rawlog.py search --tag/--text/--type` (covering the current month and `raw/archive/`). Answers conversationally; the user never needs to know or guess a tag or type name.

At this personal scale, keyword search over a small file set is sufficient — no index, no vector DB, no embedding search needed.

---

## 13. Confirmation behavior (early phase)

For the first few weeks, briefly confirm what got filed after each processed buffer. Surfaces misclassification early. Relax or turn off once trust in tagging quality is established — a toggle, not hardcoded.

---

## 14. Open items for the implementer

- Exact thresholds (promotion occurrence count, condensation size, raw log rotation window, debounce duration) are starting guesses — tune based on real usage.
- **Off-box backup** for `/working-memory/` is unresolved at this layer by default (Section 4) — decide separately whether it's worth covering for this v2.0.0 install.

---

## 15. Non-goals

- No multi-user support.
- No semantic/vector search — plain tag and keyword matching is enough at this scale.
- No server-side voice transcription — voice capture is handled entirely on-device before reaching Hermes. (Linux has no equivalent to Apple's native dictation, so voice capture on Linux is unresolved for now — typing is the fallback there.)

(Cross-platform capture — web UI, CLI, any future client — and no dependency on a single fixed chat/thread are *supported*, via the marker mechanism (Section 2), not excluded.)

---

## 16. Packaging for distribution (self-hosted, per-user)

Each person runs their own copy against their own Hermes instance — single-user per install. The package contains:

- **`SKILL.md`** — the distilled operational policy: tag format, routing rules, splitting/supersession heuristics, topic file format, consolidation behavior. Terse and rule-based, ending with a pointer to this spec, the implementation source, and `logs/`.
- **The capture gate** — hooks into the base adapter's inbound seam of the user's own already-running Hermes instance.
- **`todoist.py`** — the Todoist client through which all reminders are created and queried.
- **`crontab.example`** — the exact line(s) to add.
- **`setup.sh`** — creates the `/working-memory/` skeleton and initializes the git repo.
- **`.env.example`** — working-memory path and tunable thresholds.
- **`README.md`** — install steps assuming Hermes + a messaging integration + cron already exist.
- **This spec.**

What the package deliberately does **not** contain: a new bot/client token flow or a scheduler daemon — those would duplicate infrastructure every target install already has running.

---

## 17. Self-improvement / refinement loop

Goal: let Hermes notice when SKILL.md or the underlying design has a gap, without silently rewriting its own operating policy unsupervised.

**`meta/refinement-log.md`** — append-only, distinct from `logs/`: records curated patterns worth reviewing, not every event. Written to when: the user issues the same kind of correction more than once for a similar situation; extraction repeatedly falls back to `unfiled` for a recognizable category; a retrieval question misses something actually captured; or Hermes itself notices a SKILL.md rule doesn't fit a case it just handled.

**Review cadence:** folded into the existing consolidation schedule — periodically, review `refinement-log.md` and draft concrete proposed changes.

**Approval boundary:**
- **Low-risk, self-tuning:** adjusting an already-tunable numeric threshold based on observed friction — auto-applied, change and reasoning logged.
- **Higher-risk, needs sign-off first:** changes to classification rules, tag policy, splitting/supersession heuristics, or command-handling logic. Present as a before/after diff and wait for confirmation.
- **Not self-patchable at all:** anything in the deterministic code (capture gate, `rawlog.py`, `todoist.py`) — surfaced as a flagged issue for the user, not edited unsupervised.

**Why this is safe:** SKILL.md is kept under git version control, so every accepted refinement is diffable and revertible.

---

## 18. Versioning note

This document (`working-memory-system-spec-v3.md`) is the **v3.0.0** branch's own copy of the spec, forked from the **v2.0.0** tag — the version tagged and frozen for the current public release, including existing Reddit users, who can rely on `working-memory-system-spec.md` at that tag not changing further.

v3.0.0 reuses and adapts the plumbing in Sections 1-17 above rather than rebuilding it, and may continue to evolve this document independently of v2.0.0 as the branch develops. It's paired with `second-brain-schema.md` (the type/tag/status classification model) and `decisions.md` (build order, storage routing, backup plan) — together, the three documents that describe v3.0.0 in full. A v3.0.0 install is never affected by changes to the frozen v2.0.0 tag, and vice versa.
