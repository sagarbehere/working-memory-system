#!/usr/bin/env python3
"""Unit tests for the reminder store (reminders.py). No network.

The headline test is test_concurrent_writer_is_not_lost: it reproduces the
exact interleaving that used to destroy a capture — a slow holder doing a
read-modify-write (the cron tick, whose critical section spans Todoist
calls) while a capture lands in the middle.

Run: python3 tests/test_reminders.py   (from the package dir)
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
import reminders as rem  # noqa: E402
import wmlib  # noqa: E402

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def _root():
    td = tempfile.mkdtemp(prefix="wm-rem-test-")
    (pathlib.Path(td) / "meta").mkdir(parents=True, exist_ok=True)
    return td


def test_add_and_shape():
    root = _root()
    e = rem.add(root, "call plumber", "2026-08-30T08:00:00+05:30",
                raw_entry_id="20260829-1600-01",
                origin={"platform": "telegram", "chat_id": "111", "thread_id": ""},
                mirror=False)
    check(e["status"] == "pending", "new reminder is pending")
    check(e["mirrored"] is False and e["todoist_id"] is None, "unmirrored by default")
    check(e["raw_entry_id"] == "20260829-1600-01", "raw_entry_id round-trips")
    check(e["origin"]["chat_id"] == "111", "origin recorded")
    check(e["created_at"].endswith(("+05:30", "Z")) or "+" in e["created_at"]
          or "-" in e["created_at"][10:], "created_at carries an offset")
    check(len(rem.load(root)) == 1, "persisted")

    ids = {rem.add(root, f"m{i}", "2026-08-30T08:00:00+05:30", mirror=False)["id"]
           for i in range(5)}
    check(len(ids) == 5, "ids are unique within the same minute")

    try:
        rem.add(root, "  ", "2026-08-30T08:00:00+05:30", mirror=False)
        check(False, "empty message rejected")
    except ValueError:
        check(True, "empty message rejected")
    try:
        rem.add(root, "x", "whenever", mirror=False)
        check(False, "bad due_at rejected")
    except ValueError:
        check(True, "bad due_at rejected")


def test_due_now_excludes_mirrored():
    """A mirrored reminder is Todoist's to notify; firing locally double-notifies."""
    root = _root()
    a = rem.add(root, "local", "2026-01-01T00:00:00+00:00", mirror=False)
    b = rem.add(root, "mirrored", "2026-01-01T00:00:00+00:00", mirror=False)
    c = rem.add(root, "future", "2099-01-01T00:00:00+00:00", mirror=False)
    rem.set_status(root, b["id"], "pending", mirrored=True, todoist_id="99")

    due = [r["message"] for r in rem.due_now(root)]
    check(due == ["local"], f"only unmirrored past-due fires (got {due})")

    rem.set_status(root, a["id"], "fired", fired_at=wmlib.iso())
    check(rem.due_now(root) == [], "fired reminders do not re-fire")
    check(c["id"] not in [r["id"] for r in rem.due_now(root)], "future not due")


def test_status_transitions():
    root = _root()
    e = rem.add(root, "x", "2026-08-30T08:00:00+05:30", mirror=False)
    rem.set_status(root, e["id"], "done", completed_at=wmlib.iso())
    check(rem.load(root)[0]["status"] == "done", "done recorded")
    try:
        rem.set_status(root, "no-such-id", "done")
        check(False, "unknown id rejected")
    except ValueError:
        check(True, "unknown id rejected")
    try:
        rem.set_status(root, e["id"], "banana")
        check(False, "unknown status rejected")
    except ValueError:
        check(True, "unknown status rejected")


def test_corrupt_store_is_loud():
    """A malformed store must raise, not silently look empty and get overwritten."""
    root = _root()
    rem.store_path(root).write_text('{"not": "a list"}')
    try:
        rem.load(root)
        check(False, "non-list store rejected")
    except ValueError:
        check(True, "non-list store rejected")


