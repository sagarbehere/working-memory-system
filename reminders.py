#!/usr/bin/env python3
"""Deterministic store for the v3 reminder layer (spec §9, schema §10).

The counterpart to records.py: reminders are as structured and as critical
as records, so they get the same treatment — a CLI that owns the file, and
an instruction never to hand-edit it.

WHY THIS EXISTS. reminders.json has two independent writers: the agent
(capture) and the reminder-check cron tick. The tick took an flock the
agent knew nothing about, which serialises tick-against-tick but not the
pair that actually races. A capture landing between the tick's read and its
write-back was silently erased — and the tick's critical section spans
Todoist network calls, so the window was seconds wide, every five minutes.
Every mutation here takes ``meta/reminders.lock``, so both writers
serialise against each other.

Durability order, which the lock does not by itself give you: the local
entry is written and fsynced BEFORE the Todoist call, and the mirror result
is patched in afterwards under a fresh lock. The network call therefore
happens outside the lock (a hung API cannot block a capture), and a crash
mid-mirror leaves a durable un-mirrored reminder that the next tick picks
up — never a lost one.

Commands: add | list | done | cancel | mirror | reconcile | fire-due

Entry shape:
    {id, due_at, message, raw_entry_id, status, origin{platform,chat_id,
     thread_id}, mirrored, todoist_id, created_at, fired_at?, completed_at?}
Status: pending -> fired | done | cancelled.
"""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todoist  # noqa: E402
import wmlib  # noqa: E402

STATUSES = ("pending", "fired", "done", "cancelled")
# A mirrored reminder is Todoist's to notify, so it stays pending locally
# until it is checked off there. Without a horizon, one never-completed task
# is polled forever and reconciliation cost only ever grows.
DEFAULT_RECONCILE_HORIZON_DAYS = 30
# Completion reconciliation is a convenience, not a correctness requirement:
# nothing fires off the back of it, it only reflects a Todoist check-off into
# the local store. Running it on every 5-minute tick spends 288 API calls a
# day to shorten that lag; every 30 minutes costs 48 and nobody notices.
DEFAULT_RECONCILE_EVERY_MINUTES = 30


def state_path(root):
    """Small cache: Todoist project id + last reconcile time. Disposable."""
    return pathlib.Path(root) / "meta" / "todoist-state.json"


def _load_state(root):
    try:
        with state_path(root).open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(root, state):
    try:
        wmlib.write_json_atomic(state_path(root), state)
    except OSError:
        pass  # a cache that cannot be written is not an error


def project_id(root, tok):
    """The Todoist project id, cached across runs.

    Resolving it costs a full GET /projects, and it never changes — paying
    that on every single capture doubled the API cost of writing a reminder.
    A wrong cached id surfaces as a failed create, which clears it.
    """
    name = todoist.default_project()
    state = _load_state(root)
    if state.get("project_name") == name and state.get("project_id"):
        return state["project_id"]
    pid = todoist.ensure_project(name, tok)
    state.update({"project_name": name, "project_id": pid})
    _save_state(root, state)
    return pid


def _forget_project(root):
    state = _load_state(root)
    state.pop("project_id", None)
    _save_state(root, state)


def store_path(root):
    return pathlib.Path(root) / "reminders.json"


def lock_path(root):
    return pathlib.Path(root) / "meta" / "reminders.lock"


def load(root):
    """Read the store. Missing file -> []. A corrupt file raises."""
    try:
        with store_path(root).open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError("reminders.json must contain a JSON array")
    return data


def save(root, reminders):
    wmlib.write_json_atomic(store_path(root), reminders)


def next_id(reminders, stamp=None):
    """Timestamp id with a per-second suffix, matching the raw-log scheme."""
    stamp = stamp or wmlib.now()
    base = f"{stamp:%Y%m%d-%H%M}"
    taken = {r.get("id") for r in reminders}
    for n in range(1, 100):
        candidate = f"{base}-{n:02d}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"more than 99 reminders in the same minute ({base})")


def _find(reminders, rid):
    for r in reminders:
        if str(r.get("id")) == str(rid):
            return r
    return None


