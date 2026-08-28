# v3.0.0 — Review Notes

Branch created from `main` (the v2.0.0 release line, incl. v2.0.1/v2.0.2 docs). Carries the three second-brain documents + these notes.

## Decisions (2026-08-27)

1. **Todoist is in v3** — as the visible layer: one place to see tasks + reminders across all devices, with notifications. The local `reminders.json` store remains the *firing* source and the durable fallback (if Todoist goes down or the free plan degrades). Consistency contract: write local first → mirror to Todoist (best-effort) → completion reconciled from Todoist at the daily digest. Tasks created manually in Todoist have no local mirror. Watch: free-tier 5-active-project cap.
2. **No backfill or migration** — v3 starts fresh. Existing wiki notes and v2.0.0 data are untouched; implementation-guide §6 replaced by the scope note.
3. **spec-v3 §7 and §12 rewritten** to describe the actual v3 system (typed extraction output; retrieval across local reminders, SQLite, vault, raw log). §4/§5/§6/§8/§9 adjusted for coherence (topics/ left behind, vault+SQLite routing, two-layer reminder scheduler).

## Decisions (2026-08-28)

4. **`type` hijacked for v3** — raw entries and extraction output carry `type: reminder|record|project|reference|idea` directly; v2's `log|reminder|log+reminder` is gone (a habit capture splits into record + reminder items, schema §3.1). No `second_brain_type` field.
5. **Vault layout 6a** — type = top-level section (`references/`, `records/`, `projects/`, `ideas/`); Reference subtypes = sibling subfolders (`entities/`, `concepts/`, `procedures/`). Wiki migrated (2026-08-28): all pages → v3 frontmatter, comparisons/puzzles → references/concepts per §14; canonical tags at `_meta/tags.md`.
6. **v3 notes ARE wiki pages** — index + log entries, natural links (no forced ≥2; the wiki SCHEMA relaxation adopted).
7. **Recovery = backups, not raw-rebuild** — raw stays full-text (capture guarantee + audit), but rebuild-from-raw is dropped as the recovery path; ops cron additionally verifies the vault remote is in sync (alert on drift).
8. **Undated tasks: one home only** — quick errands → Todoist only (`todoist_only`); substantial → vault project only. Confirmation lines show the destination; corrections re-route.
9. **Mirrored reminders skip local firing** — `mirrored: true` → reminder-check skips; Todoist notification is the reminder; local fires only when the mirror is absent/failed. Completion reconciled in reminder-check (digest out of scope).
10. **CODE IMPROVEMENT refinement category** — proposals with before/after; on approval, implemented on v3.0.0 (spec + skill + code together, tested, pushed).

## Schema question — RESOLVED (2026-08-27)

The schema author ruled: **no schema change.** Comparison and Puzzle pages fit Reference / Concept — the discriminating test is evergreen-vs-scaffolding (would you reread it for a reason other than nostalgia after the decision's made?), and difficulty/subject are ordinary domain tags. Query pages fall under the same test (bucket currently empty — prospective only). General principle: extend types only for a genuinely new retrieval/engagement shape; domains and optional properties are tags. The full ruling + extension protocol (when to raise a question; repeated-pattern → refinement-log proposal with sign-off) now live in schema §14, with the new heuristics added to schema §8.

## Minor watch items

- Dataview rollups (schema §5) are desktop-only — Obsidian on iOS can't run plugins; Hermes can render the rollup on request.
- Backups: the ops-repo cron (implementation guide §5) is the only new infra; build alongside step 1.
- Classification accuracy is the load-bearing assumption — the early confirmation phase (spec §13) stays on.
