# Second Brain — Information Schema

**Document map:** this file answers *classification* questions — what type/tag/status something belongs to. For build, storage-routing, and backup mechanics, see `second-brain-implementation-guide.md`. For capture, debounce, reminders, and crash-recovery plumbing, see `working-memory-system-spec-v3.md`.

*Type: Reference (this document is itself evergreen system documentation — status: active)*

## 1. Purpose

This schema exists to answer one question for any incoming piece of information: **where does this go, and how will I get it back?** It is organized around retrieval shape, not subject domain, because domain-based folders/hierarchies force premature, brittle decisions and require constant refactoring as new topics appear. Type answers "how is this stored and retrieved." Domain tags answer "what is this about." The two are independent.

## 2. The Four Axes

Every captured item is described by up to four axes:

1. **Type** (required, exactly one) — determines storage mechanism and retrieval pattern. Five fixed values.
2. **Domain tags** (optional, zero or more) — flat, freely assigned, drawn from a canonical list where possible.
3. **Status** (only on Project and Reference) — active / superseded / archived.
4. **Relations** (optional) — links to other notes, used to compose types together without blending them. Default to a generic `related:` link; promote to a sharper relation type (`supersedes`, `derived_from`, `contradicts`) only when the distinction changes what Hermes does with it (e.g. `supersedes` suppresses the old page from default answers; `contradicts` triggers a review flag). If a sharper label wouldn't change retrieval behavior, leave it as `related:` — same discipline as domain tags: don't specialize until specialization earns its keep.

Type is the only rigid axis. Everything else is a loose filter layered on top.

## 3. The Five Types

### 3.1 Reminder
**Definition:** Has a due date or recurrence; requires a future action from you.
**Retrieval shape:** Time-triggered — you don't search for it, it surfaces itself when due.
**Storage implication:** Todoist — cloud REST API + webhooks, reachable directly from a VPS, with native completion state (needed for the "are we done with X" surfacing in §11), not just Google Calendar (which has due dates but no completion concept) or Things 3 (no cloud API at all — see §10 for the full migration rationale).
**Use cases:** "Next vitamin D pill due Friday," "renew passport by March," "meditate daily" (habit nudge).
**Edge cases:**
- A habit ("meditate daily") is a Reminder that *generates* Record entries each time it's completed — the reminder itself doesn't accumulate history, its completions do.
- Completion of a Reminder is binary (done/not done) — no separate status field needed.
- A bookmark/read-later item is *not* a Reminder — it has no due date and is disposable pending-review content. Keep it in a dedicated read-later tool or an `unread` tag as pre-capture staging; once read, whatever's worth keeping graduates into Reference or Idea/Quote like any other capture.

### 3.2 Record
**Definition:** A dated, factual entry. No action implied. Append-only.
**Retrieval shape:** Searched by date or by entity/keyword — "when did I last buy X," "what was my BP last month."
**Storage implication:** Structured log (table/CSV/database) for high-frequency numeric data; one note per event for low-frequency factual entries; free-text dated notes for journal/narrative entries. All three are the same type, different file shapes chosen by volume/nature of the data.
**Use cases:** BP readings, "took vitamin D on Friday," prescriptions received, medicine purchases with dates, journal/diary entries, investment contribution history.
**Edge cases:**
- Never needs a status field — age is inherent and informative, not staleness.
- A Record entry can `relate:` to a Reference page (e.g. a purchase Record linked to the "Investments" Reference page) to compose a rollup — see §5.
- If in doubt about classification, default new captures to Record — cheapest to fix later, nothing gets silently lost.

### 3.3 Project
**Definition:** An open thread with a lifecycle — begins, has active work, ends in a decision or completion.
**Retrieval shape:** Looked at while open, then closed and rarely revisited (but not deleted).
**Storage implication:** One note per project, with an explicit status.
**Use cases:** "Which printer to buy" (research → decision), draft blog post (draft → published), GSTR-3B filing for a given cycle, a goal ("run a half marathon by Nov").
**Edge cases:**
- A goal is a Project; a habit supporting it is a Reminder generating Records — don't conflate the two under one note.
- Needs status: active / superseded / archived (see §4).

