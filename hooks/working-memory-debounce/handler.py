"""Working-memory debounce hook for the Hermes Telegram adapter.

Installed at ~/.hermes/hooks/working-memory-debounce/ and loaded by the
gateway's HookRegistry at startup (gateway/run.py -> gateway/hooks.py).

At import time this module monkey-patches two methods on TelegramAdapter:

  _enqueue_text_event
      Only for the DEDICATED working-memory chat(s) configured via
      WM_TELEGRAM_CHAT_ID in ~/.hermes/working-memory.env (spec
      Section 2 — optional WM_TELEGRAM_THREAD_ID narrows to a DM-topic
      lane). In those chats it replaces the adapter's short client-split
      batching with the working-memory debounce: each text message is
      appended to a per-chat buffer, persisted to
      $WM_ROOT/meta/pending-buffer.json, and the whole buffer is flushed
      as a SINGLE agent turn once WM_DEBOUNCE_SECONDS of silence elapses.
      A lone "." flushes immediately and is consumed (never buffered).
      Every other chat falls through to the original batching untouched
      and behaves exactly as before. Empty WM_TELEGRAM_CHAT_ID = WM
      disabled everywhere.

  _handle_command
      "/done" flushes the chat's buffer immediately and is consumed,
      avoiding the gateway's "Unknown command" reply. All other commands
      fall through to the original handler.

The flush dispatches through the adapter's normal handle_message() path,
so the combined text reaches the agent as one ordinary user message; the
working-memory SKILL.md guides extraction/tagging/filing/answering.

Crash recovery (spec Section 10): every buffered chunk is persisted to
pending-buffer.json. On the next gateway start the buffers are reloaded
lazily on the first message for a chat and re-armed, so a thought is
never dropped — worst case it waits for the next message (or a "." or
"/done") before flushing.
"""

import asyncio
import json
import os
import pathlib
from datetime import datetime

HOOK_NAME = "working-memory-debounce"
HERMES_HOME = pathlib.Path(
    os.environ.get("HERMES_HOME") or str(pathlib.Path.home() / ".hermes")
)


def _load_env_file(path):
    env = {}
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


WM_ENV = _load_env_file(HERMES_HOME / "working-memory.env")
WM_ROOT = pathlib.Path(
    WM_ENV.get("WM_ROOT") or str(pathlib.Path.home() / "working-memory")
)
try:
    WM_DEBOUNCE = float(WM_ENV.get("WM_DEBOUNCE_SECONDS", "25"))
except ValueError:
    WM_DEBOUNCE = 25.0
# Dedicated working-memory chat(s) ONLY (spec Section 2). Messages in any
# other chat fall through to normal Hermes behavior. Empty = WM disabled.
WM_CHAT_IDS = {
    c.strip()
    for c in WM_ENV.get("WM_TELEGRAM_CHAT_ID", "").split(",")
    if c.strip()
}
# Optional thread scoping for DM-topic lanes (same chat_id, distinct
# message_thread_id). Empty = any thread in the WM chat is watched.
WM_THREAD_IDS = {
    c.strip()
    for c in WM_ENV.get("WM_TELEGRAM_THREAD_ID", "").split(",")
    if c.strip()
}
# Skill auto-loaded into the WM lane's session (spec Section 2: the
# dedicated chat is what makes a message working-memory input; this makes
# the agent deterministically follow the policy instead of hoping it
# self-loads the skill). Set on the event so the gateway injects it on
# new sessions (run.py auto_skill handling).
WM_SKILL = WM_ENV.get("WM_SKILL", "working-memory").strip() or "working-memory"
PENDING_FILE = WM_ROOT / "meta" / "pending-buffer.json"


def _log(component, event, outcome, **extra) -> None:
    """Append one JSON line to logs/YYYY-MM.log (spec Section 11).

    This is the diagnostic trail that makes "why didn't X happen"
    answerable after the fact. Never raises — logging must not break
    the message pipeline.
    """
    try:
        log_dir = WM_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "component": component,
            "event": event,
            "outcome": outcome,
            **extra,
        }
        log_file = log_dir / f"{datetime.now():%Y-%m}.log"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception as exc:
        print(f"[hooks] WM log failed: {exc}", flush=True)


