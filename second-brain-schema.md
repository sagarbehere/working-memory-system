# Second Brain — Information Schema

**What this is.** A general model for organising personal information, written
to answer one question about anything you capture: *where does this go, and how
will I get it back?* It is deliberately **implementation-independent** — no
tool, file format, or product is required to apply it. You could use it with
plain folders, a wiki, a notes app, or nothing but a text file.

**What it is not.** Not a description of any particular system. The
working-memory system in this repository is *one* application of it; for how
that system files things concretely, see `working-memory-system-spec.md`.
If you find a tool name in the sections below, that is a bug in this document.

*Type: Reference (evergreen — status: active)*

## 1. Purpose

This schema exists to answer one question for any incoming piece of information: **where does this go, and how will I get it back?** It is organized around retrieval shape, not subject domain, because domain-based folders/hierarchies force premature, brittle decisions and require constant refactoring as new topics appear. Type answers "how is this stored and retrieved." Domain tags answer "what is this about." The two are independent.

## 2. The Four Axes

Every captured item is described by up to four axes:

1. **Type** (required, exactly one) — determines storage mechanism and retrieval pattern. Five fixed values.
2. **Domain tags** (optional, zero or more) — flat, freely assigned, drawn from a canonical list where possible.
3. **Status** (only on Project and Reference) — active / superseded / archived.
4. **Relations** (optional) — links to other notes, used to compose types together without blending them. Default to a generic `related:` link; promote to a sharper relation type (`supersedes`, `derived_from`, `contradicts`) only when the distinction changes what the system *does* with it (`supersedes` suppresses the old page from default answers; `contradicts` flags a review). If a sharper label wouldn't change retrieval behavior, leave it as `related:` — same discipline as domain tags: don't specialize until specialization earns its keep.

Type is the only rigid axis. Everything else is a loose filter layered on top.

## 3. The Five Types

### 3.1 Reminder
**Definition:** Has a due date or recurrence; requires a future action from you.
**Retrieval shape:** Time-triggered — you don't search for it, it surfaces itself when due.
**Storage implication:** whatever already notifies you on the devices you carry, and tracks completion. Completion state matters: "is this done?" is a question you will ask, and a calendar entry cannot answer it.
**Use cases:** "Next vitamin D pill due Friday," "renew passport by March," "meditate daily" (habit nudge).
**Edge cases:**
- A habit ("meditate daily") is a Reminder that *generates* Record entries each time it's completed — the reminder itself doesn't accumulate history, its completions do.
- Completion of a Reminder is binary (done/not done) — no separate status field needed.
- A bookmark/read-later item is *not* a Reminder — it has no due date and is disposable pending-review content. Keep it in a dedicated read-later tool or an `unread` tag as pre-capture staging; once read, whatever's worth keeping graduates into Reference or Idea/Quote like any other capture.

### 3.2 Record
**Definition:** A dated, factual entry. No action implied. Append-only.
**Retrieval shape:** Searched by date or by entity/keyword — "when did I last buy X," "what was my BP last month."
**Storage implication — frequency determines shape.** High-frequency observations (a measurement you take weekly) belong in ONE running series, a line per entry, so the history reads as a table. Low-frequency events get one note each. Narrative entries get dated prose. All three are the same type; only the volume differs.

Resist reaching for a database as the series grows. The threshold that matters is not "many rows" but "more than can be read at once" — and for personal data (a decade of weekly readings is a few thousand short lines) that threshold is rarely crossed. A single file you can read end to end supports questions a query language cannot express: *did the headaches follow the bad nights?*
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

Only added where it changes what gets shown by default: archived and superseded items should be excluded from default answers and surfaced only when explicitly asked for. Record, Reminder, and Idea/Quote don't get a status field because nothing about their default retrieval changes based on age or relevance state.