### 3.4 Reference
**Definition:** Evergreen, stable-truth content. Updated in place. Looked up occasionally, not on a schedule.
**Retrieval shape:** Point lookup by name/topic.
**Storage implication:** One page per topic or entity, edited over time rather than appended to.
**Use cases:** GSTR-3B filing procedure, VPS hardening steps, backup system description, license keys, "how I do X," a person's page ("John — likes strawberries"), a family tree (a set of linked person-pages), a recipe, current list of financial accounts/institutions.
**Sub-types (by retrieval shape, not domain):**
- **Entity** — looked up by proper noun: a person, place, org, or thing (John, "Investments," the backup server). License keys and account details live as fields on the relevant Entity page rather than a separate sub-type.
- **Concept** — looked up as an idea or topic explanation (a technique, a principle).
- **Procedure** — looked up as ordered steps to execute (GSTR-3B filing, VPS hardening, a recipe).
Test before adding a 4th sub-type: does this get looked up by name (Entity), understood as an idea (Concept), or executed as steps (Procedure)? Every use case so far fits one of these three — don't add a sub-type for a new domain, only for a genuinely new retrieval shape.
**Edge cases:**
- People, places, and things all get Reference pages, not just procedures — same retrieval shape (look up by name, accumulate facts over time).
- Needs status: active / superseded / archived — this is the type most prone to silent bit-rot (old install guides, deprecated procedures) if status is skipped.
- Can host a live query pulling related Records for a rollup view (e.g. Investments Reference page + linked Record history) without becoming a hybrid type.

### 3.5 Idea / Quote
**Definition:** Associative, atemporal, no urgency, no action.
**Retrieval shape:** Serendipitous resurfacing / free-linking, not scheduled or task-driven lookup.
**Storage implication:** Atomic notes, freely linked (Zettelkasten-style).
**Use cases:** A haiku you liked, a nice concept from a book, a quote, a musing.
**Edge cases:** Never needs status — doesn't go stale.

## 4. Status Field (Project and Reference only)

Three values:
- **active** — current, in-use
- **superseded** — replaced by something newer; link to the replacement if one exists
- **archived** — no longer relevant, kept for record-keeping only

Only added where it changes what gets shown by default (Hermes should exclude archived/superseded from default answers, but surface them if explicitly asked). Record, Reminder, and Idea/Quote don't get a status field because nothing about their default retrieval changes based on age or relevance state.