def _wm_enabled(source) -> bool:
    """True only in the dedicated working-memory chat (spec Section 2).

    Empty WM_TELEGRAM_CHAT_ID = working-memory disabled entirely. All
    other chats are completely unaffected and behave as normal.
    """
    if not WM_CHAT_IDS:
        return False
    cid = str(getattr(source, "chat_id", "") or "")
    if cid not in WM_CHAT_IDS:
        return False
    if WM_THREAD_IDS:
        tid = str(getattr(source, "thread_id", "") or "")
        if tid not in WM_THREAD_IDS:
            return False
    return True


def _persist(adapter) -> None:
    """Write all in-memory WM buffers to pending-buffer.json (atomic)."""
    data = {}
    for key, event in getattr(adapter, "_wm_buffers", {}).items():
        src = getattr(event, "source", None)
        data[key] = {
            "text": event.text or "",
            "message_id": event.message_id,
            "source": src.to_dict() if hasattr(src, "to_dict") else {},
            "media_urls": list(event.media_urls or []),
            "media_types": list(event.media_types or []),
            "buffered_at": datetime.now().isoformat(),
        }
    try:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(PENDING_FILE)
    except Exception as exc:  # never break the message pipeline over persistence
        print(f"[hooks] WM persist failed: {exc}", flush=True)


def _recover(adapter) -> None:
    """Reload persisted buffers after a gateway restart and re-arm timers."""
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource

    for key, blob in data.items():
        if key in getattr(adapter, "_wm_buffers", {}):
            continue
        try:
            event = MessageEvent(
                text=blob.get("text", ""),
                source=SessionSource.from_dict(blob.get("source", {})),
                message_id=blob.get("message_id"),
                media_urls=list(blob.get("media_urls", [])),
                media_types=list(blob.get("media_types", [])),
            )
        except Exception as exc:
            print(f"[hooks] WM recover skip {key}: {exc}", flush=True)
            continue
        adapter._wm_buffers[key] = event
        event.auto_skill = WM_SKILL  # guarantee the skill is injected on the new session
        adapter._wm_tasks[key] = asyncio.create_task(_wm_flush(adapter, key))
        print(f"[hooks] WM recovered pending buffer {key}", flush=True)
        _log("debounce-hook", "buffer-recovered", "ok", key=key, chars=len(event.text or ""))


