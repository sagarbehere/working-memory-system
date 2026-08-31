#!/usr/bin/env python3
"""Guards on how many Todoist API calls the system makes.

Rate limits are the concern, and the failure mode is a blocked account rather
than a broken test, so the counts are asserted rather than reasoned about.

Since the 2026-08-29 cut there is no polling loop at all: nothing calls
Todoist unless the agent is acting on a reminder. The budget is therefore
proportional to use, not to time.

Works by putting a fake `curl` first on PATH — todoist.py shells out to curl
rather than using urllib (Cloudflare resets urllib's TLS), so this intercepts
every request without touching the network.

Run: python3 tests/test_todoist_budget.py   (from the package dir)
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

PKG = pathlib.Path(__file__).resolve().parents[1]

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


FAKE_CURL = r"""#!/bin/bash
url=""; meth="GET"; nxt=0
for a in "$@"; do
  if [ $nxt = 1 ]; then meth="$a"; nxt=0; fi
  case "$a" in -X) nxt=1;; https://*) url="$a";; esac
done
echo "$meth ${url##*/api/v1/}" >> "$CALL_LOG"
case "$meth $url" in
  *"/projects"*) [ "$meth" = POST ] && echo '{"id":"P1","name":"Hermes"}' || echo '{"results":[{"id":"P1","name":"Hermes"}]}';;
  "POST "*"/tasks") echo '{"id":"T77","content":"x","due":null,"completed_at":null}';;
  *"/tasks"*) echo '{"results":[{"id":"T77","content":"x"}]}';;
  *) echo '{}';;
esac
"""


def _fixture():
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-todoist-budget-"))
    (td / "bin").mkdir()
    (td / "wm" / "meta").mkdir(parents=True)
    (td / "hermes").mkdir()
    curl = td / "bin" / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    (td / "hermes" / "working-memory.env").write_text(
        f"WM_ROOT={td / 'wm'}\nTODOIST_MIRROR_ENABLED=true\nTODOIST_PROJECT=Hermes\n")
    (td / "hermes" / ".env").write_text("TODOIST_API_TOKEN=faketoken\n")
    return td


def _env(td):
    e = dict(os.environ)
    e["PATH"] = f"{td / 'bin'}{os.pathsep}{e['PATH']}"
    e["HERMES_HOME"] = str(td / "hermes")
    e["CALL_LOG"] = str(td / "calls.log")
    e.pop("WM_ROOT", None)
    return e


def _run(td, *args):
    (td / "calls.log").write_text("")
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       env=_env(td), cwd=str(PKG))
    calls = [l for l in (td / "calls.log").read_text().splitlines() if l.strip()]
    return r, calls


def test_reminder_cost():
    """Creating a reminder costs 2 calls cold, 1 warm — project id is cached."""
    td = _fixture()
    r, calls = _run(td, str(PKG / "todoist.py"), "create", "--content", "a",
                    "--due", "2026-08-30T09:00:00+05:30")
    check(r.returncode == 0, f"create ok ({r.stderr})")
    check(calls == ["GET projects", "POST tasks"], f"cold create (got {calls})")

    r, calls = _run(td, str(PKG / "todoist.py"), "create", "--content", "b",
                    "--due", "2026-08-30T10:00:00+05:30")
    check(calls == ["POST tasks"],
          f"warm create makes ONE call — project id cached (got {calls})")
    check(json.loads(r.stdout)["id"], "and still returns the task")


def test_read_costs():
    """Retrieval paths the agent uses on every 'what's due' question."""
    td = _fixture()
    _r, calls = _run(td, str(PKG / "todoist.py"), "list")
    check(calls == ["GET projects", "GET tasks"], f"list is 2 calls (got {calls})")


def test_nothing_happens_without_an_action():
    """Loading the client costs nothing; calls happen only when asked for.

    Until the 2026-08-31 cut this ran `rawlog.py add` — the one capture path
    that was a CLI in this package — to prove a non-reminder capture cost zero
    Todoist calls. Captures no longer go through any CLI here (they are vault
    writes made by the agent), so there is nothing left to invoke for that.

    What remains checkable is the invariant most likely to be broken by an
    edit: that merely starting todoist.py does no work. A project-id lookup or
    a cache warm at import or parse time would be invisible in normal use and
    would put the budget back on a per-invocation footing.
    """
    td = _fixture()
    _r, calls = _run(td, str(PKG / "todoist.py"), "--help")
    check(calls == [], f"starting the client makes NO API calls (got {calls})")


def test_disabled_makes_no_calls():
    td = _fixture()
    (td / "hermes" / ".env").write_text("")           # no token at all
    r, calls = _run(td, str(PKG / "todoist.py"), "list")
    check(calls == [], f"unconfigured Todoist makes no calls (got {calls})")
    check(r.returncode == 2, "and exits 2 (not configured), distinct from a failure")


def main():
    test_reminder_cost()
    test_read_costs()
    test_nothing_happens_without_an_action()
    test_disabled_makes_no_calls()
    print(f"ALL TODOIST BUDGET TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