Optional future refinement (from Karpathy's LLM Wiki pattern): a numeric confidence score that decays over time on Project/Reference pages instead of a binary status, letting Hermes flag "this might be stale" proactively rather than waiting for you to mark it superseded. Not adopted yet — add only if binary status proves insufficient in practice.

## 5. Composition (combining types without blending them)

When something is genuinely two things (e.g. "Investments" = current account list + historical contributions), **do not create a hybrid type.** Instead:
- Keep one Reference page for current state (accounts, institutions, balances as of now).
- Keep Record entries for each dated event (contribution, valuation snapshot), each linked via `related: [[Investments]]` or a shared domain tag.
- The Reference page embeds a query (e.g. Dataview in Obsidian) that auto-renders linked Records in chronological order.

This keeps every note a single, clean type while still giving you the combined view.

## 6. Domain Tags

Flat, unlimited, freely multi-assigned — not a hierarchy. Example: a curry recipe gets `domain: cooking, curry, indian` rather than being forced into one folder (Food vs. Health vs. Culture).

- Maintain one **canonical tag list** (a single note or Hermes's state file), started with ~20-40 domains.
- New captures should be classified *against this list first*; a new tag is created only when nothing existing fits.
- **Nested tags** (e.g. `domain/cooking/curry`, `domain/cooking/baking`) are allowed *within* one domain that's grown large enough to need sub-filtering — opt-in per domain, never imposed system-wide.
- **Coining is do-then-inform (2026-08-29 ruling).** When no canonical tag fits, the agent coins the tag in the same operation as the capture and informs the user after; the user vetoes (tag removed from the list + entry re-tagged) if it shouldn't exist. Prefer the closest existing tag whenever one genuinely fits — coining is for the unsupported topic, not for avoiding a judgment call.

## 7. Tag Hygiene & Management

1. **Closed-ish vocabulary.** Hermes classifies against the canonical list, not free-form — this prevents most drift before it happens.
2. **Near-duplicate check at write time.** Before creating a new tag, check semantic similarity against the canonical list (e.g. "vps-hardening" vs. existing "vps") and flag or auto-alias.
3. **Periodic cheap audit, not ongoing vigilance.** Monthly or quarterly: list all tags with usage counts, flag singletons, near-synonyms, and tags unused in 90+ days. Review takes ~10 minutes.
4. **Merging is a rename, not a migration.** Use Obsidian's tag-rename (or frontmatter find-replace) to consolidate near-duplicate tags in one operation across all notes.
5. **Don't pre-create a taxonomy.** Let tags emerge from actual second-occurrence use rather than designing a domain list up front.

## 8. Classification Heuristics (for Hermes)

Structural cues, not content cues:
- Has a due date / "next X" language → **Reminder**
- Dated, factual, no action implied → **Record**
- Open question or decision pending → **Project**
- "How do I / here's how" or a stable entity (person/place/thing) → **Reference**
- Decision-time analysis (comparison/tradeoffs) → **Reference / Concept** if worth rereading after the decision; otherwise Project support material (the §14 test)
- Challenge + solution (puzzle) → **Reference / Concept**; difficulty/subject are domain tags (§14)
- Musing, quote, no time element, no action → **Idea/Quote**
- Low confidence → default to **Record**

## 9. Structured Record Storage (SQLite)

Structured Records (health readings, purchases, prescriptions, etc.) live in one generic SQLite table rather than one table per domain — same anti-proliferation principle as types and tags:

```
records(id, type, domain, occurred_at, entity, data_json, notes)
```

`type`/`domain`/`entity`/`occurred_at` are indexed columns for filtering and sorting; `data_json` holds whatever fields are specific to that record kind, so a new domain never requires a schema change. Example rows:

| id | type | domain | occurred_at | entity | data_json |
|---|---|---|---|---|---|
| 1 | health_reading | blood_pressure | 2026-08-20 | blood_pressure | `{"systolic":128,"diastolic":82}` |
| 2 | prescription | medicine | 2026-08-05 | Dr. Sharma | `{"medicines":["Amlodipine 5mg","Metformin 500mg"]}` |
| 3 | purchase | medicine | 2026-08-10 | ABC Pharmacy | `{"items":["Amlodipine 5mg","Metformin 500mg"]}` |

Example queries:
```sql
-- BP trend
SELECT occurred_at, data_json FROM records
WHERE domain = 'blood_pressure' ORDER BY occurred_at;

-- last purchase from a given pharmacy
SELECT occurred_at, data_json FROM records
WHERE type='purchase' AND entity LIKE '%Pharmacy%'
ORDER BY occurred_at DESC LIMIT 1;
```
Prescription-overlap checks: pull the last two `prescription` rows, hand both `data_json` medicine lists to Hermes to diff in reasoning rather than SQL. Narrative Records (journal entries) stay as dated markdown notes in Obsidian, not in this table — only structured/numeric Records go here.

## 10. Storage & Tooling Map

| Type | Store | Why |
|---|---|---|
| Reminder | Todoist (visible layer) + local `reminders.json` (firing fallback) | Todoist: cross-device visibility + notifications. Local store: durable record + fires when the mirror is absent/failed (§9 of the spec) |
| Record (structured) | SQLite (see §9) | Queryable by date/entity for trends and lookups |
| Record (narrative) | Obsidian, `records/` dated notes | Full-text search, daily-note browsing |
| Project | Obsidian, `projects/`, `status:` field | Search + a "status:active" dashboard query |
| Reference | Obsidian, `references/{entities,concepts,procedures}/` by subtype, `status:` field | Search by name/title, backlinks |
| Idea/Quote | Obsidian, `ideas/` | Backlinks, graph view, serendipitous resurfacing |
| Undated task (quick errand) | Todoist ONLY | `todoist_only` flag on the raw entry; no vault note, no local reminder |
| Undated task (project-scoped) | Checklist in the project's vault note | `## Checklist` section at the bottom of the note; `- [ ]` lines, agent-maintained (append on capture, tick on completion) |
| Undated task (substantial) | Obsidian vault `projects/` ONLY | No Todoist mirror unless it later gains a due date (then reminder rules apply) |

**Vault layout (6a, decided 2026-08-28):** type = top-level section
(`references/`, `records/`, `projects/`, `ideas/`); Reference subtypes =
subfolders (`entities/`, `concepts/`, `procedures/`). The canonical domain-tag
list lives at `_meta/tags.md` in the vault.

**Migration note — Things 3 + Google Calendar → Todoist.** The original split (Hermes writes to Calendar, Things 3 stays manual) works but has a real gap: Calendar events have no completion state, so Hermes can tell you *when* something's due but never *whether it got done* — which is exactly what the surfacing/nagging layer (§11) needs to answer "are we done with X yet." Todoist fixes this cleanly: it has a mature, well-documented REST API plus webhooks, reachable directly from the VPS with no local bridge, and it natively supports due dates/recurrence *and* completion state *and* projects/sections (a reasonable mirror of Things 3's Projects/Areas). Recommendation: **consolidate the actionable/checkable layer into Todoist** — Hermes reads and writes it directly — and let Google Calendar become, at most, a passive read-only mirror (Todoist can export a calendar feed) if you still want a visual day view. This removes the Things-3-vs-Calendar dance entirely rather than managing around it. TickTick was also considered — it has more built-in tools (habit tracking, Pomodoro, calendar view) but a comparatively less mature/developer-friendly API and weaker natural-language parsing, so it's the weaker fit for Hermes-driven automation specifically.

**Linking Todoist state to Obsidian content:** same pattern as before — an Obsidian Project note carries a `todoist_task: <link>` frontmatter field pointing at the actual task/due-date in Todoist. Todoist owns the due-date/completion state; Obsidian owns the narrative and decision log. Nothing is duplicated.

## 11. Surfacing Layer — Daily Digest

Everything above (§1–10) is passive storage: well-organized, but nothing reaches out to you unprompted. This layer is what makes "important, when it's important" actually happen — implemented as a scheduled Hermes job, not a new schema concept.

**Two new frontmatter fields, Project notes only:**
- `target_date` — optional, only if a real deadline exists
- `last_touched` — auto-stamped by Hermes every time it edits the note (not maintained by hand)

**Daily digest job (cron, once/day), logic:**
1. Query Todoist for tasks due today, due soon, and overdue — this is the straightforward deadline nag.
2. Scan Obsidian for `type: project AND status: active`:
   - Has `target_date` approaching (e.g. ≤3 days) and `last_touched` stale (e.g. >7 days) → *"X is due in N days, no activity in M — want to start?"*
   - No `target_date` but `last_touched` beyond a staleness threshold (e.g. 14 days) → *"Still working on X, or should this close?"* — this is the "are we done yet" check, driven by staleness rather than a deadline that doesn't exist.
3. Combine both into a single Telegram digest message once a day — not scattered separate pings.
4. Any reply/edit that touches the note resets `last_touched`, quieting the nag naturally; an explicit status change to closed/archived stops it permanently for that item.

**Kickstart note for Hermes** (hand this to it as a starting spec):

> Build a daily cron job that: (1) calls the Todoist API for tasks due today/overdue, (2) scans the Obsidian vault for notes with `type: project` and `status: active`, reading `target_date` and `last_touched` from frontmatter, (3) applies the staleness rules above to decide which Projects to flag, (4) composes one combined Telegram message covering both Todoist deadlines and stale/approaching Projects, (5) on any subsequent Telegram reply that results in an edit to a flagged note, auto-updates that note's `last_touched` to the current timestamp. Start with fixed thresholds (3 days approaching, 7 days stale-with-deadline, 14 days stale-no-deadline) and treat them as tunable, not final — adjust after a couple of weeks of real digests once it's clear whether it's nagging too much or too little.

## 12. Artifacts (Images, PDFs, Binary Files)

Binary files stay in whatever existing sync system already handles them well (e.g. an iCloud paperwork folder) — never duplicated into Obsidian or SQLite. The second brain holds only a **Record** (dated, one-note-per-event, same as prescriptions) with a `file_ref:` field pointing at the file's location.

**Stale-link fix (applies regardless of storage backend):** identify the file by a stable, never-renamed location — not by its current folder path. The moment `file_ref` encodes a path you might reorganize, reorganizing breaks it — same root problem folder-hierarchy tags were avoided for in §6. Keep the file in one flat, unchanging spot (or an opaque filename you never touch) and do all real-world organization through the Record's `domain:` tags instead. This works today, on iCloud, without any storage migration.

**Constraint:** iCloud Drive has no official third-party API (same category of problem as Things 3 — only fragile, reverse-engineered access exists). Hermes can't verify or fetch the file automatically; `file_ref:` is entered by you at capture time, not auto-discovered.

**Future option (phase 2/3, not needed now):** move artifact storage to an S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2, or self-hosted MinIO on the same VPS) once there's an actual need for Hermes to read file contents — OCR, full-text search inside PDFs, auto-summarizing a report. Keyed by an opaque ID (UUID), never a path, so the stale-link problem can't recur. Pairs naturally with existing Telegram capture: send Hermes the file, it uploads and creates the Record in one step, no filing decision required. Not worth the setup cost until content-level access is an actual need, not a hypothetical one.

