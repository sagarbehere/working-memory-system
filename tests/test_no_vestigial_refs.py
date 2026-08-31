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
    "cron-session-prune.py": "pruned sessions the deleted nightly job created",
    "WM_RAW_RETENTION_DAYS": "raw rotation; search reads the archive anyway",
    "rawlog.py": "the raw capture log CLI (2026-08-31 cut)",
    "rawlog.lock": "the transcript append lock; there is no transcript",
}

# Exemptions. Map file -> "*" (whole file) or a set of specific tokens, so
# exempting one legitimate mention does not blind the guard to every other
# token in that file.
ALLOWED = {
    "tests/test_no_vestigial_refs.py": "*",     # this file lists them
    "decisions.md": "*",  # records WHY they went
    "working-memory-system-spec.md": "*",    # §9 records why the layer went
    "review-notes.md": "*",                     # dated decision log; history
    # These must NAME the removed things in order to check they are absent, or
    # to tell the user to clean them up.
    "verify-on-vps.sh": {"reminders.py", "records.py", "reminder-check.py",
                         "records.db", "reminders.json", "records-snapshot.db",
                         "tag-index.json", "wm-consolidation-gate.py",
                         "cron-session-prune.py", "rawlog.py"},
    "setup.sh": {"records.db", "reminders.json", "records-snapshot.db",
                 "reminder-check.py", "reminders.py", "records.py",
                 "wm-consolidation-gate.py", "cron-session-prune.py",
                 "rawlog.py"},
    "crontab.example": {"reminder-check.py"},
    # These two explain, in their docstrings, the removed thing that motivated
    # them. Named per-token so the rest of the guard still applies to them —
    # test_documented_cli.py shipped with an unexempted mention that went
    # unnoticed only because the file was still untracked when the suite ran.
    "tests/test_documented_cli.py": {"rawlog.py", "wm-consolidation-gate.py"},
    "tests/test_todoist_budget.py": {"rawlog.py"},
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


DOC_FOR = {"schema": "second-brain-schema.md", "spec": "working-memory-system-spec.md"}


def sections_in(doc):
    """Section numbers a document actually defines, e.g. {'3', '3.1', '9'}."""
    out = set()
    for line in (PKG / doc).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{2,4}\s+(\d+(?:\.\d+)?)[.\s]", line)
        if m:
            out.add(m.group(1))
    return out


SPEC = DOC_FOR["spec"]

# "spec §9" and "spec Section 9" are the same pointer written two ways. The
# first version of this check knew only about "§", which is exactly why the
# prose form was free to rot: setup.sh cited three sections and two of them
# were wrong, in a file the guard was already scanning.
_REF = r"(?:§\s*|Section\s+)(\d+(?:\.\d+)?)"


def check_section_refs():
    """Cross-doc and in-doc section pointers must resolve.

    Renumbering a document silently invalidates every reference into it from
    everywhere else, and nothing complains — a reader just follows a pointer
    to the wrong section. Found four such breaks the first time this ran,
    after the schema was split, and five more once it learned the prose form.

    NOT CHECKED HERE, deliberately: prose descriptions of deleted features
    ("the nightly consolidation gate"). Those were tried and abandoned as a
    token rule — after the cut, nearly every surviving mention of a deleted
    feature is a *negation* ("there is no local reminder store") or a note
    explaining where its one useful job went. A guard that fires on fifteen
    correct lines to catch one wrong one gets suppressed, and then it is
    protecting nothing. Prose staleness stays a review job.
    """
    known = {k: sections_in(v) for k, v in DOC_FOR.items()}
    bad = []
    for rel in tracked_files():
        if rel in ("tests/test_no_vestigial_refs.py", "review-notes.md"):
            continue  # this file; and review-notes is a dated historical log
        try:
            text = (PKG / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for word, doc in DOC_FOR.items():
                for num in re.findall(word + r"\s+" + _REF, line):
                    if num not in known[word]:
                        bad.append((rel, lineno, f"{word} §{num}", doc))
            # Inside the spec, a bare "(Section N)" is a self-reference.
            if rel == SPEC:
                for num in re.findall(r"\(Section\s+(\d+(?:\.\d+)?)", line):
                    if num not in known["spec"]:
                        bad.append((rel, lineno, f"Section {num}", SPEC))
    if bad:
        print(f"{len(bad)} broken section reference(s):\n")
        for rel, lineno, ref, doc in bad:
            print(f"  {rel}:{lineno}  -> {ref} does not exist in {doc}")
        sys.exit(1)
    return sum(len(v) for v in known.values())


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

    n_sections = check_section_refs()
    check(n_sections > 10, f"section map built (got {n_sections})")
    check(scanned > 20, f"scanned a plausible number of files (got {scanned})")
    check(not (PKG / "reminders.py").exists(), "reminders.py is really gone")
    check(not (PKG / "records.py").exists(), "records.py is really gone")
    check(not (PKG / "reminder-check.py").exists(), "reminder-check.py is really gone")
    check(not (PKG / "rawlog.py").exists(), "rawlog.py is really gone")
    check(not (PKG / "tests" / "test_rawlog.py").exists(),
          "test_rawlog.py is really gone")
    print(f"NO VESTIGIAL REFERENCES ({scanned} files scanned, "
          f"{len(REMOVED)} tokens, all §refs resolve, {checks} checks)")


if __name__ == "__main__":
    main()
