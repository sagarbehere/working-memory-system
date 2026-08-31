# Working Memory System — Implementation Spec

**Document map:** this file covers *capture, debounce, reminder-delivery, and crash-recovery* — the working-memory-system plumbing. This copy describes **v4.0.0** — see the versioning note at the end of this document. For type/tag/status classification, see `second-brain-schema.md`. For build, storage-routing, and backup mechanics, see `decisions.md`.

## Purpose

A frictionless personal working-memory system. Thoughts get captured via any connected client (Telegram, web UI, CLI) — typed, or dictated on-device using the OS's native speech-to-text — with zero categorization effort at capture time. An LLM-backed agent (Hermes) handles all classification, filing, reminders, and cleanup in the background. The user never manually organizes anything and never has to search by scrolling through logs — retrieval is a conversational query to the same agent.

Voice capture is handled entirely on-device (VoiceInk on macOS, native dictation on iOS/iPad) before the message ever reaches Hermes, so Hermes only ever receives text. This also means the user sees and can edit the dictated text before sending, satisfying the "review before it's saved" requirement without any server-side transcription step.

Core principle: **capture is dumb and instant; organization is the agent's job, done after the fact, and is cheap to correct because a mis-filed note is one instruction away from being moved.** Nothing regenerates automatically, and since 2026-08-31 this system keeps no copy of the message it filed from (§8).

Capture works identically on every client — Telegram, a web UI (via the api_server adapter), CLI, or any future platform — with no per-platform configuration. A message is working-memory input either because it starts with a marker, or because it arrives in a chat the user has explicitly reserved for that purpose (Section 2).

---

## 1. Components

1. **Capture gate** — a small hook on the base adapter's inbound seam (the shared path every platform's message passes through) that detects working-memory input (Section 2) and buffers it (Section 6) before anything else happens.
2. **Extraction/tagging pass** — LLM call that reads a flushed buffer, splits it into items, classifies each as capture or question, and for captures produces: freeform tags, entry type, and (if applicable) a reminder directive and/or supersession flag.
3. **Typed destinations** — the Obsidian vault (project/reference/idea/record notes) and Todoist (reminders and errands). These are the only stores this system writes. *(v3 replaced v2's flat `topics/<tag>.md` files with vault notes — see §4.)*
4. **Promotion** — happens inline, at capture time: the agent routes a capture straight to its typed destination (§5). There is no background pass and no scheduled job; the nightly gate that used to run one was removed in the 2026-08-29 cut (§8, `decisions.md`).
5. **Reminders** — created directly in Todoist, which notifies every device (§9). Nothing fires from this machine.
6. **Retrieval handler** — answers a question by searching the right store (§12), not by the user browsing anything.
7. **Deterministic layer** — `wmlib.py` (env, timezone, logging, locking) and `todoist.py`. The agent calls tools rather than hand-rolling API calls.

A **raw log** was component 3 until 2026-08-31, appending every capture verbatim before anything was decided about it. It is gone; see §18 and `decisions.md`. Nothing in this system now records the message it filed from.

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
- The Todoist client (Section 9) — creating the task and reading back what is due. Todoist owns firing and delivery; nothing here schedules or fires anything.
- Persisting `meta/pending-buffer.json` on every buffered message (Section 11), so a crash doesn't silently drop an in-progress thought.
- Basic file read/write/append/list operations over `/working-memory/`.

**Leave to the agent's own judgment, guided by a written policy — not hardcoded rules:**

- Splitting a flushed buffer into items, classifying each as capture/question/command, choosing tags, detecting supersession (Section 7).
- Choosing a capture's destination and wording the note it becomes (Sections 5, 8).
- Answering retrieval questions (Section 12).

**How this is wired together:** Hermes gets a skill document (distilled from this spec) plus generic file read/write/list tools and the CLIs in this package, and decides tagging, filing, and routing itself each time it's invoked. The capture gate invokes Hermes with the flushed buffer and relevant context (the vault's `SCHEMA.md`, candidate note excerpts) when it's time to process something. The agent is alive only while the user is talking to it: nothing invokes it on a schedule.

**Escalation path for edge cases:** SKILL.md is terse by design (Section 16) — it won't spell out every judgment call. It ends with a pointer to three things, each answering a different kind of question: this spec's file path (**why** the system works this way), the implementation source location (**how** it currently works), and `logs/` (**what actually happened** on a specific past run — e.g. "why didn't my reminder fire yesterday" needs logs, not the spec or code). This is a lookup capability, not genuine introspection — Hermes decides *whether* to escalate based on its own in-the-moment judgment of "does this look covered," which is inherently imperfect, but the pointer at least makes the right behavior available and instructed.

