"""Working-memory v2 capture gate hook (markers + reserved lanes).

Installed at ~/.hermes/hooks/working-memory-debounce/ and loaded by the
gateway's HookRegistry at startup (gateway/run.py -> gateway/hooks.py).

v2 (spec Section 18): the gate lives on the BASE adapter's inbound seam
(``BasePlatformAdapter.handle_message``), so it sees messages from every platform
(Telegram, api_server / Open WebUI, etc.), not just Telegram. Three
inputs qualify as working-memory input:

  1. Reservation / unreservation phrases ("reserve this chat for working
     memory" / "unreserve this chat") — recorded in ``meta/lanes.json``
     and passed through to the agent (skill auto-loaded) so the
     confirmation reply goes out through the platform-correct send path.
  2. A reserved lane — a chat previously reserved in-band (or the legacy
     env-var lane) where the marker is implied.
  3. A marker — a message starting with ``Hey memory`` or ``note``
     (case-insensitive, word-boundary; spec Section 18.2).

Markers and lane messages are buffered with a debounce before being
flushed as ONE agent turn (markers: short 5s; reserved lanes: 25s, the
v1 default), stamped with ``auto_skill`` so the working-memory skill
loads deterministically. The marker is deliberately LEFT in the text:
the skill's scope guard needs to see it to route the message, and the
skill strips it at extraction time (spec Section 18.2) so it never
appears in a raw entry.

Everything else falls through to the original handler untouched
(no-op default) — the gate costs one string prefix check + one
in-memory set lookup per message (spec Section 18.8).

Crash recovery (spec Section 10): every buffered chunk is persisted to
``meta/pending-buffer.json``; buffers are re-armed lazily on the next
gateway start, keyed by lane (rebuilt from the stored source), so a
thought is never dropped.

Lane persistence (spec Section 18.3/18.5): ``meta/lanes.json`` is a
small, git-backed dict of reserved lanes (lane key -> record). It is
self-populating — entries only ever come from explicit in-chat
reservation phrases, plus the legacy env-var seed below. Never edited by
hand.
"""

import asyncio
import json
import os
import pathlib
import sys
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
# Allow WM_ROOT override via env (used by tests; otherwise runtime env file).
WM_ROOT = pathlib.Path(
    os.path.expanduser(
        os.environ.get("WM_ROOT") or WM_ENV.get("WM_ROOT") or str(pathlib.Path.home() / "working-memory")
    )
)
try:
    WM_DEBOUNCE = float(os.environ.get("WM_DEBOUNCE_SECONDS") or WM_ENV.get("WM_DEBOUNCE_SECONDS", "5"))
except ValueError:
    WM_DEBOUNCE = 5.0
WM_SKILL = (os.environ.get("WM_SKILL") or WM_ENV.get("WM_SKILL", "working-memory")).strip() or "working-memory"
PENDING_FILE = WM_ROOT / "meta" / "pending-buffer.json"
LANES_FILE = WM_ROOT / "meta" / "lanes.json"

# Markers — primary + short alias. Matched case-insensitively at message
# start, followed by whitespace / punctuation / end (word boundary, so
# "notebook" never matches "note"). Spec Section 18.2.
MARKERS = ("hey memory", "note")
# Reservation phrases — exactly two, symmetric, dictionary words
# (spec 18.3; "release" is the spellchecker-clean pair for "reserve").
# Case-insensitive; trailing words tolerated ("release for memory please").
RESERVE_PHRASE = "reserve for memory"
RELEASE_PHRASE = "release for memory"


def _log(component, event, outcome, **extra) -> None:
    """Append one JSON line to logs/YYYY-MM.log (spec Section 11)."""
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


# ---------------------------------------------------------------- lanes

