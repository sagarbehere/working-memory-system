# Working Memory System — Implementation Spec

## Purpose

A frictionless personal working-memory system. Thoughts get captured via
Telegram (typed, or dictated on-device using the OS's native
speech-to-text) with zero categorization effort at capture time. An
LLM-backed agent (Hermes) handles all classification, filing, reminders,
and cleanup in the background. The user never manually organizes
anything and never has to search by scrolling through logs — retrieval
is a conversational query to the same agent.

Voice capture is handled entirely on-device (VoiceInk on macOS, native
dictation on iOS/iPad — see Section 5 note) before the message ever
reaches Telegram, so Hermes only ever receives text. This also means the
user sees and can edit the dictated text before sending, satisfying the
"review before it's saved" requirement without any server-side
transcription step.

Core principle: **capture is dumb and instant; organization is the agent's
job, done after the fact, and is always reversible because the raw log is
the source of truth.**

**Version status:** Sections 1-17 describe **v1**, the deployed system
(Telegram-only). Section 18 is a **proposed v2 generalization** — a
marker-first design ("Hey memory" / "remember:") that makes working-memory
input deterministic on any client without lane registries or thread-id
dependencies — written for review and not yet implemented. Nothing in
Section 18 is live until it is reviewed and signed off.

---

## 1. Components

1. **Capture handler** — receives Telegram text messages (voice, if any,
   is dictated on-device beforehand — see Section 5) and buffers them
   per chat (Section 6) before anything else happens.
2. **Extraction/tagging pass** — LLM call that reads a flushed buffer,
   splits it into items, classifies each as capture or question, and
   for captures produces: freeform tags, entry type, and (if
   applicable) a reminder directive and/or supersession flag.
3. **Raw log** — append-only, immutable, timestamped. Ground truth.
4. **Topic files** — derived, regenerable markdown files, one per
   recurring subject. Not authoritative; can be rebuilt from the raw log.
5. **Promotion/consolidation pass** — background job that decides when a
   tag graduates to its own topic file, and periodically condenses
   existing topic files.
6. **Reminder scheduler** — separate from note storage; actually fires
   Telegram messages at the right time.
7. **Retrieval handler** — answers a Telegram question by searching tags
   and topic files, not by the user browsing anything.

*v2 note (Section 18): the capture handler and reminder scheduler become
platform-agnostic via a message marker, with no lane registry; components
1-7 otherwise keep their roles unchanged.*

---

## 2. Scope boundary: what counts as working-memory input

**The gap:** the user already uses Hermes for general conversation and
tasks over Telegram — not everything sent to Hermes is a memory dump,
a retrieval question, or a correction. Nothing earlier in this spec
distinguishes "this message is working-memory input" from "this is a
normal request to the agent." Running every message through the
buffer → extraction → routing pipeline (Section 6) would be wrong: it
would try to classify ordinary conversation as capture/question/
command, and could misfire (e.g. "remind me to bring this up with the
team" meant as a passing remark in a work discussion, not a reminder
to log).

**Resolution: a dedicated Telegram chat, not intent-guessing on every
message.** Use a second chat with the same Hermes bot (same
integration, same token — see Section 3 — just a distinct `chat_id`),
reserved solely for working-memory input. The debounce hook (Section
3) only watches messages arriving in that specific chat; every other
chat with Hermes is completely unaffected by this system and behaves
as normal conversation always has. This is deterministic — no
classification step has to guess whether a message is "for" the
memory system at all, which removes an entire failure mode rather than
trying to make that judgment reliable.

Trade-off: this asks the user to send memory-bound thoughts to a
specific chat rather than the one they're already talking to Hermes
in. Given the frictionless requirement (Purpose), this is a reasonable
cost — switching chats in Telegram is effectively one tap, and it's a
one-time habit, not per-message overhead, in exchange for removing
ambiguity entirely rather than managing it imperfectly.

**Known residual risk (not solved, just named):** the user can still
send a memory-bound thought to the wrong chat by habit, or vice versa.
No mechanism in this spec detects or corrects that — it's a UX risk
inherent to relying on the user to pick the right chat, not a flaw
fixed by better classification. If this proves to be a recurring
problem in practice, it's a candidate for the refinement loop (Section
17).

*v2 note (Section 18): the dedicated chat remains the frictionless path,
but the boundary becomes a message marker rather than a chat. A message
starting with "Hey memory" / "remember:" is WM input from any chat or
client; the dedicated chat survives only as an optional convenience where
the marker is implied. The marker is a syntactic rule, not classification
— determinism is preserved.*

---

## 3. Implementation approach (code vs. agent judgment)

This is not "just a prompt to Hermes," and it's not "a pile of Python
scripts with no agent involved" — it's a deliberate split between the
two, placed at whichever side handles each concern more reliably.

