#!/usr/bin/env python3
"""Standalone logic test for the working-memory debounce hook.

Runs the patched handle_message / flush behavior against a minimal stub —
no gateway process, no network. The hook patches
BasePlatformAdapter.handle_message (the shared inbound seam every platform
adapter routes through), so the test drives that seam directly and swaps
handler.orig_handle_message for a recorder. Verifies:

  1. messages are buffered, not dispatched immediately
  2. buffer is persisted to meta/pending-buffer.json
  3. rapid messages merge into a single flush (newline-joined)
  4. a lone "." flushes immediately and is consumed
  5. "." with an empty buffer is consumed silently
  6. recovery: a persisted buffer is reloaded and re-armed on first touch
  7. non-WM chats fall through to the original handler untouched

Run from the package dir with the Hermes venv python:
  HERMES_HOME=$(mktemp -d) PYTHONPATH=$HERMES_HOME/hermes-agent \
    $HERMES_HOME/hermes-agent/venv/bin/python tests/test_debounce.py
"""

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

PKG = pathlib.Path(__file__).resolve().parent.parent
REPO = pathlib.Path(
    os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes")
) / "hermes-agent"


def load_handler():
    spec = importlib.util.spec_from_file_location(
        "wm_handler_test", str(PKG / "hooks/working-memory-debounce/handler.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wm_handler_test"] = mod
    spec.loader.exec_module(mod)  # runs install_patches() at import
    return mod


def build_events(chat="111"):
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource
    from gateway.config import Platform

    def ev(text, cid=chat):
        return MessageEvent(
            text=text,
            source=SessionSource(
                platform=Platform.TELEGRAM, chat_id=cid, user_id="9"
            ),
        )

    return ev


def make_stub(mod, recovered=True):
    # Plain stub carrying the hook's runtime state, with the patched
    # seam attached: install_patches() replaces
    # BasePlatformAdapter.handle_message with wm_handle_message, so
    # attaching that exact function exercises the real hook code path.
    from gateway.platforms.base import BasePlatformAdapter

    class Stub:
        def __init__(self):
            self._wm_buffers = {}
            self._wm_tasks = {}
            self._wm_recovered = recovered

    Stub.handle_message = BasePlatformAdapter.handle_message
    return Stub()


async def scenario(mod, tmp, ev):
    # Replace the flush/dispatch sink: the hook's _wm_flush and
    # _dispatch_now call the module-global orig_handle_message, which
    # normally dispatches into a live session — in this test it records.
    orig_calls = []

    async def _record_orig(adapter, event):
        orig_calls.append(event.text or "")

    mod.orig_handle_message = _record_orig

    # 1-3: buffering, persistence, merge + flush
    s = make_stub(mod)
    await s.handle_message(ev("first message"))
    assert orig_calls == [], "must not dispatch before the debounce elapses"
    assert s._wm_buffers["telegram:111:"].auto_skill == "working-memory", (
        "buffered event must carry auto_skill for deterministic skill injection"
    )
    pf = tmp / "wm/meta/pending-buffer.json"
    assert pf.exists(), "buffer must be persisted on every message"
    blob = json.loads(pf.read_text())
    assert "telegram:111:" in blob and blob["telegram:111:"]["text"] == "first message", blob
    await s.handle_message(ev("second message"))
    await asyncio.sleep(0.6)
    assert orig_calls == ["first message\nsecond message"], orig_calls
    assert not pf.exists() or json.loads(pf.read_text()) == {}, "buffer cleared after flush"

    # 4-5: "." manual flush (dispatch is async — yield to the loop first)
    s2 = make_stub(mod)
    await s2.handle_message(ev("alpha"))
    await s2.handle_message(ev("."))
    await asyncio.sleep(0.05)
    assert orig_calls == ["first message\nsecond message", "alpha"], orig_calls
    await s2.handle_message(ev("."))
    await asyncio.sleep(0.05)
    assert orig_calls == ["first message\nsecond message", "alpha"], "'.' with empty buffer consumed silently"

    # 6: recovery
    from gateway.session import SessionSource
    from gateway.config import Platform
    src = SessionSource(platform=Platform.TELEGRAM, chat_id="111", user_id="9").to_dict()
    (tmp / "wm/meta").mkdir(parents=True, exist_ok=True)
    (tmp / "wm/meta/pending-buffer.json").write_text(
        json.dumps({"telegram:111:": {"text": "recovered thought", "message_id": None,
                                      "source": src, "media_urls": [], "media_types": [],
                                      "buffered_at": "x"}})
    )
    s3 = make_stub(mod, recovered=False)
    await s3.handle_message(ev("continuation"))
    assert s3._wm_buffers["telegram:111:"].auto_skill == "working-memory", (
        "recovered event must also carry auto_skill"
    )
    await asyncio.sleep(0.6)
    assert orig_calls == ["first message\nsecond message", "alpha", "recovered thought\ncontinuation"], orig_calls
    # 7: non-WM chat falls through to the original handler untouched
    s4 = make_stub(mod)
    await s4.handle_message(ev("normal message", cid="222"))
    assert orig_calls == ["first message\nsecond message", "alpha", "recovered thought\ncontinuation", "normal message"], orig_calls
    assert not s4._wm_buffers, "non-WM chat must not enter the WM buffer"
    print("ALL DEBOUNCE TESTS PASSED")


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="wm-debounce-test-"))
    (tmp / "working-memory.env").write_text(
        f"WM_ROOT={tmp / 'wm'}\nWM_DEBOUNCE_SECONDS=0.2\nWM_TELEGRAM_CHAT_ID=111\n"
    )
    os.environ["HERMES_HOME"] = str(tmp)
    sys.path.insert(0, str(REPO))

    mod = load_handler()
    ev = build_events()
    asyncio.run(scenario(mod, tmp, ev))


if __name__ == "__main__":
    main()