def for_display(entry):
    """A copy with timestamps rendered in the configured zone.

    Stored values keep whatever offset they were captured with (the instant
    is what matters, and comparisons are timezone-aware); display always
    normalises to WM_TZ so the user never reads someone else's offset.
    Returns a COPY — callers print this, never write it back.
    """
    out = dict(entry)
    for field in ("due_at", "created_at", "fired_at", "completed_at"):
        if out.get(field):
            out[field] = wmlib.local_iso(out[field])
    return out


# --------------------------------------------------------------- origin

def resolve_origin(root, origin):
    """Validate (and where possible repair) a capture origin.

    The agent constructs `origin` from whatever it can infer about the chat,
    and it gets this wrong: an observed capture stored the lane's THREAD id
    as chat_id, which reminder-check would then have used as a Telegram chat
    address. Nothing downstream questioned it, because a present-but-wrong
    chat_id looks exactly like a valid one.

    Two repairs, both conservative:

    * A chat_id that matches no reserved lane's chat but DOES equal some
      lane's thread_id is the swap. Correct it to that lane.
    * An empty/absent origin with exactly one reserved lane adopts it.

    Deliberately NOT repaired: an unrecognised chat_id that collides with
    nothing. Marker captures ("note ...") legitimately come from chats that
    were never reserved, so an unknown chat is normal, not evidence of a bug.

    Returns (origin, note_or_None).
    """
    origin = dict(origin or {})
    lanes = wmlib.load_lanes(root)
    if not lanes:
        return origin, None

    chat = str(origin.get("chat_id") or "").strip()
    known_chats = {str(l.get("chat_id") or "") for l in lanes.values()}

    if not chat:
        if len(lanes) == 1:
            lane = next(iter(lanes.values()))
            origin.update({
                "platform": origin.get("platform") or lane.get("platform") or "telegram",
                "chat_id": str(lane.get("chat_id") or ""),
                "thread_id": str(lane.get("thread_id") or ""),
            })
            return origin, f"origin defaulted to the only reserved lane ({origin['chat_id']})"
        return origin, None

    if chat in known_chats:
        # Fill in a missing thread only when the chat has exactly one lane.
        if not str(origin.get("thread_id") or "").strip():
            matches = [l for l in lanes.values() if str(l.get("chat_id") or "") == chat]
            if len(matches) == 1 and matches[0].get("thread_id"):
                origin["thread_id"] = str(matches[0]["thread_id"])
                return origin, f"thread_id filled in from the reserved lane for chat {chat}"
        return origin, None

    for lane in lanes.values():
        if str(lane.get("thread_id") or "") == chat:
            was = dict(origin)
            origin.update({
                "platform": lane.get("platform") or "telegram",
                "chat_id": str(lane.get("chat_id") or ""),
                "thread_id": str(lane.get("thread_id") or ""),
            })
            return origin, (
                f"origin repaired: chat_id {chat!r} is a THREAD id, not a chat id "
                f"(was {was}, now chat {origin['chat_id']} thread {origin['thread_id']})")
    return origin, None


# ------------------------------------------------------------------ add

