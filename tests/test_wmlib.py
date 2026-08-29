#!/usr/bin/env python3
"""Unit tests for wmlib — env parsing, timezone handling, locking.

Run: python3 tests/test_wmlib.py   (from the package dir)
"""
import datetime as _dt
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import wmlib  # noqa: E402

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def test_env_quoting():
    """Quoted and bare values must resolve identically.

    Only the consolidation gate used to strip quotes, so WM_ROOT="~/wm"
    sent the gate and the capture path to different directories.
    """
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "e.env"
        p.write_text(
            'WM_ROOT="~/wm"\n'
            "WM_DEBOUNCE_SECONDS='7'\n"
            "BARE=~/wm\n"
            "# comment=ignored\n"
            "\n"
            "EMPTY=\n"
            "WITH_EQUALS=a=b\n"
            "  SPACED  =  x  \n"
        )
        env = wmlib.load_env_file(p)
        check(env["WM_ROOT"] == "~/wm", "double quotes stripped")
        check(env["WM_DEBOUNCE_SECONDS"] == "7", "single quotes stripped")
        check(env["BARE"] == "~/wm", "bare value unchanged")
        check(env["WM_ROOT"] == env["BARE"], "quoted == bare")
        check("comment" not in env, "comments skipped")
        check(env["EMPTY"] == "", "empty value kept")
        check(env["WITH_EQUALS"] == "a=b", "only first = splits")
        check(env["SPACED"] == "x", "key and value trimmed")
    check(wmlib.load_env_file("/nonexistent/x.env") == {}, "missing file -> {}")


def test_time():
    check(wmlib.now().tzinfo is not None, "now() is aware")
    naive = wmlib.parse_iso("2026-08-20T09:00:00")
    check(naive is not None and naive.tzinfo is not None, "naive input made aware")
    check(wmlib.parse_iso("not-a-date") is None, "garbage -> None")
    check(wmlib.parse_iso(None) is None, "None -> None")

    os.environ["WM_TZ"] = "Asia/Kolkata"
    try:
        check(wmlib.now().utcoffset() == _dt.timedelta(hours=5, minutes=30),
              "WM_TZ honoured")
        os.environ["WM_TZ"] = "Not/AZone"
        check(wmlib.now().tzinfo is not None, "bad WM_TZ degrades, does not raise")
    finally:
        os.environ.pop("WM_TZ", None)


def test_display_is_never_utc():
    """Stored timestamps are UTC; displayed ones must be in the configured zone."""
    os.environ["WM_TZ"] = "Asia/Kolkata"
    try:
        check(wmlib.local_iso("2026-08-21T04:00:00+00:00") == "2026-08-21T09:30:00+05:30",
              "UTC input rendered in the configured zone")
        check(wmlib.local_iso("2026-08-20T02:00:00-05:00") == "2026-08-20T12:30:00+05:30",
              "another offset rendered in the configured zone")
        os.environ["WM_TZ"] = "America/New_York"
        check(wmlib.local_iso("2026-08-21T04:00:00+00:00").endswith("-04:00"),
              "follows WM_TZ when it changes")
        check(wmlib.local_iso("garbage") == "garbage",
              "unparseable input passes through rather than raising")
        check(wmlib.local_iso(None) is None, "None passes through")
    finally:
        os.environ.pop("WM_TZ", None)



def test_lock_excludes():
    """The lock must actually block a second holder."""
    with tempfile.TemporaryDirectory() as td:
        lock = pathlib.Path(td) / "l.lock"
        with wmlib.FileLock(lock):
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sys;sys.path.insert(0,%r);import wmlib;"
                 "\nfrom wmlib import FileLock, LockBusy\n"
                 "try:\n"
                 "    with FileLock(%r, blocking=False): print('ACQUIRED')\n"
                 "except LockBusy: print('BUSY')"
                 % (str(pathlib.Path(__file__).resolve().parents[1]), str(lock))],
                capture_output=True, text=True)
            check("BUSY" in probe.stdout,
                  f"second holder blocked while lock held (got {probe.stdout!r})")
        after = subprocess.run(
            [sys.executable, "-c",
             "import sys;sys.path.insert(0,%r)\n"
             "from wmlib import FileLock, LockBusy\n"
             "try:\n"
             "    with FileLock(%r, blocking=False): print('ACQUIRED')\n"
             "except LockBusy: print('BUSY')"
             % (str(pathlib.Path(__file__).resolve().parents[1]), str(lock))],
            capture_output=True, text=True)
        check("ACQUIRED" in after.stdout, "lock released on context exit")


def main():
    test_env_quoting()
    test_time()
    test_display_is_never_utc()
    test_lock_excludes()
    print(f"ALL WMLIB TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
