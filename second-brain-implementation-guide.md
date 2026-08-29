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
  vault plus structured rows in SQLite. If you find code referencing a
  `topics/` directory, it is v2 residue, not a feature. *(The consolidation
  gate still had such a check in 2026-08-29; it was dead code and was
  removed.)*
- **"Collapse into a rolling summary" as the default consolidation behaviour.**
  In v3 this applies only to Reference-flavoured content. **Structured Records
  in SQLite are never collapsed** — the whole point of the records table is
  itemised history you can query, and summarising it destroys exactly what it
  exists for.
- **Backfill and migration (decided 2026-08-27).** v3 starts fresh. Existing
  wiki notes stay as they are with no tagging pass; v2 captures stay in the
  frozen v2 line. New writes are schema-compliant going forward. Rejected
  because a bulk retag is a large, risky, low-value operation on content the
  user can upgrade one note at a time if they ever care to.

### Correction (2026-08-29): reminders were NOT replaced by Todoist

An earlier version of this document said `reminders.json` and the cron script
were "replaced by Todoist's API." **That is not what was built, and it would
have been the wrong design.** Todoist is a *mirror*: it owns cross-device
visibility and notification, but the local store remains the durable record
and the firing source, so reminders keep working when Todoist is down,
degraded, rate-limited, or removed. See spec §9 for the two-layer contract.
The statement is corrected rather than deleted because it was load-bearing —
an agent reading it could reasonably have deleted the local reminder layer.

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

### Correction (2026-08-29): the live database is never replaced

An earlier version specified that the nightly snapshot "replaces `records.db`
in the working tree, so every committed DB file is a consistent point-in-time
copy." **That was implemented and it was a data-corruption bug.** The database
runs in WAL mode; swapping the main file while connections are open leaves a
stale `-wal` to be checkpointed against different content. Measured outcome:
`PRAGMA integrity_check` reporting a broken index and 201 committed rows lost.

The live `records.db` is now never copied, moved, or replaced. The committed
artifact is a separate `records-snapshot.db`, written by SQLite's backup API
while the live file is only ever read, and `records.db*` is gitignored. See
`tests/test_backup.py`, which exercises the checkpoint that made the old
behaviour fail — without it the bug usually stayed invisible, which is what
made it dangerous.

---

## Artifacts

Files stay where the user already keeps them (an iCloud paperwork folder),
untouched. A structured record stores a `file_ref` pointing at a **stable,
never-renamed location** — deliberately not a path the user might reorganise,
because a broken reference is worse than no reference. iCloud's own redundancy
covers the files; no separate backup action was warranted.

---

## Deferred indefinitely

Built only if a real need appears, not speculatively: confidence decay on
Reference/Project status, S3-backed artifact storage, nested domain tags, and
the daily digest / surfacing layer. The digest in particular is referenced by
`second-brain-schema.md` §11 but is **out of scope and not implemented** —
treat any mention of it as a design note, not a description of the system.

---

## The refinement loop's approval boundary

`meta/refinement-log.md` (in the data repo) is a decision record and an async
mailbox — not the channel where decisions get made. Numeric thresholds already
flagged tunable may be auto-applied. **Classification rules, routing rules,
storage-routing changes, edits to the canonical domain-tag list, and SKILL.md
edits are policy** and need the user's sign-off with a before/after diff.
Deterministic code is never self-edited outside that sanctioned flow.