**Build as real code (deterministic; can't depend on an LLM choosing
to act) — but reuse what already exists rather than rebuilding it:**
- The debounce buffer (Section 6, step 2) — an exact timer that fires
  N seconds after the last message with no intervening one. This needs
  an actual timer/event loop, most naturally as a hook into Hermes's
  **existing** Telegram integration, filtered to the dedicated
  working-memory `chat_id` (Section 2) — not a new Telegram bot/client
  built in Python, and not watching every chat Hermes is in.
- The reminder scheduler (Section 9) — firing a message at a specific
  `due_at`. Use the **VPS's existing cron**, with a small script that
  checks `reminders.json` and sends via Hermes's existing Telegram
  channel — not a new scheduler process reimplementing what cron
  already does.
- Persisting `meta/pending-buffer.json` on every buffered message
  (Section 11), so a crash doesn't silently drop an in-progress
  thought.
- Basic file read/write/append/list operations over
  `/working-memory/`.

Concretely: no new Telegram client, no new bot token, no new
long-running scheduler daemon. The debounce hook and the reminder
script are the only new code, and both sit on top of infrastructure
that's already running.

**Leave to the agent's own judgment, guided by a written policy —
not hardcoded rules:**
- Splitting a flushed buffer into items, classifying each as
  capture/question/command, choosing tags, detecting supersession
  (Section 7) — this needs real language understanding; brittle
  if/else logic breaks on real phrasing.
- Consolidation/condensation wording, deciding when to split or merge
  topic files (Section 8) — a judgment call each time, not a fixed
  algorithm.
- Answering retrieval questions (Section 12) — inherently
  conversational.

