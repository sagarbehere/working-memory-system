#!/usr/bin/env python3
"""Tests for the consolidation gate's log scanning and health checks.

These paths had no coverage: last_consolidation_ts, health_issues and the
timezone handling were all untested, and the gate's whole value rests on
being silent when there is nothing to do.

Run: python3 tests/test_gate_health.py   (from the package dir)
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

spec = importlib.util.spec_from_file_location("wm_gate", PKG / "wm-consolidation-gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def _root():
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-gate-test-"))
    (td / "logs").mkdir()
    (td / "raw").mkdir()
    (td / "meta").mkdir()
    return td


def _log_lines(root, *entries):
    with (root / "logs" / "2026-08.log").open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_log_scan():
    root = _root()
    _log_lines(root,
               {"ts": "2026-08-20T01:00:00+05:30", "component": "consolidation",
                "event": "consolidation", "outcome": "ok"},
               {"ts": "2026-08-22T01:00:00+05:30", "component": "consolidation",
                "event": "consolidation", "outcome": "ok"},
               {"ts": "2026-08-21T01:00:00+05:30", "component": "other",
                "event": "consolidation", "outcome": "ok"})
    entries = gate.read_log_entries(str(root))
    check(len(entries) == 3, f"all lines read once (got {len(entries)})")
    since = gate.last_consolidation_ts(entries)
    check(since is not None and since.day == 22, "latest consolidation wins")
    check(since.tzinfo is not None, "timestamp is aware")

    # Malformed lines must not abort the scan.
    with (root / "logs" / "2026-08.log").open("a") as f:
        f.write("not json\n\n")
    check(len(gate.read_log_entries(str(root))) == 3, "garbage lines skipped")
    check(gate.read_log_entries(str(_root())) == [], "empty logs dir -> []")


def test_health_only_counts_since():
    root = _root()
    _log_lines(root,
               {"ts": "2026-08-20T01:00:00+05:30", "component": "consolidation",
                "event": "consolidation", "outcome": "ok"},
               {"ts": "2026-08-19T01:00:00+05:30", "component": "reminder-cron",
                "event": "fire", "outcome": "failed"},
               {"ts": "2026-08-21T01:00:00+05:30", "component": "reminder-cron",
                "event": "fire", "outcome": "failed"},
               {"ts": "2026-08-21T02:00:00+05:30", "component": "todoist",
                "event": "create", "outcome": "failed"})
    entries = gate.read_log_entries(str(root))
    since = gate.last_consolidation_ts(entries)
    issues = gate.health_issues(str(root), since, entries)
    joined = " | ".join(issues)
    check("reminder-cron fire: 1 failure" in joined,
          f"only failures after the last consolidation count (got {joined})")
    check("todoist create: 1 failure" in joined, "todoist failures surface")
    check(len(issues) == 2, f"nothing else reported (got {issues})")

    check(gate.health_issues(str(_root()), None, []) == [],
          "healthy system reports nothing")


def test_quiet_night_is_silent():
    """The whole point of the gate: no work -> no output -> no agent session."""
    root = _root()
    hermes = pathlib.Path(tempfile.mkdtemp())
    (hermes / "working-memory.env").write_text(f"WM_ROOT={root}\n")
    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)
    r = subprocess.run([sys.executable, str(PKG / "wm-consolidation-gate.py")],
                       capture_output=True, text=True, env=env)
    check(r.returncode == 0, "gate exits 0")
    check(r.stdout.strip() == "", f"quiet night prints nothing (got {r.stdout!r})")

    # A new raw entry is work, so the gate must speak.
    (root / "raw" / "2026-08.md").write_text(
        "## 2026-08-28T10:00:00+05:30 [id: 20260828-1000-01]\ntext\n")
    r = subprocess.run([sys.executable, str(PKG / "wm-consolidation-gate.py")],
                       capture_output=True, text=True, env=env)
    check("1 new raw entry" in r.stdout, f"work is reported (got {r.stdout!r})")


def test_no_hardcoded_timezone():
    """The gate used to bake in UTC+05:30 regardless of the machine."""
    src = (PKG / "wm-consolidation-gate.py").read_text()
    check("hours=5, minutes=30" not in src, "no hardcoded +05:30 offset remains")
    os.environ["WM_TZ"] = "America/New_York"
    try:
        dt = gate.parse_dt("2026-08-20T09:00:00")
        check(str(dt.utcoffset()) in ("-1 day, 20:00:00", "-4:00:00"),
              f"naive stamps use the configured zone (got {dt})")
    finally:
        os.environ.pop("WM_TZ", None)


def main():
    test_log_scan()
    test_health_only_counts_since()
    test_quiet_night_is_silent()
    test_no_hardcoded_timezone()
    print(f"ALL GATE HEALTH TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