## 13. Open Items for Later Refinement

- Exact split point for Record between "running table" (high-frequency) vs. "one note per event" (low-frequency) — to be decided per domain as volume becomes clear, not upfront.
- Whether Karpathy-style confidence decay (§4) gets adopted once binary status is tested in practice.
- Staleness thresholds in §11 — tune after real-world digest runs.
- Whether Todoist migration (§10) fully replaces Things 3, or Things 3 stays for non-second-brain personal task use.

## 14. Schema decisions & extension protocol (2026-08-27)

Ruling from the schema author on the wiki page shapes that didn't obviously fit the five types. **No schema change.**

**Comparison pages → Reference / Concept.** Being retrieved at a decision moment doesn't distinguish a type — every Reference page is read exactly when it's needed, whether that's a procedure being followed or a person's page being checked. What matters is whether the content stays evergreen (updated in place, worth rereading later for its own sake) versus disposable scaffolding for one closing decision. **Test:** would you reread this for a reason other than nostalgia, after the decision's made? Yes → it's a genuine Concept page (a tradeoff analysis is exactly the "idea/topic explanation" Concept covers). No → it was really supporting material for a Project, and should close/archive alongside that Project rather than live on as standalone Reference.

**Puzzle pages → Reference / Concept.** A challenge statement plus its solution is structurally the same shape as any other explanatory page. (A hidden-until-revealed answer would have been a genuinely new retrieval shape, not covered by any existing type or subtype — but that property isn't in play here, so it doesn't apply.) Difficulty and subject are ordinary domain tags (`domain: puzzle, math, difficulty: medium`) — the same flat-tag mechanism used everywhere else; if a puzzle collection grows large enough to need finer filtering, that's the existing nested-tag exception (`domain/puzzle/math`), not a new type.