def add(root, message, due_at, raw_entry_id=None, origin=None, mirror=True):
    """Create a reminder, then mirror it to Todoist. Returns the entry.

    The two phases are deliberately separate locks: local durability first,
    network second. See the module docstring.
    """
    due = wmlib.parse_iso(due_at)
    if due is None:
        raise ValueError(
            f"unparseable --due-at {due_at!r}: expected ISO-8601, "
            "e.g. 2026-08-30T09:00:00+05:30")
    if not (message or "").strip():
        raise ValueError("--message must not be empty")

    origin, note = resolve_origin(root, origin)
    if note:
        print(f"reminders: {note}", file=sys.stderr)
        wmlib.log(root, "reminders", "origin", "repaired", detail=note)

    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        entry = {
            "id": next_id(reminders),
            "due_at": due.isoformat(timespec="seconds"),
            "message": message.strip(),
            "raw_entry_id": raw_entry_id,
            "status": "pending",
            "origin": origin or {},
            "mirrored": False,
            "todoist_id": None,
            "created_at": wmlib.iso(),
        }
        reminders.append(entry)
        save(root, reminders)
    wmlib.log(root, "reminders", "add", "ok", reminder_id=entry["id"])

    if mirror and todoist.enabled():
        try:
            tok = todoist.token()
            task = todoist.create_task(entry["message"], due=entry["due_at"],
                                       tok=tok, project_id_=project_id(root, tok))
            with wmlib.FileLock(lock_path(root)):
                reminders = load(root)
                target = _find(reminders, entry["id"])
                if target is not None:
                    target["todoist_id"] = str(task["id"])
                    target["mirrored"] = True
                    save(root, reminders)
                    entry = target
            wmlib.log(root, "reminders", "mirror", "ok",
                      reminder_id=entry["id"], todoist_id=entry["todoist_id"])
        except todoist.TodoistError as exc:
            # Not fatal: the durable entry exists and the next tick retries.
            # Drop the cached project id in case that is what went stale.
            _forget_project(root)
            wmlib.log(root, "reminders", "mirror", "failed",
                      reminder_id=entry["id"], error=str(exc))
            print(f"reminders: Todoist mirror deferred to cron: {exc}",
                  file=sys.stderr)
    return entry


# --------------------------------------------------------------- mirror

def mirror_pending(root, limit=None):
    """Catch-up: mirror pending reminders that have no todoist_id.

    Resolves the project ONCE for the batch — this used to be a full
    project-list request per reminder.
    """
    if not todoist.enabled():
        return []
    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        todo = [r for r in reminders
                if r.get("status") == "pending" and not r.get("todoist_id")]
    if not todo:
        return []
    if limit:
        todo = todo[:limit]
    try:
        tok = todoist.token()
        pid = project_id(root, tok)
    except todoist.TodoistError as exc:
        wmlib.log(root, "reminders", "mirror", "failed", error=str(exc))
        return []

    done = {}
    for r in todo:
        try:
            task = todoist.create_task(r["message"], due=r.get("due_at"),
                                       tok=tok, project_id_=pid)
            done[r["id"]] = str(task["id"])
        except todoist.TodoistError as exc:
            wmlib.log(root, "reminders", "mirror", "failed",
                      reminder_id=r.get("id"), error=str(exc))
    if not done:
        return []
    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        for rid, tid in done.items():
            target = _find(reminders, rid)
            if target is not None:
                target["todoist_id"] = tid
                target["mirrored"] = True
        save(root, reminders)
    for rid, tid in done.items():
        wmlib.log(root, "reminders", "mirror", "ok", reminder_id=rid, todoist_id=tid)
    return list(done)


def reconcile_due(root, every_minutes=None):
    """True when enough time has passed since the last reconcile.

    The 5-minute tick exists for FIRING, which must be prompt. Completion
    sync does not: it only mirrors a Todoist check-off back into the local
    store, and nothing depends on the result. Rate-limiting it here is the
    difference between 288 and 48 API calls a day.
    """
    if every_minutes is None:
        try:
            every_minutes = int(wmlib.wm_env().get(
                "TODOIST_RECONCILE_MINUTES", DEFAULT_RECONCILE_EVERY_MINUTES))
        except ValueError:
            every_minutes = DEFAULT_RECONCILE_EVERY_MINUTES
    if every_minutes <= 0:
        return True  # 0 or negative = reconcile on every tick
    last = wmlib.parse_iso(_load_state(root).get("last_reconcile_at"))
    if last is None:
        return True
    return (wmlib.now() - last).total_seconds() >= every_minutes * 60


