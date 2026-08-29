#!/usr/bin/env python3
"""Unit tests for records.py — matching, timestamp normalisation, migration.

Run: python3 tests/test_records.py   (from the package dir)
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time

PKG = pathlib.Path(__file__).resolve().parents[1]
RECORDS = str(PKG / "records.py")

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def run(root, *args, expect_ok=True):
    r = subprocess.run([sys.executable, RECORDS, "--root", str(root), *args],
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, f"records.py {args} failed: {r.stderr}"
    return r


def rows(root, *args):
    out = run(root, "query", *args).stdout.strip()
    return [json.loads(l) for l in out.splitlines() if l]


def _root():
    return tempfile.mkdtemp(prefix="wm-records-test-")


def test_exact_vs_like():
    """Snake_case values must match literally; wildcards are opt-in.

    Matching used to switch to SQL LIKE whenever a value contained _ or %,
    so --entity blood_pressure silently also matched "bloodXpressure" and
    there was no way to request the literal.
    """
    root = _root()
    run(root, "init")
    for ent in ("blood_pressure", "bloodXpressure", "blood_pressure_cuff"):
        run(root, "add", "--type", "m", "--domain", "health",
            "--occurred-at", "2026-08-20T09:00:00+05:30", "--entity", ent)

    got = [r["entity"] for r in rows(root, "--entity", "blood_pressure")]
    check(got == ["blood_pressure"], f"exact match only (got {got})")

    got = sorted(r["entity"] for r in rows(root, "--entity", "blood_pressure", "--like"))
    check(got == ["bloodXpressure", "blood_pressure"],
          f"--like restores wildcard behaviour (got {got})")

    got = sorted(r["entity"] for r in rows(root, "--entity", "blood%", "--like"))
    check(len(got) == 3, f"--like with an explicit %% matches the prefix (got {got})")


def test_utc_normalisation():
    """Range filters and ordering are string comparisons, so storage must be UTC."""
    root = _root()
    run(root, "init")
    run(root, "add", "--type", "m", "--domain", "d", "--entity", "utc",
        "--occurred-at", "2026-08-21T04:00:00+00:00")
    run(root, "add", "--type", "m", "--domain", "d", "--entity", "ist",
        "--occurred-at", "2026-08-21T09:30:00+05:30")  # the same instant

    all_rows = rows(root, "--domain", "d")
    stamps = {r["occurred_at"] for r in all_rows}
    check(len(stamps) == 1, f"same instant stored identically (got {stamps})")
    ist = [r for r in all_rows if r["entity"] == "ist"][0]
    check(ist["occurred_at_local"] == "2026-08-21T09:30:00+05:30",
          "original offset preserved alongside the UTC form")

    # Both are before the cutoff, so neither may come back.
    got = rows(root, "--domain", "d", "--since", "2026-08-21T05:00:00+00:00")
    check(got == [], f"--since filters both spellings alike (got {got})")
    got = rows(root, "--domain", "d", "--since", "2026-08-21T03:00:00+00:00")
    check(len(got) == 2, f"--since includes both spellings alike (got {len(got)})")

    # A bound in a different offset must mean the same instant.
    got = rows(root, "--domain", "d", "--since", "2026-08-21T10:30:00+05:30")
    check(got == [], "--since honours the bound's own offset")


def test_ordering():
    root = _root()
    run(root, "init")
    for ent, ts in (("c", "2026-08-21T09:30:00+05:30"),   # 04:00Z
                    ("a", "2026-08-21T01:00:00+00:00"),
                    ("b", "2026-08-20T22:00:00-05:00")):  # 03:00Z next day
        run(root, "add", "--type", "m", "--domain", "o", "--entity", ent,
            "--occurred-at", ts)
    got = [r["entity"] for r in rows(root, "--domain", "o")]
    check(got == ["a", "b", "c"], f"chronological across offsets (got {got})")


def test_update():
    root = _root()
    run(root, "init")
    run(root, "add", "--type", "m", "--domain", "health", "--entity", "bp",
        "--occurred-at", "2026-08-20T09:00:00+05:30", "--json", '{"v":"128/82"}')
    rid = str(rows(root, "--domain", "health")[0]["id"])

    before = rows(root, "--domain", "health")[0]
    # Timestamps are second-precision, so wait past the tick rather than
    # racing it — this assertion was flaky when both landed in one second.
    time.sleep(1.1)
    run(root, "update", "--id", rid, "--json", '{"note":"after walk"}')
    r = rows(root, "--domain", "health")[0]
    check(r["data"] == {"v": "128/82", "note": "after walk"}, f"--json merges (got {r['data']})")
    check(r["updated_at"] > before["updated_at"], "updated_at advances")
    check(r["created_at"] == before["created_at"], "created_at is immutable")

    run(root, "update", "--id", rid, "--replace-json", '{"only":"this"}')
    check(rows(root, "--domain", "health")[0]["data"] == {"only": "this"},
          "--replace-json overwrites")

    run(root, "update", "--id", rid, "--domain", "fitness")
    check(rows(root, "--domain", "fitness")[0]["entity"] == "bp", "domain re-routed")

    r = run(root, "update", "--id", "99999", "--domain", "x", expect_ok=False)
    check(r.returncode == 1, "unknown id is an error")
    r = run(root, "update", "--id", rid, expect_ok=False)
    check(r.returncode == 1, "no-op update is an error")


def test_delete_guard():
    root = _root()
    run(root, "init")
    for i in range(3):
        run(root, "add", "--type", "m", "--domain", "d", "--entity", f"e{i}",
            "--occurred-at", "2026-08-20T09:00:00+05:30")

    r = run(root, "delete", expect_ok=False)
    check(r.returncode == 1 and "refusing" in r.stderr,
          "refuses an unbounded delete")
    check(len(rows(root, "--domain", "d")) == 3, "nothing deleted by the refusal")

    r = run(root, "delete", "--domain", "d", "--dry-run")
    check("would delete 3" in r.stderr, "dry-run reports the count")
    check(len(rows(root, "--domain", "d")) == 3, "dry-run deletes nothing")

    rid = str(rows(root, "--domain", "d")[0]["id"])
    run(root, "delete", "--id", rid)
    check(len(rows(root, "--domain", "d")) == 2, "delete by id")
    run(root, "delete", "--domain", "d")
    check(rows(root, "--domain", "d") == [], "delete by filter")


def test_migrate():
    """A database written before UTC normalisation converts idempotently."""
    root = _root()
    db = pathlib.Path(root) / "records.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,"
        " domain TEXT NOT NULL, occurred_at TEXT NOT NULL, entity TEXT,"
        " data_json TEXT NOT NULL DEFAULT '{}', notes TEXT);")
    for ent, ts in (("a", "2026-08-20T09:00:00+05:30"),
                    ("b", "2026-08-20T04:00:00+00:00"),
                    ("c", "2026-08-20T02:00:00-05:00")):
        con.execute("INSERT INTO records(type,domain,occurred_at,entity,data_json)"
                    " VALUES ('m','d',?,?,?)", (ts, ent, json.dumps({"k": ent})))
    con.commit()
    con.close()

    r = run(root, "migrate", "--dry-run")
    check("would rewrite 2" in r.stdout, f"dry-run counts only non-UTC rows ({r.stdout})")
    r = run(root, "migrate")
    check("rewrote 2" in r.stdout, f"migrate rewrites them ({r.stdout})")
    check((pathlib.Path(root) / "records.db.pre-migrate").exists(),
          "migrate takes a backup first")
    r = run(root, "migrate")
    check("nothing to do" in r.stdout, "migrate is idempotent")

    got = rows(root, "--domain", "d")
    check([x["entity"] for x in got] == ["a", "b", "c"], "now ordered chronologically")
    check(all(x["occurred_at"].endswith("+00:00") for x in got), "all stored as UTC")
    check(got[0]["data"] == {"k": "a"}, "payload preserved")
    check(got[0]["occurred_at_local"] == "2026-08-20T09:00:00+05:30",
          "original offset recorded")

    # The legacy schema had no created_at/updated_at; opening must add them.
    check("created_at" in got[0], "missing columns added on open")


def test_bad_input_is_clean():
    root = _root()
    run(root, "init")
    r = run(root, "add", "--type", "m", "--domain", "d",
            "--occurred-at", "2026-08-20T09:00:00+05:30", "--json", "{bad",
            expect_ok=False)
    check(r.returncode == 2 and "Traceback" not in r.stderr,
          "malformed --json is a clean error")
    r = run(root, "add", "--type", "m", "--domain", "d",
            "--occurred-at", "whenever", expect_ok=False)
    check(r.returncode == 2 and "Traceback" not in r.stderr,
          "malformed --occurred-at is a clean error")
    r = run(root, "add", "--type", "m", "--domain", "d",
            "--occurred-at", "2026-08-20T09:00:00+05:30", "--json", '"a string"',
            expect_ok=False)
    check(r.returncode == 2, "non-object --json rejected")


def main():
    test_exact_vs_like()
    test_utc_normalisation()
    test_ordering()
    test_update()
    test_delete_guard()
    test_migrate()
    test_bad_input_is_clean()
    print(f"ALL RECORDS TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