**Query pages** (filed answers with provenance): covered by the same test — evergreen/reusable → Reference/Concept; one-off → Project support material. (The `queries/` bucket is currently empty, so this is prospective, not a migration.)

**General principle:** extend the schema for a genuinely new retrieval or engagement shape — something no existing type/subtype captures — never for a new domain or a property content could have but doesn't actually get used for here. Domains and optional properties are tags; only a real structural mismatch justifies a new type.

**When to raise a schema-extension question rather than force-fit:** if something to be captured doesn't clearly match any existing type/subtype after applying the standard classification heuristics, and forcing it into the closest-fitting one would produce a page that's misleading to future retrieval (e.g. filed as Concept but doesn't actually explain anything, or filed as Record but needs to be queried in a way SQLite's flat schema can't support) — that's the signal to stop and ask, rather than silently coin a new tag or bend an existing type's meaning. One genuinely ambiguous item is fine to default (the low-confidence fallback); if the same kind of content recurs and keeps sitting awkwardly in its assigned type, that's a repeated-pattern signal for the refinement log — surface it as a proposed schema change with a brief before/after rationale, and wait for sign-off before applying, the same approval-boundary rule governing other policy changes. (Tag coining is the exception — do-then-inform per §6; the wait rule covers type/subtype extensions, routing rules, and classification rules.)
