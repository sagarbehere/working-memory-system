#!/usr/bin/env python3
"""Deterministic storage layer for v3 structured Records (second-brain-schema.md §9).

Generic SQLite table, one per install — never one table per domain:
    records(id, type, domain, occurred_at, entity, data_json, notes)

Commands: init | add | query | recent | backup
Root: WM_ROOT env var, default ~/working-memory. Stdlib only (sqlite3 ships with Python).
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

DEFAULT_ROOT = os.path.expanduser(os.environ.get("WM_ROOT", "~/working-memory"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    domain TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    entity TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_domain   ON records(domain);
CREATE INDEX IF NOT EXISTS idx_records_type     ON records(type);
CREATE INDEX IF NOT EXISTS idx_records_entity   ON records(entity);
CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at);
"""


def db_path(root):
    return os.path.join(root, "records.db")


def connect(root):
    os.makedirs(root, exist_ok=True)
    con = sqlite3.connect(db_path(root))
    con.execute("PRAGMA journal_mode=WAL")
    return con


def cmd_init(root):
    con = connect(root)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    print(f"initialized {db_path(root)}")


def cmd_add(root, args):
    con = connect(root)
    con.executescript(SCHEMA)
    data = json.loads(args.json) if args.json else {}
    cur = con.execute(
        "INSERT INTO records(type, domain, occurred_at, entity, data_json, notes) "
        "VALUES (?,?,?,?,?,?)",
        (args.type, args.domain, args.occurred_at, args.entity,
         json.dumps(data, ensure_ascii=False), args.notes),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    print(f"added record {rid}")


def cmd_query(root, args):
    con = connect(root)
    con.executescript(SCHEMA)
    where, params = [], []
    for col, val in (("type", args.type), ("domain", args.domain), ("entity", args.entity)):
        if val:
            if "%" in val or "_" in val:
                where.append(f"{col} LIKE ?")
            else:
                where.append(f"{col} = ?")
            params.append(val)
    if args.since:
        where.append("occurred_at >= ?"); params.append(args.since)
    if args.until:
        where.append("occurred_at <= ?"); params.append(args.until)
    sql = "SELECT id, type, domain, occurred_at, entity, data_json, notes FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_at" + (" DESC" if args.desc else " ASC") + " LIMIT ?"
    params.append(args.limit)
    rows = con.execute(sql, params).fetchall()
    con.close()
    for r in rows:
        print(json.dumps({
            "id": r[0], "type": r[1], "domain": r[2], "occurred_at": r[3],
            "entity": r[4], "data": json.loads(r[5]), "notes": r[6],
        }, ensure_ascii=False))


def cmd_recent(root, args):
    cmd_query(root, argparse.Namespace(type=None, domain=None, entity=None,
                                       since=None, until=None, limit=args.limit, desc=True))


def cmd_backup(root, args):
    con = connect(root)
    out = args.out or os.path.join(root, f"records-{datetime.now().strftime('%Y-%m-%d')}.db")
    dest = sqlite3.connect(out)
    try:
        con.backup(dest)  # safe against concurrent writes, unlike a raw copy
    finally:
        dest.close()
        con.close()
    print(f"backed up to {out}")


def main():
    p = argparse.ArgumentParser(description="v3 structured records store (schema §9)")
    p.add_argument("--root", default=DEFAULT_ROOT, help="WM_ROOT (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    a = sub.add_parser("add")
    a.add_argument("--type", required=True)
    a.add_argument("--domain", required=True)
    a.add_argument("--occurred-at", required=True, help="ISO-8601, e.g. 2026-08-20T09:15:00+05:30")
    a.add_argument("--entity")
    a.add_argument("--json", default="{}", help="domain-specific fields")
    a.add_argument("--notes")

    q = sub.add_parser("query")
    q.add_argument("--type")
    q.add_argument("--domain")
    q.add_argument("--entity")
    q.add_argument("--since")
    q.add_argument("--until")
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--desc", action="store_true")

    b = sub.add_parser("backup")
    b.add_argument("--out")

    r = sub.add_parser("recent")
    r.add_argument("--limit", type=int, default=10)

    args = p.parse_args()
    if args.cmd == "init":
        cmd_init(args.root)
    elif args.cmd == "add":
        cmd_add(args.root, args)
    elif args.cmd == "query":
        cmd_query(args.root, args)
    elif args.cmd == "recent":
        cmd_recent(args.root, args)
    elif args.cmd == "backup":
        cmd_backup(args.root, args)


if __name__ == "__main__":
    main()
