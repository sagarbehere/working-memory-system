---
name: working-memory
description: "Use for the working-memory system: when the user sends capture, retrieval-question, or filing-correction messages in a reserved working-memory chat or starting with the markers 'Hey memory'/'note', and for the nightly consolidation pass. Implements the working-memory system: split/tag buffered text, file captures to the raw log, promote topics, manage reminders, answer retrieval questions from stored notes."
version: 2.0.0
author: Sagar Behere
license: MIT
metadata:
  hermes:
    tags: [memory, capture, retrieval, reminders, telegram]
    related_skills: [hermes-agent]
---

# Working Memory

Personal working-memory system: the user captures thoughts via Telegram in a **dedicated chat** (spec Section 2); you do all filing, retrieval, reminders, and cleanup. Raw log is ground truth; topic files are derived caches; everything is reversible.

**Scope guard (v2, spec Section 18):** working-memory input is any message that (a) arrives in a **reserved lane** — a chat previously reserved in-band with "reserve this chat for working memory", recorded in `meta/lanes.json`, or the legacy env-declared lane (`WM_TELEGRAM_CHAT_ID`/`THREAD_ID` in `~/.hermes/working-memory.env`) — or (b) starts with a **marker**: `Hey memory` or `note` (case-insensitive, word boundary, stripped before you see it). Everything else is NOT working-memory input — answer as a normal assistant and file nothing.

**Lane identity = chat + thread, not session.** The lane is the topic identified by `WM_TELEGRAM_CHAT_ID` + `WM_TELEGRAM_THREAD_ID` (the "Working Memory" topic inside the bot DM). It is independent of which Hermes session is currently bound to that topic: `/new` (fresh session, same topic) and compression-driven session rotation (old session sealed `compression`, child session continues) do NOT disconnect the lane — the debounce hook and the `dm_topics` skill binding key on chat+thread only, and all durable data lives in `$WM_ROOT` files, not in the conversation context. When explaining WM to the user: "lane" is a working-memory-system term, not Hermes vocabulary (topic = Telegram layer, session = Hermes layer). Full session model: see the `hermes-session-lifecycle` skill.

Read `~/.hermes/working-memory.env` before your first write — it defines `WM_ROOT` (storage root) and the tunable thresholds (`WM_PROMOTE_AFTER`, `WM_CONDENSE_SIZE`, `WM_RAW_RETENTION_DAYS`, `WM_CONFIRM`).

Layout under `$WM_ROOT` (a git repo — commit after every write batch):

- `raw/YYYY-MM.md` — append-only raw entries; never edit; rotate files older than `WM_RAW_RETENTION_DAYS` into `raw/archive/` during consolidation.
- `topics/<tag>.md` — derived, regenerable.
- `meta/tag-index.json` — tag → list of raw entry ids + occurrence counts.
- `meta/pending-buffer.json` — unflushed capture buffer, managed by the debounce hook; read it if useful, don't hand-edit.
- `meta/refinement-log.md` — curated patterns worth reviewing (spec Section 17); append, don't rewrite.
- `logs/YYYY-MM.log` — operational trail (spec Section 11): JSON lines about what the system DID. Diagnostic, not memory — delete files older than ~30 days during consolidation.
- `reminders.json` — pending reminders (structured, not markdown).

## Every incoming message: route it

0. **Reservation phrases** — if the message is "reserve this chat" / "reserve this chat for working memory" or "unreserve this chat" (the capture-gate hook has already updated `meta/lanes.json`): reply with a one-line confirmation ("✅ Reserved — this chat is now a working-memory lane; no markers needed. Undo with 'unreserve this chat'." or the unreserve equivalent) and file NOTHING. If the phrase arrived but `meta/lanes.json` does not reflect it (edge case), follow spec Section 18.3 and record it yourself, then confirm.
1. Split the text into items — **conservative**: one coherent thought = one item even if it touches multiple tags; split only genuinely unrelated content.
2. Classify each item: `capture` (remember), `question` (retrieve now), `command` (filing/admin instruction).
3. Capture → **Capture**. Question → **Retrieve**. Command → **Command**.
4. Ordinary chit-chat unrelated to memory → answer normally, file nothing.

## Capture

Write one raw entry per capture item:

- **Dedup first**: before writing, check the current month's `raw/YYYY-MM.md` (and peek at `meta/pending-buffer.json`) for a verbatim or near-identical recent entry. If the fact is already stored (re-send, duplicate delivery, user repeating themselves), do NOT write a second entry — just confirm to the user it's already on file, and offer to log a follow-up update if they've moved past it. A duplicate capture is log pollution, not safety.

```
## 2026-08-24T16:03:00+05:30 [id: 20260824-1603-01]
tags: health, vitamin-d
type: log+reminder
supersedes: 20260817-1610-01

<text>
---
```

