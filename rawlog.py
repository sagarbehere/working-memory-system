#!/usr/bin/env python3
"""The raw capture log — append-only ground truth (spec §5).

Every capture is written here first, then routed to its typed destination.
This module owns the on-disk format so the agent never hand-writes it.

WHY THIS EXISTS. The entry format used to live only in SKILL.md, leaving the
agent to reproduce a structured header by hand on every capture. Two failure
modes, both SILENT:

  * A malformed header does not match the consolidation gate's
    ``^##\\s+(\\S+)`` — the entry is never counted as new work, never
    consolidated, and nothing reports it. It is simply invisible forever.
  * The per-flush id suffix (-01, -02, …) has to be derived from entries
    already written that minute. A collision breaks the ``raw_entry_id``
    links that reminders and records point back with, and again nothing
    notices.

Neither is recoverable by inspection later, which is why this is code and not
an instruction. The agent calls ``add`` and gets an id back.

Commands: add | search | recent | show

The log is append-only and never edited in place. Nothing here rewrites or
deletes an entry; rotation to raw/archive/ is the consolidation pass's job.
"""

import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402

# Fields written between the header and the body, in this order. `tags` is
# freeform (no fixed vocabulary); `domain` comes from the canonical list in
# the vault. They are deliberately different fields — see spec §5.
FIELDS = ("tags", "type", "domain", "status", "record_kind", "subtype",
          "file_ref", "supersedes")
TYPES = ("reminder", "record", "project", "reference", "idea", "unfiled")
SEPARATOR = "---"
HEADER_RE = re.compile(r"^##\s+(\S+)\s*(?:\[id:\s*([^\]]+)\])?\s*$")
# Exact-text re-sends inside this window are treated as the same capture: a
# client retry, not a second thought. Deliberately exact rather than fuzzy —
# silently dropping a genuine near-repeat is worse than storing a duplicate.
DEDUP_WINDOW_HOURS = 24


def raw_dir(root):
    return pathlib.Path(root) / "raw"


def month_file(root, when=None):
    return raw_dir(root) / f"{(when or wmlib.now()):%Y-%m}.md"


def lock_path(root):
    return pathlib.Path(root) / "meta" / "rawlog.lock"


def _norm(text):
    """Normalised form used for duplicate comparison."""
    return " ".join((text or "").split()).casefold()


# --------------------------------------------------------------- parsing

def parse_entries(text, source=""):
    """Parse a raw-log file into entry dicts. Tolerant of hand-written files.

    Entries are delimited by the HEADER line, not by the ``---`` separator.
    That distinction is load-bearing: captured text legitimately contains
    lines that are exactly ``---`` (a dictated horizontal rule, pasted
    markdown), and treating the first one as a terminator silently truncated
    the entry on read — the tail stayed on disk but vanished from every
    search, with no error. The trailing separator is still written for
    spec-compliance and readability, and is stripped here only when it is
    genuinely the last line of the block.

    Unparseable stretches are skipped rather than raising: this log is the
    ground truth and predates the CLI, so it may contain entries written by
    hand or by an older version.
    """
    entries, current = [], None
    for line in text.splitlines():
        m = HEADER_RE.match(line)
        if m:
            if current:
                entries.append(_finish(current))
            current = {"ts": m.group(1), "id": (m.group(2) or "").strip(),
                       "fields": {}, "body": [], "source": source,
                       "_in_body": False}
            continue
        if current is None:
            continue
        if not current["_in_body"]:
            if not line.strip():
                current["_in_body"] = True
                continue
            key, sep, value = line.partition(":")
            if sep and key.strip() in FIELDS:
                current["fields"][key.strip()] = value.strip()
                continue
            current["_in_body"] = True  # not a field line: body starts here
        current["body"].append(line)
    if current:
        entries.append(_finish(current))
    return entries


def _finish(entry):
    entry.pop("_in_body", None)
    body = entry.pop("body")
    while body and not body[-1].strip():
        body.pop()
    if body and body[-1].strip() == SEPARATOR:   # our own terminator, not content
        body.pop()
    entry["text"] = "\n".join(body).strip()
    for f in FIELDS:
        entry.setdefault(f, entry["fields"].get(f, ""))
    entry.pop("fields")
    return entry


def read_entries(root, include_archive=False):
    """Every entry, oldest file first. Archive is opt-in (it is large)."""
    out = []
    files = sorted(raw_dir(root).glob("*.md")) if raw_dir(root).is_dir() else []
    if include_archive:
        arch = raw_dir(root) / "archive"
        if arch.is_dir():
            files = sorted(arch.glob("*.md")) + files
    for path in files:
        try:
            out.extend(parse_entries(path.read_text(encoding="utf-8"), path.name))
        except OSError:
            continue
    return out


# --------------------------------------------------------------- writing

def next_id(entries, when):
    """`YYYYMMDD-HHMM-NN`, unique within the minute (spec §5)."""
    base = f"{when:%Y%m%d-%H%M}"
    taken = {e["id"] for e in entries if e.get("id", "").startswith(base)}
    for n in range(1, 100):
        candidate = f"{base}-{n:02d}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"more than 99 raw entries in one minute ({base})")


def find_duplicate(entries, text, when, window_hours=DEDUP_WINDOW_HOURS):
    """An identical capture within the window, or None."""
    target = _norm(text)
    if not target:
        return None
    for e in reversed(entries):
        ts = wmlib.parse_iso(e.get("ts"))
        if ts is None:
            continue
        age = (when - ts).total_seconds() / 3600.0
        if age > window_hours:
            continue
        if _norm(e.get("text")) == target:
            return e
    return None


