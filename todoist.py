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
import json
import os
import pathlib
import subprocess
import sys

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
    s.add_argument("--project", default=None, help="project name (default: TODOIST_PROJECT or 'Hermes')")

    l = sub.add_parser("list")
    l.add_argument("--project", default=None)

    c = sub.add_parser("close")
    c.add_argument("--id", required=True)

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
        if args.due:
            body["due_datetime"] = args.due
        elif args.due_string:
            body["due_string"] = args.due_string
        task = _req("POST", "tasks", body)
        print(json.dumps({"id": task["id"], "content": task["content"],
                          "due": task.get("due"), "completed_at": task.get("completed_at")}))
    elif args.cmd == "list":
        pid = project_id(args.project or default_project)
        if not pid:
            print("[]")
            return
        tasks = (_req("GET", "tasks") or {}).get("results", [])
        for t in sorted(
            (x for x in tasks if x.get("project_id") == pid),
            key=lambda x: (x.get("due") or {}).get("datetime") or "",
        ):
            print(json.dumps({
                "id": t["id"], "content": t["content"],
                "completed_at": t.get("completed_at"),
                "due": (t.get("due") or {}).get("date"),
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
