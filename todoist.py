#!/usr/bin/env python3
"""Todoist mirror helper for the working-memory v3 reminder layer (schema §10).

Deterministic API client for the Todoist API v1 (api.todoist.com/api/v1/).
Uses curl (subprocess) rather than urllib — Cloudflare's edge resets urllib's
TLS handshake. Stdlib only. Token: TODOIST_API_TOKEN in ~/.hermes/.env.
Project + enable flags: TODOIST_PROJECT / TODOIST_MIRROR_ENABLED in
~/.hermes/working-memory.env.

Commands: ensure-project | create | list | close | delete | get

Also importable as a library (reminders.py and reminder-check.py both use it,
rather than each carrying its own copy of the client): the request helpers
RAISE ``TodoistError`` / ``TodoistNotConfigured`` instead of calling
sys.exit, and only main() turns those into exit codes. An earlier version
exited from inside _req, which is why callers had to duplicate the client.
"""
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from datetime import timezone
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402

API = "https://api.todoist.com/api/v1/"
HERMES_HOME = wmlib.HERMES_HOME


class TodoistError(RuntimeError):
    """An API call failed (network, auth, rate limit, bad response)."""


class TodoistNotConfigured(TodoistError):
    """No API token — Todoist is optional, so callers may treat this as 'skip'."""


def token(required: bool = True):
    """The API token, or None when unset and required=False.

    Callers that must stay silent when Todoist simply isn't configured
    (the nightly backup watchdog) pass required=False and skip; callers
    that were asked to do Todoist work let the exception propagate.
    """
    tok = wmlib.hermes_env().get("TODOIST_API_TOKEN", "").strip()
    if not tok and required:
        raise TodoistNotConfigured(
            "TODOIST_API_TOKEN not set in ~/.hermes/.env")
    return tok or None


def enabled() -> bool:
    """True when a token exists AND TODOIST_MIRROR_ENABLED=true."""
    return bool(
        wmlib.hermes_env().get("TODOIST_API_TOKEN", "").strip()
        and wmlib.wm_env().get("TODOIST_MIRROR_ENABLED", "false").strip().lower() == "true"
    )


def default_project() -> str:
    return wmlib.wm_env().get("TODOIST_PROJECT", "Hermes") or "Hermes"


def _req(method, path, body=None, tok=None):
    """curl-backed request. Returns parsed JSON, or None for empty bodies (204)."""
    cmd = [
        "curl", "-sf", "-m", "30", "-X", method,
        API + path,
        "-H", f"Authorization: Bearer {tok or token()}",
    ]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise TodoistError(
            f"{method} {path}: "
            f"{out.stderr.strip() or out.stdout.strip() or f'curl exit {out.returncode}'}")
    try:
        return json.loads(out.stdout) if out.stdout.strip() else None
    except json.JSONDecodeError as exc:
        raise TodoistError(f"{method} {path}: bad JSON response: {exc}") from exc


def projects(tok=None):
    return (_req("GET", "projects", tok=tok) or {}).get("results", [])


def project_id(name, tok=None):
    for p in projects(tok):
        if p.get("name") == name:
            return p["id"]
    return None


def ensure_project(name, tok=None):
    pid = project_id(name, tok)
    if pid:
        return pid
    p = _req("POST", "projects", {"name": name}, tok=tok)
    if not p or not p.get("id"):
        raise TodoistError(f"could not create project {name!r}")
    print(f"created project: {name} ({p['id']})", file=sys.stderr)
    return p["id"]


def create_task(content, due=None, due_string=None, project=None, parent=None,
                tok=None, project_id_=None):
    """Create one task; returns the API's task dict.

    project_id_ lets a batch caller resolve the project once instead of
    paying a full project list per task.
    """
    tok = tok or token()
    pid = project_id_ or ensure_project(project or default_project(), tok)
    body = {"content": content, "project_id": pid}
    if parent:
        body["parent_id"] = parent
    if due:
        body["due_datetime"] = due
    elif due_string:
        body["due_string"] = due_string
    task = _req("POST", "tasks", body, tok=tok)
    if not task or not task.get("id"):
        raise TodoistError("task creation returned no id")
    return task


def open_task_ids(tok=None):
    """Set of ids of all currently-open tasks — ONE request.

    Completion reconciliation used to GET each mirrored task separately on
    every tick, so cost grew with the number of outstanding reminders and
    never fell. An id missing from this set is closed or deleted.
    """
    tasks = (_req("GET", "tasks", tok=tok) or {}).get("results", [])
    return {str(t["id"]) for t in tasks if t.get("id")}