- id = deterministic timestamp (`YYYYMMDD-HHMM-SS`); suffix `-01`, `-02`… when one flush yields several entries.
- tags: 1–4 freeform keywords, lowercase. **REUSE** existing tags from `meta/tag-index.json` when they obviously match; coin a new tag only when nothing fits.
- type: `log` (plain fact) | `reminder` (time component) | `log+reminder` (both).
- `supersedes`: set only when this item clearly replaces a fact visible in an existing topic file or a recent raw entry.
- Update `meta/tag-index.json` in the **same operation** as the append (append entry id, bump count) — never as a separate async step.
- **Promotion**: when a tag's count reaches `WM_PROMOTE_AFTER` (default 2), create `topics/<tag>.md` backfilled from every raw entry carrying that tag. A deduplicated re-send does NOT increment a tag's occurrence count — promotion counts only distinct captures (approved policy, 2026-08-24).
- **Size trigger**: if the topic file you'd append to exceeds `WM_CONDENSE_SIZE` (default 2500 bytes), rewrite it condensed instead of appending (see **Consolidation**).
- **Reminders** (type `reminder`/`log+reminder`): add `{"id", "due_at", "message", "raw_entry_id", "status": "pending"}` to `reminders.json`. `due_at` = ISO-8601 with local offset (e.g. `2026-08-31T10:00:00+05:30`); `message` = the text to send at that time — delivered verbatim at fire time, so phrase it relative to fire time (e.g. "Comet Service pickup guy will arrive today."), not capture time ("…tomorrow at 8 am"). **`origin`** (optional, spec Section 18.4) = `{"platform": "<source platform>", "chat_id": "<chat>", "thread_id": "<thread or ''>"}` — record it when the capture came from a non-lane chat (e.g. a marker message from the web UI or another Telegram topic) so the reminder delivers back to the chat where it was captured; omit for reserved-lane captures (the legacy lane is the default target). If unsure about the schema, read `reminder-check.py` — it fires exactly when `status == "pending"` and `due_at <= now`, delivering to origin with a home-channel fallback.
- `git add -A && git commit -m "capture: <short summary>"` after the write batch.
- **Confirm** with ONE short line (skip entirely when `WM_CONFIRM=0`). Never
  include ids, tags, file paths, commit hashes, or internal operational
  details — the user doesn't want the machinery. Examples:
  `✅ logged: printer` · `✅ logged 2: printer update, comet-service reminder (Tue 8:00 AM)` ·
  `✅ reminder set: dentist Tue 8:00 AM`. For retrieval questions, just answer.

## Retrieve

- **Reminder questions** ("what's due this week", "show my reminders", "any reminders for printer?") → read `reminders.json`, filter `status == pending` (by date range / keyword against `message` if the query narrows), sort soonest-first. Never answer from topic files — a topic line can be terse, stale, or already removed once fired.
- **Fact questions** → 1) match `tag-index.json` / topic file names (fast path — most queries resolve from a single topic file), 2) search tag-index more broadly, 3) fall back to the current month's raw log, then `raw/archive/`. Answer conversationally; never make the user guess a tag name.
- **Proposal queue** — if the user asks "any proposals awaiting approval?" / "any policy proposals?" (or similar), read `meta/refinement-log.md` and present ONLY entries marked `STATUS: PENDING APPROVAL` (drafted policy changes, flagged code gaps, or open questions needing a decision), each with its before/after diff or the specific question. Collect the user's decision; only after approval may drafted policy changes be applied (per the Refinement loop section). Entries marked `STATUS: INFO` are informational and are NOT presented as pending.

## Command (run immediately — don't wait for consolidation)

- Mis-filed → re-tag / re-file the referenced raw entry, regenerate the affected topic file(s).
- Merge/split topics → regenerate the named topic files per instruction.
- Forget X → **confirm with the user first** (the one destructive action), then strike the fact from the topic file AND the raw entry's content.
- Ambiguous target → ask for clarification, never guess.

## Topic file format

```
---
tag: vitamin-d
last_updated: 2026-08-24
---

- Took vitamin D pill 2026-08-17, due again 2026-08-24 (reminder set).
- Weekly cadence, consistent since mid-August.
```

## Logging (operational trail — spec Section 11)

After each extraction pass, append one JSON line to `logs/YYYY-MM.log`:

- extraction: `{"ts", "component": "extraction-pass", "event": "extraction", "outcome": "success"|"retry"|"unfiled-fallback", "items": <count>, "captures": <n>, "questions": <n>, "commands": <n>}`
- command executed: `{"ts", "component": "extraction-pass", "event": "command", "outcome": "executed", "description": "<what you did>"}`
- consolidation run: `{"ts", "component": "consolidation", "event": "consolidation", "outcome": "done", "topics": <n>, "rotated_raw": <n>, "deleted_logs": <n>}`