**Net shape:** a capture gate on the shared adapter seam, two small modules (`wmlib`, `todoist`), one nightly health watchdog, and one skill document for the judgment layer — all reusing infrastructure that already runs, with a documented escalation path back to this spec, the source, and the logs.

---

## 4. Storage layout (VPS-only, no cross-device sync required for this component)

```
/working-memory/
  logs/
    2026-08.log               # one file per month — diagnostic, not memory (Section 11)
  meta/
    pending-buffer.json      # unflushed per-chat message buffer (Section 11)
    lanes.json               # reserved chats (Section 2)
    todoist-state.json       # disposable cache: project id + last reconcile (gitignored)
    refinement-log.md        # curated patterns worth reviewing (Section 17)
```

**v3 change:** `topics/<tag>.md` flat files are left behind (see "Left behind from v2.0.0" in the decisions doc) — derived content now routes to the Obsidian vault as typed notes.

**2026-08-31:** `raw/` and `meta/rawlog.lock` are gone with the transcript (§18). What remains here is coordination state and diagnostics — no captured *content* at all. The content lives in the vault and in Todoist, each with its own backup.

- An existing install may still hold a `raw/` directory from before that cut. Nothing reads or writes it; it is kept or archived at the user's discretion, and no tool in this package deletes it.
- `logs/` records operational events, never content.
- **Everything durable this system owns lives under this single `/working-memory/` directory.** A full backup is just archiving this one folder — but note that the *memories* are not in it.
- **This directory is not backed up, deliberately (2026-08-31).** It was committed and pushed nightly to a private remote; that remote is retired and nothing automated commits here any more. What is left — `lanes.json`, diagnostics, a disposable cache — is cheap to lose and cheap to recreate. It is still a git repo, so a manual commit remains available. The notes are in the vault, which has its own remote; see `decisions.md`, "The 2026-08-31 watchdog cut".
- **Timestamps:** every stored timestamp is timezone-aware; everything user-facing is rendered in `WM_TZ`, defaulting to the machine's zone. No component hardcodes an offset.

---

## 5. Where each type is filed

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

Vault notes carry `type`, `tags`, `status` (where applicable), `subtype`
(references), and `created`/`updated` in frontmatter. Every vault write is
committed **and pushed** — a local-only commit in a syncing repo is not backed
up.

---

## 6. Capture flow

1. Message arrives on any connected client.
2. The capture gate checks: does it start with the marker, or is this chat in a reserved lane? If neither, it's ordinary conversation — untouched, falls through with no effect. If either, `auto_skill: working-memory` is set and the message is buffered.
3. **Buffer, don't process immediately.** A single debounce timer (`WM_DEBOUNCE_SECONDS`, default 5s) resets on every new buffered message. The same timer applies to marker input and reserved lanes alike — a longer lane timer was specified once and never built, and the idea was abandoned (see `handler.py`'s docstring). Only when the timer elapses does the buffer flush: messages concatenated in order into one logical input. A lone `.` or `/done` flushes immediately.
4. Once flushed, the extraction pass (Section 7) runs against the flushed buffer, returning items classified `capture`, `question`, or `command`.
5. Question items go to the retrieval handler (Section 12); command items are executed immediately (Section 8).
6. Capture items are written straight to their destination (Section 5) — a vault note or a Todoist task — with tags/type/reminder resolved by the same extraction call. There is no intermediate store and no copy of the message.
7. Any reminder directive creates a Todoist task (Section 9). There is no local reminder store.
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
- `command` items are administrative/corrective instructions ("that's mis-filed," "merge these notes," "forget X") — executed immediately (Section 8). Corrections touch whichever destination the original item was routed to (a vault note or a Todoist task) — same confirm-before-destructive rule.
- Splitting is conservative — one coherent thought touching two tags stays one entry; split only for genuinely unrelated content.
- One LLM call per flushed buffer, kept cheap and fast.

---

## 8. Promotion & consolidation policy

**Promotion happens at capture time.** A capture is routed straight to its typed destination (§5 is canonical) — there is no staging period, nothing graduates later, and nothing is written down on the way through.

**Consolidation is not a job.** The nightly pass was removed in the 2026-08-29 cut: it cost tokens every night to report work the agent had already done inline at capture time. What survives is tidying the agent does while it is already editing a note:

