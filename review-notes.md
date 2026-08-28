# v3.0.0 — Review Notes

Branch created from `main` (the v2.0.0 release line, incl. v2.0.1/v2.0.2 docs). Carries the three second-brain documents + these notes.

## Decisions (2026-08-27)

1. **Todoist is in v3** — as the visible layer: one place to see tasks + reminders across all devices, with notifications. The local `reminders.json` store remains the *firing* source and the durable fallback (if Todoist goes down or the free plan degrades). Consistency contract: write local first → mirror to Todoist (best-effort) → completion reconciled from Todoist at the daily digest. Tasks created manually in Todoist have no local mirror. Watch: free-tier 5-active-project cap.
2. **No backfill or migration** — v3 starts fresh. Existing wiki notes and v2.0.0 data are untouched; implementation-guide §6 replaced by the scope note.
3. **spec-v3 §7 and §12 rewritten** to describe the actual v3 system (typed extraction output; retrieval across local reminders, SQLite, vault, raw log). §4/§5/§6/§8/§9 adjusted for coherence (topics/ left behind, vault+SQLite routing, two-layer reminder scheduler).

## Open question — for the schema author

The five types don't cover three existing wiki page shapes. Shareable description:

> Your schema defines five types chosen by retrieval shape (reminder, record, project, reference, idea). The existing wiki this system writes into has three page shapes that don't map onto any of the five:
>
> 1. **Comparison** — a side-by-side analysis of options (e.g. "Working Memory vs LLM Wiki"). Retrieved when making the choice it analyzes; not stable truth (Reference), not a dated event (Record), not an open decision (Project), not a musing (Idea).
> 2. **Query** — a filed answer to a specific question, with provenance, so it's not re-derived. Closest to Reference, but none of the three sub-types (entity/concept/procedure) is "question with answer".
> 3. **Puzzle** — a curated challenge with difficulty/subject facets and an answer that must stay hidden until revealed. No type captures the hidden-answer property.
>
> Do these justify extending the type list (or the Reference sub-types), or should they be folded into reference/idea with the retrieval distinction accepted as lost — and if folded, how should the schema record that loss as a conscious choice rather than silent drift?

## Minor watch items

- Dataview rollups (schema §5) are desktop-only — Obsidian on iOS can't run plugins; Hermes can render the rollup on request.
- Backups: the ops-repo cron (implementation guide §5) is the only new infra; build alongside step 1.
- Classification accuracy is the load-bearing assumption — the early confirmation phase (spec §13) stays on.
