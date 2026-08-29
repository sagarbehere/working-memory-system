#!/usr/bin/env python3
"""Tests for the raw capture log (rawlog.py).

The two failure modes this CLI exists to prevent are both silent, so they are
asserted directly: a header the consolidation gate cannot parse (the entry
becomes invisible to consolidation forever) and a colliding id (breaks the
raw_entry_id links reminders and records point back with).

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


def test_format_matches_the_spec():
    """The on-disk shape is spec §5, exactly."""
    root = _root()
    at = wmlib.parse_iso("2026-08-24T16:03:00+05:30")
    rawlog.add(root, "Took vitamin D pill. Next one due in a week.",
               when=at, type="reminder", tags="health, vitamin-d",
               domain="health, vitamin-d", supersedes="20260817-1610-01")
    text = (root / "raw" / "2026-08.md").read_text()
    expected = (
        "## 2026-08-24T16:03:00+05:30 [id: 20260824-1603-01]\n"
        "tags: health, vitamin-d\n"
        "type: reminder\n"
        "domain: health, vitamin-d\n"
        "supersedes: 20260817-1610-01\n"
        "\n"
        "Took vitamin D pill. Next one due in a week.\n"
        "\n"
        "---\n"
    )
    check(text == expected, f"byte-exact spec §5 layout\n--got--\n{text}\n--want--\n{expected}")


def test_gate_can_parse_every_entry():
    """THE silent failure: a header the gate cannot read is invisible forever."""
    root = _root()
    for i in range(5):
        rawlog.add(root, f"entry {i}", type="record", domain="misc")
    # Awkward content that a hand-written entry might mangle.
    rawlog.add(root, "text with --- a separator-looking line\nand ## a hash line",
               type="idea", domain="misc")
    rawlog.add(root, "unicode ✅ and : colons: everywhere", type="idea", domain="misc")

    count, newest = gate.raw_entries_since(str(root), None)
    check(count == 7, f"the gate counts every entry written (got {count}/7)")
    check(newest is not None and newest.tzinfo is not None,
          "and reads an aware timestamp from each header")


def test_body_containing_a_separator_survives():
    """Captured text may legitimately contain a '---' line or a '##' heading.

    Treating the first '---' in the body as the entry terminator truncated the
    entry on read: the tail stayed on disk but disappeared from every search,
    with no error anywhere. Entries are delimited by the header instead.
    """
    root = _root()
    danger = "Notes from the meeting:\n---\n## Action items\n- ship it"
    rawlog.add(root, danger, type="record", domain="misc")
    rawlog.add(root, "a following entry", type="idea", domain="misc")

    entries = rawlog.read_entries(root)
    check(len(entries) == 2, f"a '---' in the body does not split the entry (got {len(entries)})")
    check(entries[0]["text"] == danger,
          f"the body round-trips losslessly (got {entries[0]['text']!r})")
    check(entries[1]["text"] == "a following entry", "the next entry is unaffected")
    check(len(rawlog.search(root, text="ship it")) == 1,
          "and the tail is still searchable")

    # Body whose own last line is '---': we write our terminator after it, and
    # strip exactly one on read, so even this round-trips unchanged.
    ends_with_rule = "before\n---"
    rawlog.add(root, ends_with_rule, type="idea", domain="misc")
    check(rawlog.read_entries(root)[2]["text"] == ends_with_rule,
          f"a body ending in '---' round-trips too "
          f"(got {rawlog.read_entries(root)[2]['text']!r})")


def test_ids_are_unique_within_a_minute():
    """THE other silent failure: a colliding id breaks raw_entry_id links."""
    root = _root()
    at = wmlib.parse_iso("2026-08-24T16:03:00+05:30")
    ids = [rawlog.add(root, f"thought number {i}", when=at, force=True,
                      type="record", domain="misc")[0]["id"] for i in range(12)]
    check(len(set(ids)) == 12, f"12 captures in one minute -> 12 ids (got {ids})")
    check(ids[0] == "20260824-1603-01" and ids[11] == "20260824-1603-12",
          f"suffixes increment per spec §5 (got {ids[0]}..{ids[11]})")

    # Re-reads existing entries, so a later process continues the sequence.
    again = rawlog.add(root, "from a separate call", when=at, type="record")[0]
    check(again["id"] == "20260824-1603-13", f"sequence survives a new process (got {again['id']})")


def test_dedup_is_exact_and_windowed():
    root = _root()
    t0 = wmlib.parse_iso("2026-08-24T10:00:00+05:30")
    first, dup = rawlog.add(root, "buy stamps", when=t0, type="record", domain="misc")
    check(not dup, "first write is not a duplicate")

    same, dup = rawlog.add(root, "buy stamps", when=t0, type="record")
    check(dup and same["id"] == first["id"], "identical re-send returns the original id")

    _e, dup = rawlog.add(root, "  BUY   STAMPS  ", when=t0, type="record")
    check(dup, "whitespace and case differences still count as identical")

    _e, dup = rawlog.add(root, "buy stamps today", when=t0, type="record")
    check(not dup, "a NEAR match is a real capture — exact matching only")

    later = wmlib.parse_iso("2026-08-25T11:00:00+05:30")   # 25h on
    _e, dup = rawlog.add(root, "buy stamps", when=later, type="record")
    check(not dup, "outside the 24h window it is a new capture")

    _e, dup = rawlog.add(root, "buy stamps", when=t0, force=True, type="record")
    check(not dup, "--force overrides dedup")

    entries = rawlog.read_entries(root)
    check(len(entries) == 4, f"exactly the non-duplicate writes landed (got {len(entries)})")


def test_search_and_show():
    root = _root()
    rawlog.add(root, "printer is out of ink", type="record",
               tags="printer", domain="home",
               when=wmlib.parse_iso("2026-08-20T09:00:00+05:30"))
    rawlog.add(root, "vitamin D taken", type="record",
               tags="health", domain="health",
               when=wmlib.parse_iso("2026-08-22T09:00:00+05:30"))
    rawlog.add(root, "idea about the printer stand", type="idea",
               tags="printer", domain="home",
               when=wmlib.parse_iso("2026-08-24T09:00:00+05:30"))

    got = [e["text"] for e in rawlog.search(root, tag="printer")]
    check(len(got) == 2, f"tag search matches the tags field (got {got})")
    check(got[0].startswith("idea about"), "newest first")

    check(len(rawlog.search(root, tag="health")) == 1, "tag search also covers domain")
    check(len(rawlog.search(root, etype="idea")) == 1, "type filter")
    check(len(rawlog.search(root, text="PRINTER")) == 2, "text search is case-insensitive")
    check(len(rawlog.search(root, since="2026-08-23T00:00:00+05:30")) == 1, "--since")
    check(len(rawlog.search(root, until="2026-08-21T00:00:00+05:30")) == 1, "--until")
    check(rawlog.search(root, tag="nope") == [], "no match -> empty")


def test_reads_hand_written_history():
    """The log predates this CLI, so the parser must tolerate older entries."""
    root = _root()
    (root / "raw" / "2026-07.md").write_text(
        "## 2026-07-04T08:00:00+05:30 [id: 20260704-0800-01]\n"
        "tags: legacy\n"
        "type: record\n"
        "\n"
        "an entry written before the CLI existed\n"
        "\n"
        "---\n"
        "\n"
        "## 2026-07-05T08:00:00+05:30\n"          # no id at all
        "type: idea\n"
        "\n"
        "no id on this one\n"
        "---\n"
        "\ntrailing junk that is not an entry\n")
    entries = rawlog.read_entries(root)
    check(len(entries) == 2, f"both legacy entries parsed (got {len(entries)})")
    check(entries[0]["id"] == "20260704-0800-01", "id read")
    check(entries[1]["id"] == "", "a missing id does not break parsing")
    check("no id on this one" in entries[1]["text"], "body still recovered")

    # And a new write must not collide with, or corrupt, that history.
    new, _ = rawlog.add(root, "modern entry", type="record")
    check(new["id"], "new entry still gets an id alongside legacy data")
    check(len(rawlog.read_entries(root)) == 3, "history intact after appending")


def test_cli_round_trip():
    root = _root()
    env = dict(os.environ, HERMES_HOME="/nonexistent-hermes-home")
    env.pop("WM_ROOT", None)

    def run(*args):
        return subprocess.run([sys.executable, str(PKG / "rawlog.py"),
                               "--root", str(root), *args],
                              capture_output=True, text=True, env=env)

    r = run("add", "--text", "cli capture", "--type", "record", "--domain", "misc")
    check(r.returncode == 0, f"cli add ok ({r.stderr})")
    out = json.loads(r.stdout)
    check(out["duplicate"] is False and out["id"], "prints the id for the routing step")

    r = run("add", "--text", "cli capture", "--type", "record")
    check(json.loads(r.stdout)["duplicate"] is True, "cli reports a duplicate")
    check("not re-filed" in r.stderr, "and explains it on stderr")

    r = run("show", "--id", out["id"])
    check(json.loads(r.stdout)["text"] == "cli capture", "show by id")
    r = run("show", "--id", "nope")
    check(r.returncode == 1, "unknown id exits 1")

    r = run("recent", "--limit", "5")
    check(len(r.stdout.strip().splitlines()) == 1, "recent lists entries")

    r = run("add", "--text", "x", "--type", "banana")
    check(r.returncode == 2 and "Traceback" not in r.stderr,
          f"bad --type is a clean error (got {r.stderr!r})")
    r = run("add", "--text", "   ")
    check(r.returncode == 2, "empty text rejected")


def test_concurrent_adds_do_not_collide():
    """Two captures at once must not race for the same id."""
    root = _root()
    env = dict(os.environ, HERMES_HOME="/nonexistent-hermes-home")
    env.pop("WM_ROOT", None)
    procs = [subprocess.Popen(
        [sys.executable, str(PKG / "rawlog.py"), "--root", str(root), "add",
         "--text", f"concurrent {i}", "--type", "record"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for i in range(8)]
    ids = []
    for p in procs:
        out, _err = p.communicate(timeout=60)
        ids.append(json.loads(out)["id"])
    check(len(set(ids)) == 8, f"8 concurrent writers -> 8 distinct ids (got {sorted(ids)})")
    check(len(rawlog.read_entries(root)) == 8, "and 8 entries on disk")
    count, _ = gate.raw_entries_since(str(root), None)
    check(count == 8, "all 8 remain parseable by the gate")


def main():
    test_format_matches_the_spec()
    test_gate_can_parse_every_entry()
    test_body_containing_a_separator_survives()
    test_ids_are_unique_within_a_minute()
    test_dedup_is_exact_and_windowed()
    test_search_and_show()
    test_reads_hand_written_history()
    test_cli_round_trip()
    test_concurrent_adds_do_not_collide()
    print(f"ALL RAWLOG TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
