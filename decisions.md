# Second Brain — Decisions & Rejected Alternatives (v3.0.0)

**What this document is for.** Everything here is knowledge you cannot recover
by reading the code: choices that were made deliberately, alternatives that
were considered and rejected, and things that are *absent on purpose*. An
absence leaves no trace in a codebase — that is the whole reason this file
still exists.

**What it is NOT.** Not a description of how the system works — that drifts,
and the code is the truth. Data model: `second-brain-schema.md`. Plumbing:
`working-memory-system-spec-v3.md`. Orientation for coding agents:
`CLAUDE.md`. This was a full implementation guide until 2026-08-29; the
descriptive half was cut because it had started contradicting the code, as
the two corrections below show.

---

## Left behind from v2.0.0 — deliberately absent

- **`topics/<tag>.md` flat files.** Replaced by typed notes in the Obsidian
  vault. If you find code referencing a `topics/` directory, it is v2 residue,
  not a feature.
- **"Collapse into a rolling summary" as the default consolidation behaviour.**
  It applies only to reference-flavoured content. **Series notes — repeated
  measurements like blood pressure or headaches — are never collapsed**: the
  itemised history is the whole point, and summarising destroys it.
- **Backfill and migration (decided 2026-08-27).** v3 starts fresh. Existing
  wiki notes stay as they are with no tagging pass; v2 captures stay in the
  frozen v2 line. New writes are schema-compliant going forward. Rejected
  because a bulk retag is a large, risky, low-value operation on content the
  user can upgrade one note at a time if they ever care to.

### The reminder layer, in three acts (read this before "fixing" anything)

This document has said opposite things about reminders at different times, and
a future reader deserves the whole arc rather than whichever sentence survived:

1. **v3 planning:** "`reminders.json` and the cron script are replaced by
   Todoist's API." That was *not* what got built.
2. **What was built:** a two-layer design — a local store as the durable
   firing source, with Todoist as a mirror — so the system would work without
   a Todoist account. The plan above was corrected to match.
3. **2026-08-29 cut:** the local layer was deleted and Todoist became the sole
   mechanism, which is where act 1 pointed all along. See the section below
   for why, and spec §9 for the current contract.

The lesson is not "act 1 was right." Act 2 was a reasonable design for the
audience it imagined; the mistake was imagining that audience at all.

---

## The 2026-08-29 simplification cut

Three components were deleted deliberately. All are recoverable —
`git checkout v3.0.0-full -- <path>` — so this section records *why*, which
the tag does not.

- **The local reminder store** (`reminders.json`, `reminder-check.py`, the
  five-minute cron). It existed so the system would work for someone without
  a Todoist account — a deployment the author does not run, built for
  hypothetical users from a GitHub star count rather than a request. It cost
  ~18% of the codebase and produced nearly all of the concurrency: two
  processes writing one file, a lost-update race that silently erased
  captures, a wrong-origin bug that would have retried into a nonexistent chat
  forever, and a polling loop making ~288 API calls a day. Todoist's own
  reliability comfortably exceeds that. **Lesson: polish is worth buying
  freely; generality is worth buying only against a real request.**
- **The SQLite records store** (`records.py`, `records.db`). Motivated by real
  retrieval failures, but never used: zero rows after the system had been
  running, while the same data was kept happily in markdown and a phone app.
  Structured storage wins when data outgrows a context window; a single
  person's health log never will, so **the LLM is the query engine** and a
  series note in the vault is the better answer.
- **The raw log's structure** (ids, typed fields, the search index). Kept as a
  verbatim transcript, because the one genuinely unreliable component here is
  the LLM's judgment, and the transcript is the only thing upstream of it. The
  *machinery* went because nothing linked back to an entry any more.

What the cut deliberately did NOT touch: the capture hook, the transcript
itself, the vault routing, and the watchdogs. Those were either expensive to
learn or cheap and load-bearing.

---

## Storage routing

Canonical rules: `second-brain-schema.md` §10. Only the decisions here:

- **The vault is the user's existing private git repo**, viewed through
  Obsidian and Working Copy. Backup was therefore already solved; what needed
  changing was the *skill* (frontmatter discipline), not the storage.
