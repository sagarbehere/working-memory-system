#!/usr/bin/env python3
"""Tests for the raw capture transcript (rawlog.py).

The transcript is deliberately tiny — a timestamp and verbatim text — so the
surface worth testing is narrow: the consolidation gate must be able to count
every entry, captured text must survive verbatim whatever it contains, and
entries written by earlier versions (which carried ids and typed fields) must
still read back.

Run: python3 tests/test_rawlog.py   (from the package dir)
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
import rawlog  # noqa: E402
import wmlib  # noqa: E402

_spec = importlib.util.spec_from_file_location("wm_gate", PKG / "wm-consolidation-gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def _root():
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-rawlog-test-"))
    (td / "raw").mkdir(parents=True)
    (td / "meta").mkdir()
    return td


def test_format():
    root = _root()
    at = wmlib.parse_iso("2026-08-24T16:03:00+05:30")
    rawlog.add(root, "Took vitamin D pill. Next one due in a week.", when=at)
    text = (root / "raw" / "2026-08.md").read_text()
    check(text == "## 2026-08-24T16:03:00+05:30\n\n"
                  "Took vitamin D pill. Next one due in a week.\n\n",
          f"timestamp and text, nothing else (got {text!r})")
    check("type:" not in text and "[id:" not in text,
          "no classification and no id — the destination note carries those")


def test_gate_counts_every_entry():
    """A header the gate cannot read means an entry never gets consolidated."""
    root = _root()
    for i in range(5):
        rawlog.add(root, f"entry {i}", force=True)
    rawlog.add(root, "unicode ✅ and : colons: everywhere", force=True)
    count, newest = gate.raw_entries_since(str(root), None)
    check(count == 6, f"gate counts every entry (got {count}/6)")
    check(newest is not None and newest.tzinfo is not None, "aware timestamp read")


def test_text_survives_verbatim():
    """Captured text may contain anything, including our own delimiters."""
    root = _root()
    danger = "Notes from the meeting:\n---\n## Action items\n- ship it"
    rawlog.add(root, danger)
    rawlog.add(root, "a following entry")
    entries = rawlog.read_entries(root)
    check(len(entries) == 2, f"a '---' body line does not split the entry (got {len(entries)})")
    check(entries[0]["text"] == danger, f"round-trips losslessly (got {entries[0]['text']!r})")
    check(len(rawlog.search(root, text="ship it")) == 1, "and the tail is searchable")


def test_dedup():
    root = _root()
    t0 = wmlib.parse_iso("2026-08-24T10:00:00+05:30")
    _e, dup = rawlog.add(root, "buy stamps", when=t0)
    check(not dup, "first write is not a duplicate")
    _e, dup = rawlog.add(root, "  BUY   STAMPS  ", when=t0)
    check(dup, "identical modulo case and whitespace")
    _e, dup = rawlog.add(root, "buy stamps today", when=t0)
    check(not dup, "a NEAR match is a real capture — exact matching only")
    _e, dup = rawlog.add(root, "buy stamps",
                         when=wmlib.parse_iso("2026-08-25T11:00:00+05:30"))
    check(not dup, "outside 24h it is a new capture")
    _e, dup = rawlog.add(root, "buy stamps", when=t0, force=True)
    check(not dup, "--force overrides")
    check(len(rawlog.read_entries(root)) == 4, "only non-duplicates landed")


def test_search():
    root = _root()
    for text, ts in (("printer is out of ink", "2026-08-20T09:00:00+05:30"),
                     ("vitamin D taken", "2026-08-22T09:00:00+05:30"),
                     ("idea about the printer stand", "2026-08-24T09:00:00+05:30")):
        rawlog.add(root, text, when=wmlib.parse_iso(ts))
    got = [e["text"] for e in rawlog.search(root, text="PRINTER")]
    check(len(got) == 2, f"case-insensitive text search (got {got})")
    check(got[0].startswith("idea about"), "newest first")
    check(len(rawlog.search(root, since="2026-08-23T00:00:00+05:30")) == 1, "--since")
    check(len(rawlog.search(root, until="2026-08-21T00:00:00+05:30")) == 1, "--until")
    check(rawlog.search(root, text="nope") == [], "no match -> empty")


def test_reads_older_entries():
    """Entries written before the cut carried ids and typed fields."""
    root = _root()
    (root / "raw" / "2026-07.md").write_text(
        "## 2026-07-04T08:00:00+05:30 [id: 20260704-0800-01]\n"
        "tags: legacy\n"
        "type: record\n"
        "domain: misc\n"
        "\n"
        "an entry written before the transcript cut\n"
        "\n"
        "---\n"
        "\n"
        "## 2026-07-05T08:00:00+05:30\n"
        "\n"
        "a plain one\n"
        "\n")
    entries = rawlog.read_entries(root)
    check(len(entries) == 2, f"both old entries parsed (got {len(entries)})")
    check(entries[0]["text"] == "an entry written before the transcript cut",
          f"field lines and trailing --- stripped (got {entries[0]['text']!r})")
    check(entries[1]["text"] == "a plain one", "new-style entry reads too")
    check(len(rawlog.search(root, text="before the transcript")) == 1,
          "old entries stay searchable")


def test_cli():
    root = _root()
    env = dict(os.environ, HERMES_HOME="/nonexistent-hermes-home")
    env.pop("WM_ROOT", None)

    def run(*args):
        return subprocess.run([sys.executable, str(PKG / "rawlog.py"),
                               "--root", str(root), *args],
                              capture_output=True, text=True, env=env)

    r = run("add", "--text", "cli capture")
    check(r.returncode == 0 and json.loads(r.stdout)["duplicate"] is False,
          f"cli add ok ({r.stderr})")
    r = run("add", "--text", "cli capture")
    check(json.loads(r.stdout)["duplicate"] is True, "cli reports a duplicate")
    check("not re-filed" in r.stderr, "and explains it on stderr")
    r = run("search", "--text", "cli")
    check(len(r.stdout.strip().splitlines()) == 1, "cli search")
    r = run("add", "--text", "   ")
    check(r.returncode == 2 and "Traceback" not in r.stderr, "empty text is a clean error")


def test_concurrent_appends():
    root = _root()
    env = dict(os.environ, HERMES_HOME="/nonexistent-hermes-home")
    env.pop("WM_ROOT", None)
    procs = [subprocess.Popen(
        [sys.executable, str(PKG / "rawlog.py"), "--root", str(root), "add",
         "--text", f"concurrent {i}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for i in range(8)]
    for p in procs:
        p.communicate(timeout=60)
    check(len(rawlog.read_entries(root)) == 8,
          f"8 concurrent writers -> 8 entries (got {len(rawlog.read_entries(root))})")
    count, _ = gate.raw_entries_since(str(root), None)
    check(count == 8, "all 8 parseable by the gate")


def main():
    test_format()
    test_gate_counts_every_entry()
    test_text_survives_verbatim()
    test_dedup()
    test_search()
    test_reads_older_entries()
    test_cli()
    test_concurrent_appends()
    print(f"ALL RAWLOG TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
