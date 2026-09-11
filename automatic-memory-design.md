# Automatic memory — design analysis

> ## ⚠️ THOUGHT EXPERIMENT ONLY — NONE OF THIS EXISTS
>
> **Nothing described in this document is built, decided, or in progress.**
> There is no automatic capture, no automatic recall, no index over the vault,
> no MCP server, and no integration with any third-party memory service. The
> system that actually exists is the one described in
> `working-memory-system-spec.md`: explicit capture via a `Hey memory` marker
> or a reserved lane, and nothing else.
>
> This file is speculation written down so that a future decision is cheaper to
> make. Do not implement from it, do not cite it as a description of the
> system, and do not assume any component named here is available. Estimates of
> effort and cost are guesses, not measurements.

**What happens when something here is chosen.** The outcome belongs in
`decisions.md` — including a rejection, which is the more likely outcome and
the more valuable thing to record. This file's corresponding section should
then be cut down to a pointer, so that speculation is never left sitting where
a reader might mistake it for a plan.

**The question.** Today the system captures when it is told to — a `Hey memory`
marker or a reserved lane (`working-memory-system-spec.md` §2). What would it
take for it to *also* save and recall relevant things on its own, without
losing the explicit path? And if that automatic memory should eventually be
shared by every AI agent the user runs, what does that add?

---

## 1. The thing to understand first

**The marker is not friction. It is the label.**

`Hey memory` does two jobs at once, and only one of them is obvious. It marks a
message as in-scope — but it also asserts, for free and with certainty, that a
durable fact is present and that the user meant to keep it. Every downstream
step trusts that assertion. Classification only has to decide *what kind* of
thing this is, never *whether* there is a thing.

Automatic capture deletes that assertion and has to synthesise it from
judgment. That is not an incremental change to the capture gate; it moves the
single most load-bearing decision in the system from the deterministic layer
into the agent layer, which is the exact direction `CLAUDE.md` says not to go.

This does not make automatic memory wrong. It means automatic memory is a
**second lane with different trust properties**, not a relaxation of the first
one. The explicit path stays authoritative and unchanged. The automatic path
is lower-confidence by construction and must be prevented, structurally, from
contaminating the high-confidence store. Every recommendation below follows
from that one distinction.

A second consequence: **recall and capture are separable, and they are not
equally hard.** Automatic recall is deterministic code with a reversible
failure mode. Automatic capture is judgment with a compounding one. They
should not be built at the same time.

---

## 2. Automatic recall

### What exists already

The seam is in place. `hooks/working-memory-debounce/handler.py` patches
`BasePlatformAdapter.handle_message`, so it already sees every message on every
platform. Today a non-memory message takes one early
`return await orig_handle_message(...)`. **That early return is where context
injection goes.** No new infrastructure, no new process, no new cron entry.

### What is missing

An index. Right now *the agent is the query engine* — it greps the vault, reads
`index.md`, reasons over a series note. That is correct at conversational
latency and useless at per-message latency.

- **A zero-token retrieval index over the vault.** SQLite FTS5 first: Hermes
  already depends on it for `state.db`, it needs no model, it is deterministic,
  and it is testable in the shape this repo already tests things — a fixture
  corpus, a query, an expected note. Embeddings buy semantic recall later and
  are a strictly bigger commitment (a model on the VPS, a rebuild path, a
  similarity threshold that has to be tuned rather than reasoned about). Do not
  start there.
- **A relevance floor and a hard token budget.** Top-k with a score threshold.
- **Silence as the default.** If nothing clears the bar, inject nothing. This
  is the same rule as rule #3 for watchdogs and for the same reason: the
  failure mode of auto-recall is not a missed memory, it is quietly polluting
  every conversation with near-misses until the user stops trusting the
  injected block.
- **An index rebuild on vault write**, so the index cannot silently drift from
  the notes. A stale index fails the way this repo has been bitten before — it
  looks exactly like a correct index that found nothing.