def render(entry_id, when, text, fields):
    lines = [f"## {when.isoformat(timespec='seconds')} [id: {entry_id}]"]
    for f in FIELDS:
        value = (fields.get(f) or "").strip()
        if value:
            lines.append(f"{f}: {value}")
    lines += ["", text.strip(), "", SEPARATOR, ""]
    return "\n".join(lines)


def add(root, text, when=None, force=False, **fields):
    """Append one entry. Returns (entry_dict, was_duplicate).

    Takes the log lock so two concurrent captures cannot pick the same id.
    """
    if not (text or "").strip():
        raise ValueError("--text must not be empty")
    etype = (fields.get("type") or "").strip()
    if etype and etype not in TYPES:
        raise ValueError(f"--type must be one of {', '.join(TYPES)}")
    when = when or wmlib.now()

    with wmlib.FileLock(lock_path(root)):
        entries = read_entries(root)
        if not force:
            dup = find_duplicate(entries, text, when)
            if dup:
                return dup, True
        entry_id = next_id(entries, when)
        path = month_file(root, when)
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = render(entry_id, when, text, fields)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
    wmlib.log(root, "rawlog", "add", "ok", entry_id=entry_id, type=etype or None)
    parsed = parse_entries(blob, path.name)
    return parsed[0], False


# ------------------------------------------------------------- retrieval

def search(root, tag=None, text=None, etype=None, since=None, until=None,
           limit=20, include_archive=True):
    """Entries matching every supplied filter, newest first.

    Replaces the agent reading raw files itself — improvising that is how a
    capture session ended up guessing at file paths.
    """
    since_dt = wmlib.parse_iso(since) if since else None
    until_dt = wmlib.parse_iso(until) if until else None
    needle = _norm(text) if text else None
    out = []
    for e in read_entries(root, include_archive=include_archive):
        if etype and e.get("type") != etype:
            continue
        if tag:
            pool = {t.strip().casefold()
                    for field in ("tags", "domain")
                    for t in (e.get(field) or "").split(",") if t.strip()}
            if tag.casefold() not in pool:
                continue
        if needle and needle not in _norm(e.get("text")):
            continue
        ts = wmlib.parse_iso(e.get("ts"))
        if since_dt and (ts is None or ts < since_dt):
            continue
        if until_dt and (ts is None or ts > until_dt):
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return out[:limit] if limit else out


def _display(entry):
    out = {"id": entry.get("id"), "ts": wmlib.local_iso(entry.get("ts")),
           "text": entry.get("text")}
    for f in FIELDS:
        if entry.get(f):
            out[f] = entry[f]
    return out


# -------------------------------------------------------------- the CLI

def main():
    p = argparse.ArgumentParser(description="raw capture log (spec §5)")
    p.add_argument("--root", default=str(wmlib.wm_root()))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="append one capture; prints its id")
    a.add_argument("--text", required=True)
    a.add_argument("--type", dest="etype", help=f"one of: {', '.join(TYPES)}")
    a.add_argument("--tags", help="freeform, comma-separated")
    a.add_argument("--domain", help="canonical tags, comma-separated")
    a.add_argument("--status")
    a.add_argument("--record-kind")
    a.add_argument("--subtype")
    a.add_argument("--file-ref")
    a.add_argument("--supersedes", help="raw entry id this one replaces")
    a.add_argument("--at", help="capture time (ISO-8601); default now")
    a.add_argument("--force", action="store_true",
                   help="write even if an identical entry exists in the window")

    s = sub.add_parser("search", help="find entries by tag, text, or type")
    s.add_argument("--tag")
    s.add_argument("--text")
    s.add_argument("--type", dest="etype")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--no-archive", action="store_true")

    r = sub.add_parser("recent", help="the latest entries")
    r.add_argument("--limit", type=int, default=10)

    g = sub.add_parser("show", help="one entry by id")
    g.add_argument("--id", required=True)

    args = p.parse_args()
    root = os.path.expanduser(args.root)

    if args.cmd == "add":
        entry, dup = add(
            root, args.text, when=wmlib.parse_iso(args.at) if args.at else None,
            force=args.force, type=args.etype, tags=args.tags,
            domain=args.domain, status=args.status,
            record_kind=args.record_kind, subtype=args.subtype,
            file_ref=args.file_ref, supersedes=args.supersedes)
        out = _display(entry)
        out["duplicate"] = dup
        print(json.dumps(out, ensure_ascii=False))
        if dup:
            print(f"rawlog: identical capture within {DEDUP_WINDOW_HOURS}h "
                  f"({entry['id']}) — not re-filed; use --force to override",
                  file=sys.stderr)
    elif args.cmd == "search":
        for e in search(root, tag=args.tag, text=args.text, etype=args.etype,
                        since=args.since, until=args.until, limit=args.limit,
                        include_archive=not args.no_archive):
            print(json.dumps(_display(e), ensure_ascii=False))
    elif args.cmd == "recent":
        for e in search(root, limit=args.limit):
            print(json.dumps(_display(e), ensure_ascii=False))
    elif args.cmd == "show":
        for e in read_entries(root, include_archive=True):
            if e.get("id") == args.id:
                print(json.dumps(_display(e), ensure_ascii=False))
                return 0
        print(f"no raw entry with id {args.id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, wmlib.LockBusy) as exc:
        print(f"rawlog: {exc}", file=sys.stderr)
        sys.exit(2)