- Reference-flavoured content (procedures, entity pages) can be condensed when it grows unwieldy — the vault's git history is what makes that safe to do.
- Series notes (BP, headaches) stay itemized and are never collapsed — a measurement history is the point, and summarising destroys it.
- Supersession: a newer fact replaces the older line; `status: superseded` on vault notes suppresses them from default answers.
- Expired lines drop (resolved reminders, "due in a week" facts); the raw entry itself is untouched.

**A note is not rebuildable from anything this system stores.** Reversibility comes from the vault's own git history, which is why every write commits *and* pushes. Two things this system does not do, and never did: regenerate a note automatically, and keep a copy of the message a note came from.

**Handling `command` items:** run immediately — there is no later pass to defer to. "Forget entirely" removes or deprecates what was filed (the vault note, the Todoist task) — confirm with the user first, since it's the one destructive, hard-to-reverse action in the system. If a command is ambiguous about which entry/topic it means, ask rather than guess.

**The reach of "forget" ends at what was filed.** The original message stays in Hermes' session history, which this system does not own or touch (§12 D). Say so if it matters to the user, rather than implying every trace is gone.

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
- **If the create call fails**, the reminder does not exist and this system
  has kept nothing — report that plainly and repeat the reminder text back in
  the reply, so the user can act on it.
- **Recurrence** uses Todoist's own (`--due-string "every monday 9am"`); do
  not hand-roll regeneration.
- **There is no local export of Todoist tasks.** A nightly one existed until
  2026-08-31 and went with the backup half of the watchdog; Todoist's own
  hosting is the copy. This is the one place where that cut removed a real
  redundancy rather than a redundant one — see `decisions.md`.

---

## 10. Cleanup & aging

1. **Expiry** — time-bound lines (resolved reminders, "due in a week" facts) drop from derived notes once resolved.
2. **Supersession** — new fact replaces old rather than accumulating (Section 8).
3. **Size-triggered condensation** — a reference-flavoured note that has grown unwieldy gets condensed on its next write. The vault's `SCHEMA.md` carries the threshold (~200 lines) and the series-note exemption; it is the authority, not this line.
4. **Log rotation** — `logs/` deleted (not archived) after ~30 days. These are diagnostics, not memory; nothing of the user's is in them.

---

## 11. Error handling & crash recovery

Every event below writes one line to the current month's `logs/` file — timestamp, component, event, outcome. Minimum logged events: every extraction pass invocation and result, every reminder fire attempt and result, every `command` item executed, and each failure scenario below.

- **Buffer durability** — the per-chat buffer persists to `meta/pending-buffer.json` so a Hermes restart doesn't silently drop an in-progress thought.
- **Extraction pass failures** — retry once; on continued failure, tell the user plainly that nothing was filed and repeat back what you were trying to file, so it is visible in the reply. Since 2026-08-31 there is no fallback store to park it in (§18), and an `unfiled` entry written somewhere the user never looks was never the real safety net anyway — the conversation is.
- **Reminder delivery during downtime** — if the VPS/Hermes was down when a reminder's `due_at` passed, the next cron run fires it as soon as it's back up (check for any `due_at` in the past with `status: pending`, not just entries due since the last check).
- **Delivery failures** — retry with backoff; don't let a failed send silently mark a reminder as fired.

---

## 12. Retrieval flow

Questions are diverted here by the extraction pass — a question is answered, never filed. Which store gets searched depends on what is being asked:

**A. Reminder queries** ("what's due this week", "did I take it?") — `todoist.py list`, soonest-first; `todoist.py completed --since … --until …` for history. Todoist holds every reminder, including ones the user created there by hand.

**B. Series and measurements** ("when did I last buy X", "BP last month") — read the relevant vault series note and reason over it directly. These files are small; the agent is the query engine, so correlate and summarise rather than quoting lines.

**C. Vault content (project / reference / idea / narrative record)** — search the vault by title, backlink, or domain tag; exclude `status: archived` / `superseded` from default answers (surface them if explicitly asked). Reference pages look up by name (entity/concept/procedure); Projects by open status.

**D. Everything else, and fallback ("did I ever say…")** — Hermes' own `session_search` over past conversations, backed by `~/.hermes/state.db` (FTS5). This is the one store in this list that **this package does not own**: it belongs to the platform, and this system's reliance on it is a documented assumption rather than a guarantee — see `decisions.md`, "The 2026-08-31 transcript cut". A thought that was never filed exists only there. Say which store was searched, since "not in your notes" and "not in your chat history" are different answers.

At this personal scale, keyword search over a small file set is sufficient — no index, no vector DB, no embedding search needed.

---

## 13. Confirmation behavior (early phase)

