"""Unit tests for reminder origin resolution (spec 18.4). No network.

Run: python3 tests/test_reminder_resolver.py  (from the package dir)
"""
import importlib.util
import pathlib
import sys

pkg = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rc", pkg / "reminder-check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

passed = 0

def check(cond, label):
    global passed
    assert cond, f"FAIL: {label}"
    passed += 1

# telegram origin -> direct delivery
t = rc._resolve_target({"origin": {"platform": "telegram", "chat_id": "111", "thread_id": "222"}}, "home", "")
check(t == ("111", "222", False), "telegram origin direct")

# missing origin -> legacy lane fallback
t = rc._resolve_target({}, "home", "tid")
check(t == ("home", "tid", True), "missing origin falls back")

# api_server origin -> fallback (not deliverable by this script yet)
t = rc._resolve_target({"origin": {"platform": "api_server", "chat_id": "x"}}, "home", "tid")
check(t == ("home", "tid", True), "api_server origin falls back")

# telegram origin without thread -> default thread
t = rc._resolve_target({"origin": {"platform": "telegram", "chat_id": "111"}}, "home", "tid")
check(t == ("111", "tid", False), "telegram origin without thread")

# telegram origin with empty thread -> default thread
t = rc._resolve_target({"origin": {"platform": "telegram", "chat_id": "111", "thread_id": ""}}, "home", "tid")
check(t == ("111", "tid", False), "telegram origin empty thread")

print(f"ALL RESOLVER TESTS PASSED ({passed} checks)")
