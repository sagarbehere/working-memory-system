# Scenario checklist — the manual half

`tests/run_all.py` proves the deterministic layer works: the gate sees the
right messages, the Todoist client stays inside its call budget, the
watchdogs stay quiet. It says
**nothing** about whether the agent then classified and filed the thing
sensibly, because that is an LLM judgment and there is no cheap deterministic
test for it.

This file is that half. Walk it after a change that touches `SKILL.md`, the
hook, or a CLI — fifteen minutes, in your real Telegram lane, against your
real vault. It is deliberately not automated: at one user's volume you are the
integration test, and a hand-run checklist costs nothing to keep honest.

**Start a new conversation first.** The skill is injected when a session is
created, so a lane you have had open for days is still running the `SKILL.md`
it started with — `/reload-skills` does not change that. Walking this list in
an old session tests the old policy and tells you nothing about the change you
just made.

**How to use it.** Say the left column, check the right. Anything that
disagrees is either a skill bug (fix the policy), a tooling gap (fix the CLI —
better, because it makes the mistake impossible), or a wrong expectation
(fix this file, and note why).

**When something surprises you:** add a row here. If the surprise was about
*whether the message was seen at all*, add a line to `gate-cases.txt` instead
— that one is automated.

---

## Capture and routing

| Say | Expect |
|---|---|
| `printer is out of ink` *(in the lane)* | Filed. Confirmation starts `✅ →` and names the destination. |
| `Hey memory printer is out of ink` *(outside the lane)* | Same, and the marker is **not** part of the stored text. |
| `remind me Tuesday at 8am to call the plumber` | A Todoist task, due Tue 08:00 **in your timezone**. Confirmation says Todoist. |
| `BP 128/82 this morning` | Appended as ONE line to the blood-pressure series note — not a new note per reading. |
| `BP 131/84` *(next day)* | Appended to the **same** note, same line format. |
| `headache today, slept badly, took one ibuprofen` | Appended to the headaches series note. |
| `renew the passport before the trip in March` | A project note, `status: active`. |
| `the plumber's number is 555-0134` | A reference note (entity). |
| `what if the balcony became a reading nook` | An idea note. |
| `buy stamps` | A Todoist task only — no vault note. |
| Three thoughts sent in quick succession | **One** agent turn covering all three, not three. (This is the debounce; if it fires three times, the hook is broken.) |
| The exact same message twice within a minute | Filed once. The second is reported with `↩︎`, **not** `✅ →`. |

## Retrieval

| Ask | Expect |
|---|---|
| `Hey memory what's due this week?` | Answered from Todoist, soonest first. |
| `Hey memory what did I decide about the printer?` | Answered from the vault note. |
| `Hey memory what's my BP been doing?` | Reads the series note and **summarises the trend** — it should reason over the numbers, not list them. |
| `Hey memory do my headaches follow bad sleep?` | A correlational answer across the series note. This is the case a database could not have answered; if it just quotes lines, the skill is under-using the file. |
| `Hey memory did I ever mention the taxi driver?` | Searches the vault; if it is not there, falls back to `session_search` over the chat history and **says which one answered**. Since 2026-08-31 this is the only path to something never filed, and it is the one behaviour here that depends on a store this repo does not own. |
| `Hey memory what did I finish last month?` | Todoist completion history, grouped sensibly. |

## Corrections and commands

| Say | Expect |
|---|---|
| `that should have been a project, not an idea` | Re-routed; the old note is gone or updated, not duplicated. |
| Ask for something the wiki already covers | `↩︎ already filed → …`, naming the existing page or line. A `✅ →` here is a bug: it claims a write that did not happen. |
| `mark the plumber task done` | Closed in Todoist. |
| `clear the completed items off the X checklist` | Only ticked items go. The log entry names each one **and carries any detail the line held** — the line is gone, so the log is the record. Ticked items are never cleared unasked. |
| `forget what I said about the taxi driver` | **Confirms first.** Then removes the note/task it filed. Does not claim every trace is gone — the original message is still in the chat history. |
| `reserve for memory` *(in a new chat)* | Confirms; that chat now captures without a marker. |
| `release for memory` | Confirms; the chat returns to ordinary conversation. |

## Things that must NOT happen

| Say | Expect |
|---|---|
| `what's the weather tomorrow` *(outside the lane)* | Answered normally. **Nothing filed.** |
| `Note that the deadline moved to Friday` | Answered normally. Nothing filed — `note` is not a marker. |
| `thanks, that worked` *(in the lane)* | Recognised as chit-chat. Nothing filed. |
| Any capture | No approval prompts. If the agent asks to run inline `python3`, a tool is missing — that is the bug, not the prompt. |
| `Hey, memory, remind me…` *(comma inside the marker, outside the lane)* | Captured. If the agent instead answers conversationally and has to fetch the skill itself, the gate missed it. |

## Health

| Check | Expect |
|---|---|
| Wait for the nightly watchdog | Silence. Any output is a real problem: an unpushed vault commit, a failed vault pull, or something that failed quietly in the last day. It backs nothing up, so there is no push to verify. |
| `cd ~/working-memory && git log --oneline -3` | Recent commits; nothing uncommitted for long. |
| `cd <vault> && git status` | Clean and pushed — a local-only commit is not backed up. |