async def _wm_flush(adapter, key: str) -> None:
    """Debounce-timer body: dispatch the buffered event as one agent turn."""
    task = asyncio.current_task()
    try:
        await asyncio.sleep(WM_DEBOUNCE)
        event = adapter._wm_buffers.pop(key, None)
        _persist(adapter)
        if event is None or not (event.text or event.media_urls):
            return
        print(
            f"[hooks] WM flush {key}: {len(event.text or '')} chars", flush=True
        )
        _log("debounce-hook", "buffer-flushed", "dispatched", key=key, chars=len(event.text or ""))
        await adapter.handle_message(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Match stock batching semantics: log loudly, don't re-dispatch
        # (a re-dispatch after a partial success could duplicate entries).
        print(f"[hooks] WM flush error {key}: {exc}", flush=True)
        _log("debounce-hook", "buffer-flushed", "error", key=key, error=str(exc))
    finally:
        if adapter._wm_tasks.get(key) is task:
            adapter._wm_tasks.pop(key, None)


async def _dispatch_now(adapter, event) -> None:
    try:
        await adapter.handle_message(event)
    except Exception as exc:
        print(f"[hooks] WM dispatch error: {exc}", flush=True)


def install_patches() -> None:
    """Monkey-patch the Telegram adapter (called once at import)."""
    from gateway.platforms import telegram as tg
    from gateway.platforms.base import MessageType

    orig_enqueue = tg.TelegramAdapter._enqueue_text_event
    orig_command = tg.TelegramAdapter._handle_command

    def _ensure_state(self) -> None:
        if not hasattr(self, "_wm_buffers"):
            self._wm_buffers = {}
        if not hasattr(self, "_wm_tasks"):
            self._wm_tasks = {}
        if not getattr(self, "_wm_recovered", False):
            self._wm_recovered = True
            _recover(self)

    def wm_enqueue(self, event):
        """Wrapper for TelegramAdapter._enqueue_text_event."""
        _ensure_state(self)
        if not _wm_enabled(event.source):
            return orig_enqueue(self, event)

        text = (event.text or "").strip()
        if text == ".":
            # Manual flush: consume the "." and dispatch what's buffered.
            key = self._text_batch_key(event)
            buffered = self._wm_buffers.pop(key, None)
            prior = self._wm_tasks.pop(key, None)
            if prior and not prior.done():
                prior.cancel()
            _persist(self)
            if buffered is not None:
                _log("debounce-hook", "manual-flush", "dispatched", trigger=".", key=key)
                asyncio.create_task(_dispatch_now(self, buffered))
            return

        key = self._text_batch_key(event)
        event.auto_skill = WM_SKILL  # deterministic skill injection on new sessions
        existing = self._wm_buffers.get(key)
        if existing is None:
            self._wm_buffers[key] = event
        else:
            if event.text:
                existing.text = (
                    f"{existing.text}\n{event.text}" if existing.text else event.text
                )
            if event.media_urls:
                existing.media_urls.extend(event.media_urls)
                existing.media_types.extend(event.media_types)
        _persist(self)

        prior = self._wm_tasks.get(key)
        if prior and not prior.done():
            prior.cancel()
        self._wm_tasks[key] = asyncio.create_task(_wm_flush(self, key))
        print(
            f"[hooks] WM buffered {key} "
            f"({len(self._wm_buffers[key].text or '')} chars)",
            flush=True,
        )

    async def wm_command(self, update, context):
        """Wrapper for TelegramAdapter._handle_command — intercepts /done."""
        try:
            msg = self._effective_update_message(update)
        except Exception:
            msg = None
        if msg is not None and getattr(msg, "text", None):
            raw = msg.text.strip()
            cmd = raw.split()[0].split("@")[0].lower() if raw else ""
            if cmd == "/done":
                chat_id = str(getattr(getattr(msg, "chat", None), "id", "") or "")
                thread_id = str(getattr(msg, "message_thread_id", "") or "")
                if not WM_CHAT_IDS or chat_id not in WM_CHAT_IDS:
                    return await orig_command(self, update, context)
                if WM_THREAD_IDS and thread_id not in WM_THREAD_IDS:
                    return await orig_command(self, update, context)
                if not self._should_process_message(msg, is_command=True):
                    return
                _ensure_state(self)
                event = self._build_message_event(
                    msg, MessageType.COMMAND, update_id=update.update_id
                )
                key = self._text_batch_key(event)
                buffered = self._wm_buffers.pop(key, None)
                prior = self._wm_tasks.pop(key, None)
                if prior and not prior.done():
                    prior.cancel()
                _persist(self)
                if buffered is not None:
                    _log("debounce-hook", "manual-flush", "dispatched", trigger="/done", key=key)
                    await self.handle_message(buffered)
                else:
                    _log("debounce-hook", "manual-flush", "empty", trigger="/done", key=key)
                    try:
                        await context.bot.send_message(
                            chat_id=msg.chat.id, text="Nothing buffered."
                        )
                    except Exception:
                        pass
                return  # consumed — never reaches the "Unknown command" path
        return await orig_command(self, update, context)

    tg.TelegramAdapter._enqueue_text_event = wm_enqueue
    tg.TelegramAdapter._handle_command = wm_command
    if not WM_CHAT_IDS:
        print(
            f"[hooks] {HOOK_NAME}: loaded but DISABLED "
            "(WM_TELEGRAM_CHAT_ID not set — set it to your dedicated "
            "working-memory chat to activate; spec Section 2)",
            flush=True,
        )
    else:
        print(
            f"[hooks] {HOOK_NAME}: patched TelegramAdapter "
            f"(debounce={WM_DEBOUNCE}s, root={WM_ROOT}, chats={sorted(WM_CHAT_IDS)})",
            flush=True,
        )


async def handle(event_type: str, context: dict) -> None:
    """Hook entry point (gateway:startup)."""
    if event_type == "gateway:startup":
        status = "disabled" if not WM_CHAT_IDS else f"watching {sorted(WM_CHAT_IDS)}"
        _log("debounce-hook", "startup", "loaded", status=status, debounce=WM_DEBOUNCE)
        print(
            f"[hooks] {HOOK_NAME}: loaded ({status}, debounce={WM_DEBOUNCE}s, "
            f"root={WM_ROOT})",
            flush=True,
        )


if not globals().get("_WM_PATCHED"):
    _WM_PATCHED = True
    install_patches()
