"""Unit tests for the capture-gate pure logic (no gateway required).

Run: python3 tests/test_gate.py   (from the package dir)
"""
import json
import os
import pathlib
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="wmtest_")
os.environ["WM_SKIP_PATCH"] = "1"          # don't monkey-patch at import
os.environ["WM_ROOT"] = TMP                 # never touch real data
# Hermetic: the hook seeds reserved lanes from the env file at import —
# point it at a scratch env so the test never depends on the host machine's
# ~/.hermes/working-memory.env (or its absence).
os.environ["HERMES_HOME"] = TMP
seed_env = pathlib.Path(TMP) / "working-memory.env"
seed_env.write_text("WM_TELEGRAM_CHAT_ID=100200300\nWM_TELEGRAM_THREAD_ID=42\n")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks" / "working-memory-debounce"))
import handler  # noqa: E402

passed = 0

def check(cond, label):
    global passed
    assert cond, f"FAIL: {label}"
    passed += 1

# --- markers (word-boundary, case-insensitive) ---
check(handler._parse_marker("note printer arrived") == "note", "note + space")
check(handler._parse_marker("Note: printer") == "note", "Note: case + colon")
check(handler._parse_marker("notebook arrived") is None, "notebook no-match")
check(handler._parse_marker("Hey memory, vitamin D") == "hey memory", "Hey memory + comma")
check(handler._parse_marker("hey memory") == "hey memory", "exact hey memory")
check(handler._parse_marker("HEY MEMORY what's due") == "hey memory", "upper case")
check(handler._parse_marker("notes app") is None, "notes no-match")
check(handler._parse_marker("note") == "note", "bare note")
check(handler._parse_marker("") is None, "empty")
check(handler._parse_marker(None) is None, "None")

# --- reservations ---
check(handler._reservation_action("reserve for memory") == "reserve", "reserve exact")
check(handler._reservation_action("Reserve for memory please") == "reserve", "reserve trailing")
check(handler._reservation_action("release for memory") == "release", "release exact")
check(handler._reservation_action("Release for memory now") == "release", "release trailing")
check(handler._reservation_action("reserve this chat") is None, "old reserve phrase gone")
check(handler._reservation_action("unreserve this chat") is None, "old unreserve phrase gone")
check(handler._reservation_action("reserve this chat for working memory") is None, "old long phrase gone")
check(handler._reservation_action("reserve a table for two") is None, "non-reservation")
check(handler._reservation_action("note printer") is None, "marker not reservation")

# --- lane keys / reservation round-trip ---
class FakePlatform:
    value = "telegram"

class FakeSource:
    platform = FakePlatform()
    chat_id = "100200300"
    thread_id = "42"

check(handler._lane_key(FakeSource()) == "telegram:100200300:42", "lane key")
check(handler._telegram_lane_key("100200300", "42") == "telegram:100200300:42", "telegram lane key")
check(handler._is_reserved(FakeSource()) is True, "env-seed lane reserved")

class FakeSource2:
    platform = FakePlatform()
    chat_id = "999"
    thread_id = "555"

check(handler._is_reserved(FakeSource2()) is False, "fresh chat not reserved")
handler._record_reservation(FakeSource2(), "reserve")
check(handler._is_reserved(FakeSource2()) is True, "reserved after phrase")

lanes_file = pathlib.Path(TMP) / "meta" / "lanes.json"
check(lanes_file.exists(), "lanes.json written")
data = json.loads(lanes_file.read_text())
check("telegram:999:555" in data, "lanes.json contains new lane")
check("telegram:100200300:42" in data, "lanes.json contains env seed")

reloaded = handler._load_lanes()
check("telegram:999:555" in reloaded, "reload picks up reservation")

handler._record_reservation(FakeSource2(), "unreserve")
check(handler._is_reserved(FakeSource2()) is False, "unreserved after phrase")
reloaded2 = handler._load_lanes()
check("telegram:999:555" not in reloaded2, "reload drops unreserved lane")
check("telegram:100200300:42" in reloaded2, "env seed survives")

# --- debounce knob ---
check(handler.WM_DEBOUNCE == 5.0, "debounce default 5s")

print(f"ALL GATE TESTS PASSED ({passed} checks)")