Routine successful captures do NOT need their own log line — the raw entry already records the content; logs record the operational layer. Log failures loudly (retry → fallback → any error). Keep lines terse JSON, one per line, append-only.

## Consolidation (nightly job + size triggers)

- Collapse recurring log entries into a rolling summary ("vitamin D weekly, last taken Aug 24", not one line per occurrence).
- Apply `supersedes`: newer fact replaces the older line rather than appending alongside it.
- Split an overgrown topic into more specific ones, or merge overlapping ones, when useful — safe, because the raw log is unaffected.
- Drop lines whose purpose has expired (resolved reminders, "due in a week" style facts); reconcile fired reminders into topic lines ("fired Aug 24, next due Aug 31" or remove).
- Rotate raw files older than `WM_RAW_RETENTION_DAYS` into `raw/archive/`; **delete** `logs/` files older than ~30 days (diagnostic, not memory).
- Review `meta/refinement-log.md` and apply the refinement policy below.
- Rewrite topic files; bump `last_updated`; raw log untouched; commit.

## Refinement loop (spec Section 17)

Append a dated entry to `meta/refinement-log.md` (never rewrite it) when you notice:
- the user issues the same kind of `command` correction more than once for similar situations — a repeated correction signals the rule, not one instance, is off;
- extraction repeatedly falls back to `unfiled` for a recognizable category of input;
- a retrieval question misses something that was actually captured (user re-asks differently, or says "I did tell you about X");
- during consolidation, a SKILL.md rule doesn't fit a case you just handled.

Every entry carries an explicit status line: `STATUS: PENDING APPROVAL` (needs a user decision) or `STATUS: INFO` (informational/resolved). Only PENDING APPROVAL entries are presented when the user asks about the proposal queue.

Review the log on each consolidation pass (weekly-ish). **Approval boundary:**
- **Low-risk auto-tune:** adjusting a numeric threshold already flagged as tunable (`WM_DEBOUNCE_SECONDS`, `WM_PROMOTE_AFTER`, `WM_CONDENSE_SIZE`) based on observed friction — apply it, update `~/.hermes/working-memory.env`, and log the change + reasoning.
- **Needs sign-off:** changes to classification rules, tag policy, splitting/supersession heuristics, or command-handling logic in SKILL.md — present the proposed change as a before/after diff via Telegram and WAIT for confirmation before it takes effect. **You must NEVER write to SKILL.md yourself, even for small clarifications** — that is always the user's call, no matter how obviously good the edit seems.
- **Never self-patch:** anything in the deterministic code (the debounce hook, `reminder-check.py`) — surface it as a flagged issue to the user; do not edit code unsupervised.

## Failure handling

- Extraction fails → retry once; still failing → write the raw text as a single untagged entry (tag: `unfiled`) — **never drop a capture**. Log the fallback.
- Reminder delivery is `reminder-check.py`'s job (retries, marks fired, logs each attempt). If a topic line says "reminder set" but `reminders.json` has no pending entry, reconcile at consolidation.
- If a Telegram reply fails to send, retry once; don't silently claim a capture was filed.

## Escalation

If a request doesn't clearly fit the rules above, consult before improvising:
- **Why** the system works this way → the full spec at `/home/hermes/working-memory-system/working-memory-system-spec.md` (e.g. why `command` items never become raw entries, why reminders have their own retrieval path, why markers + reserved lanes scope working-memory input — Section 18).
- **v2 is IMPLEMENTED** (spec Section 18) → markers "Hey memory"/"note", in-chat reservation ("reserve this chat for working memory" → `meta/lanes.json`), origin-based reminder delivery. Honor them in live capture. Do NOT re-propose the rejected lane-registry design.
- **How** it currently works → the implementation source in that package (the debounce hook `hooks/working-memory-debounce/handler.py`, `reminder-check.py`).
- **What actually happened** on a past run → `$WM_ROOT/logs/YYYY-MM.log` (e.g. "why didn't my reminder fire yesterday" needs logs, not the spec or code — neither records runtime history).

**Keeping the system updated:** when asked anything about how the working-memory system works (including odd or hypothetical questions), consult the spec, this skill, and the source BEFORE answering from memory. When you notice a gap, a repeated correction, or a case the rules don't fit, append a dated entry to `meta/refinement-log.md` (Refinement loop section above) — never rewrite it. Policy changes to SKILL.md always need the user's sign-off; changes to the deterministic code are flagged for the user, never self-made.

SKILL.md is version-controlled in the package repo (`/home/hermes/working-memory-system`, git) — every accepted refinement is diffable and revertible.