For the first few weeks, briefly confirm what got filed after each processed buffer. Surfaces misclassification early. Relax or turn off once trust in tagging quality is established — a toggle, not hardcoded.

---

## 14. Open items for the implementer

- Debounce duration (`WM_DEBOUNCE_SECONDS`, default 5s) is a starting guess — tune based on real usage.
- **Off-box backup of `WM_ROOT` — closed as not wanted (2026-08-31).** It was built (nightly commit + push to a private remote) and then removed once the directory no longer held anything worth the machinery. The vault, which does hold the notes, has always been backed up by its own remote — and `wm-watchdog.py` still checks nightly that it is actually pushed. See `decisions.md`.

---

## 15. Non-goals

- No multi-user support.
- No semantic/vector search — plain tag and keyword matching is enough at this scale.
- No server-side voice transcription — voice capture is handled entirely on-device before reaching Hermes. (Linux has no equivalent to Apple's native dictation, so voice capture on Linux is unresolved for now — typing is the fallback there.)

(Cross-platform capture — web UI, CLI, any future client — and no dependency on a single fixed chat/thread are *supported*, via the marker mechanism (Section 2), not excluded.)

---

## 16. Packaging for distribution (self-hosted, per-user)

Each person runs their own copy against their own Hermes instance — single-user per install. The package contains:

- **`SKILL.md`** — the distilled operational policy: what counts as capture input, routing rules, splitting/supersession heuristics, confirmation shapes. Terse and rule-based, deferring the vault's own rules to its `SCHEMA.md`, and ending with a pointer to this spec, the implementation source, and `logs/`.
- **The capture gate** — hooks into the base adapter's inbound seam of the user's own already-running Hermes instance.
- **`todoist.py`** — the Todoist client through which all reminders are created and queried.
- **`wmlib.py`** — env, timezone, logging and locking, shared by the above.
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
- **Not self-patchable at all:** anything in the deterministic code (capture gate, `wmlib.py`, `todoist.py`) — surfaced as a flagged issue for the user, not edited unsupervised.

**Why this is safe:** SKILL.md is kept under git version control, so every accepted refinement is diffable and revertible.

---

## 18. Versioning note

`main` is **v4.0.0**. The line so far:

- **v2.0.x** — tagged and frozen. Capture, debounce and a local reminder cron,
  with topic files as the storage model. Anyone still running it is unaffected
  by anything on `main`; its spec is preserved at those tags.
- **v3.0.0** — the second-brain rework: five types, domain tags, notes routed
  into an Obsidian vault, structured records in SQLite, and a two-layer
  reminder scheduler with Todoist mirroring a local store.
- **v4.0.0** — the simplification. The local reminder store, the SQLite
  records store, and the nightly consolidation job were removed; the raw log
  became a plain verbatim transcript; Todoist became the sole reminder
  mechanism. Roughly half the code, and no scheduled job that invokes the
  agent. The reasoning is in `decisions.md`; the deleted code is recoverable
  from the tag `v3.0.0-full`.
- **2026-08-31 — the watchdog cut.** `wm-backup-push.py` became
  `wm-watchdog.py` and lost three of its five jobs: the Todoist export, the
  `WM_ROOT` commit, and the push to a private remote. Those were the backup
  half, and after the transcript cut they protected a directory holding
  nothing the user minds losing. What survives is the health half — the
  quiet-failure check and the vault sync check — plus log pruning. The
  private remote is retired. The rename is deliberate: a file called
  `wm-backup-push.py` that backs nothing up misleads its next reader.
- **2026-08-31 — the transcript cut.** `rawlog.py` and the `raw/` log were
  removed. Every capture used to be appended there verbatim before anything
  was decided about it; now a capture goes straight to the vault or Todoist
  and this system keeps no copy of the message. Its two jobs — recovering a
  mis-filed or dropped capture, and answering "did I ever say X" about
  something never filed — are covered by Hermes' own session history
  (`~/.hermes/state.db`, FTS5, via `session_search`), which is why the
  component had no remaining use case here. That reliance is an external
  dependency this repository cannot verify; it is recorded as a monitored
  assumption in `decisions.md`. Existing `raw/` directories are left in
  place — the code went, not the words already written.

The version filename suffix is gone. `working-memory-system-spec-v3.md`
existed only while the v2 and v3 specs coexisted on one branch; v2's copy is
frozen at its tags, so the unversioned name is correct again — and a spec
whose filename encodes its version has to be renamed, with every cross
reference updated, on every bump.

Paired documents: `second-brain-schema.md` (the classification model, written
to stand alone) and `decisions.md` (why things are as they are, and what was
deliberately not built).