def test_concurrent_writer_is_not_lost():
    """THE regression test for the lost-update race.

    Holder: takes the lock, reads, waits (standing in for the tick's Todoist
    calls), appends its own entry, writes back.
    Capture: runs `reminders.py add` while the holder is mid-transaction.

    Without shared locking the holder's stale write-back erases the capture.
    Both entries must survive, in either order.
    """
    root = _root()
    rem.add(root, "pre-existing", "2026-08-30T08:00:00+05:30", mirror=False)

    holder_src = f"""
import sys, time, json
sys.path.insert(0, {str(PKG)!r})
import reminders as rem, wmlib
with wmlib.FileLock(rem.lock_path({root!r})):
    data = rem.load({root!r})
    time.sleep(1.5)                       # the tick's network window
    data.append({{"id": "holder-01", "due_at": "2026-08-30T08:00:00+05:30",
                 "message": "from holder", "status": "pending",
                 "mirrored": False, "todoist_id": None}})
    rem.save({root!r}, data)
print("HOLDER DONE")
"""
    holder = subprocess.Popen([sys.executable, "-c", holder_src],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.4)  # let the holder take the lock and read
    t0 = time.monotonic()
    capture = subprocess.run(
        [sys.executable, str(PKG / "reminders.py"), "--root", root, "add",
         "--message", "from capture", "--due-at", "2026-08-30T09:00:00+05:30",
         "--no-mirror"],
        capture_output=True, text=True)
    waited = time.monotonic() - t0
    holder.wait(timeout=30)

    check(capture.returncode == 0, f"capture succeeded ({capture.stderr.strip()})")
    check(waited > 0.5, f"capture actually waited for the lock ({waited:.2f}s)")

    messages = [r["message"] for r in rem.load(root)]
    check("from holder" in messages, f"holder's write survived (got {messages})")
    check("from capture" in messages,
          f"CAPTURE WAS NOT LOST — the original bug (got {messages})")
    check("pre-existing" in messages, "pre-existing entry survived")
    check(len(messages) == 3, f"exactly three entries (got {messages})")


def test_cli_round_trip():
    root = _root()
    r = subprocess.run(
        [sys.executable, str(PKG / "reminders.py"), "--root", root, "add",
         "--message", "cli entry", "--due-at", "2026-08-30T08:00:00+05:30",
         "--no-mirror"], capture_output=True, text=True)
    check(r.returncode == 0, f"cli add ok ({r.stderr})")
    entry = json.loads(r.stdout)
    check(entry["message"] == "cli entry", "cli add prints the entry")

    r = subprocess.run([sys.executable, str(PKG / "reminders.py"), "--root", root,
                        "list"], capture_output=True, text=True)
    check(json.loads(r.stdout.strip())["id"] == entry["id"], "cli list shows it")

    r = subprocess.run([sys.executable, str(PKG / "reminders.py"), "--root", root,
                        "done", "--id", entry["id"]], capture_output=True, text=True)
    check(json.loads(r.stdout)["status"] == "done", "cli done marks done")

    r = subprocess.run([sys.executable, str(PKG / "reminders.py"), "--root", root,
                        "list"], capture_output=True, text=True)
    check(r.stdout.strip() == "", "done entry drops out of the pending list")

    r = subprocess.run([sys.executable, str(PKG / "reminders.py"), "--root", root,
                        "add", "--message", "x", "--due-at", "nope"],
                       capture_output=True, text=True)
    check(r.returncode == 2 and "Traceback" not in r.stderr,
          "bad input is a clean exit 2, not a traceback")


def main():
    test_add_and_shape()
    test_due_now_excludes_mirrored()
    test_status_transitions()
    test_corrupt_store_is_loud()
    test_cli_round_trip()
    test_concurrent_writer_is_not_lost()
    print(f"ALL REMINDER STORE TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