def _lane_key(source) -> str:
    """Deterministic lane key from a message source: platform:chat:thread."""
    plat = getattr(source, "platform", "") or ""
    if hasattr(plat, "value"):
        plat = plat.value
    cid = str(getattr(source, "chat_id", "") or "")
    tid = str(getattr(source, "thread_id", "") or "")
    return f"{plat}:{cid}:{tid}"


def _telegram_lane_key(chat_id, thread_id) -> str:
    return f"telegram:{chat_id}:{thread_id or ''}"


def _load_lanes() -> dict:
    """lane key -> record, from lanes.json plus the legacy env-var seed.

    The env-var lane (WM_TELEGRAM_CHAT_ID / WM_TELEGRAM_THREAD_ID) is
    treated as one pre-reserved lane so v1 setups keep working unchanged
    (spec Section 18.5 — legacy seed, droppable once reservations are in
    use).
    """
    lanes = {}
    try:
        data = json.loads(LANES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            lanes.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    legacy_thread = (WM_ENV.get("WM_TELEGRAM_THREAD_ID") or "").strip()
    for cid in (
        c.strip()
        for c in WM_ENV.get("WM_TELEGRAM_CHAT_ID", "").split(",")
        if c.strip()
    ):
        key = _telegram_lane_key(cid, legacy_thread)
        lanes.setdefault(
            key,
            {
                "platform": "telegram",
                "chat_id": cid,
                "thread_id": legacy_thread,
                "reserved_at": "env-seed",
            },
        )
    return lanes


LANES = _load_lanes()


def _persist_lanes() -> None:
    """Atomic write of the reserved-lane set (git-backed under WM_ROOT)."""
    try:
        LANES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LANES_FILE.with_name(LANES_FILE.name + ".tmp")
        tmp.write_text(json.dumps(LANES, indent=2), encoding="utf-8")
        tmp.replace(LANES_FILE)
    except Exception as exc:
        print(f"[hooks] WM lanes persist failed: {exc}", flush=True)


def _is_reserved(source) -> bool:
    return _lane_key(source) in LANES


def _record_reservation(source, action: str) -> None:
    key = _lane_key(source)
    if action == "reserve":
        LANES[key] = {
            "platform": str(getattr(source, "platform", "") or ""),
            "chat_id": str(getattr(source, "chat_id", "") or ""),
            "thread_id": str(getattr(source, "thread_id", "") or ""),
            "reserved_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        LANES.pop(key, None)
    _persist_lanes()


# --------------------------------------------------------------- marker

def _parse_marker(text) -> "str | None":
    """Return the matched marker token, or None.

    Word-boundary rule: the marker must be followed by whitespace,
    punctuation, or end-of-message (spec Section 18.2).
    """
    if not text:
        return None
    low = text.lower()
    for m in MARKERS:
        if low.startswith(m):
            nxt = low[len(m):len(m) + 1]
            if not nxt or not nxt.isalnum():
                return m
    return None


def _reservation_action(text) -> "str | None":
    """Return 'reserve' / 'release' when the message is a reservation
    phrase (exact or with trailing words), else None."""
    if not text:
        return None
    low = text.strip().lower()
    for phrase, action in (
        (RESERVE_PHRASE, "reserve"),
        (RELEASE_PHRASE, "release"),
    ):
        if low == phrase or low.startswith(phrase + " "):
            return action
    return None


# -------------------------------------------------------------- buffer

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
            src = SessionSource.from_dict(blob.get("source", {}))
            event = MessageEvent(
                text=blob.get("text", ""),
                source=src,
                message_id=blob.get("message_id"),
                media_urls=list(blob.get("media_urls", [])),
                media_types=list(blob.get("media_types", [])),
            )
        except Exception as exc:
            print(f"[hooks] WM recover skip {key}: {exc}", flush=True)
            continue
        # Re-key by the current lane scheme (v1 keys were Telegram batch keys).
        new_key = _lane_key(src)
        adapter._wm_buffers[new_key] = event
        event.auto_skill = WM_SKILL  # guarantee the skill is injected on the new session
        adapter._wm_tasks[new_key] = asyncio.create_task(_wm_flush(adapter, new_key))
        print(f"[hooks] WM recovered pending buffer {new_key}", flush=True)
        _log("capture-gate", "buffer-recovered", "ok", key=new_key, chars=len(event.text or ""))


async def _wm_flush(adapter, key: str, debounce: "float | None" = None) -> None:
    """Debounce-timer body: dispatch the buffered event as one agent turn."""
    debounce = WM_DEBOUNCE if debounce is None else debounce
    task = asyncio.current_task()
    try:
        await asyncio.sleep(debounce)
        event = adapter._wm_buffers.pop(key, None)
        _persist(adapter)
        if event is None or not (event.text or event.media_urls):
            return
        print(f"[hooks] WM flush {key}: {len(event.text or '')} chars", flush=True)
        _log("capture-gate", "buffer-flushed", "dispatched", key=key, chars=len(event.text or ""))
        await orig_handle_message(adapter, event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[hooks] WM flush error {key}: {exc}", flush=True)
        _log("capture-gate", "buffer-flushed", "error", key=key, error=str(exc))
    finally:
        if adapter._wm_tasks.get(key) is task:
            adapter._wm_tasks.pop(key, None)


async def _dispatch_now(adapter, event) -> None:
    try:
        await orig_handle_message(adapter, event)
    except Exception as exc:
        print(f"[hooks] WM dispatch error: {exc}", flush=True)


# ---------------------------------------------------------------- patch

def install_patches() -> None:
    """Monkey-patch the shared inbound seam (called once at import).

    Patches BaseAdapter.handle_message (every platform's messages pass
    through it — the one seam that exists for all adapters) and keeps the
    Telegram /done interception.
    """
    global orig_handle_message
    from gateway.platforms.base import BasePlatformAdapter

    orig_handle_message = BasePlatformAdapter.handle_message

    def _ensure_state(self) -> None:
        if not hasattr(self, "_wm_buffers"):
            self._wm_buffers = {}
        if not hasattr(self, "_wm_tasks"):
            self._wm_tasks = {}
        if not getattr(self, "_wm_recovered", False):
            self._wm_recovered = True
            _recover(self)

    async def wm_handle_message(self, event) -> None:
        """Wrapper for BasePlatformAdapter.handle_message — the shared gate."""
        _ensure_state(self)
        text = (event.text or "").strip()

        # 1. Reservation / unreservation — record, then pass through so
        #    the agent replies with the confirmation via the normal,
        #    platform-correct send path (spec 18.3; hook-side reply is a
        #    future optimization).
        action = _reservation_action(text)
        if action:
            _record_reservation(event.source, action)
            event.auto_skill = WM_SKILL
            _log("capture-gate", "reservation", action, key=_lane_key(event.source))
            print(f"[hooks] WM reservation {action}: {_lane_key(event.source)}", flush=True)
            return await orig_handle_message(self, event)

        # 2/3. Marker or reserved lane → working-memory input.
        marker = _parse_marker(text)
        if not (_is_reserved(event.source) or marker):
            return await orig_handle_message(self, event)

        key = _lane_key(event.source)

        # Manual flush: a lone "." consumes the buffer (reserved lanes and
        # marker sessions alike).
        if text == ".":
            buffered = self._wm_buffers.pop(key, None)
            prior = self._wm_tasks.pop(key, None)
            if prior and not prior.done():
                prior.cancel()
            _persist(self)
            if buffered is not None:
                _log("capture-gate", "manual-flush", "dispatched", trigger=".", key=key)
                asyncio.create_task(_dispatch_now(self, buffered))
            return

        event.auto_skill = WM_SKILL  # deterministic skill injection on new sessions
        # Marker stays visible — the skill's scope guard routes on it and
        # the extraction pass strips it before filing (spec 18.2/18.3).

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

        debounce = WM_DEBOUNCE
        prior = self._wm_tasks.get(key)
        if prior and not prior.done():
            prior.cancel()
        self._wm_tasks[key] = asyncio.create_task(_wm_flush(self, key, debounce))
        print(
            f"[hooks] WM buffered {key} "
            f"({len(self._wm_buffers[key].text or '')} chars, debounce={debounce}s)",
            flush=True,
        )

    BasePlatformAdapter.handle_message = wm_handle_message

    # /done — Telegram command interception for the manual flush.
    # (Base has no _handle_command; other platforms rely on "." or the
    # debounce itself.) The telegram module moved from gateway/platforms/
    # to plugins/platforms/ in Hermes v0.20.x. Prefer an ALREADY-LOADED
    # module (the gateway imports the plugin when Telegram is enabled) —
    # never force-import it here: a plugin module can pull in heavy stack,
    # and non-Telegram installs must stay unaffected.
    tg = None
    for mod_name in (
        "plugins.platforms.telegram.adapter",
        "gateway.platforms.telegram",
    ):
        if mod_name in sys.modules:
            tg = sys.modules[mod_name]
            break
    if tg is None:
        try:
            from gateway.platforms import telegram as tg  # legacy Hermes
        except ImportError:
            tg = None
    if tg is not None:
        try:
            from gateway.platforms.base import MessageType

            orig_command = tg.TelegramAdapter._handle_command

            async def wm_command(self, update, context):
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
                        if _telegram_lane_key(chat_id, thread_id) not in LANES:
                            return await orig_command(self, update, context)
                        if not self._should_process_message(msg, is_command=True):
                            return
                        _ensure_state(self)
                        event = self._build_message_event(
                            msg, MessageType.COMMAND, update_id=update.update_id
                        )
                        key = _lane_key(event.source)
                        buffered = self._wm_buffers.pop(key, None)
                        prior = self._wm_tasks.pop(key, None)
                        if prior and not prior.done():
                            prior.cancel()
                        _persist(self)
                        if buffered is not None:
                            _log("capture-gate", "manual-flush", "dispatched", trigger="/done", key=key)
                            await orig_handle_message(self, buffered)
                        else:
                            _log("capture-gate", "manual-flush", "empty", trigger="/done", key=key)
                            try:
                                await context.bot.send_message(
                                    chat_id=msg.chat.id, text="Nothing buffered."
                                )
                            except Exception:
                                pass
                        return  # consumed — never reaches the "Unknown command" path
                return await orig_command(self, update, context)

            tg.TelegramAdapter._handle_command = wm_command
        except Exception as exc:
            print(f"[hooks] {HOOK_NAME}: /done interception unavailable: {exc}", flush=True)

    if not LANES:
        print(
            f"[hooks] {HOOK_NAME}: loaded but no lanes reserved — marker mode "
            f"only ({WM_SKILL}); reserve a chat with "
            f"'{RESERVE_PHRASE}' (spec Section 18)",
            flush=True,
        )
    else:
        print(
            f"[hooks] {HOOK_NAME}: patched BasePlatformAdapter.handle_message "
            f"(markers={MARKERS}, lanes={len(LANES)}, "
            f"debounce={WM_DEBOUNCE}s, "
            f"root={WM_ROOT})",
            flush=True,
        )


async def handle(event_type: str, context: dict) -> None:
    """Hook entry point (gateway:startup)."""
    if event_type == "gateway:startup":
        status = f"{len(LANES)} lanes, markers={MARKERS}" if LANES else f"marker-mode only ({MARKERS})"
        _log("capture-gate", "startup", "loaded", status=status, lanes=len(LANES))
        print(
            f"[hooks] {HOOK_NAME}: loaded ({status}, root={WM_ROOT})",
            flush=True,
        )


if os.environ.get("WM_SKIP_PATCH") != "1":
    install_patches()
