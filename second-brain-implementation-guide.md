# Second Brain — Implementation Guide (for Hermes)

**Document map:** this file covers *build, storage-routing, and backup* — what gets written where, and how it's kept safe, for **v3.0.0**. For type/tag/status classification, see `second-brain-schema.md`. For capture, debounce, reminders, and crash-recovery plumbing (shared with v2.0.0), see `working-memory-system-spec-v3.md`.

**Status:** implementation spec, ready to build against, in the **v3.0.0 branch**.
**Depends on:** `second-brain-schema.md` (the type/tag/status data model — read this first) and `working-memory-system-spec-v3.md` (the capture plumbing this guide reuses and adapts).

## 0. What this guide is

v2.0.0 (tagged, frozen — see `working-memory-system-spec.md` at that tag) already solved capture reliably; existing Reddit users keep running it unaffected by anything in this guide. v3.0.0 is a **separate branch**, with its own copy of the spec (`working-memory-system-spec-v3.md`), that reuses and adapts v2.0.0's plumbing rather than rebuilding it. This guide tells you precisely what to copy over unmodified, what to leave behind, and what's genuinely new for v3.0.0.

## 1. Reuse as-is (copy over, adapt only where noted)

These components carry over from v2.0.0 into the v3.0.0 branch with little to no change:

