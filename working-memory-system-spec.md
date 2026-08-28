# Working Memory System — Implementation Spec

**Document map:** this file covers *capture, debounce, reminder-delivery, and crash-recovery* — the working-memory-system plumbing. It describes **v2.0.0**, tagged and frozen. Ongoing second-brain development happens in a separate **v3.0.0** branch, with its own evolving copy of this spec plus `second-brain-schema.md` (type/tag/status classification) and `second-brain-implementation-guide.md` (build/storage-routing/backup) — see the versioning note at the end of this document.

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
4. **Topic files** — derived, regenerable markdown files, one per recurring subject. Not authoritative; can be rebuilt from the raw log.
5. **Promotion/consolidation pass** — background job that decides when a tag graduates to its own topic file, and periodically condenses existing topic files.
6. **Reminder scheduler** — separate from note storage; fires a message at the right time, to the chat where the reminder was captured.
7. **Retrieval handler** — answers a question by searching tags and topic files, not by the user browsing anything.

---

## 2. Scope boundary: what counts as working-memory input

Working-memory input is defined by a deterministic **message marker**, not by guessing intent on every message the user sends Hermes — this avoids misfiring on ordinary conversation (e.g. "remind me to bring this up with the team" as a passing remark, not a capture instruction).

**The marker:** two forms, matched case-insensitively at message start, word-boundary rule (so "notebook" never matches "note"):
- **Primary:** `Hey memory` — explicit, natural to dictate.
- **Short alias:** `note` — fast to type or dictate, no punctuation.

A message starting with either marker is working-memory input, from any client. The marker is left in the text through the capture gate; the extraction pass strips it before writing, so it's never filed as part of an entry.

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
  topics/
    vitamin-d.md
    printer.md
    contacts.md
    ...
  reminders.json             # active scheduled reminders
  logs/
    2026-08.log               # one file per month — diagnostic, not memory (Section 11)
  meta/
    tag-index.json           # tag -> list of raw entry ids, + occurrence counts
    pending-buffer.json      # unflushed per-chat message buffer (Section 11)
    lanes.json               # reserved chats (Section 2)
    refinement-log.md        # curated patterns worth reviewing (Section 17)
```

- Raw log files are **never edited**, only appended to. Rotate monthly.
- Topic files ARE edited/rewritten by the consolidation pass — they're a cache, not history.
- `tag-index.json` lets the agent find "which raw entries mention X" without re-reading every raw file.
- `logs/` records operational events — distinct from `raw/`, which records *content*.
- **Everything durable lives under this single `/working-memory/` directory.** A full backup is just archiving this one folder.
- **Backup:** a local git repo over `/working-memory/`, committing on each write, gives point-in-time recovery. No off-box copy is specified at this layer by default — if losing the VPS entirely is a concern, add one (periodic push to a private remote, or a cron `tar`+`scp`/rclone).

---

## 5. Raw log entry format

```
## 2026-08-24T16:03:00+05:30 [id: 20260824-1603-01]
tags: health, vitamin-d
type: log+reminder
supersedes: 20260817-1610-01

Took vitamin D pill. Next one due in a week.

---
```

- **id** — deterministic, timestamp-based.
- **tags** — freeform, assigned by the extraction pass. No fixed vocabulary.
- **type** — `log` / `reminder` / `log+reminder`.
- **supersedes** — optional, raw entry id of a prior entry this one updates or replaces.

---

## 6. Capture flow

1. Message arrives on any connected client.
2. The capture gate checks: does it start with the marker, or is this chat in a reserved lane? If neither, it's ordinary conversation — untouched, falls through with no effect. If either, `auto_skill: working-memory` is set and the message is buffered.
3. **Buffer, don't process immediately.** A debounce timer (default ~5s for marker input, ~25s for a reserved lane, both tunable) resets on every new buffered message. Only when the timer elapses does the buffer flush: messages concatenated in order into one logical input. A lone `.` or `/done` flushes immediately.
4. Once flushed, the extraction pass (Section 7) runs against the flushed buffer, returning items classified `capture`, `question`, or `command`.
5. Question items go to the retrieval handler (Section 12); command items go to the consolidation pass (Section 8). Neither becomes a raw log entry.
6. Capture items are written as raw log entries (Section 5), tags/type/reminder already resolved by the same extraction call.
7. Any reminder directive updates `reminders.json` (Section 9).
8. A brief confirmation is sent back once processed ("logged 2 items: health/vitamin-d, printer"), especially useful early on (Section 13).

---

## 7. Extraction/tagging pass (LLM call, per flushed buffer)

**Input:** the flushed, concatenated text, plus context for tag reuse and supersession detection: the current tag list from `tag-index.json`, and — for any tag already present as a topic file — that file's current content.

This pass does routing (capture/question/command) and, for captures, tagging, in one call:

- Output is a **list** of items, each with `text`, `kind` (`capture`/`question`/`command`), and — for `capture` items — `tags` (1-4 freeform keywords, reusing an existing tag when it obviously matches), `type` (`log`/`reminder`/`log+reminder`), `reminder` (`{due_at, message}` if applicable), and `supersedes` (optional).
- `command` items are administrative/corrective instructions ("that's mis-filed," "merge these topics," "forget X") — handed to consolidation (Section 8), never producing a raw log entry.
- Splitting is conservative — one coherent thought touching two tags stays one entry; split only for genuinely unrelated content.
- One LLM call per flushed buffer, kept cheap and fast.

---

## 8. Promotion & consolidation policy

**Promotion:** occurrence counts per tag tracked in `tag-index.json`, updated synchronously on every raw-entry write. On a tag's 2nd or 3rd occurrence, the agent creates `/topics/<tag>.md`, backfilling prior raw entries. No user input required.

**Topic file format:**
```
---
tag: vitamin-d
last_updated: 2026-08-24
---