- **Writes must commit AND push.** A local-only commit in a repo whose purpose
  is syncing across devices is not backed up. This is a recurring failure mode,
  which is why the nightly watchdog checks for unpushed vault commits.
- **Todoist needs no backup infrastructure** beyond a nearly-free nightly
  export, being reasonably trusted hosted infrastructure.
- **The canonical domain-tag list lives in the vault (`_meta/tags.md`), not in
  `/working-memory/meta/`.** It is already git-backed and synced to every
  device there, and it is content the user reads and edits — one fewer thing
  for the backup push to cover. This decision matters because the vocabulary
  and the raw-log index were once considered for the same home, and the names
  still invite confusion: `_meta/tags.md` is the **vocabulary** (which tags
  may be used); `meta/tag-index.json` was an **inverted index** (which raw
  entries mention a tag). Different jobs, different files, different repos.

---

## Backup design

Four systems exist; only one needed new infrastructure. The reasoning matters
more than the mechanism:

- **The public package repo must never contain personal data, even in
  history.** This is a security boundary, not a fragmentation problem to
  tidy up. Do not "simplify" by merging the data repo into it.
- **The vault is a live sync target, not a cold-backup destination.** Folding
  unrelated snapshots into it would bloat a repo that syncs to phones.
- **A git repo on the VPS's own disk is not a backup.** That was the actual
  gap, and it closes with one private remote plus one nightly push.
- **Git history IS the point-in-time store**, so no snapshot pruning and no
  continuous-replication tooling (Litestream was considered and rejected).
  At personal scale — a database measured in tens of kilobytes — unbounded
  history is a non-issue for years. Revisit only if volume actually becomes a
  problem, not preemptively.

### Historical: the live database was never to be replaced

*(The SQLite store was removed later the same day — see the cut above. This is
kept because the mistake it describes generalises to any file a running
process holds open.)*

An earlier version specified that the nightly snapshot "replaces `records.db`
in the working tree, so every committed DB file is a consistent point-in-time
copy." **That was implemented and it was a data-corruption bug.** The database
runs in WAL mode; swapping the main file while connections are open leaves a
stale `-wal` to be checkpointed against different content. Measured outcome:
`PRAGMA integrity_check` reporting a broken index and 201 committed rows lost.

The fix was to commit a separate snapshot written by SQLite's backup API while
the live file was only ever read. **The general rule survives the deletion:
never swap a file out from under a process that has it open, and when a bug
only manifests after a later trigger (here, a WAL checkpoint), a test that
skips that trigger will pass while the bug is still there.**

---

## Artifacts

Files stay where the user already keeps them (an iCloud paperwork folder),
untouched. A structured record stores a `file_ref` pointing at a **stable,
never-renamed location** — deliberately not a path the user might reorganise,
because a broken reference is worse than no reference. iCloud's own redundancy
covers the files; no separate backup action was warranted.

---

## Considered and not built

- **A daily digest / surfacing layer.** Specified in early drafts as a
  scheduled job that would push "what's due, what's stale, what's untouched"
  at the user each morning. Never implemented, and after the 2026-08-29 cut it
  is actively contrary to the design: nothing invokes the agent on a schedule.
  The failure mode of such a layer is well known — a daily message that is
  usually not worth reading gets ignored, and then the one that mattered is
  ignored too. Todoist already notifies for the only things with a real
  deadline. *(Removed from the schema, where it had been sitting as though it
  were part of the data model.)*
- **S3-backed artifact storage.** Considered so the agent could read file
  *contents* — OCR a PDF, search inside a scan. Rejected as speculative: it
  solves a problem nobody has had yet, and the current design (a Record
  pointing at wherever the file already lives) costs nothing. Revisit only
  when there is a specific thing you tried to ask and could not.

## Deferred indefinitely

Built only if a real need appears, not speculatively: confidence decay on
Reference/Project status, and nested domain tags.

---

## The refinement loop's approval boundary

`meta/refinement-log.md` (in the data repo) is a decision record and an async
mailbox — not the channel where decisions get made. Numeric thresholds already
flagged tunable may be auto-applied. **Classification rules, routing rules,
storage-routing changes, edits to the canonical domain-tag list, and SKILL.md
edits are policy** and need the user's sign-off with a before/after diff.
Deterministic code is never self-edited outside that sanctioned flow.
