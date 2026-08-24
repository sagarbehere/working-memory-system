#!/usr/bin/env python3
"""Standalone logic test for the working-memory debounce hook.

Runs the patched _enqueue_text_event / flush behavior against a stub
adapter — no gateway, no network. Verifies:

  1. messages are buffered, not dispatched immediately
  2. buffer is persisted to meta/pending-buffer.json
  3. rapid messages merge into a single flush (newline-joined)
  4. a lone "." flushes immediately and is consumed
  5. "." with an empty buffer is consumed silently
  6. recovery: a persisted buffer is reloaded and re-armed on first touch

Run from the package dir:
  HERMES_HOME=$(mktemp -d) PYTHONPATH=/home/hermes/.hermes/hermes-agent \
    /home/hermes/.hermes/hermes-agent/venv/bin/python tests/test_debounce.py
"""

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

PKG = pathlib.Path(__file__).resolve().parent.parent
REPO = "/home/hermes/.hermes/hermes-agent"


def load_handler():
    spec = importlib.util.spec_from_file_location(
        "wm_handler_test", str(PKG / "hooks/working-memory-debounce/handler.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wm_handler_test"] = mod
    spec.loader.exec_module(mod)  # runs install_patches() at import
    return mod


def build_events(mod, chat="111"):
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


def make_stub(tg, recovered=True, chat="111"):
    class Stub(tg.TelegramAdapter):
        def __init__(self):
            self._wm_buffers = {}
            self._wm_tasks = {}
            self._wm_recovered = recovered
            self.dispatched = []
            # For the original (non-WM) batching path fall-through test:
            self._pending_text_batches = {}
            self._pending_text_batch_tasks = {}
            self._text_batch_delay_seconds = 0.05

        def _text_batch_key(self, event):
            return f"key:{event.source.chat_id}"

        async def handle_message(self, event):
            self.dispatched.append(event.text or "")

    return Stub()


async def scenario(mod, tmp, ev):
    # 1-3: buffering, persistence, merge + flush
    s = make_stub(__import__("gateway.platforms.telegram", fromlist=["x"]))
    s._enqueue_text_event(ev("first message"))
    assert s.dispatched == [], "must not dispatch before the debounce elapses"
    pf = tmp / "wm/meta/pending-buffer.json"
    assert pf.exists(), "buffer must be persisted on every message"
    blob = json.loads(pf.read_text())
    assert "key:111" in blob and blob["key:111"]["text"] == "first message", blob
    s._enqueue_text_event(ev("second message"))
    await asyncio.sleep(0.6)
    assert s.dispatched == ["first message\nsecond message"], s.dispatched
    assert not pf.exists() or json.loads(pf.read_text()) == {}, "buffer cleared after flush"

    # 4-5: "." manual flush (dispatch is async — yield to the loop first)
    s2 = make_stub(__import__("gateway.platforms.telegram", fromlist=["x"]))
    s2._enqueue_text_event(ev("alpha"))
    s2._enqueue_text_event(ev("."))
    await asyncio.sleep(0.05)
    assert s2.dispatched == ["alpha"], s2.dispatched
    s2._enqueue_text_event(ev("."))
    await asyncio.sleep(0.05)
    assert s2.dispatched == ["alpha"], "'.' with empty buffer consumed silently"

    # 6: recovery
    from gateway.session import SessionSource
    from gateway.config import Platform
    src = SessionSource(platform=Platform.TELEGRAM, chat_id="111", user_id="9").to_dict()
    (tmp / "wm/meta").mkdir(parents=True, exist_ok=True)
    (tmp / "wm/meta/pending-buffer.json").write_text(
        json.dumps({"key:111": {"text": "recovered thought", "message_id": None,
                                "source": src, "media_urls": [], "media_types": [],
                                "buffered_at": "x"}})
    )
    s3 = make_stub(__import__("gateway.platforms.telegram", fromlist=["x"]), recovered=False)
    s3._enqueue_text_event(ev("continuation"))
    await asyncio.sleep(0.6)
    assert s3.dispatched == ["recovered thought\ncontinuation"], s3.dispatched
    # 7: non-WM chat falls through to original batching (spec Section 2)
    s4 = make_stub(__import__("gateway.platforms.telegram", fromlist=["x"]))
    s4._enqueue_text_event(ev("normal message", cid="222"))
    assert "key:222" in s4._pending_text_batches, (
        "non-WM chat must use the original batching path"
    )
    assert not s4._wm_buffers, "non-WM chat must not enter the WM buffer"
    print("ALL DEBOUNCE TESTS PASSED")


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="wm-debounce-test-"))
    (tmp / "working-memory.env").write_text(
        f"WM_ROOT={tmp / 'wm'}\nWM_DEBOUNCE_SECONDS=0.2\nWM_TELEGRAM_CHAT_ID=111\n"
    )
    os.environ["HERMES_HOME"] = str(tmp)
    sys.path.insert(0, REPO)

    mod = load_handler()
    ev = build_events(mod)
    asyncio.run(scenario(mod, tmp, ev))


if __name__ == "__main__":
    main()