### Cost and risk

Zero LLM calls. The only cost is added input tokens per turn, which is real and
recurring but bounded by the budget above. The failure mode is reversible:
setting the threshold to infinity turns the feature off without touching
anything else.

**Estimate: a weekend for a credible v1.** This fits the existing design almost
suspiciously well — deterministic code the agent calls, silent when it has
nothing, testable offline.

---

## 3. Automatic capture

Possible, but this is where the months go, and the hard part is not the part
that looks hard.

### Where extraction runs

| Approach | Cost | Quality |
|---|---|---|
| Main agent decides inline, every turn | free | poor — pollutes every system prompt, and the agent is mid-task on something else |
| Cheap model per message | ~1 call/message | good, but this is the polling loop already rejected (see the Todoist call budget), wearing a different hat |
| **Cheap model at session end** | **1 call/conversation** | **best — sees the whole exchange, so it can tell a passing remark from a commitment** |

Session-end extraction is the only economical option, and it is also the only
one with enough context to be accurate. Note the tension with rule #4: nothing
runs the agent on a schedule. A session-idle trigger is event-driven rather
than cron'd, and a Haiku-class extractor is arguably not "the agent" — but that
is a distinction to make **deliberately and in writing**, not to arrive at by
drift.

### Automatic writes must not land in the vault

The vault has its own `SCHEMA.md`, its own linter, and an explicit invariant
that a filed capture is indistinguishable from a hand-written page (`SKILL.md`,
"Vault write discipline"). Machine-extracted fragments at realistic precision
will destroy that invariant quickly, and the damage is not reversible by
reading a diff — it is thousands of plausible-looking pages.

Automatic captures need a **separate, lower-trust store** outside the linted
tree, with an explicit promotion path into the vault: on user confirmation, or
on a second independent observation of the same fact. The vault stays the
high-trust tier and is weighted accordingly at retrieval time.

### Dedup is the actual product

Extraction is the part that looks hard and isn't. Merging "I'm moving to Berlin
in March" with its four later restatements — and superseding it when the move
falls through — is what separates a memory system from a junk drawer. The
schema already has `status: superseded`; that is the right primitive and it
would carry most of the weight.

### Review surface

Pull-based, per rule #4: *"Hey memory, what did you file on your own this
week."* **Not a digest.** A scheduled daily summary was considered and rejected
(`decisions.md`, "Considered and not built") and every reason given there
applies with more force to a list of things the system did without being asked.

### Evaluation

`tests/gate-cases.txt` is already the right substrate, and the habit of adding
one line per real phrasing is already established. Extending it into a labelled
extraction corpus is the cheapest available quality lever, and it should exist
*before* the extractor does.

### Cost and risk

A few weekends to something that runs, then a long tail of precision tuning.
The risk is asymmetric and worth stating plainly: **a wrong auto-memory does
not merely fail, it gets restated to the user as fact.** Trust erodes faster
than it accumulates, and a store that has lost the user's trust is worse than
no store, because it still costs tokens on every turn.

---

## 4. Sharing memory across every agent

This promotes memory from a Hermes skill to a **service**, and adds four
requirements that a single-agent design never has to face.

- **An MCP server** (`search`, `write`, `supersede`) is the lingua franca — one
  server reaches Claude Code, Claude Desktop, and most MCP-capable clients
  without bespoke per-client wiring. Hermes keeps its hook for *automatic*
  injection; everything else gets tool-call recall, where the agent asks when
  it judges it needs to. That is cheaper and degrades gracefully.
- **Auto-injection generalises worse than recall.** Injecting into context
  requires a hook in each client's loop. Claude Code has one
  (`UserPromptSubmit`); most clients have none. Design for tool-based recall as
  the baseline and treat hook-based injection as a per-client upgrade, not an
  assumption.
- **Provenance and scoping become mandatory, and they are a safety property.**
  Which agent wrote this, in which session, under what scope. With one agent an
  unscoped store is merely noisy. With several, a fact a coding agent extracted
  surfaces in the middle of an unrelated personal conversation.
