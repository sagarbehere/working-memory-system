"""Working-memory capture gate hook (markers + reserved lanes).

Installed at ~/.hermes/hooks/working-memory-debounce/ and loaded by the
gateway's HookRegistry at startup (gateway/run.py -> gateway/hooks.py).

This is the one component carried through every version of the system
largely unchanged — see working-memory-system-spec.md, "Scope boundary"
and "Capture flow". It lives on the BASE adapter's inbound seam
(``BasePlatformAdapter.handle_message``), so it sees messages from every
platform (Telegram, api_server / Open WebUI, etc.), not just Telegram.
Three inputs qualify as working-memory input:

  1. Reservation / unreservation phrases ("reserve for memory" /
     "release for memory") — recorded in ``meta/lanes.json`` and passed
     through to the agent (skill auto-loaded) so the confirmation reply
     goes out through the platform-correct send path.
  2. A reserved lane — a chat previously reserved in-band (or the legacy
     env-var lane) where the marker is implied.
  3. A marker — a message starting with ``Hey memory`` (case-insensitive,
     word-boundary).

Markers and lane messages are buffered with a single debounce
(``WM_DEBOUNCE_SECONDS``, default 5s) before being flushed as ONE agent
turn, stamped with ``auto_skill`` so the working-memory skill loads
deterministically. (An earlier design considered a separate, longer
debounce for reserved lanes; that idea was abandoned — there is only
ever the one timer.) The marker is deliberately LEFT in the text: the
skill's scope guard needs to see it to route the message, and the skill
strips it at extraction time so it never appears in a raw entry.

Everything else falls through to the original handler untouched
(no-op default) — the gate costs one string prefix check + one
in-memory dict lookup per message.

Crash recovery: every buffered chunk is persisted to
``meta/pending-buffer.json``; buffers are re-armed lazily on the next
gateway start, keyed by lane (rebuilt from the stored source), so a
thought is never dropped.

Lane persistence: ``meta/lanes.json`` is a small, git-backed dict of
reserved lanes (lane key -> record). It is self-populating — entries
only ever come from explicit in-chat reservation phrases, plus the
legacy env-var seed below. Never edited by hand.
"""

import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime

HOOK_NAME = "working-memory-debounce"
# Set by install_patches(); referenced by the flush/dispatch coroutines.
orig_handle_message = None
HERMES_HOME = pathlib.Path(
    os.environ.get("HERMES_HOME") or str(pathlib.Path.home() / ".hermes")
)

# Share the package's env/time helpers when reachable. The hook directory is
# symlinked into ~/.hermes/hooks/, so resolve() lands in the package and the
# parent is its root. Guarded on purpose: this module is imported by the
# gateway at startup, and an ImportError here would silently disable capture
# for every platform — a shared helper is not worth that risk, so a failure
# falls back to the local implementations below.
try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import wmlib as _wmlib
except Exception:  # noqa: BLE001 - never break gateway startup
    _wmlib = None


def _load_env_file(path):
    if _wmlib is not None:
        return _wmlib.load_env_file(path)
    env = {}
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[key.strip()] = value
    except FileNotFoundError:
        pass
    return env


def _now():
    """Timezone-aware now. Log timestamps used to be naive local time, which
    made them incomparable with the offset-aware stamps in raw entries — and
    the consolidation gate compares exactly those two."""
    if _wmlib is not None:
        return _wmlib.now()
    return datetime.now().astimezone()


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

# The marker. Matched case-insensitively at message start, followed by
# whitespace / punctuation / end (word boundary, so "hey memories" does not
# match). See spec: Scope boundary.
#
# There used to be a short alias, ``note``. It was removed on 2026-08-29
# because it is an ordinary English sentence-opener: "Note that the deadline
# moved", "Note the difference", "Note: I disagree" were all silently filed as
# captures. A marker must be something nobody types by accident, and
# "Hey memory" is. The cost — four more characters — falls only on capture
# from an unreserved chat, since a reserved lane needs no marker at all.
MARKERS = ("hey memory",)
# Reservation phrases — exactly two, symmetric, dictionary words
# ("release" is the spellchecker-clean pair for "reserve").
# Case-insensitive; trailing words tolerated ("release for memory please").
RESERVE_PHRASE = "reserve for memory"
RELEASE_PHRASE = "release for memory"


