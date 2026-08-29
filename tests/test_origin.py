#!/usr/bin/env python3
"""Origin validation, repair, and delivery escalation.

Reproduces the bug observed in a live Telegram capture: the agent recorded
the lane's THREAD id as the reminder's chat_id. Nothing downstream questioned
it, because a present-but-wrong chat_id is indistinguishable from a good one
— so the reminder would have retried into a nonexistent chat forever.

Run: python3 tests/test_origin.py   (from the package dir)
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
import reminders as rem  # noqa: E402

_spec = importlib.util.spec_from_file_location("rc", PKG / "reminder-check.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

# The real values from the observed session.
CHAT, THREAD = "143386153", "87471"

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def _root(lanes=True):
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-origin-test-"))
    (td / "meta").mkdir(parents=True)
    if lanes:
        (td / "meta" / "lanes.json").write_text(json.dumps({
            f"telegram:{CHAT}:{THREAD}": {
                "platform": "telegram", "chat_id": CHAT,
                "thread_id": THREAD, "reserved_at": "2026-08-01T00:00:00+05:30"},
        }))
    return td


def test_repairs_the_observed_swap():
    root = _root()
    bad = {"platform": "telegram", "chat_id": THREAD, "thread_id": ""}
    fixed, note = rem.resolve_origin(root, bad)
    check(fixed["chat_id"] == CHAT, f"chat_id repaired to the real chat (got {fixed})")
    check(fixed["thread_id"] == THREAD, "thread_id restored")
    check(note and "THREAD id" in note, f"repair is reported, not silent (got {note!r})")


def test_leaves_legitimate_unknown_chats_alone():
    """A marker capture from an unreserved chat is normal, not an error."""
    root = _root()
    origin = {"platform": "telegram", "chat_id": "999999", "thread_id": ""}
    fixed, note = rem.resolve_origin(root, origin)
    check(fixed == origin, f"unknown chat left untouched (got {fixed})")
    check(note is None, "and nothing is reported")


def test_defaults_and_fills():
    root = _root()
    fixed, note = rem.resolve_origin(root, {})
    check(fixed["chat_id"] == CHAT, "empty origin adopts the sole reserved lane")
    check(note is not None, "and says so")

    fixed, _ = rem.resolve_origin(root, {"platform": "telegram", "chat_id": CHAT})
    check(fixed["thread_id"] == THREAD, "missing thread filled from the lane")

    # Two lanes: no unambiguous default, so do not guess.
    root2 = _root()
    lanes = json.loads((root2 / "meta" / "lanes.json").read_text())
    lanes["telegram:222:"] = {"platform": "telegram", "chat_id": "222", "thread_id": ""}
    (root2 / "meta" / "lanes.json").write_text(json.dumps(lanes))
    fixed, note = rem.resolve_origin(root2, {})
    check(not fixed.get("chat_id"), "two lanes -> no guess")
    check(note is None, "and no claim of a repair")

    root3 = _root(lanes=False)
    fixed, note = rem.resolve_origin(root3, {"chat_id": "5"})
    check(fixed["chat_id"] == "5" and note is None, "no lanes file -> pass through")


def test_add_repairs_at_capture():
    root = _root()
    entry = rem.add(root, "test", "2026-08-30T09:00:00+05:30",
                    origin={"platform": "telegram", "chat_id": THREAD, "thread_id": ""},
                    mirror=False)
    check(entry["origin"]["chat_id"] == CHAT,
          f"the stored entry has the corrected chat (got {entry['origin']})")


def test_update_can_repair_existing():
    """The observed entry must be fixable without recreating it."""
    root = _root(lanes=False)          # no lanes yet: the bad origin gets stored
    entry = rem.add(root, "already wrong", "2026-08-30T09:00:00+05:30",
                    origin={"platform": "telegram", "chat_id": THREAD, "thread_id": ""},
                    mirror=False)
    check(entry["origin"]["chat_id"] == THREAD, "precondition: stored wrong")

    (root / "meta" / "lanes.json").write_text(json.dumps({
        f"telegram:{CHAT}:{THREAD}": {"platform": "telegram", "chat_id": CHAT,
                                      "thread_id": THREAD}}))
    fixed = rem.update(root, entry["id"], repair_origin=True)
    check(fixed["origin"]["chat_id"] == CHAT, "--repair-origin corrects it in place")
    check(fixed["id"] == entry["id"], "id preserved (Todoist mirror stays linked)")

    fixed = rem.update(root, entry["id"], message="renamed")
    check(fixed["message"] == "renamed", "update can also change the message")
    try:
        rem.update(root, "nope", message="x")
        check(False, "unknown id rejected")
    except ValueError:
        check(True, "unknown id rejected")
    try:
        rem.update(root, entry["id"])
        check(False, "empty update rejected")
    except ValueError:
        check(True, "empty update rejected")


def test_delivery_escalates_after_repeated_failure():
    """A present-but-wrong address must not retry forever."""
    good = {"origin": {"platform": "telegram", "chat_id": "999", "thread_id": ""}}
    chat, _thread, fell_back = rc._resolve_target(good, CHAT, THREAD)
    check(chat == "999" and not fell_back, "a fresh origin is trusted")

    for n, expect_fallback in ((1, False), (2, False), (3, True), (9, True)):
        r = dict(good, send_failures=n)
        chat, _t, fell_back = rc._resolve_target(r, CHAT, THREAD)
        check(fell_back is expect_fallback,
              f"{n} failures -> fallback={expect_fallback} (got {fell_back})")
        if expect_fallback:
            check(chat == CHAT, "escalation targets the home channel")

    chat, _t, fell_back = rc._resolve_target({"origin": {}}, CHAT, THREAD)
    check(fell_back, "a missing origin still falls back immediately")


def test_failures_are_counted_end_to_end():
    """The tick must actually persist the counter it escalates on."""
    root = _root()
    hermes = pathlib.Path(tempfile.mkdtemp())
    (hermes / "working-memory.env").write_text(
        f"WM_ROOT={root}\nWM_TELEGRAM_CHAT_ID={CHAT}\n")
    (hermes / ".env").write_text("TELEGRAM_BOT_TOKEN=fake\n")
    rem.add(root, "will fail", "2020-01-01T00:00:00+00:00",
            origin={"platform": "telegram", "chat_id": "999", "thread_id": ""},
            mirror=False)

    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)
    # No network in tests: sends fail, which is exactly the path under test.
    for expected in (1, 2):
        subprocess.run([sys.executable, str(PKG / "reminder-check.py")],
                       capture_output=True, text=True, env=env, timeout=300)
        got = rem.load(root)[0]
        check(got.get("send_failures") == expected,
              f"failure {expected} recorded (got {got.get('send_failures')})")
        check(got["status"] == "pending", "still pending after a failed send")


def main():
    test_repairs_the_observed_swap()
    test_leaves_legitimate_unknown_chats_alone()
    test_defaults_and_fills()
    test_add_repairs_at_capture()
    test_update_can_repair_existing()
    test_delivery_escalates_after_repeated_failure()
    test_failures_are_counted_end_to_end()
    print(f"ALL ORIGIN TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
