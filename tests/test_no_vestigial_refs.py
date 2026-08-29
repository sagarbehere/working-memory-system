#!/usr/bin/env python3
"""Fails if a reference to a removed component reappears anywhere in the repo.

The 2026-08-29 cut deleted the local reminder store and the SQLite records
store. The danger with a deletion that large is not the deletion itself — it
is the reference left behind in a doc, a shell script, or a config example,
which surfaces months later as "why doesn't this work? oh, we deleted that."

This is the durable version of "review carefully": it keeps checking after
everyone has forgotten. It scans EVERY tracked file, not just Python.

If you are deliberately reintroducing one of these, delete its entry from
REMOVED below — that edit is the moment to think twice.

Run: python3 tests/test_no_vestigial_refs.py   (from the package dir)
"""
import pathlib
import re
import subprocess
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]

# Token -> why it went, so a future reader gets the reason with the failure.
REMOVED = {
    "reminders.py": "the local reminder store; Todoist owns reminders now",
    "reminder-check.py": "the firing cron; nothing fires locally",
    "reminders.json": "the local reminder store file",
    "records.py": "the SQLite records CLI",
    "records.db": "the SQLite database",
    "records-snapshot.db": "the nightly DB snapshot",
    "tag-index.json": "the inverted raw-log index",
    "raw_entry_id": "raw entries have no ids; nothing links back to them",
    "record_kind": "structured/narrative split; records are vault notes now",
    "WM_PROMOTE_AFTER": "vestigial v2 config",
    "WM_CONDENSE_SIZE": "vestigial v2 config",
    "TODOIST_RECONCILE_MINUTES": "there is no reconcile loop any more",
    "reminder-check.lock": "the tick lock; there is no tick",
    "reminders.lock": "the reminder store lock; there is no store",
    "wm-consolidation-gate.py": "the nightly gate; nothing schedules the agent",
    "WM_RAW_RETENTION_DAYS": "raw rotation; search reads the archive anyway",
}

# Exemptions. Map file -> "*" (whole file) or a set of specific tokens, so
# exempting one legitimate mention does not blind the guard to every other
# token in that file.
ALLOWED = {
    "tests/test_no_vestigial_refs.py": "*",     # this file lists them
    "second-brain-implementation-guide.md": "*",  # records WHY they went
    "working-memory-system-spec-v3.md": "*",    # §9 records why the layer went
    "review-notes.md": "*",                     # dated decision log; history
    # README.md is PENDING A FULL REWRITE and still describes the pre-cut
    # system. Remove this exemption as part of that rewrite and the guard will
    # list exactly what needs fixing.
    "README.md": "*",
    # These must NAME the removed things in order to check they are absent, or
    # to tell the user to clean them up.
    "verify-on-vps.sh": {"reminders.py", "records.py", "reminder-check.py",
                         "records.db", "reminders.json", "records-snapshot.db",
                         "tag-index.json", "wm-consolidation-gate.py"},
    "setup.sh": {"records.db", "reminders.json", "records-snapshot.db",
                 "reminder-check.py", "reminders.py", "records.py",
                 "wm-consolidation-gate.py"},
    "crontab.example": {"reminder-check.py"},
    # Strips field lines from entries written before the cut; must name them.
    "rawlog.py": {"record_kind"},
}


def _is_allowed(rel, token):
    rule = ALLOWED.get(rel)
    return rule == "*" or (rule is not None and token in rule)


checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def tracked_files():
    out = subprocess.run(["git", "-C", str(PKG), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.splitlines() if f.strip()]


def main():
    offenders = []
    scanned = 0
    for rel in tracked_files():
        if ALLOWED.get(rel) == "*":
            continue
        path = PKG / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for token, why in REMOVED.items():
                # Word-ish boundary so "records.py" does not match inside a
                # longer path, and so prose about "records" is not flagged.
                if _is_allowed(rel, token):
                    continue
                if re.search(r"(?<![\w.-])" + re.escape(token) + r"(?![\w])", line):
                    offenders.append((rel, lineno, token, why, line.strip()[:100]))

    if offenders:
        print(f"Found {len(offenders)} reference(s) to removed components:\n")
        for rel, lineno, token, why, line in offenders:
            print(f"  {rel}:{lineno}")
            print(f"    token : {token}   ({why})")
            print(f"    line  : {line}")
        print("\nEither remove the reference, or — if you are deliberately")
        print("reintroducing the component — drop its entry from REMOVED.")
        sys.exit(1)

    check(scanned > 20, f"scanned a plausible number of files (got {scanned})")
    check(not (PKG / "reminders.py").exists(), "reminders.py is really gone")
    check(not (PKG / "records.py").exists(), "records.py is really gone")
    check(not (PKG / "reminder-check.py").exists(), "reminder-check.py is really gone")
    print(f"NO VESTIGIAL REFERENCES ({scanned} files scanned, "
          f"{len(REMOVED)} tokens, {checks} checks)")


if __name__ == "__main__":
    main()
