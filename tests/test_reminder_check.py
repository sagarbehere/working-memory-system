#!/usr/bin/env python3
"""Tests for the reminder-check tick (stdout mode — no Telegram, no network).

Run: python3 tests/test_reminder_check.py   (from the package dir)
"""
import pathlib
import subprocess
import sys
import tempfile
import time

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
import reminders as rem  # noqa: E402

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def _fixture():
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-tick-test-"))
    root, hermes = td / "wm", td / "hermes"
    (root / "meta").mkdir(parents=True)
    hermes.mkdir()
    (hermes / "working-memory.env").write_text(f"WM_ROOT={root}\n")
    (hermes / ".env").write_text("")  # no bot token -> stdout mode
    return root, hermes


def _tick(hermes, root):
    import os
    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)
    return subprocess.run([sys.executable, str(PKG / "reminder-check.py")],
                          capture_output=True, text=True, env=env)


def test_fires_due_only_once():
    root, hermes = _fixture()
    rem.add(root, "past due", "2020-01-01T09:00:00+00:00", mirror=False)
    rem.add(root, "not yet", "2099-01-01T09:00:00+00:00", mirror=False)

    r = _tick(hermes, root)
    check(r.returncode == 0, f"tick exits 0 ({r.stderr})")
    check("past due" in r.stdout, f"due reminder delivered on stdout ({r.stdout!r})")
    check("not yet" not in r.stdout, "future reminder not delivered")

    statuses = {x["message"]: x["status"] for x in rem.load(root)}
    check(statuses["past due"] == "fired", "fired entry marked")
    check(statuses["not yet"] == "pending", "future entry still pending")

    # The critical property: a second tick must not re-deliver it.
    r2 = _tick(hermes, root)
    check("past due" not in r2.stdout, "a fired reminder never fires again")
    check(r2.stdout.strip() == "", f"quiet tick is silent on stdout ({r2.stdout!r})")


def test_mirrored_is_not_fired_locally():
    """Todoist owns the notification for a mirrored reminder."""
    root, hermes = _fixture()
    e = rem.add(root, "mirrored one", "2020-01-01T09:00:00+00:00", mirror=False)
    rem.set_status(root, e["id"], "pending", mirrored=True, todoist_id="123")

    r = _tick(hermes, root)
    check("mirrored one" not in r.stdout, "mirrored reminder is not fired locally")
    check(rem.load(root)[0]["status"] == "pending",
          "mirrored reminder stays pending for Todoist to complete")


def test_single_flight():
    """Two overlapping ticks must not both deliver the same reminder."""
    root, hermes = _fixture()
    rem.add(root, "only once", "2020-01-01T09:00:00+00:00", mirror=False)

    import os
    import wmlib
    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)

    # Hold the tick lock, then run a tick: it must decline, not duplicate.
    with wmlib.FileLock(root / "meta" / "reminder-check.lock"):
        r = subprocess.run([sys.executable, str(PKG / "reminder-check.py")],
                           capture_output=True, text=True, env=env)
    check(r.returncode == 0, "blocked tick still exits 0 (cron-friendly)")
    check("only once" not in r.stdout, "blocked tick delivers nothing")
    check("another tick in progress" in r.stderr, "blocked tick says why")
    check(rem.load(root)[0]["status"] == "pending", "reminder untouched, fires next time")

    r = subprocess.run([sys.executable, str(PKG / "reminder-check.py")],
                       capture_output=True, text=True, env=env)
    check("only once" in r.stdout, "it does fire once the lock is free")


def test_no_store_is_harmless():
    root, hermes = _fixture()
    r = _tick(hermes, root)
    check(r.returncode == 0, "missing reminders.json is not an error")
    check(r.stdout.strip() == "", "and prints nothing")


def main():
    test_fires_due_only_once()
    test_mirrored_is_not_fired_locally()
    test_single_flight()
    test_no_store_is_harmless()
    print(f"ALL REMINDER-CHECK TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