- **Concurrent writes break the current write path.** `git -C <vault> pull
  --ff-only` followed by a push works because there is exactly one writer
  moving at human pace. Several agents writing concurrently turns that into a
  contention bug of the kind this repo has been bitten by before. The fix is a
  single writer that serialises commits — which in practice means the database
  becomes the write path and the vault becomes a rendered view of it, or every
  writer queues behind one service.

---

## 5. Scale

Two different questions, and the boring one is the one people worry about.

**Volume is a non-issue.** Twenty durable facts a day is ~7k/year, ~50k after
seven years. FTS5 is unbothered by that; 50k embeddings at 768 dimensions is
~150MB and brute-force cosine is milliseconds. **Do not build a vector database
for this.** Personal scale never reaches the regime where the infrastructure
question is interesting.

**Signal-to-noise is the real wall.** At 200 memories everything works and
feels like magic. At 20,000 — most of them auto-captured near-restatements —
top-k retrieval starts returning plausible-but-stale context and the agent uses
it confidently. This degrades with **write volume, not with time**, which is
precisely why automatic capture is the risky half, and why dedup, supersession,
and archival of never-retrieved memories are load-bearing rather than
housekeeping.

**Token cost is the recurring line item.** Roughly a thousand injected tokens
per turn, across every agent, every day, indefinitely. That is the budget
question — not storage, and not the extraction calls.

---

## 6. Build order

1. **Automatic recall first.** Best payoff-to-risk ratio, deterministic, and
   reversible with one threshold.
2. **Live with it for a month.** Learn what "relevant" means for this corpus
   before growing the corpus automatically.
3. **Then automatic capture, quarantined.** Session-end extraction into a
   separate store; nothing reaches the vault without promotion.
4. **Cross-agent last** — but design the write interface before there are two
   writers, not after.

Stated in one line: **recall is a genuine extension of this system; capture is
a new system that borrows its storage.**

---

## 7. Build vs. adopt: Second Brain (thesecondbrain.dev)

**Evaluated 2026-09-11. Not decided.** Recorded here because it is the closest
existing thing to section 4, and because the reason to reject it — if it is
rejected — is not obvious from its feature list.

`github.com/rahilp/second-brain-cloudflare`, MIT, ~755 stars, v3.0.0, actively
developed by a single maintainer. Self-hosted in the user's **own** Cloudflare
account (Workers + D1 + Vectorize + Workers AI), so the vendor holds no data.
Exposes an MCP server (`remember`, `recall`, `append`, `update`, `forget`,
`set_status`, `link`/`unlink`, `share`, `get_prompt_capsule`), reaching Claude
Desktop, Claude Code, ChatGPT, Cursor, Codex and anything else that speaks MCP.
Semantic recall via Vectorize (384-dim, cosine) with keyword fallback when
Vectorize is down.

**Capture comes in four kinds, and they are easy to conflate** (verified
2026-09-11 against the wiki and the integration README, not against source):

1. **Human-triggered, per item** — browser extension, bookmarklet, CLI
   (`brain remember "..."`), iOS Shortcuts.
2. **Scheduled mirrors, no human and no model** — Notion mirrors shared pages
   and keeps them synced; email syncs hourly, deduped by `Message-ID`;
   calendar live-mirrors the next 30 days.
