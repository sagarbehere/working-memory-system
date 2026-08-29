#!/usr/bin/env python3
"""Runs tests/gate-cases.txt — real phrasings against the capture gate.

This is the cheap, deterministic half of "does normal usage still work". It
answers ONE question per case: outside a reserved lane, does the gate treat
this message as memory input?

What it CANNOT tell you is whether the agent then classified or filed the
thing sensibly — that is an LLM judgment, and it belongs in
tests/scenarios.md, which you walk through by hand.

Adding a case is one line in gate-cases.txt. That is the point: when the
system surprises you, the cost of turning the surprise into a permanent check
should be near zero.

Run: python3 tests/test_gate_cases.py   (from the package dir)
"""
import importlib.util
import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
CASES = PKG / "tests" / "gate-cases.txt"

# Import the hook without letting it patch anything.
import os  # noqa: E402
os.environ["WM_SKIP_PATCH"] = "1"
_spec = importlib.util.spec_from_file_location(
    "wm_handler_cases", PKG / "hooks" / "working-memory-debounce" / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

VALID = {"capture", "ignore", "reserve", "release"}


def classify(message):
    """What the gate would do with this message, outside a reserved lane.

    Mirrors the branch order in wm_handle_message: reservation phrases are
    checked before the marker, so "reserve for memory" is never a capture.
    """
    text = (message or "").strip()
    action = handler._reservation_action(text)
    if action:
        return action
    return "capture" if handler._parse_marker(text) else "ignore"


def load_cases():
    cases = []
    for lineno, line in enumerate(CASES.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "|" not in line:
            raise SystemExit(f"{CASES}:{lineno}: expected '<expected> | <message>'")
        expected, _, message = line.partition("|")
        expected = expected.strip().lower()
        if expected not in VALID:
            raise SystemExit(
                f"{CASES}:{lineno}: unknown expectation {expected!r}; "
                f"use one of {', '.join(sorted(VALID))}")
        # Only the leading space after '|' is a separator; the rest is content.
        cases.append((lineno, expected, message[1:] if message[:1] == " " else message))
    return cases


def main():
    cases = load_cases()
    if not cases:
        raise SystemExit(f"{CASES}: no cases found")

    failures = []
    for lineno, expected, message in cases:
        got = classify(message)
        if got != expected:
            failures.append((lineno, expected, got, message))

    if failures:
        print(f"{len(failures)} of {len(cases)} gate case(s) FAILED:\n")
        for lineno, expected, got, message in failures:
            print(f"  gate-cases.txt:{lineno}")
            print(f"    message  : {message!r}")
            print(f"    expected : {expected}")
            print(f"    got      : {got}")
        print("\nIf the expectation is right, the gate has a bug.")
        print("If the gate is right, fix the expectation — and note why.")
        sys.exit(1)

    by_kind = {}
    for _l, expected, _m in cases:
        by_kind[expected] = by_kind.get(expected, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items()))
    print(f"ALL GATE CASES PASSED ({len(cases)} cases: {summary})")


if __name__ == "__main__":
    main()