def main():
    p = argparse.ArgumentParser(description="Todoist mirror helper (API v1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ensure-project").add_argument("--name", default=None)
    s = sub.add_parser("create")
    s.add_argument("--content", required=True)
    s.add_argument("--due", help="ISO-8601 datetime, e.g. 2026-08-29T09:00:00+05:30")
    s.add_argument("--due-string", help="natural language, e.g. 'friday 9am'")
    s.add_argument("--parent", help="parent task id (creates a subtask)")
    s.add_argument("--project", default=None, help="project name (default: TODOIST_PROJECT or 'Hermes')")

    l = sub.add_parser("list")
    l.add_argument("--project", default=None, help="filter to one project (default: all projects)")
    l.add_argument("--notes", action="store_true",
                   help="fetch comments for tasks that have them (note_count > 0)")

    c = sub.add_parser("close")
    c.add_argument("--id", required=True)

    co = sub.add_parser("completed")
    co.add_argument("--since", required=True, help="start date YYYY-MM-DD")
    co.add_argument("--until", required=True, help="end date YYYY-MM-DD")
    co.add_argument("--by", default="completion", choices=["completion", "due"],
                    help="group by completion date (default) or due date")
    co.add_argument("--project", default=None, help="filter to one project")

    d = sub.add_parser("delete")
    d.add_argument("--id", required=True)

    g = sub.add_parser("get")
    g.add_argument("--id", required=True)

    args = p.parse_args()

    default_proj = default_project()

    if args.cmd == "ensure-project":
        print(ensure_project(args.name or default_proj))
    elif args.cmd == "create":
        task = create_task(args.content, due=args.due, due_string=args.due_string,
                           project=args.project or default_proj, parent=args.parent)
        print(json.dumps({"id": task["id"], "content": task["content"],
                          "due": task.get("due"), "completed_at": task.get("completed_at")}))
    elif args.cmd == "list":
        projects_by_id = {p["id"]: p["name"] for p in projects()}
        tasks = (_req("GET", "tasks") or {}).get("results", [])
        if args.project:
            pid = project_id(args.project)
            if not pid:
                print("[]")
                return
            tasks = [t for t in tasks if t.get("project_id") == pid]
        if args.notes:
            # Comments live under /comments?task_id=; note_count is unreliable
            # in v1 (stays 0 even with comments), so fetch per task directly.
            for t in tasks:
                try:
                    data = _req("GET", f"comments?task_id={t['id']}") or {}
                    t["_comments"] = [
                        c.get("content") for c in data.get("results", [])
                        if c.get("content")
                    ]
                except TodoistError:
                    t["_comments"] = None  # one failed fetch doesn't kill the list
        for t in sorted(tasks, key=lambda x: (x.get("due") or {}).get("date") or ""):
            due = t.get("due") or {}
            row = {
                "id": t["id"], "content": t["content"],
                "project": projects_by_id.get(t.get("project_id")),
                "parent_id": t.get("parent_id"),
                "completed_at": t.get("completed_at"),
                "due": due.get("date"),
                "due_string": due.get("string"),
                "is_recurring": due.get("is_recurring"),
                "updated_at": t.get("updated_at"),
                "description": t.get("description"),
            }
            if t.get("priority", 1) != 1:  # v1 default priority is 1
                row["priority"] = t.get("priority")
            labels = t.get("labels") or []
            if labels:
                row["labels"] = labels
            if args.notes and t.get("_comments"):
                row["comments"] = t["_comments"]
            print(json.dumps(row))
    elif args.cmd == "completed":
        projects_by_id = {p["id"]: p["name"] for p in projects()}
        # The API wants full ISO datetimes; date-only returns empty. Convert
        # YYYY-MM-DD to configured-timezone day bounds, expressed in UTC.
        zone = wmlib.tz()
        since_utc = _dt.datetime.fromisoformat(args.since).replace(tzinfo=zone).astimezone(timezone.utc).isoformat()
        until_utc = (_dt.datetime.fromisoformat(args.until).replace(tzinfo=zone, hour=23, minute=59, second=59)
                     .astimezone(timezone.utc).isoformat())
        path = ("tasks/completed/by_completion_date" if args.by == "completion"
                else "tasks/completed/by_due_date")
        pid = None
        if args.project:
            pid = project_id(args.project)
            if not pid:
                print("[]")
                return
        items, cursor = [], None
        for _ in range(5):  # bounded pagination
            q = f"{path}?since={quote(since_utc)}&until={quote(until_utc)}&limit=100"
            if pid:
                q += f"&project_id={pid}"
            if cursor:
                q += f"&cursor={cursor}"
            data = _req("GET", q) or {}
            items += data.get("items", [])
            cursor = data.get("next_cursor")
            if not cursor:
                break
        for t in sorted(items, key=lambda x: x.get("completed_at") or x.get("completed_date") or ""):
            print(json.dumps({
                "id": t.get("id"), "content": t.get("content"),
                "project": projects_by_id.get(t.get("project_id")),
                "completed_at": t.get("completed_at") or t.get("completed_date"),
            }))
    elif args.cmd == "close":
        _req("POST", f"tasks/{args.id}/close")
        print(f"closed {args.id}")
    elif args.cmd == "delete":
        _req("DELETE", f"tasks/{args.id}")
        print(f"deleted {args.id}")
    elif args.cmd == "get":
        t = _req("GET", f"tasks/{args.id}")
        print(json.dumps({"id": t["id"], "content": t["content"],
                          "completed_at": t.get("completed_at")}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except TodoistNotConfigured as exc:
        print(f"todoist: {exc}", file=sys.stderr)
        sys.exit(2)   # distinct code: "not configured", not "call failed"
    except TodoistError as exc:
        print(f"todoist API {exc}", file=sys.stderr)
        sys.exit(3)