Optional future refinement (from Karpathy's LLM Wiki pattern): a numeric confidence score that decays over time on Project/Reference pages instead of a binary status, letting the system flag "this might be stale" proactively rather than waiting for you to mark it superseded. Not adopted yet — add only if binary status proves insufficient in practice.

## 5. Composition (combining types without blending them)

When something is genuinely two things (e.g. "Investments" = current account list + historical contributions), **do not create a hybrid type.** Instead:
- Keep one Reference page for current state (accounts, institutions, balances as of now).
- Keep Record entries for each dated event (contribution, valuation snapshot), each linked via `related: [[Investments]]` or a shared domain tag.
- The Reference page gathers those Records into a chronological view — by embedded query if your tool supports one, by links if not.

This keeps every note a single, clean type while still giving you the combined view.

## 6. Domain Tags

Flat, unlimited, freely multi-assigned — not a hierarchy. Example: a curry recipe gets `domain: cooking, curry, indian` rather than being forced into one folder (Food vs. Health vs. Culture).

- Maintain one **canonical tag list** in a single place, starting with ~20-40 domains.
- New captures should be classified *against this list first*; a new tag is created only when nothing existing fits.
- **Nested tags** (e.g. `domain/cooking/curry`, `domain/cooking/baking`) are allowed *within* one domain that's grown large enough to need sub-filtering — opt-in per domain, never imposed system-wide.
- **Coining a new tag should be cheap and reversible, not a ceremony.** Add it when nothing fits, note that you did, and remove it later if it turns out to be a synonym. Prefer the closest existing tag whenever one genuinely fits — a new tag is for an unsupported topic, not for avoiding a judgment call.

## 7. Tag Hygiene & Management

1. **Closed-ish vocabulary.** Classify against the canonical list rather than free-form — this prevents most drift before it happens.
2. **Near-duplicate check at write time.** Before creating a new tag, compare it against the canonical list ("vps-hardening" vs. an existing "vps") and alias rather than duplicate.
3. **Periodic cheap audit, not ongoing vigilance.** Monthly or quarterly: list all tags with usage counts, flag singletons, near-synonyms, and tags unused in 90+ days. Review takes ~10 minutes.
4. **Merging is a rename, not a migration.** Consolidating two near-duplicate tags is one find-and-replace across all notes, not a re-filing exercise. This is a direct benefit of tags over folders.
5. **Don't pre-create a taxonomy.** Let tags emerge from actual second-occurrence use rather than designing a domain list up front.

## 8. Classification Heuristics

Structural cues, not content cues:
- Has a due date / "next X" language → **Reminder**
- Dated, factual, no action implied → **Record**
- Open question or decision pending → **Project**
- "How do I / here's how" or a stable entity (person/place/thing) → **Reference**
- Decision-time analysis (comparison/tradeoffs) → **Reference / Concept** if worth rereading after the decision; otherwise Project support material
- Challenge + solution (puzzle) → **Reference / Concept**; difficulty and subject are domain tags
- Musing, quote, no time element, no action → **Idea/Quote**
- Low confidence → default to **Record**

## 9. Artifacts (images, PDFs, binary files)

Binary files stay wherever already syncs them well. Do **not** copy them into
the knowledge base — you would gain a second copy to keep in step and lose the
tooling that already handles them.

Instead the base holds a **Record** with a reference to the file's location.
Two rules make that reference survive:

- Point at a **stable location**, never a path you might reorganise. A
  reference that breaks when you tidy a folder is worse than no reference,
  because it looks correct until you follow it.
- Prefer an **opaque, permanent identifier** over a human-readable path if
  your storage offers one.

## 10. What this schema deliberately does not decide

- **Which tools to use.** Every type above describes a retrieval shape; the
  storage that serves it is your choice, and is expected to change.
- **A surfacing or digest layer.** Everything here is passive: it makes things
  findable, not attention-seeking. A layer that pushes things at you
  unprompted is a separate design problem with its own failure modes (the
  main one being that people learn to ignore it), and folding it into the
  schema would make it look like a storage question. It isn't.
- **Confidence decay.** See §4 — binary status first; add gradients only when
  binary demonstrably fails.

## 11. Open questions

- The exact split point between a running series and one-note-per-event
  (§3.2) is per-domain and best decided by trying it.
- Whether binary status (§4) proves insufficient in practice.

## 12. Schema decisions & extension protocol

Ruling from the schema author on the wiki page shapes that didn't obviously fit the five types. **No schema change.**

**Comparison pages → Reference / Concept.** Being retrieved at a decision moment doesn't distinguish a type — every Reference page is read exactly when it's needed, whether that's a procedure being followed or a person's page being checked. What matters is whether the content stays evergreen (updated in place, worth rereading later for its own sake) versus disposable scaffolding for one closing decision. **Test:** would you reread this for a reason other than nostalgia, after the decision's made? Yes → it's a genuine Concept page (a tradeoff analysis is exactly the "idea/topic explanation" Concept covers). No → it was really supporting material for a Project, and should close/archive alongside that Project rather than live on as standalone Reference.

**Puzzle pages → Reference / Concept.** A challenge statement plus its solution is structurally the same shape as any other explanatory page. (A hidden-until-revealed answer would have been a genuinely new retrieval shape, not covered by any existing type or subtype — but that property isn't in play here, so it doesn't apply.) Difficulty and subject are ordinary domain tags (`domain: puzzle, math, difficulty: medium`) — the same flat-tag mechanism used everywhere else; if a puzzle collection grows large enough to need finer filtering, that's the existing nested-tag exception (`domain/puzzle/math`), not a new type.

**Query pages** (filed answers with provenance): covered by the same test — evergreen/reusable → Reference/Concept; one-off → Project support material. (The `queries/` bucket is currently empty, so this is prospective, not a migration.)

**General principle:** extend the schema for a genuinely new retrieval or engagement shape — something no existing type/subtype captures — never for a new domain or a property content could have but doesn't actually get used for here. Domains and optional properties are tags; only a real structural mismatch justifies a new type.

**When to raise a schema-extension question rather than force-fit:** if something to be captured doesn't clearly match any existing type/subtype after applying the standard classification heuristics, and forcing it into the closest-fitting one would produce a page that's misleading to future retrieval (e.g. filed as Concept but doesn't actually explain anything, or filed as Record but needs to be retrieved in a way its shape cannot support) — that's the signal to stop and ask, rather than silently coin a new tag or bend an existing type's meaning. One genuinely ambiguous item is fine to default (the low-confidence fallback); if the same kind of content recurs and keeps sitting awkwardly in its assigned type, that's a repeated-pattern signal for the refinement log — surface it as a proposed schema change with a brief before/after rationale, and wait for sign-off before applying, (Coining a domain tag is the exception; the wait applies to type and subtype extensions and to classification rules.)