**How to wire the two together:** give Hermes a skill document
(distilled from this spec) plus generic file read/write/list tools,
and let it decide tagging, filing, and consolidation itself each time
it's invoked — the same pattern behind this document's own authoring
process: a written policy plus tools, not a custom classifier. The
debounce hook (on top of Hermes's existing Telegram integration)
invokes Hermes with the flushed buffer and relevant context (tag list,
candidate topic file excerpts — see Section 7's Input note) when it's
time to process something; Hermes does the actual extraction/tagging/
writing via its file tools; the cron-fired reminder script handles
what Hermes shouldn't be trusted to act on purely by its own
initiative.

**Escalation path for edge cases:** SKILL.md is terse by design (see
Section 16) — it won't spell out every judgment call. Have SKILL.md
end with an explicit pointer to three things, each answering a
different kind of question: this spec's file path (**why** the system
works this way — design intent), the implementation source location
(**how** it currently works — actual code), and `logs/` (Section 4)
(**what actually happened** on a specific past run — e.g. "why didn't
my reminder fire yesterday" needs logs, not the spec or the code,
since neither records runtime history). Instruct Hermes to consult
whichever is relevant before improvising when a request doesn't
clearly fit the rules above. Without that pointer, Hermes has no
reason to go looking — it would just act on SKILL.md alone even when
the fuller design already covers the situation (e.g. this document's
rationale for why `command` items are handled the way they are, or why
reminders have their own retrieval path in Section 12).

One honest caveat: this is a lookup capability, not genuine
introspection — Hermes decides *whether* to escalate based on its own
in-the-moment judgment of "does this look covered by SKILL.md," which
is inherently imperfect. It can confidently misjudge a case as covered
when the fuller spec actually says something more nuanced. There's no
way to fully engineer that away; the pointer just makes the right
behavior available and instructed, not guaranteed.

**Net shape:** a debounce hook on Hermes's existing Telegram
integration, a small cron-fired script for reminders (both reusing
infrastructure that already runs, not new services), and one skill
document for the judgment layer Hermes runs each time it's invoked —
with a documented escalation path back to this spec, the source, and
the logs for anything the skill document doesn't cover.

*v2 note (Section 18): the hook's patch point moves from the
TelegramAdapter to the base adapter's inbound seam, where it detects the
WM marker and stamps the skill; the debounce is dropped and marked
messages process immediately. The reminder script delivers to the
capture's origin chat with a home-channel fallback. The code-vs-judgment
split itself is unchanged.*

---

## 4. Storage layout (VPS-only, no cross-device sync required)

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
  reminders.json             # active scheduled reminders (structured, not markdown)
  logs/
    2026-08.log               # one file per month — diagnostic, not memory (Section 11)
  meta/
    tag-index.json           # tag -> list of raw entry ids, + occurrence counts
    pending-buffer.json      # unflushed per-chat message buffer (Section 11) — durable so a restart doesn't lose it
    refinement-log.md        # curated patterns worth reviewing, distinct from logs/ (Section 17)
```

- Raw log files are **never edited**, only appended to. Rotate monthly.
- Topic files ARE edited/rewritten by the consolidation pass — they're a
  cache, not history.
- `tag-index.json` lets the agent find "which raw entries mention X"
  without re-reading every raw file.
- `logs/` records operational events (Section 11) — distinct from
  `raw/`, which records *content* (what you told the system). Logs
  record *behavior* (what the system did with it, and whether it
  worked). Shorter retention than raw log (Section 10) — logs are
  diagnostic, not memory to preserve indefinitely.
- **Everything durable lives under this single `/working-memory/`
  directory** — nothing the system depends on is stored elsewhere (no
  separate database, no state in the process's memory that isn't also
  mirrored here). A full backup is just archiving this one folder
  (`tar`/`zip`); there's no second location to remember.
- **Backup provisions:** the only mechanism specified is the git repo
  below (commit on each write, giving point-in-time recovery). Nothing
  else is speced — no offsite/off-VPS copy, no scheduled export, no
  pruning of git history itself (which will grow indefinitely
  alongside the repo). If losing the VPS entirely is a real concern,
  add an off-box copy (e.g. a periodic push of the git repo to a
  private remote, or a cron `tar` + `scp`/rclone to other storage) —
  flagged as an open item below rather than assumed.
- Recommend a local git repo over `/working-memory/` purely for backup /
  point-in-time recovery (commit on each write), even though there's no
  multi-device sync requirement. Cheap insurance against a bad
  consolidation pass.

---

## 5. Raw log entry format

Each entry is a single markdown block, appended to the current month's file:

```markdown
## 2026-08-24T16:03:00+05:30 [id: 20260824-1603-01]
tags: health, vitamin-d
type: log+reminder
supersedes: 20260817-1610-01

Took vitamin D pill. Next one due in a week.

---
```

Fields:
- **id** — deterministic, timestamp-based, used for dedup and cross-referencing from topic files/tag index.
- **tags** — freeform, assigned by the extraction pass. No fixed vocabulary.
- **type** — one of: `log` (plain fact), `reminder` (has a time component), `log+reminder` (both).
- **supersedes** — optional, raw entry id of a prior entry this one
  updates or replaces. Absent on most entries. Consumed by the
  consolidation pass (Section 8) when rewriting the topic file — the
  raw entry itself is left untouched either way, since raw log entries
  are never edited.

Note: whether a message was typed or dictated on-device is not tracked —
by the time it reaches Telegram it's plain text either way, so there's
no distinct "source" to record.

---

## 6. Capture flow

1. Text message arrives on Telegram (typed, or dictated on-device and
   reviewed by the user before sending — no distinction needed on
   Hermes's side).
2. **Buffer, don't process immediately.** The message is added to a
   per-chat pending buffer. A debounce timer (default ~20-30s,
   tunable) resets on every new message in that buffer. Only when the
   timer elapses with no further message does the buffer get flushed:
   its messages are concatenated in order (newline-joined) into a
   single logical input. This absorbs both a thought split across
   several messages and a quick typo-correction sent right after —
   neither should become its own entry.
   - **Manual flush override:** the user can send a lone `.` or `/done`
     to flush the buffer immediately, skipping the debounce wait —
     useful when they know they're finished, especially for retrieval
     questions where a fast reply matters.
   - If a debounce window is missed (user pauses longer than the
     timeout, then continues the same thought), the result is simply
     two raw log entries instead of one — acceptable, since the
     `supersedes` mechanism (Section 7/8) reconciles this at
     consolidation time regardless.
3. Once flushed, run the extraction pass (Section 7) against the
   flushed buffer. It returns a list of one or more items, each
   classified as a **capture** (something to remember), a **retrieval
   question** (something to answer now), or a **command** (a
   correction or administrative instruction) — this replaces any
   separate up-front routing check, since extraction already has to
   read the whole buffer anyway.
4. Any item classified as a question is handed to the retrieval handler
   (Section 12) — it never becomes a raw log entry. Any item classified
   as a command is handed to the consolidation pass (Section 8) to act
   on directly — it also never becomes a raw log entry.
5. Any item classified as a capture is written as its own raw log entry
   (Section 5), with its tags/type/reminder directive already resolved
   by the same extraction call — no placeholder-then-amend step needed,
   since the buffer's debounce delay already means capture isn't racing
   a tight latency budget the way a single incoming message would.
6. If any entry's extraction determines a reminder is needed,
   write/update `reminders.json` (Section 9) for that entry.
7. Optionally send a brief confirmation back to the user once the
   buffer has flushed and been processed, especially during the first
   weeks of use (see Section 13) — e.g. "logged 2 items: health/
   vitamin-d (reminder set for Aug 31), printer".

*v2 note (Section 18): the debounce is dropped for marked messages — they
process immediately. Everything else in this flow is unchanged.*

---

## 7. Extraction/tagging pass (LLM call, per flushed buffer)

**Input:** the flushed, concatenated text from Section 6, **plus
context the model needs to do tag reuse and supersession detection**:
the current tag list from `tag-index.json` (tag names only, not full
content), and — for any tag in the buffer text that already has a
topic file — that topic file's current content. Without this context
the model has no way to know an existing `printer` topic exists or
what it currently says, so tag reuse and `supersedes` would be
unreliable. A cheap pre-step (keyword match against the tag list) can
narrow which topic files to include rather than sending all of them.

This pass does three jobs at once: routing (is each part of the buffer
a capture, question, or command — see `kind` below), and, for captures,
tagging.

The buffer may contain more than one distinct thought (e.g. a note about
a taxi driver's number followed by an unrelated printer update, both
sent in the same debounce window). The extraction pass should **split**
in this case rather than force one set of tags onto mixed content:

- Output is a **list** of one or more items, each with:
  - `text`: the relevant slice of the input for this item.
  - `kind`: `capture`, `question`, or `command`.
  - For `capture` items only:
    - `tags`: 1-4 freeform keywords, lowercase, agent's own judgment —
      reuse an existing tag from the provided tag list when it
      obviously matches; only coin a new one when nothing fits.
    - `type`: `log` / `reminder` / `log+reminder`.
    - `reminder`: if applicable, `{due_at, message}`.
    - `supersedes`: optional raw entry id — set when this item clearly
      updates or replaces a fact visible in one of the provided topic
      file excerpts (e.g. "bought the Canon printer" replacing
      "thinking about buying a Canon printer").
  - `command` items are administrative/corrective instructions that
    don't fit capture or question — e.g. "that's mis-filed, it's about
    X not Y", "merge the printer and electronics topics", "forget what
    I said about the taxi driver". These are handed to the
    consolidation pass (Section 8) to act on directly, and never
    produce a raw log entry of their own (though they may cause
    existing topic files to be regenerated).
- The common case (one topic per buffer, all one kind) is just a list
  of length one — no special-casing needed elsewhere in the pipeline.
- Each `capture` item becomes its own raw log entry (Section 5), all
  sharing the same buffer/flush timestamp but with distinct ids. Each
  `question` item is passed to the retrieval handler (Section 12)
  instead. Each `command` item is passed to the consolidation pass.
- Splitting should be conservative: don't fragment a single coherent
  thought just because it touches two tags (e.g. "taxi driver Ravi,
  9876543210, was great for the airport run" is one entry tagged both
  `taxi` and `contacts`, not two). Split only when the content is
  genuinely about unrelated things.

This is a single LLM call per flushed buffer — keep it cheap and fast,
it doesn't need the most capable model. (The one exception is when
topic-file context is attached for supersession checking — that's
still one call, just with a larger prompt.)

---

## 8. Promotion & consolidation policy

**Promotion (raw → topic file):**
- Maintain occurrence counts per tag in `tag-index.json`, updated
  synchronously whenever a raw entry is written (same operation that
  appends the raw log entry — not a separate async job, so the index
  never drifts out of sync with the log).
- On a tag's 2nd or 3rd occurrence, the agent creates
  `/topics/<tag>.md`, backfilling all prior raw entries carrying that tag.
- No user input required. No fixed list of allowed topics.

**Topic file format** (kept simple and human-readable):
```markdown
---
tag: vitamin-d
last_updated: 2026-08-24
---

- Took vitamin D pill 2026-08-17, due again 2026-08-24 (reminder set).
- Weekly cadence, taking consistently since mid-August.
```

**Consolidation (agent-triggered, no user involvement):**
- Runs on a schedule (e.g. nightly) or when a topic file crosses a size
  threshold.
- Collapses recurring log entries into a rolling summary rather than
  keeping one line per occurrence (e.g. "vitamin D weekly, last taken
  Aug 24" instead of 52 lines/year).
- Applies `supersedes` flags from Section 7: newer fact overwrites older
  line rather than appending alongside it.
- Splits an overgrown topic file into more specific ones, or merges two
  overlapping ones, if the agent judges it useful. This is safe because
  the raw log is unaffected either way.
- Removes lines whose purpose has expired (see Section 10).

**Reversibility:** because topic files are fully derived from the raw
log + tag index, the user can at any time tell the agent "that's
mis-filed" or "split/merge these" — the agent regenerates, nothing is
lost.

**Handling `command` items (Section 7):** these run immediately rather
than waiting for the next scheduled consolidation pass, since a
correction is only useful if it takes effect right away:
- "Mis-filed" corrections: re-tag or move the referenced raw entry
  (found via the tag index or recent-entry context) and regenerate the
  affected topic file(s).
- Merge/split requests: regenerate the named topic files per the
  user's instruction.
- "Forget X": remove the fact from the relevant topic file. If the
  user means forget it entirely (not just correct it), also strike the
  underlying raw log entry's content rather than leaving it to
  resurface on a future rebuild — this is one of the few justified
  exceptions to "raw log is never edited." Confirm with the user before
  doing this, since it's the one destructive, hard-to-reverse action in
  the system.
- If a command item is ambiguous about which entry/topic it refers to,
  the agent should ask for clarification rather than guess — a wrong
  guess here silently corrupts a topic file.

---

## 9. Reminder scheduler

Separate mechanism from note storage — a topic file line saying "due in a
week" does nothing on its own. Built on infrastructure that already
exists: the VPS's cron and Hermes's existing Telegram integration —
no new scheduler daemon, no new bot.

- `reminders.json`: flat list of `{id, due_at, message, raw_entry_id, status}`.
- A **cron entry on the existing VPS crontab** (e.g. every few minutes)
  runs a small script that checks this file and, for any due entry,
  sends the message via Hermes's existing Telegram bot connection —
  into the dedicated working-memory chat (Section 2), not a separate
  bot/client and not the user's general conversation chat.
- On firing, mark `status: fired` and let the consolidation pass update
  the corresponding topic file line (e.g. "due" → "fired Aug 24, next due
  Aug 31" or removed, per the topic's own recurrence pattern).
- Recurring reminders (weekly vitamin D) should regenerate their next
  `due_at` automatically rather than requiring a fresh capture each time,
  once the agent recognizes the pattern — flag this as a nice-to-have,
  not required for v1.

*v2 note (Section 18): delivery goes to the reminder's origin chat (the
chat where it was captured), falling back to the home channel if that
address is unreachable — no registry, no endless retry loop. A due
reminder is never left pending solely because an address went stale.*

---

## 10. Cleanup & aging

Five distinct mechanisms, all agent-driven, no manual pruning:

1. **Raw log rotation** — raw files older than ~60-90 days move to a
   `raw/archive/` folder. Still fully grep-able, just outside the
   default working set for extraction/retrieval queries.
2. **Expiry** — entries/lines whose purpose is time-bound (reminders,
   "due in a week" style facts) are removed from topic files once
   resolved. The raw log entry itself is untouched (history), only the
   topic file's current-state line drops it.
3. **Supersession** — see Section 8; new fact replaces old rather than
   accumulating.
4. **Size-triggered condensation** — when a topic file passes a size
   threshold (e.g. ~2-3KB), the next write to it is a full
   condense-and-rewrite rather than a plain append.
5. **Log rotation** — `logs/` (Section 4, Section 11) is diagnostic,
   not memory: delete (not archive) log files older than ~30 days,
   shorter retention than the raw log, since the value of a log entry
   is answering "what just happened," not building a long-term record.

Goal: topic files stay small and current indefinitely; only the raw
archive grows, and it's cold storage the agent doesn't need to touch for
normal retrieval.

---

## 11. Error handling & crash recovery

Gaps worth closing explicitly, since this runs unattended on a VPS.

**Logging provision:** every event below writes one line to the
current month's file in `logs/` (Section 4) — timestamp, component
(debounce-hook / extraction-pass / reminder-cron / consolidation),
event, outcome. This is what makes "why didn't X happen" answerable
after the fact (Section 3's escalation path), rather than only being
inferable from current state. Keep entries terse and structured (e.g.
one JSON object per line) — this is a diagnostic trail, not prose.
Minimum events to log: every extraction pass invocation and its
result (success / retry / fallback), every reminder fire attempt and
its result (sent / retried / failed), every `command` item executed,
and every one of the failure scenarios below when it occurs. Routine
successful captures don't need their own log line — the raw log
entry already records that; logs are for the operational layer around
it, not a duplicate of content.

- **Buffer durability.** The per-chat message buffer (Section 6) should
  not be purely in-memory — if Hermes restarts between messages
  arriving and the debounce timer firing, an in-memory buffer is lost
  silently. Persist buffered-but-unflushed messages to
  `meta/pending-buffer.json` (Section 4) so a restart can recover and
  resume the debounce rather than dropping the thought.
- **Extraction pass failures.** If the LLM call in Section 7 fails or
  times out, don't drop the buffer. Retry once; if it still fails,
  fall back to writing the raw text as a single untagged entry (tag:
  `unfiled`) rather than losing the capture — an untagged entry is
  recoverable later, a lost message is not.
- **Reminder delivery during downtime.** If the VPS/Hermes process is
  down when a reminder's `due_at` passes, the next cron run (Section 9)
  should fire it as soon as the VPS is back up rather than silently
  skipping it — check for any `due_at` in the past with `status:
  pending` on each run, not just entries due since the last check.
- **Telegram delivery failures.** If a confirmation or reminder message
  fails to send (network blip, rate limit), retry with backoff; don't
  let a failed notification silently mark a reminder as fired.

---

## 12. Retrieval flow

Questions are diverted here by the extraction pass (Section 7), which
classifies each item in a flushed buffer (Section 6) as capture,
question, or command — question items never produce a raw log entry,
so asking things doesn't pollute what you're trying to remember.

**Two distinct retrieval paths**, since active reminders are structured
data with their own store, not something to fuzzy-search for:

**A. Reminder queries** ("show me all reminders", "what's due this
week", "any reminders for printer?") — read `reminders.json` (Section
8) directly, filter by `status: pending` (and by date range or keyword
match against `message` if the query narrows it), and present as a
formatted list — due date, message, sorted soonest-first. This is the
authoritative source for "what's still pending"; topic files are not
used for this, since a topic file's reminder line can be terse,
stale, or already removed once fired (Section 10), while
`reminders.json` is always current for anything still outstanding.

**B. Everything else** (facts, "what was I thinking of", people,
purchases, etc.):
1. User asks a question via Telegram ("what printer was I thinking of?").
2. Agent checks `tag-index.json` / topic file names for an obvious match
   first (fast path — most queries should resolve from a single topic
   file).
3. If no clear match, agent searches raw log tags more broadly (grep-
   equivalent across tag index), and falls back to the current month's
   raw log if needed.
4. Answers conversationally from what it finds. Does not require the
   user to know or guess the tag name.

At this personal scale, keyword/tag search over a small set of files is
sufficient — no vector DB or embedding search needed for v1.

---

## 13. Confirmation behavior (early phase)

For the first few weeks, the agent should briefly confirm what it filed
after each processed buffer ("logged under printer — noted you're
considering the Canon [model]"). This surfaces misclassification early,
while trust in the tagging is being established. This can be
relaxed/turned off later once the user is confident in the filing
quality — make it a toggle, not a hardcoded behavior.

---

## 14. Open items for the implementer

- LLM access for extraction/consolidation: assumed already available to
  Hermes (per user confirmation) — reuse whatever client/credentials
  Hermes already uses.
- Exact thresholds (promotion at 2nd vs 3rd occurrence, size limits for
  condensation, raw log rotation window, message buffer debounce
  duration) are starting guesses — tune based on actual usage volume
  once running. The debounce window in particular trades off latency
  (shorter = faster confirmations) against correctness (longer = fewer
  split entries from multi-message thoughts).
- **Off-box backup is unresolved.** The spec only covers on-VPS git
  history (Section 4); it does not specify anything for recovering
  from loss of the VPS itself. Decide whether that risk is worth
  covering (e.g. periodic git push to a private remote, or a cron job
  archiving `/working-memory/` off-box) before relying on this as the
  only copy of the data.
- **v2 (Section 18) open items** are listed in Section 18.10 and are not
  resolved by this section.

---

## 15. Non-goals (v1)

- No cross-device file sync (VPS-only storage, accessed via Telegram).
- No web UI — Telegram is the only interface for capture and retrieval
  in v1. The proposed v2 (Section 18) explicitly targets other clients
  (web UI via the api_server adapter, CLI, and any future platform) and
  is under review.
- No multi-user support.
- No semantic/vector search — plain tag and keyword matching is enough
  at this scale.
- No server-side voice transcription — voice capture is handled entirely
  on-device (dictation) before reaching Telegram; Hermes never receives
  audio. (Note: Linux has no equivalent to Apple's native dictation, so
  voice capture on Linux is unresolved for now — typing is the fallback
  there.)

---

## 16. Packaging for distribution (self-hosted, per-user)

If this is shared for others to run on their own VPS, each person runs
their own copy against their own Hermes instance — single-user per
install, matching the non-goals above (no shared/multi-tenant service,
no new auth layer needed). The package should contain:

- **`SKILL.md`** — the distilled operational policy (the judgment layer
  from Section 3): tag format, routing rules (capture/question/
  command), splitting and supersession heuristics, topic file format,
  consolidation behavior. Terse and rule-based, not this document's
  rationale-heavy prose — but it must end with a pointer to this
  spec's file path and the implementation source location, with an
  instruction to consult them before improvising on anything the
  terse rules don't clearly cover (see Section 3's escalation path
  note).
- **The debounce hook** — a small script/plugin that hooks into the
  *user's own already-running* Hermes Telegram integration to implement
  buffering (Section 6, step 2). Not a bundled Telegram client, since
  the target install already has one.
- **`reminder-check.sh`** (or equivalent) — the script a cron entry
  calls to scan `reminders.json` and fire due reminders through the
  user's existing Hermes Telegram channel. Ships as a script to be
  cron'd, not a daemon to be run.
- **`crontab.example`** — the exact line(s) to add to the user's own
  crontab for the reminder check, so setup is copy-paste rather than
  guesswork.
- **`setup.sh`** — creates the empty `/working-memory/` skeleton
  (`raw/`, `topics/`, `meta/`, `logs/`) and initializes the git repo
  for backup (Section 4).
- **`.env.example`** — working-memory path and the tunable thresholds
  flagged as open items (debounce seconds, promotion threshold,
  condensation size).
- **`README.md`** — install steps assuming Hermes + Telegram + cron
  already exist and work (this package adds to that, it doesn't stand
  one up): where to point the debounce hook, how to add the cron line,
  and — per the unresolved backup gap (Section 14) — an optional note
  on setting up an off-box git remote.
- **This spec**, kept alongside the package as design documentation for
  anyone extending it or wondering why a given choice was made.

What the package deliberately does **not** contain: a Telegram bot
token flow, a Telegram client library, or a scheduler daemon — those
would duplicate infrastructure every target install already has
running.

*v2 note (Section 18): packaging gains a marker-detecting capture gate on
the base-adapter seam (replacing the Telegram-specific debounce hook) and
an origin-address field in reminder records; the "no new client/daemon"
rule is unchanged.*

---

## 17. Self-improvement / refinement loop

Goal: let Hermes notice when SKILL.md or the underlying design has a
gap, without silently rewriting its own operating policy unsupervised
— self-tuning needs the same kind of care as the destructive-action
confirmation already required for "forget entirely" (Section 8), since
a bad self-edit to policy compounds by shaping every future decision,
not just one.

**`meta/refinement-log.md`** — append-only, and distinct from `logs/`
(Section 4/11): logs record raw operational events, this records
*curated patterns worth reviewing*, not every event. Write to it when:
- The user issues the same kind of `command` correction more than once
  for a similar situation (Section 8) — a repeated correction is a
  signal the underlying rule, not just one instance, is off.
- The extraction pass (Section 7) repeatedly falls back to `unfiled`
  for a recognizable category of input.
- A retrieval question (Section 12) misses something that was actually
  captured — noticed via the user re-asking differently or explicitly
  saying "I did tell you about X."
- During consolidation, Hermes itself notices a SKILL.md rule doesn't
  fit a case it just handled.

**Review cadence:** fold into the existing consolidation schedule
(Section 8) — periodically (e.g. weekly), review `refinement-log.md`
and draft concrete proposed changes.

**Approval boundary — this is the part that matters:**
- **Low-risk, self-tuning:** adjusting a numeric threshold already
  flagged as tunable (Section 14 — debounce seconds, promotion
  occurrence count, condensation size) based on observed friction can
  be auto-applied, with the change and reasoning logged.
- **Higher-risk, needs the user's sign-off first:** changes to
  classification rules, tag policy, splitting/supersession heuristics,
  or command-handling logic in SKILL.md. Present the proposed change
  (e.g. as a Telegram message with a before/after diff) and wait for
  confirmation before it takes effect — the same pattern as the
  destructive-command confirmation in Section 8, applied here because
  a policy change affects all future behavior, not a single entry.
- **Not self-patchable at all:** anything the refinement points to in
  the *deterministic code* (the debounce hook, `reminder-check.sh`) —
  surface it as a flagged issue for the user to look at rather than
  editing code unsupervised. Section 3 draws this same line between
  what's safe for the agent to decide and what needs a human, and it
  applies here too.

**Why this is safe to build on:** SKILL.md should be kept under git
version control (whether inside `/working-memory/` or the Hermes
plugin directory it lives in — either is fine, as long as it's
tracked), so every accepted refinement is diffable and revertible,
the same insurance the git recommendation in Section 4 already gives
the rest of the system.

---

## 18. Generalization: marker-first, platform-agnostic capture (PROPOSED — under review)

**Status: proposal only. Not implemented. v1 (Sections 1-17) remains the
description of the deployed system until this section is reviewed,
approved, and phased in (Section 18.9). The refinement loop (Section 17)
governs any further changes to this section once approved.**

### 18.1 Why marker-first

The first v2 draft (superseded) proposed a lane registry to decouple the
system from a single Telegram thread. On review, a much simpler design
does the same job: **define working-memory input by a deterministic
message marker rather than by location.** If every WM interaction starts
with a fixed phrase, the system needs no registry, no lane bookkeeping,
and no thread-id dependence at all:

- v1's three Telegram-shaped seams (Section 3's hook, Section 2's
  boundary, Section 9's delivery) collapse to one rule: *"a message that
  starts with the marker is WM input, anywhere."*
- The failure mode that motivated v2 — deleting a Telegram topic jams
  capture and reminders — disappears by construction: no lane, no
  dependency, nothing to break.
- Every client (Telegram, web UI via the api_server adapter, CLI, future
  platforms) works identically with zero per-platform configuration.

The cost is per-message overhead: each capture (and each retrieval
question) must carry the marker. Section 2's original trade-off chose
"one-tap chat switch" over "per-message overhead"; the marker flips
that. For a single-user personal system the marker is a habit, not a
burden, and the payoff is the elimination of an entire class of
configuration and failure.

### 18.2 The marker

- **Primary marker:** a message whose text starts with `Hey memory`
  (case-insensitive, allowing any following space or punctuation) is
  working-memory input.
- **Short alias:** `remember:` is accepted as an equivalent marker for
  fast typing.
- The marker covers all three routes: the extraction pass (Section 7)
  classifies the message as capture, question, or command as usual — the
  marker only says "this belongs to the WM system", it does not guess
  intent.
- The marker is stripped from the text before extraction, so it is never
  filed as part of an entry.
- The marker is deliberately a natural-language prefix, not a slash
  command: it works identically on every platform (Telegram, web UI,
  CLI, groups) without platform-specific command plumbing.

### 18.3 Capture gate (replaces the Telegram-only hook)

A minimal hook on the base adapter's inbound seam (the shared path every
platform's MessageEvent passes through) does three things, and nothing
else:

1. If the message starts with a marker → set `auto_skill:
   working-memory` (deterministic skill load) and strip the marker.
2. Process immediately — **no debounce**. A marked message is a
   deliberate capture; the 25s buffer existed to merge rapid-fire
   unmarked thoughts in a dedicated chat, which no longer exists. If the
   user sends a follow-up correction ("Hey memory, actually…"), the
   `supersedes` mechanism (Section 7/8) reconciles it at consolidation
   time.
3. Everything else falls through untouched (no-op default).

Blast-radius control: marker-gated; non-marker traffic is byte-identical
to stock behavior; verified per platform in Phase 1 (18.9).

*Alternative considered (open item 18.10.4): drop the hook entirely and
rely on the agent's automatic skill selection — the skill's description
begins "Use when the user writes 'Hey memory'…", so the model reliably
loads it. Deterministic-but-code (hook) vs simpler-but-probabilistic (no
hook); the hook is the default because determinism is a core principle
(Section 2/3), but this is the one place where simplicity could win on
review.*

### 18.4 Delivery: reminders and confirmations go to the origin

- Each raw entry already records its source (Section 5); extend the
  reminder record to carry the **origin chat** of the capture
  (`platform`, `chat_id`, `thread_id`/room when present).
- At fire time, `reminder-check` delivers to the origin; if that address
  is unreachable (e.g. the Telegram topic was deleted), it falls back to
  the home channel. No registry, no re-resolution: the fallback is one
  config value, and the "deleted topic" failure mode degrades to a
  one-line log entry instead of an endless retry loop.
- Confirmations ("✅ logged …") reply to the chat where the capture
  happened — natural with immediate processing.

### 18.5 Optional frictionless lane (demoted from v1's hard requirement)

The dedicated chat is no longer required, but it remains a nice-to-have:
one optional config entry designates a chat/topic where the marker is
**implied** (auto-stamped, exactly like today). This preserves v1's
zero-friction capture for the user's most-used client.

Critical property: the lane is convenience, not dependency. If that topic
is deleted, the system simply falls back to marker mode everywhere —
capture, retrieval, and reminders keep working (reminders go to
origin/home). No self-healing machinery is required for correctness;
re-binding the lane when the user recreates the topic is a one-line
config change (or, at most, a lazy re-adoption by name as a future
nice-to-have).

### 18.6 Web UI and CLI

- Web UI (Open WebUI via the api_server adapter) and CLI work
  identically: start a message with the marker in any chat. No
  api_server-specific chat-id knowledge is needed for capture.
- Reminder delivery to an api_server chat uses the origin address
  recorded at capture; if the web chat id format ever changes, the
  home-channel fallback covers it.

### 18.7 What does not change

Storage layout (Section 4), raw entry format (Section 5) plus the new
origin field on reminders, extraction/tagging policy (Section 7),
promotion/consolidation (Section 8), retrieval paths (Section 12),
confirmation behavior (Section 13), the refinement loop (Section 17),
single-user packaging (Section 16), and every filing rule in SKILL.md.
Only the scope guard (18.2), the capture gate (18.3), and reminder
delivery (18.4) change.

### 18.8 Trade-offs

- **Per-message marker overhead** — every capture and retrieval question
  must start with the marker (or come from the optional lane).
  Acceptable for a personal system; eliminates all per-platform config.
- **No debounce** — rapid multi-message thoughts via marker split into
  entries; `supersedes` reconciles at consolidation. Simpler code,
  slightly more consolidation work.
- **Hook still exists** — but it is a single small gate (marker match +
  `auto_skill` stamp), far simpler than the registry-driven version; the
  no-hook alternative (18.10.4) may remove it entirely.
- **Optional lane keeps a small config** — one entry, vs. v1's env
  vars; deleting it breaks nothing.

### 18.9 Phased rollout

Each phase is gated by review and a real-input test; v1 keeps working
throughout.

1. **Phase 1 — marker + gate.** Implement marker detection +
   `auto_skill` stamping + stripping on the base-adapter seam; verify
   Telegram behavior and that non-marker traffic is unaffected
   everywhere.
2. **Phase 2 — origin delivery.** Add origin to reminder records;
   `reminder-check` delivers to origin with home-channel fallback; test
   the delete-the-topic scenario (reminders still arrive).
3. **Phase 3 — lane demotion + web UI.** Convert the existing WM topic
   to the optional implied-marker lane; verify capture still works
   frictionlessly there, and marker capture works from Open WebUI/CLI.

### 18.10 Open items (added by this section)

1. **Marker syntax final** — `Hey memory` primary + `remember:` alias
   (case-insensitive prefix match). Confirm the alias; consider one more
   (e.g. `wm:`) only if the user wants it.
2. **Marker stripping** — exact rule (strip the marker token(s) plus
   following whitespace/punctuation before extraction).
3. **Debounce decision** — v2 default is immediate processing; keep the
   option to restore a short debounce (e.g. 5s) for marked messages if
   rapid follow-ups prove common.
4. **Hook vs no-hook** — decide whether to keep the deterministic
   capture gate or rely on skill auto-selection (18.3 alternative).
5. **Reminder origin schema** — extend the reminder record with origin
   {platform, chat_id, thread_id?}; confirm the home-channel fallback
   value.
6. **Optional lane config** — final shape (one auto-capture entry:
   platform + chat_id + thread_id), and whether to adopt-by-name on
   recreation.
