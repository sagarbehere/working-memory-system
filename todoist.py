#!/usr/bin/env python3
"""Todoist mirror helper for the working-memory v3 reminder layer (schema §10).

Deterministic API client for the Todoist API v1 (api.todoist.com/api/v1/).
Uses curl (subprocess) rather than urllib — Cloudflare's edge resets urllib's
TLS handshake. Stdlib only. Token: TODOIST_API_TOKEN in ~/.hermes/.env.
Project + enable flags: TODOIST_PROJECT / TODOIST_MIRROR_ENABLED in
~/.hermes/working-memory.env.

Commands: ensure-project | create | list | close | delete | get
"""
import argparse
import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
from datetime import timezone
from urllib.parse import quote

API = "https://api.todoist.com/api/v1/"
HOME = pathlib.Path.home()
HERMES_HOME = pathlib.Path(os.environ.get("HERMES_HOME") or str(HOME / ".hermes"))


def _load_env(path):
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


def _token():
    tok = _load_env(HERMES_HOME / ".env").get("TODOIST_API_TOKEN", "").strip()
    if not tok:
        print("todoist: TODOIST_API_TOKEN not set in ~/.hermes/.env", file=sys.stderr)
        sys.exit(2)
    return tok


def _req(method, path, body=None):
    """curl-backed request. Returns parsed JSON, or None for empty bodies (204)."""
    cmd = [
        "curl", "-sf", "-m", "30", "-X", method,
        API + path,
        "-H", f"Authorization: Bearer {_token()}",
    ]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print(f"todoist API {method} {path}: {out.stderr.strip() or out.stdout.strip() or 'curl failed'}",
              file=sys.stderr)
        sys.exit(3)
    return json.loads(out.stdout) if out.stdout.strip() else None


def _projects():
    return (_req("GET", "projects") or {}).get("results", [])


def project_id(name):
    for p in _projects():
        if p.get("name") == name:
            return p["id"]
    return None


def ensure_project(name):
    pid = project_id(name)
    if pid:
        return pid
    p = _req("POST", "projects", {"name": name})
    print(f"created project: {name} ({p['id']})", file=sys.stderr)
    return p["id"]


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

    wm_env = _load_env(HERMES_HOME / "working-memory.env")
    default_project = wm_env.get("TODOIST_PROJECT", "Hermes")

    if args.cmd == "ensure-project":
        print(ensure_project(args.name or default_project))
    elif args.cmd == "create":
        pid = ensure_project(args.project or default_project)
        body = {"content": args.content, "project_id": pid}
        if args.parent:
            body["parent_id"] = args.parent
        if args.due:
            body["due_datetime"] = args.due
        elif args.due_string:
            body["due_string"] = args.due_string
        task = _req("POST", "tasks", body)
        print(json.dumps({"id": task["id"], "content": task["content"],
                          "due": task.get("due"), "completed_at": task.get("completed_at")}))
    elif args.cmd == "list":
        projects = {p["id"]: p["name"] for p in _projects()}
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
                except SystemExit:
                    t["_comments"] = None  # one failed fetch doesn't kill the list
        for t in sorted(tasks, key=lambda x: (x.get("due") or {}).get("date") or ""):
            due = t.get("due") or {}
            row = {
                "id": t["id"], "content": t["content"],
                "project": projects.get(t.get("project_id")),
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
        projects = {p["id"]: p["name"] for p in _projects()}
        # The API wants full ISO datetimes; date-only returns empty. Convert
        # YYYY-MM-DD to local-timezone day bounds, expressed in UTC.
        tz = _dt.datetime.now().astimezone().tzinfo
        since_utc = _dt.datetime.fromisoformat(args.since).replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
        until_utc = (_dt.datetime.fromisoformat(args.until).replace(tzinfo=tz, hour=23, minute=59, second=59)
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
                "project": projects.get(t.get("project_id")),
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


if __name__ == "__main__":
    main()
