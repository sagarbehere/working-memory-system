#!/usr/bin/env python3
"""Checks the vault's SCHEMA.md has not drifted from the model here.

WHY THIS IS SO SMALL. `second-brain-schema.md` is the tool-independent model:
it names no tool, no field and no folder. The vault's `SCHEMA.md` is the
opposite — fields, folders, Obsidian specifics, and the workflows an agent
follows. They are deliberately disjoint, and the vault's copy is deliberately
self-contained, because an agent working on the wiki must not need this
repository to act correctly.

That independence costs exactly one shared surface: the **five type names**
and the **three status values** appear in both. Everything else can change on
either side without touching the other. So the entire drift risk is eight
words, and this checks those eight words rather than trying to diff prose.

Skips when no vault is reachable, so it is a no-op on a dev machine and a real
check on the box where both live.

Run: python3 tests/test_vault_schema_sync.py   (from the package dir)
"""
import pathlib
import re
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))
import wmlib  # noqa: E402

MODEL = PKG / "second-brain-schema.md"


def model_types(text):
    """Type names from the model's `### 3.N Name` headings.

    "Idea / Quote" is one type with a two-word name; take the first word, which
    is what every implementation actually writes into `type:`.
    """
    return {h.split("/")[0].strip().split()[0].lower()
            for h in re.findall(r"^### 3\.\d+ (.+)$", text, re.M)}


def vault_types(text):
    """Type names from the vault's routing table, minus its header row."""
    rows = {t.lower() for t in re.findall(r"^\| [`*](\w+)[`*] \|", text, re.M)}
    return rows - {"type"}


def model_status(text):
    return set(re.findall(r"^- \*\*(active|superseded|archived)\*\*", text, re.M))


def vault_status(text):
    """Whatever status values the vault declares — not the ones we hope for.
    Matching the expected literal would report drift as "cannot parse", which
    sends the reader looking for a formatting bug instead of the real
    disagreement.

    Two shapes have existed. The vault first declared them inline as
    ``status: a | b | c``; wiki commit 0352cbb (2026-09-01) split that into a
    bulleted definition list so each value could carry its own behaviour, and
    this check went blind until it learned the second shape. Both are read,
    because the point is the vocabulary, not the layout.
    """
    m = re.search(r"status: (\w+) \| (\w+) \| (\w+)", text)
    if m:
        return set(m.groups())
    # Bulleted form: a `status:` intro paragraph, then one `- \`value\`` per
    # status, ending at the next bold paragraph or heading.
    sec = re.search(r"^`status:`.*?$(.*?)(?=^\*\*|^#)", text, re.M | re.S)
    if not sec:
        return set()
    return set(re.findall(r"^- `(\w+)`", sec.group(1), re.M))


def main():
    vault_schema = wmlib.vault_path() / "SCHEMA.md"
    if not vault_schema.is_file():
        print(f"VAULT SCHEMA SYNC: skipped (no vault at {vault_schema})")
        return 0

    model = MODEL.read_text(encoding="utf-8")
    vault = vault_schema.read_text(encoding="utf-8")

    problems = []
    mt, vt = model_types(model), vault_types(vault)
    if not mt or not vt:
        problems.append(
            f"could not extract type names (model={sorted(mt)}, vault={sorted(vt)}) "
            "— a heading or table format changed, so this check has gone blind")
    elif mt != vt:
        problems.append(f"type names differ:\n"
                        f"      model only: {sorted(mt - vt) or '—'}\n"
                        f"      vault only: {sorted(vt - mt) or '—'}")

    ms, vs = model_status(model), vault_status(vault)
    if not ms or not vs:
        problems.append(
            f"could not extract status values (model={sorted(ms)}, "
            f"vault={sorted(vs)}) — the declaration was reformatted, so this "
            "check has gone blind. This is a PARSER problem, not drift: fix "
            "the extractor here, do not edit either document to suit it")
    elif ms != vs:
        problems.append(f"status values differ: model={sorted(ms)} vault={sorted(vs)}")

    if problems:
        print(f"VAULT SCHEMA DRIFT ({vault_schema}):\n")
        for p in problems:
            print(f"    {p}")
        print("\n  These are the only two things the two documents share. Decide "
              "which is right,\n  update both, and note it in the vault's log.md.")
        return 1

    print(f"VAULT SCHEMA SYNC OK (types: {', '.join(sorted(vt))}; "
          f"status: {', '.join(sorted(vs))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