- Took vitamin D pill 2026-08-17, due again 2026-08-24 (reminder set).
- Weekly cadence, taking consistently since mid-August.
```

**Consolidation:** runs on a schedule or size threshold. Collapses recurring entries into a rolling summary, applies `supersedes` flags (newer replaces older), splits/merges topic files as judged useful, removes expired lines (Section 10). Fully reversible — topic files are derived from the raw log, so "that's mis-filed" or "split/merge these" just triggers a regeneration.

**Handling `command` items:** run immediately, not on the next scheduled pass. "Forget entirely" strikes the underlying raw entry's content too (the one justified exception to "raw log is never edited") — confirm with the user first, since it's the one destructive, hard-to-reverse action in the system. If a command is ambiguous about which entry/topic it means, ask rather than guess.

---

## 9. Reminder scheduler

Separate mechanism from note storage. Built on the VPS's existing cron and Hermes's existing messaging integration — no new scheduler daemon.

- `reminders.json`: flat list of `{id, due_at, message, raw_entry_id, status, origin: {platform, chat_id, thread_id?}}`.
- A cron entry runs a script that checks this file and, for any due entry, sends the message to the reminder's **origin** — the chat where it was captured. If that address is unreachable (e.g. a deleted chat), it falls back to a configured home channel. No registry, no retry loop — a stale origin degrades to one log line, not silent failure.
- On firing, mark `status: fired`; the consolidation pass updates the corresponding topic-file line.
- Recurring reminders should regenerate their next `due_at` automatically once the agent recognizes the pattern (nice-to-have, not required).

---

## 10. Cleanup & aging

1. **Raw log rotation** — files older than ~60-90 days move to `raw/archive/`, still grep-able.
2. **Expiry** — time-bound lines (resolved reminders, "due in a week" facts) drop from topic files once resolved; the raw entry itself is untouched.
3. **Supersession** — new fact replaces old rather than accumulating (Section 8).
4. **Size-triggered condensation** — a topic file past ~2-3KB gets a full condense-and-rewrite on its next write.
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

Questions are diverted here by the extraction pass — question items never produce a raw log entry.

**A. Reminder queries** ("what's due this week") — read `reminders.json` directly, filter by `status: pending`, present sorted soonest-first. Authoritative source for "what's still pending"; topic files aren't used here since a topic file's reminder line can be stale once fired.

**B. Everything else** — check `tag-index.json`/topic file names for an obvious match first; if none, search raw log tags more broadly, falling back to the current month's raw log if needed. Answers conversationally; the user doesn't need to know or guess the tag name.

At this personal scale, keyword/tag search over a small file set is sufficient — no vector DB or embedding search needed.

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
- **`reminder-check.sh`** (or equivalent) — the cron-called script scanning `reminders.json` and firing due reminders.
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
- **Not self-patchable at all:** anything in the deterministic code (capture gate, `reminder-check.sh`) — surfaced as a flagged issue for the user, not edited unsupervised.

**Why this is safe:** SKILL.md is kept under git version control, so every accepted refinement is diffable and revertible.

---

## 18. Versioning note

This spec describes **v2.0.0** — tagged and frozen at this state. Anyone running the current public release (including existing Reddit users) can rely on it not changing further; nothing below affects this document or the install it describes.

Ongoing development toward a second-brain-oriented data model and storage layer (routing captures by type to Todoist, SQLite, and an Obsidian vault instead of `reminders.json`/topic files) continues in a separate **v3.0.0** branch. That branch carries its own evolving copy of this spec, plus `second-brain-schema.md` (the type/tag/status classification model) and `second-brain-implementation-guide.md` (build order, storage routing, backup plan). v3.0.0 reuses and adapts the plumbing described in Sections 1-17 above rather than rebuilding it — but as a separate branch, not a runtime option on this codebase — so a v2.0.0 install is never affected by v3.0.0 changes, and vice versa.
