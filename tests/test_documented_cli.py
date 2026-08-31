#!/usr/bin/env python3
"""Every CLI invocation quoted in the docs must actually be runnable.

WHY THIS EXISTS. The docs teach the agent how to call these tools, and the
agent believes them. Three separate times a document has cited a subcommand or
flag that the code does not have:

  - spec §12 offered `rawlog.py search --tag/--type`; the cut removed tags and
    types from the transcript, and nothing updated the retrieval section.
  - spec §8 described the CLI striking a raw entry's content — a capability
    that never existed at all.
  - spec §1 pointed at `wm-consolidation-gate.py`, a deleted script.

Every one of those is a doc claiming a behaviour the code does not implement,
which is the exact failure mode this repo keeps hitting. Unlike prose drift,
it is mechanically checkable: parse the invocations out of the markdown, ask
argparse whether they exist.

This checks NAMES, not semantics — that `--since` is a real flag on `search`,
not that the sentence around it is true. That is the honest limit, and it is
still enough to have caught all three.

Run: python3 tests/test_documented_cli.py   (from the package dir)
"""
import pathlib
import re
import subprocess
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
CLIS = ("todoist.py",)

# `todoist.py completed --since … --until …` inside backticks. Prose ellipses,
# placeholders and [optional] brackets are all normal in these docs.
INVOCATION = re.compile(r"`(" + "|".join(re.escape(c) for c in CLIS) + r")\s+([^`]*)`")

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def helptext(script, *args):
    out = subprocess.run([sys.executable, str(PKG / script), *args, "--help"],
                         capture_output=True, text=True)
    return out.stdout + out.stderr


def subcommands(script):
    """Subcommand names argparse advertises, from the `{a,b,c}` choice line."""
    m = re.search(r"\{([a-z,\-]+)\}", helptext(script))
    return set(m.group(1).split(",")) if m else set()


def flags(script, sub):
    return set(re.findall(r"(--[a-z][a-z-]*)", helptext(script, sub)))


def docs():
    out = subprocess.run(["git", "-C", str(PKG), "ls-files", "*.md"],
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.splitlines() if f.strip()]


def main():
    known_subs = {c: subcommands(c) for c in CLIS}
    for c, subs in known_subs.items():
        check(len(subs) >= 2, f"{c} advertises its subcommands (got {sorted(subs)})")
    # rawlog.py was the second CLI here until the 2026-08-31 cut. If a doc ever
    # cites it again the vestigial-reference guard catches that, not this one.

    flag_cache = {}
    bad = []
    seen = 0
    for rel in docs():
        if rel == "tests/test_documented_cli.py":
            continue
        text = (PKG / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for script, rest in INVOCATION.findall(line):
                words = rest.replace("/", " ").split()
                if not words:
                    continue
                sub = words[0]
                if not re.fullmatch(r"[a-z][a-z-]*", sub):
                    continue  # not a subcommand — a bare path or prose
                seen += 1
                if sub not in known_subs[script]:
                    bad.append((rel, lineno, f"{script} {sub}",
                                f"no such subcommand; has {sorted(known_subs[script])}"))
                    continue
                key = (script, sub)
                if key not in flag_cache:
                    flag_cache[key] = flags(script, sub)
                for w in words[1:]:
                    w = w.strip("[](),.")
                    if w.startswith("--") and w not in flag_cache[key]:
                        bad.append((rel, lineno, f"{script} {sub} {w}",
                                    f"no such flag; has {sorted(flag_cache[key])}"))

    if bad:
        print(f"{len(bad)} documented CLI call(s) that cannot run:\n")
        for rel, lineno, what, why in bad:
            print(f"  {rel}:{lineno}\n    {what}\n    {why}")
        print("\nThe docs are what the agent follows. Either fix the doc, or add")
        print("the capability it describes — but they must not disagree.")
        sys.exit(1)

    check(seen >= 5, f"found a plausible number of invocations (got {seen})")
    print(f"DOCUMENTED CLI CALLS OK ({seen} invocations across "
          f"{len(CLIS)} CLIs, {checks} checks)")


if __name__ == "__main__":
    main()