def _log(component, event, outcome, **extra) -> None:
    """Append one JSON line to logs/YYYY-MM.log (spec: Error handling)."""
    try:
        stamp = _now()
        log_dir = WM_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": stamp.isoformat(timespec="seconds"),
            "component": component,
            "event": event,
            "outcome": outcome,
            **extra,
        }
        with (log_dir / f"{stamp:%Y-%m}.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
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
    treated as one pre-reserved lane so pre-reservation setups keep
    working unchanged (legacy seed, droppable once reservations are in
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
            "reserved_at": _now().isoformat(timespec="seconds"),
        }
    else:
        LANES.pop(key, None)
    _persist_lanes()


# --------------------------------------------------------------- marker

def _parse_marker(text) -> "str | None":
    """Return the matched marker token, or None.

    Word-boundary rule: the marker must be followed by whitespace,
    punctuation, or end-of-message. Hyphen and underscore count as word
    characters, not punctuation, so a hyphenated continuation does not match.
    """
    if not text:
        return None
    low = text.lower()
    for m in MARKERS:
        if low.startswith(m):
            nxt = low[len(m):len(m) + 1]
            if not nxt or not (nxt.isalnum() or nxt in "-_"):
                return m
    return None


def _reservation_action(text) -> "str | None":
    """Return 'reserve' / 'release' when the message is a reservation
    phrase (exact, or followed by more words), else None.

    The phrase may be followed by whitespace OR punctuation: only a trailing
    space used to count, so "Release for memory, thanks" — a natural way to
    type it — silently did nothing.

    Trailing words are tolerated deliberately ("reserve for memory please"),
    which means a sentence *about* the phrase ("reserve for memory is what you
    say") also triggers it. That false positive is accepted: it is rare, and
    it is undone by one message. Requiring an exact match would trade it for a
    more common annoyance.
    """
    if not text:
        return None
    low = text.strip().lower()
    for phrase, action in (
        (RESERVE_PHRASE, "reserve"),
        (RELEASE_PHRASE, "release"),
    ):
        if low == phrase:
            return action
        nxt = low[len(phrase):len(phrase) + 1] if low.startswith(phrase) else ""
        if nxt and not (nxt.isalnum() or nxt in "-_"):
            return action
    return None


# -------------------------------------------------------------- buffer

def _adapter_platform(adapter) -> str:
    """Best-effort platform name for an adapter instance ('' if unknown)."""
    for attr in ("platform", "PLATFORM", "name"):
        val = getattr(adapter, attr, None)
        if val is None:
            continue
        if hasattr(val, "value"):
            val = val.value
        val = str(val).strip().lower()
        if val:
            return val
    return ""


def _persist(adapter) -> None:
    """Merge this adapter's buffers into pending-buffer.json (atomic).

    MERGE, not overwrite. Every platform adapter is a separate instance with
    its own ``_wm_buffers``, but they share one pending-buffer.json — so
    writing the file from a single adapter's view erased every other
    platform's buffered thought. On a Telegram + Open WebUI install, any
    message on one platform silently dropped the other's crash-recovery
    entry. Keys not owned by this adapter are carried through untouched.
    """
    own = getattr(adapter, "_wm_buffers", {})
    data = {}
    try:  # start from what is already on disk (other adapters' keys)
        existing = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            data = existing
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    mine = set(getattr(adapter, "_wm_owned_keys", ()))
    for key in mine - set(own):
        data.pop(key, None)  # this adapter flushed it — drop it from the file

    for key, event in own.items():
        src = getattr(event, "source", None)
        data[key] = {
            "text": event.text or "",
            "message_id": event.message_id,
            "source": src.to_dict() if hasattr(src, "to_dict") else {},
            "media_urls": list(event.media_urls or []),
            "media_types": list(event.media_types or []),
            "buffered_at": _now().isoformat(timespec="seconds"),
        }
    adapter._wm_owned_keys = set(own)

    try:
        PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PENDING_FILE)
    except Exception as exc:  # never break the message pipeline over persistence
        print(f"[hooks] WM persist failed: {exc}", flush=True)


def _recover(adapter) -> None:
    """Reload this adapter's persisted buffers and re-arm their timers.

    Only keys belonging to THIS adapter's platform are claimed. Recovery
    used to load every key into whichever adapter initialised first, so a
    Telegram buffer could be re-armed on the api_server adapter and then
    flushed back out through the wrong platform's send path.
    """
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    from gateway.platforms.base import MessageEvent
    from gateway.session import SessionSource

    platform = _adapter_platform(adapter)
    for key, blob in data.items():
        if key in getattr(adapter, "_wm_buffers", {}):
            continue
        # Lane keys are "<platform>:<chat>:<thread>"; claim only our own.
        # An adapter whose platform can't be determined claims everything,
        # preserving the old behaviour rather than dropping the buffer.
        if platform and not key.startswith(platform + ":"):
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

    # Idempotence guard. This captured whatever handle_message currently was
    # and installed on top of it, so a second import under a different module
    # name (the hook dir is symlinked, which makes that easy) would capture
    # the ALREADY-PATCHED function as "original" and recurse forever on the
    # next message.
    if getattr(BasePlatformAdapter.handle_message, "_wm_patched", False):
        print(f"[hooks] {HOOK_NAME}: already installed — skipping re-patch",
              flush=True)
        return

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
        #    platform-correct send path (hook-side reply is a future
        #    optimization).
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
                # Keep a reference: a bare create_task is only weakly held by
                # the loop and can be garbage-collected mid-dispatch.
                task = asyncio.create_task(_dispatch_now(self, buffered))
                self._wm_tasks[key] = task
                task.add_done_callback(
                    lambda t, k=key: self._wm_tasks.pop(k, None)
                    if self._wm_tasks.get(k) is t else None)
            return

        event.auto_skill = WM_SKILL  # deterministic skill injection on new sessions
        # Marker stays visible — the skill's scope guard routes on it and
        # the extraction pass strips it before filing.

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

    wm_handle_message._wm_patched = True
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
                        lane = _telegram_lane_key(chat_id, thread_id)
                        _ensure_state(self)
                        # Consume /done for a reserved lane OR any chat with a
                        # live buffer: a marker-started capture ("note ...") is
                        # buffered without reserving the lane, and /done used to
                        # fall through to "Unknown command" there while "."
                        # worked — same flush, two different behaviours.
                        if lane not in LANES and lane not in self._wm_buffers:
                            return await orig_command(self, update, context)
                        if not self._should_process_message(msg, is_command=True):
                            return
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
            f"'{RESERVE_PHRASE}'",
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
