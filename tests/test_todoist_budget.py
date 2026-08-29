#!/usr/bin/env python3
"""Guards on how many Todoist API calls the system makes.

Rate limits are the concern: an unnoticed extra call in the 5-minute tick is
288 extra requests a day, and the failure mode is a blocked account rather
than a broken test. So the counts are asserted, not just reasoned about.

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


def _fixture(reconcile_minutes=None):
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-todoist-budget-"))
    (td / "bin").mkdir()
    (td / "wm" / "meta").mkdir(parents=True)
    (td / "hermes").mkdir()
    curl = td / "bin" / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    env_lines = [f"WM_ROOT={td / 'wm'}", "TODOIST_MIRROR_ENABLED=true",
                 "TODOIST_PROJECT=Hermes"]
    if reconcile_minutes is not None:
        env_lines.append(f"TODOIST_RECONCILE_MINUTES={reconcile_minutes}")
    (td / "hermes" / "working-memory.env").write_text("\n".join(env_lines) + "\n")
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


def test_capture_cost():
    """A capture costs 2 calls cold, 1 warm — the project id is cached."""
    td = _fixture()
    wm = str(td / "wm")
    r, calls = _run(td, str(PKG / "reminders.py"), "--root", wm, "add",
                    "--message", "a", "--due-at", "2026-08-30T09:00:00+05:30")
    check(r.returncode == 0, f"capture ok ({r.stderr})")
    check(calls == ["GET projects", "POST tasks"], f"cold capture (got {calls})")
    check(json.loads(r.stdout)["mirrored"] is True, "mirrored at capture time")

    r, calls = _run(td, str(PKG / "reminders.py"), "--root", wm, "add",
                    "--message", "b", "--due-at", "2026-08-30T10:00:00+05:30")
    check(calls == ["POST tasks"],
          f"warm capture makes ONE call — project id cached (got {calls})")


def test_tick_cost_and_rate_limit():
    """The tick costs at most 1 call, and only every TODOIST_RECONCILE_MINUTES."""
    td = _fixture(reconcile_minutes=30)
    wm = str(td / "wm")
    _run(td, str(PKG / "reminders.py"), "--root", wm, "add", "--message", "a",
         "--due-at", "2026-08-30T09:00:00+05:30")

    r, calls = _run(td, str(PKG / "reminder-check.py"))
    check(calls == ["GET tasks"],
          f"first tick reconciles with ONE list call (got {calls})")

    for n in (2, 3, 4):
        r, calls = _run(td, str(PKG / "reminder-check.py"))
        check(calls == [], f"tick {n} within the window makes NO calls (got {calls})")


def test_reconcile_window_can_be_disabled():
    td = _fixture(reconcile_minutes=0)  # 0 = every tick, the old behaviour
    wm = str(td / "wm")
    _run(td, str(PKG / "reminders.py"), "--root", wm, "add", "--message", "a",
         "--due-at", "2026-08-30T09:00:00+05:30")
    _run(td, str(PKG / "reminder-check.py"))
    r, calls = _run(td, str(PKG / "reminder-check.py"))
    check(calls == ["GET tasks"], f"0 minutes = reconcile every tick (got {calls})")


def test_idle_system_is_free():
    """Nothing pending means zero API calls, however often cron runs."""
    td = _fixture()
    r, calls = _run(td, str(PKG / "reminder-check.py"))
    check(calls == [], f"empty store makes no calls (got {calls})")

    wm = str(td / "wm")
    _run(td, str(PKG / "reminders.py"), "--root", wm, "add", "--message", "a",
         "--due-at", "2026-08-30T09:00:00+05:30")
    out = subprocess.run([sys.executable, str(PKG / "reminders.py"), "--root", wm,
                          "list"], capture_output=True, text=True, env=_env(td))
    rid = json.loads(out.stdout)["id"]
    _run(td, str(PKG / "reminders.py"), "--root", wm, "done", "--id", rid)
    _run(td, str(PKG / "reminder-check.py"))          # consumes the window
    r, calls = _run(td, str(PKG / "reminder-check.py"))
    check(calls == [], f"no pending mirrored reminders -> no calls (got {calls})")


def test_disabled_makes_no_calls():
    """With the mirror off, nothing reaches the API at all."""
    td = _fixture()
    (td / "hermes" / "working-memory.env").write_text(
        f"WM_ROOT={td / 'wm'}\nTODOIST_MIRROR_ENABLED=false\n")
    wm = str(td / "wm")
    r, calls = _run(td, str(PKG / "reminders.py"), "--root", wm, "add",
                    "--message", "a", "--due-at", "2026-08-30T09:00:00+05:30")
    check(calls == [], f"capture makes no calls when disabled (got {calls})")
    check(json.loads(r.stdout)["mirrored"] is False, "and is recorded unmirrored")
    r, calls = _run(td, str(PKG / "reminder-check.py"))
    check(calls == [], f"tick makes no calls when disabled (got {calls})")


def main():
    test_capture_cost()
    test_tick_cost_and_rate_limit()
    test_reconcile_window_can_be_disabled()
    test_idle_system_is_free()
    test_disabled_makes_no_calls()
    print(f"ALL TODOIST BUDGET TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