- **Debounce buffering** (Section 6) — message buffering, timer, manual flush override. Unchanged.
- **Buffer durability** (`meta/pending-buffer.json`, Section 11) — unchanged.
- **Extraction pass architecture** (Section 7) — the capture/question/command split and the "split multiple thoughts, don't fragment one" logic carry over unchanged. The mechanism (one LLM call per flushed buffer, context-aware tag reuse) is reused as-is; the output schema for capture items is extended — see §3 below.
- **Command handling** (Section 8's "mis-filed"/"forget"/"merge-split" logic) — reuse the pattern, extended so a correction can touch an Obsidian note, a SQLite row, or a Todoist task, depending on where the original item was routed, instead of just a topic file. Same confirm-before-destructive-action rule for "forget entirely."
- **Crash recovery & error handling** (Section 11) — logging format and retry-then-fallback-to-unfiled carry over unchanged. The reminder-delivery-during-downtime case now applies to Todoist sync instead of `reminders.json`.
- **Refinement loop** (Section 17) — reuse the mechanism and its approval boundary (auto-tune numeric thresholds; require sign-off for policy changes) unchanged, additionally treating type-classification rules (§3 below), storage-routing rules (§4), and edits to the canonical domain-tag list as policy changes requiring sign-off.
- **Packaging pattern** (Section 16) — SKILL.md + escalation pointer (spec = why, code = how, logs = what happened) is reused directly; add this guide and `second-brain-schema.md` as additional required-reading pointers in v3.0.0's own SKILL.md.

## 2. Leave behind — not carried into v3.0.0

- **`reminders.json` + cron reminder script** (Section 9 of the spec) — replaced by Todoist's API, which owns due dates, recurrence, and — critically — completion state, which the homegrown version never had. This component simply isn't part of the v3.0.0 branch; it remains exactly as-is in v2.0.0.
- **`topics/<tag>.md` flat files** (Section 4/8) — replaced by writing directly into the existing Obsidian vault. Not part of v3.0.0.
- **Consolidation's "collapse into rolling summary" behavior** (Section 8), as a default — in v3.0.0 this applies only to Reference-flavored content; structured Records stay itemized in SQLite, never collapsed.

## 3. Extraction pass — extended output schema

For every item classified `capture`, add:

- `second_brain_type`: one of `reminder | record | project | reference | idea`
- if `record`: `record_kind`: `structured | narrative`
- if `reference`: `subtype`: `entity | concept | procedure`
- `domain`: 1+ tags, checked against the canonical list before coining a new one (see §5)
- if `project` or `reference`: `status`, defaulting to `active`
- if `record` or `reference` involves a file: `file_ref` (see §12 of the schema doc)

Classification heuristics: use §8 of `second-brain-schema.md` directly (structural cues — due date language → reminder, dated/factual/no action → record, open decision → project, "how do I"/stable entity → reference, musing/quote → idea). Default to `record` on low confidence, same as before.

## 4. Storage routing

**Storage routing is defined in `second-brain-schema.md` §10 (canonical)** —
including the undated-task single-home rule and the 6a vault layout
(`references/{entities,concepts,procedures}`, `records/`, `projects/`, `ideas/`).
Build-relevant notes below.

**Obsidian vault:** already your private GitHub repo, viewed via Working Copy/Obsidian — backup is solved. What needs to change is the *skill*, not the storage: adjust the existing LLM Wiki skill so every write includes `type`, `domain`, `status` (where applicable), and `subtype` (for Reference) in frontmatter, matching `second-brain-schema.md`. Confirm the skill already commits+pushes after each write; if it only commits locally, add the push step — a local-only commit in a repo meant to sync across your devices isn't actually backed up until it's pushed.

**Todoist:** reasonably trusted infrastructure per your own assessment — lower backup priority, no action needed beyond the API integration itself.

## 5. Backup consolidation — one "ops" repo, one cron job

Four systems exist in this design, and only one of them needs new backup infrastructure built for it — the other three are either already solved or must deliberately stay separate:

- **Working-memory-system code repo** (public, on Reddit — v2.0.0 tagged and frozen there) — **never** put personal data here, even in history. Stays fully separate; this is a security boundary, not a fragmentation problem to solve.
- **Obsidian vault** (private repo, synced via Working Copy) — already backed up as a side effect of normal use. Don't fold other data into it; it's a live sync target, not a cold-backup destination, and adding unrelated binary snapshots would only bloat it. Confirm the LLM Wiki skill commits *and pushes* after each write — a local-only commit isn't actually backed up.
- **Todoist** — SaaS-hosted, low priority per your own assessment. No dedicated backup infra needed, but see the optional export below since it's nearly free once the ops repo exists.
- **SQLite + residual `/working-memory/` folder** (raw log, `meta/`, `logs/`) — **neither has a backup mechanism today.** This is the one real gap, and both close together with a single new private "second-brain-ops" repo and one cron job:

1. Nightly cron: `sqlite3 records.db ".backup /path/to/backup/records-$(date +%F).db"` (the `.backup` command is safe against concurrent writes, unlike a raw copy).
2. Same cron run: `git add -A && git commit && git push` over `/working-memory/` and the SQLite backup file, both into the ops repo.
3. Optional, same run: export Todoist tasks to JSON, commit alongside — costs almost nothing once the job exists, and removes Todoist as a total blind spot despite its low priority.
4. Prune old SQLite snapshots periodically (e.g. last 30 daily + 12 monthly) so the repo doesn't grow unbounded — binary diffs won't compress well, but at personal scale this is a non-issue. No need for continuous-replication tooling (e.g. Litestream) unless volume actually becomes a real problem later.

**Canonical domain-tag list placement:** move this into the Obsidian vault (e.g. a `_meta/tags.md` note) rather than `/working-memory/meta/tag-index.json` — it's already git-backed and synced there, one fewer thing the ops repo needs to cover.

## 6. Scope decision: no backfill or migration (2026-08-27)

Decided: v3.0.0 **starts fresh**. Existing content is not backfilled or migrated:

- The existing LLM wiki notes stay exactly as they are — no tagging pass over them.
- Existing v2.0.0 captures/topics stay in the frozen v2.0.0 line.
- v3 writes typed, schema-compliant notes into the vault going forward; old notes may be upgraded later at the user's discretion, one at a time, if ever.

(Replaces the earlier "migrate the existing LLM Wiki" task; the section number is kept so later cross-references stay stable.)

## 7. Artifacts

Per `second-brain-schema.md` §12: files stay in your iCloud paperwork folder, untouched. A `record` (structured) row gets `file_ref` pointing at a stable, never-renamed location — not a path you might reorganize. iCloud's own redundancy covers the file itself; no separate backup action needed there.

## 8. Build order

1. **Extraction schema + SQLite + structured/narrative Record routing.** Reuse all capture plumbing unmodified. This alone fixes the retrieval failures that started this whole conversation (prescriptions, purchases).
2. **Reference/Project routing into the Obsidian vault** — adjust the LLM Wiki skill for the new frontmatter, confirm push-after-commit.
3. **Todoist integration** — the two-layer reminder scheduler (spec §9): the local store stays as the firing source and durable fallback; Todoist mirrors for cross-device visibility; completion is reconciled back at the daily digest.
4. **Backups** (§5) — do this alongside step 1, not last; there's no reason the new SQLite data should go unbacked-up even during early testing.
5. **Daily digest / surfacing layer** (schema doc §11) — only once 1-3 are stable and actually used for a few weeks.
6. **Deferred, no timeline:** confidence decay on Reference/Project status, S3-backed artifact storage, nested domain tags — build only if a real need shows up.

## 9. Escalation pointers for Hermes

When a case isn't clearly covered by SKILL.md, consult in this order: this guide (routing/backup decisions), `second-brain-schema.md` (the type/tag/status model and why it's shaped this way), `working-memory-system-spec-v3.md` (capture plumbing rationale), then `logs/` (what actually happened on a specific past run). Same three-tier pattern as v2.0.0's spec, extended with the two new documents.
