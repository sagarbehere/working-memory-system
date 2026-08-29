#!/usr/bin/env python3
"""Deterministic storage layer for v3 structured Records (second-brain-schema.md §9).

Generic SQLite table, one per install — never one table per domain:
    records(id, type, domain, occurred_at, entity, data_json, notes,
            created_at, updated_at)

Commands: init | add | query | recent | update | delete | migrate | backup
Root: --root, else WM_ROOT, else ~/working-memory. Stdlib only.

Two invariants worth knowing before editing:

* ``occurred_at`` is stored NORMALISED TO UTC. Range filters and ORDER BY
  are string comparisons in SQLite, so mixed offsets used to sort and
  filter wrongly — two records at the same instant written as +05:30 and
  as +00:00 landed on opposite sides of a --since cutoff. The offset the
  caller supplied is preserved in ``data_json._occurred_at_local`` when it
  differs from UTC, so nothing is lost. Run ``migrate`` once to convert a
  database written before this rule.
* Text filters are EXACT unless --like is passed. Matching used to switch
  to SQL LIKE whenever a value contained % or _, which silently turned
  every snake_case entity into a wildcard: --entity blood_pressure also
  matched "bloodXpressure", with no way to ask for the literal.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402

DEFAULT_ROOT = str(wmlib.wm_root())

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

# Added after the initial release; applied by _ensure_schema on every open so
# an existing database gains them without a separate migration step.
ADDED_COLUMNS = (
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
)


def db_path(root):
    return os.path.join(root, "records.db")


def connect(root):
    os.makedirs(root, exist_ok=True)
    con = sqlite3.connect(db_path(root), timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")  # concurrent cron + agent writers
    return con


def _ensure_schema(con):
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(records)")}
    for name, decl in ADDED_COLUMNS:
        if name not in have:
            con.execute(f"ALTER TABLE records ADD COLUMN {name} {decl}")
    con.commit()


def _open(root):
    con = connect(root)
    _ensure_schema(con)
    return con


def _normalise_occurred(value):
    """(utc_iso, local_iso_or_None) for a caller-supplied timestamp.

    The local form is kept only when it differs from the UTC form, so a
    caller who already passed UTC gets no redundant bookkeeping.
    """
    aware = wmlib.parse_iso(value)
    if aware is None:
        raise ValueError(
            f"unparseable --occurred-at {value!r}: expected ISO-8601, "
            "e.g. 2026-08-20T09:15:00+05:30"
        )
    utc = aware.astimezone(timezone.utc).isoformat(timespec="seconds")
    local = aware.isoformat(timespec="seconds")
    return utc, (local if local != utc else None)


def _load_data(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("--json must be a JSON object")
    return data


def _row_to_dict(r):
    """Render a row for display.

    occurred_at is STORED as UTC (so range filters and ORDER BY, which are
    string comparisons, match chronological order) but is SHOWN in the
    configured zone — nobody should be reading UTC off their own records.
    The canonical value stays available as occurred_at_utc, and the offset
    originally supplied is surfaced only when it differs from both.
    """
    data = json.loads(r[5]) if r[5] else {}
    original = data.pop("_occurred_at_local", None)
    shown = wmlib.local_iso(r[3])
    out = {
        "id": r[0], "type": r[1], "domain": r[2],
        "occurred_at": shown,
        "occurred_at_utc": r[3],
        "entity": r[4], "data": data, "notes": r[6],
    }
    if original and original != shown:
        out["occurred_at_original"] = original
    if len(r) > 7:
        out["created_at"] = wmlib.local_iso(r[7]) if r[7] else r[7]
        out["updated_at"] = wmlib.local_iso(r[8]) if r[8] else r[8]
    return out


SELECT_COLS = ("SELECT id, type, domain, occurred_at, entity, data_json, notes, "
               "created_at, updated_at FROM records")


def cmd_init(root):
    con = _open(root)
    con.close()
    print(f"initialized {db_path(root)}")


def cmd_add(root, args):
    con = _open(root)
    data = _load_data(args.json)
    utc, local = _normalise_occurred(args.occurred_at)
    if local:
        data["_occurred_at_local"] = local
    stamp = wmlib.iso()
    cur = con.execute(
        "INSERT INTO records(type, domain, occurred_at, entity, data_json, notes,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (args.type, args.domain, utc, args.entity,
         json.dumps(data, ensure_ascii=False), args.notes, stamp, stamp),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    print(f"added record {rid}")


def _filters(args):
    """WHERE fragments + params shared by query and delete.

    Exact match by default; --like opts into SQL wildcards explicitly
    rather than inferring them from the value's punctuation.
    """
    where, params = [], []
    like = getattr(args, "like", False)
    for col, val in (("type", args.type), ("domain", args.domain), ("entity", args.entity)):
        if val:
            where.append(f"{col} LIKE ?" if like else f"{col} = ?")
            params.append(val)
    for col, val, op in (("occurred_at", args.since, ">="),
                         ("occurred_at", args.until, "<=")):
        if val:
            bound = wmlib.to_utc(val)
            if bound is None:
                raise ValueError(f"unparseable bound {val!r}: expected ISO-8601")
            where.append(f"{col} {op} ?")
            params.append(bound.isoformat(timespec="seconds"))
    return where, params


def cmd_query(root, args):
    con = _open(root)
    where, params = _filters(args)
    sql = SELECT_COLS
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_at" + (" DESC" if args.desc else " ASC") + " LIMIT ?"
    params.append(args.limit)
    rows = con.execute(sql, params).fetchall()
    con.close()
    for r in rows:
        print(json.dumps(_row_to_dict(r), ensure_ascii=False))


def cmd_recent(root, args):
    cmd_query(root, argparse.Namespace(
        type=None, domain=None, entity=None, since=None, until=None,
        limit=args.limit, desc=True, like=False))


def cmd_update(root, args):
    """Patch one record by id — the sanctioned path for a mis-filed row.

    SKILL.md tells the agent to fix mis-filed rows and never to hand-edit
    records.db; without this command those two instructions had no
    intersection. --json merges into the existing data by default so a
    correction need not restate every field; --replace-json overwrites.
    """
    con = _open(root)
    row = con.execute(SELECT_COLS + " WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        con.close()
        print(f"no record with id {args.id}", file=sys.stderr)
        return 1
    sets, params = [], []
    for col, val in (("type", args.type), ("domain", args.domain),
                     ("entity", args.entity), ("notes", args.notes)):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if args.occurred_at:
        utc, _local = _normalise_occurred(args.occurred_at)
        sets.append("occurred_at = ?")
        params.append(utc)
    if args.json or args.replace_json:
        incoming = _load_data(args.replace_json or args.json)
        if args.replace_json:
            data = incoming
        else:
            data = json.loads(row[5]) if row[5] else {}
            data.update(incoming)
        sets.append("data_json = ?")
        params.append(json.dumps(data, ensure_ascii=False))
    if not sets:
        con.close()
        print("nothing to update: pass at least one field", file=sys.stderr)
        return 1
    sets.append("updated_at = ?")
    params.append(wmlib.iso())
    params.append(args.id)
    con.execute(f"UPDATE records SET {', '.join(sets)} WHERE id = ?", params)
    con.commit()
    con.close()
    print(f"updated record {args.id}")
    return 0


def cmd_delete(root, args):
    """Remove records — the 'forget X' path. Refuses an unbounded delete.

    Requires either --id or at least one filter, so a missing argument can
    never turn into "delete everything". --dry-run prints what would go.
    """
    con = _open(root)
    if args.id:
        where, params = ["id = ?"], [args.id]
    else:
        where, params = _filters(args)
        if not where:
            con.close()
            print("refusing to delete without --id or a filter", file=sys.stderr)
            return 1
    clause = " WHERE " + " AND ".join(where)
    rows = con.execute(SELECT_COLS + clause, params).fetchall()
    if args.dry_run:
        for r in rows:
            print(json.dumps(_row_to_dict(r), ensure_ascii=False))
        print(f"would delete {len(rows)} record(s)", file=sys.stderr)
        con.close()
        return 0
    con.execute("DELETE FROM records" + clause, params)
    con.commit()
    con.close()
    print(f"deleted {len(rows)} record(s)")
    return 0


def cmd_migrate(root, args):
    """Normalise pre-existing occurred_at values to UTC. Idempotent.

    Deliberately a command rather than an automatic step on open: it
    rewrites stored rows, so it runs when you choose and after the backup
    it takes for you. Rows already in UTC are left untouched, so running
    it twice changes nothing.
    """
    con = _open(root)
    rows = con.execute("SELECT id, occurred_at, data_json FROM records").fetchall()
    pending = []
    for rid, occurred, data_raw in rows:
        aware = wmlib.parse_iso(occurred)
        if aware is None:
            print(f"  skip record {rid}: unparseable occurred_at {occurred!r}",
                  file=sys.stderr)
            continue
        utc = aware.astimezone(timezone.utc).isoformat(timespec="seconds")
        if utc == occurred:
            continue
        data = json.loads(data_raw) if data_raw else {}
        data.setdefault("_occurred_at_local", aware.isoformat(timespec="seconds"))
        pending.append((utc, json.dumps(data, ensure_ascii=False), rid))
    if not pending:
        con.close()
        print("migrate: nothing to do — all occurred_at values already UTC")
        return 0
    if args.dry_run:
        con.close()
        print(f"migrate: would rewrite {len(pending)} row(s)")
        return 0
    backup = os.path.join(root, "records.db.pre-migrate")
    dest = sqlite3.connect(backup)
    try:
        con.backup(dest)
    finally:
        dest.close()
    con.executemany(
        "UPDATE records SET occurred_at = ?, data_json = ? WHERE id = ?", pending)
    con.commit()
    con.close()
    print(f"migrate: rewrote {len(pending)} row(s); backup at {backup}")
    return 0


def cmd_backup(root, args):
    con = _open(root)
    out = args.out or os.path.join(
        root, f"records-{wmlib.now():%Y-%m-%d}.db")
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
    q.add_argument("--like", action="store_true",
                   help="treat text filters as SQL LIKE patterns (%% and _ wildcards)")

    u = sub.add_parser("update", help="patch one record by id")
    u.add_argument("--id", type=int, required=True)
    u.add_argument("--type")
    u.add_argument("--domain")
    u.add_argument("--occurred-at")
    u.add_argument("--entity")
    u.add_argument("--json", help="merge these keys into data_json")
    u.add_argument("--replace-json", help="replace data_json wholesale")
    u.add_argument("--notes")

    d = sub.add_parser("delete", help="delete by id or filter ('forget X')")
    d.add_argument("--id", type=int)
    d.add_argument("--type")
    d.add_argument("--domain")
    d.add_argument("--entity")
    d.add_argument("--since")
    d.add_argument("--until")
    d.add_argument("--like", action="store_true")
    d.add_argument("--dry-run", action="store_true", help="print matches, delete nothing")

    m = sub.add_parser("migrate", help="normalise occurred_at to UTC (idempotent)")
    m.add_argument("--dry-run", action="store_true")

    b = sub.add_parser("backup")
    b.add_argument("--out")

    r = sub.add_parser("recent")
    r.add_argument("--limit", type=int, default=10)

    args = p.parse_args()
    handlers = {
        "init": lambda: cmd_init(args.root),
        "add": lambda: cmd_add(args.root, args),
        "query": lambda: cmd_query(args.root, args),
        "recent": lambda: cmd_recent(args.root, args),
        "update": lambda: cmd_update(args.root, args),
        "delete": lambda: cmd_delete(args.root, args),
        "migrate": lambda: cmd_migrate(args.root, args),
        "backup": lambda: cmd_backup(args.root, args),
    }
    try:
        return handlers[args.cmd]() or 0
    except ValueError as exc:  # bad --json / bad timestamp: report, don't traceback
        print(f"records: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