def reconcile(root, horizon_days=DEFAULT_RECONCILE_HORIZON_DAYS):
    """Mark reminders done when their Todoist task is closed.

    ONE request for all open task ids, rather than one GET per mirrored
    reminder per tick. Anything mirrored but no longer open is complete.
    Reminders past the horizon stop being polled and are marked `fired`, so
    a task never checked off in Todoist cannot accumulate cost forever.
    """
    if not todoist.enabled():
        return [], []
    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        watched = [r for r in reminders
                   if r.get("status") == "pending" and r.get("todoist_id")]
    if not watched:
        return [], []
    try:
        open_ids = todoist.open_task_ids()
    except todoist.TodoistError as exc:
        wmlib.log(root, "reminders", "reconcile", "failed", error=str(exc))
        return [], []
    # Record the attempt, not just successes — otherwise a persistently
    # failing API would be retried on every tick, the opposite of the intent.
    state = _load_state(root)
    state["last_reconcile_at"] = wmlib.iso()
    _save_state(root, state)

    now = wmlib.now()
    closed, aged = [], []
    for r in watched:
        if str(r["todoist_id"]) not in open_ids:
            closed.append(r["id"])
            continue
        due = wmlib.parse_iso(r.get("due_at"))
        if due is not None and (now - due).days > horizon_days:
            aged.append(r["id"])
    if not (closed or aged):
        return [], []
    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        for rid in closed:
            target = _find(reminders, rid)
            if target is not None:
                target["status"] = "done"
                target["completed_at"] = wmlib.iso()
        for rid in aged:
            target = _find(reminders, rid)
            if target is not None:
                target["status"] = "fired"
                target["fired_at"] = target.get("fired_at") or wmlib.iso()
                target["delivered_via"] = "todoist-unreconciled"
        save(root, reminders)
    for rid in closed:
        wmlib.log(root, "reminders", "reconcile", "done", reminder_id=rid)
    for rid in aged:
        wmlib.log(root, "reminders", "reconcile", "aged-out",
                  reminder_id=rid, horizon_days=horizon_days)
    return closed, aged


# ------------------------------------------------------------ mutations

def set_status(root, rid, status, **fields):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        target = _find(reminders, rid)
        if target is None:
            raise ValueError(f"no reminder with id {rid}")
        target["status"] = status
        target.update(fields)
        save(root, reminders)
    wmlib.log(root, "reminders", "set-status", status, reminder_id=rid)
    return target


def update(root, rid, message=None, due_at=None, origin=None, repair_origin=False):
    """Patch one reminder — the sanctioned way to fix a mis-recorded entry.

    Without this, a reminder captured with a wrong origin could only be
    cancelled and recreated (losing its Todoist mirror and its id), or fixed
    by hand-editing the file the whole design forbids.
    """
    fields = {}
    if message is not None:
        if not message.strip():
            raise ValueError("--message must not be empty")
        fields["message"] = message.strip()
    if due_at is not None:
        due = wmlib.parse_iso(due_at)
        if due is None:
            raise ValueError(f"unparseable --due-at {due_at!r}: expected ISO-8601")
        fields["due_at"] = due.isoformat(timespec="seconds")

    with wmlib.FileLock(lock_path(root)):
        reminders = load(root)
        target = _find(reminders, rid)
        if target is None:
            raise ValueError(f"no reminder with id {rid}")
        if origin is not None or repair_origin:
            candidate = origin if origin is not None else target.get("origin")
            fixed, note = resolve_origin(root, candidate)
            fields["origin"] = fixed
            if note:
                print(f"reminders: {note}", file=sys.stderr)
        if not fields:
            raise ValueError("nothing to update: pass at least one field")
        target.update(fields)
        save(root, reminders)
    wmlib.log(root, "reminders", "update", "ok",
              reminder_id=rid, fields=sorted(fields))
    return target


def due_now(root, at=None):
    """Pending, past-due reminders that local firing owns.

    A successfully mirrored reminder is excluded: Todoist's notification is
    the reminder, and firing locally too would double-notify.
    """
    at = at or wmlib.now()
    out = []
    for r in load(root):
        if r.get("status") != "pending":
            continue
        if r.get("mirrored") and r.get("todoist_id"):
            continue
        due = wmlib.parse_iso(r.get("due_at"))
        if due is None or due > at:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.get("due_at") or "")


# -------------------------------------------------------------- the CLI