3. **Model judgment, driven by a shipped instruction file**
   (`AI_Instructions/CLAUDE_INSTRUCTIONS.md`, pasted into a client's rules). It
   tells the model to "Store EVERYTHING important automatically", to "Store
   important content from YOUR OWN responses too", and — the line that matters
   — "Never ask permission to store — store silently and keep going." In any
   client with those rules installed, the risk is **over**-capture, not under.
4. **Claude Code hooks — deterministic and unattended, but narrow.**
   `SessionStart` (startup/clear/compact) injects up to 5 recalled memories
   once per session, caching the block and reprinting it on compaction.
   `SessionEnd` POSTs the **raw transcript, not a summary**: the last 3 human
   turns read backwards from the end, capped at 2,000 characters, tool use and
   thinking stripped, credentials redacted, gated on a 40+ character human turn
   and 200+ total characters. Opt out with `SECOND_BRAIN_HOOK_RECALL=0` /
   `SECOND_BRAIN_HOOK_CAPTURE=0`.

**This is not section 3's design, and the difference is the whole quality
argument.** Section 3 proposes a cheap model distilling a whole conversation
into *facts* at session end. Second Brain does no extraction anywhere: the
hooks store a raw tail of the conversation, and the instruction file stores
model-chosen prose. So an adopted store fills with conversational fragments
rather than durable facts, and section 5's signal-to-noise wall arrives sooner.
Kind 3 is the sharper hazard: the model storing its own recommendations and
plans is speculation laundered into fact across sessions.

What it does deliver, cleanly: **automatic recall in Claude Code is real,
deterministic, and independent of all the capture messiness** — the whole of
section 4 and a shortcut past section 2 for roughly a day of setup.

**Token cost, for calibration.** Roughly 2–5k tokens of fixed prefix per
session (MCP tool schemas, the instruction file if installed, the 5-memory
recall block), largely absorbed by prompt caching, plus whatever `recall` and
`remember` the model chooses to call. Note this is **per session, not per
turn** — cheaper and less responsive than section 2's per-message injection.
The cost is therefore negligible for long sessions and proportionally large for
short ones, where each brief exchange pays the prefix again. A `Hey memory`
capture needs no recall injection at all; the existing gate already knows the
difference.

**The structural catch, which is the whole decision.** The Obsidian plugin is
**one-way: vault → Second Brain, chunked** (~1600 chars, 200 overlap). Notes
flow in. Nothing flows back. So:

- The **read** path does not split. Sync the vault in and every agent can
  recall over the real notes. The store becomes a derived index of the vault —
  the thing section 2 says is needed anyway, with a freshness problem rather
  than an authority problem.
- The **write** path splits, permanently. Anything an agent remembers via
  `remember` lives in D1 and Vectorize and never becomes a wiki page. It has no
  `SCHEMA.md` shape, no index entry, no backlinks, no git history. Within
  months there are two stores with different authority, different lifecycle
  rules, and no merge path — and the vault, which is the curated one, is the
  one that stops growing.

**The mitigation that makes adoption viable** is to refuse the split by
policy rather than accept it: **Second Brain is a derived read layer, never a
system of record.** Vault syncs in one-way; every durable write still goes
through this system into the vault; agent `remember` calls are treated as a
scratch tier that is either promoted into the vault or allowed to expire. That
buys the cross-agent reach from section 4 without surrendering the vault's
position — but it requires enforcing a discipline the tool does not enforce
itself, since `remember` is right there and will be used.

**What adopting costs regardless:** a Cloudflare dependency and account-level
outage surface this system does not currently have; content chunked into a
managed store instead of whole notes in git; a single-maintainer upstream on a
young codebase; an embedding model that reads English best; and Vectorize as a
component whose degradation (semantic → keyword) is quiet.

**What building instead costs:** sections 2 through 4, in full, against the
reality that the cross-agent half is genuinely a lot of work — an MCP server,
per-client integration, scoping, provenance, and the concurrent-write
serialisation problem — for a result that would be roughly what already exists,
MIT-licensed, on a free tier.

**The honest framing:** this is not build-vs-buy. It is *whether the vault
stays the system of record*. If it does, Second Brain is a strong answer to
section 4 and a shortcut past most of section 2 — adopted as an index, not as a
brain. If it doesn't, this repo's central design premise is what is actually
being traded away, and that is a decision worth making explicitly rather than
discovering a year in, when half the memory is in Cloudflare and the wiki has
gone quiet.
