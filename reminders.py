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
            task = todoist.create_task(entry["message"], due=entry["due_at"])
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
        pid = todoist.ensure_project(todoist.default_project(), tok)
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
        print(json.dumps(entry, ensure_ascii=False))
    elif args.cmd == "list":
        cutoff = wmlib.parse_iso(args.due_before) if args.due_before else None
        for r in sorted(load(root), key=lambda x: x.get("due_at") or ""):
            if args.status != "all" and r.get("status") != args.status:
                continue
            if cutoff is not None:
                due = wmlib.parse_iso(r.get("due_at"))
                if due is None or due > cutoff:
                    continue
            print(json.dumps(r, ensure_ascii=False))
    elif args.cmd in ("done", "cancel"):
        status = "done" if args.cmd == "done" else "cancelled"
        extra = {"completed_at": wmlib.iso()} if status == "done" else {}
        entry = set_status(root, args.id, status, **extra)
        print(json.dumps(entry, ensure_ascii=False))
    elif args.cmd == "mirror":
        done = mirror_pending(root)
        print(f"mirrored {len(done)} reminder(s)")
    elif args.cmd == "reconcile":
        closed, aged = reconcile(root, args.horizon_days)
        print(f"reconciled {len(closed)} done, {len(aged)} aged out")
    elif args.cmd == "fire-due":
        for r in due_now(root, wmlib.parse_iso(args.at) if args.at else None):
            print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, wmlib.LockBusy) as exc:
        print(f"reminders: {exc}", file=sys.stderr)
        sys.exit(2)