def main():
    p = argparse.ArgumentParser(description="v3 reminder store (spec §9)")
    p.add_argument("--root", default=str(wmlib.wm_root()))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="create a reminder and mirror it to Todoist")
    a.add_argument("--message", required=True)
    a.add_argument("--due-at", required=True, help="ISO-8601 with offset")
    a.add_argument("--raw-entry-id")
    a.add_argument("--origin-platform")
    a.add_argument("--origin-chat")
    a.add_argument("--origin-thread")
    a.add_argument("--no-mirror", action="store_true",
                   help="skip the Todoist call (cron catch-up will do it)")

    l = sub.add_parser("list")
    l.add_argument("--status", default="pending",
                   help="filter by status, or 'all' (default: pending)")
    l.add_argument("--due-before", help="only entries due before this ISO time")

    for name, help_text in (("done", "mark completed"), ("cancel", "cancel")):
        c = sub.add_parser(name, help=help_text)
        c.add_argument("--id", required=True)

    u = sub.add_parser("update", help="patch a reminder (message, due, origin)")
    u.add_argument("--id", required=True)
    u.add_argument("--message")
    u.add_argument("--due-at")
    u.add_argument("--origin-platform")
    u.add_argument("--origin-chat")
    u.add_argument("--origin-thread")
    u.add_argument("--repair-origin", action="store_true",
                   help="re-validate the stored origin against meta/lanes.json")

    sub.add_parser("mirror", help="catch-up: mirror unmirrored pending reminders")
    rc = sub.add_parser("reconcile", help="sync completion state from Todoist")
    rc.add_argument("--horizon-days", type=int, default=DEFAULT_RECONCILE_HORIZON_DAYS)

    fd = sub.add_parser("fire-due", help="list due reminders local firing owns")
    fd.add_argument("--at", help="evaluate as of this ISO time (testing)")

    args = p.parse_args()
    root = os.path.expanduser(args.root)

    if args.cmd == "add":
        origin = {}
        if args.origin_platform:
            origin = {"platform": args.origin_platform,
                      "chat_id": args.origin_chat or "",
                      "thread_id": args.origin_thread or ""}
        entry = add(root, args.message, args.due_at,
                    raw_entry_id=args.raw_entry_id, origin=origin,
                    mirror=not args.no_mirror)
        print(json.dumps(for_display(entry), ensure_ascii=False))
    elif args.cmd == "list":
        cutoff = wmlib.parse_iso(args.due_before) if args.due_before else None
        for r in sorted(load(root), key=lambda x: x.get("due_at") or ""):
            if args.status != "all" and r.get("status") != args.status:
                continue
            if cutoff is not None:
                due = wmlib.parse_iso(r.get("due_at"))
                if due is None or due > cutoff:
                    continue
            print(json.dumps(for_display(r), ensure_ascii=False))
    elif args.cmd == "update":
        origin = None
        if args.origin_platform or args.origin_chat or args.origin_thread:
            origin = {"platform": args.origin_platform or "telegram",
                      "chat_id": args.origin_chat or "",
                      "thread_id": args.origin_thread or ""}
        entry = update(root, args.id, message=args.message, due_at=args.due_at,
                       origin=origin, repair_origin=args.repair_origin)
        print(json.dumps(for_display(entry), ensure_ascii=False))
    elif args.cmd in ("done", "cancel"):
        status = "done" if args.cmd == "done" else "cancelled"
        extra = {"completed_at": wmlib.iso()} if status == "done" else {}
        entry = set_status(root, args.id, status, **extra)
        print(json.dumps(for_display(entry), ensure_ascii=False))
    elif args.cmd == "mirror":
        done = mirror_pending(root)
        print(f"mirrored {len(done)} reminder(s)")
    elif args.cmd == "reconcile":
        closed, aged = reconcile(root, args.horizon_days)
        print(f"reconciled {len(closed)} done, {len(aged)} aged out")
    elif args.cmd == "fire-due":
        for r in due_now(root, wmlib.parse_iso(args.at) if args.at else None):
            print(json.dumps(for_display(r), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, wmlib.LockBusy) as exc:
        print(f"reminders: {exc}", file=sys.stderr)
        sys.exit(2)
