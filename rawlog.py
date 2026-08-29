#!/usr/bin/env python3
"""The raw capture log — an append-only verbatim transcript (spec §5).

Every capture is appended here first, then routed to its destination. This is
NOT a store: nothing references a raw entry, nothing links back to one, and
nothing is ever reconstructed from it.

WHY IT EXISTS. The unreliable part of this system is the judgment layer — an
LLM decides what a thought means and where it goes. Storage, sync, cron and
git are all boring and dependable; the classifier is not. It will
occasionally mis-file a capture, or decide a real thought was chit-chat and
file nothing at all. The vault's git history records changes to what WAS
filed; only this log sits upstream of that judgment and records what you
actually said. It is the difference between a misjudgment being recoverable
and being silent, permanent loss.

WHY IT IS SO SMALL. It used to carry ids, typed fields, and a search index,
because reminders and SQLite rows linked back to entries by id. Both are gone
(2026-08-29), so nothing consumes an id and nothing needs the classification
duplicated here — the vault note already carries it. What remains is a
timestamp and the text.

Entry format:

    ## 2026-08-29T16:03:00+05:30

    Took vitamin D pill. Next one due in a week.

Commands: add | search
"""

import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402

# The trailing `[id: …]` is not written any more, but entries captured before
# the 2026-08-29 cut carry one and MUST still parse — the transcript is
# append-only, so old and new formats coexist in the same file forever.
HEADER_RE = re.compile(r"^##\s+(\S+)\s*(?:\[id:[^\]]*\])?\s*$")
# An identical re-send inside this window is a client retry, not a second
# thought. Exact match only: silently dropping a genuine near-repeat would be
# worse than keeping a duplicate.
DEDUP_WINDOW_HOURS = 24


def month_file(root, when=None):
    return pathlib.Path(root) / "raw" / f"{(when or wmlib.now()):%Y-%m}.md"


def lock_path(root):
    return pathlib.Path(root) / "meta" / "rawlog.lock"


def _norm(text):
    return " ".join((text or "").split()).casefold()


def read_entries(root, include_archive=True):
    """Every entry as {ts, text}, oldest first. Tolerant of older formats.

    Entries are delimited by the header, never by a `---` line: captured text
    legitimately contains one (a dictated horizontal rule, pasted markdown),
    and treating it as a terminator silently truncated the entry on read.
    """
    raw = pathlib.Path(root) / "raw"
    files = []
    if include_archive and (raw / "archive").is_dir():
        files += sorted((raw / "archive").glob("*.md"))
    if raw.is_dir():
        files += sorted(raw.glob("*.md"))

    entries = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        current = None
        for line in lines:
            m = HEADER_RE.match(line)
            if m:
                if current:
                    entries.append(_finish(current))
                current = {"ts": m.group(1), "body": []}
                continue
            if current is not None:
                current["body"].append(line)
        if current:
            entries.append(_finish(current))
    return entries


def _finish(entry):
    body = entry.pop("body")
    # Historical entries carried `tags:`/`type:` field lines and a trailing
    # `---`; strip both so old and new entries read alike.
    while body and (not body[0].strip() or re.match(
            r"^(tags|type|domain|status|record_kind|subtype|file_ref|supersedes):",
            body[0].strip())):
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if body and body[-1].strip() == "---":
        body.pop()
    entry["text"] = "\n".join(body).strip()
    return entry


def add(root, text, when=None, force=False):
    """Append one capture. Returns (entry, was_duplicate)."""
    if not (text or "").strip():
        raise ValueError("--text must not be empty")
    when = when or wmlib.now()
    with wmlib.FileLock(lock_path(root)):
        if not force:
            target = _norm(text)
            for e in reversed(read_entries(root, include_archive=False)):
                ts = wmlib.parse_iso(e["ts"])
                if ts is None or (when - ts).total_seconds() / 3600.0 > DEDUP_WINDOW_HOURS:
                    continue
                if _norm(e["text"]) == target:
                    return e, True
        path = month_file(root, when)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"## {when.isoformat(timespec='seconds')}\n\n{text.strip()}\n\n")
            fh.flush()
            os.fsync(fh.fileno())
    wmlib.log(root, "rawlog", "add", "ok")
    return {"ts": when.isoformat(timespec="seconds"), "text": text.strip()}, False


def search(root, text=None, since=None, until=None, limit=20):
    """Entries matching the filters, newest first."""
    since_dt = wmlib.parse_iso(since) if since else None
    until_dt = wmlib.parse_iso(until) if until else None
    needle = _norm(text) if text else None
    out = []
    for e in read_entries(root):
        if needle and needle not in _norm(e["text"]):
            continue
        ts = wmlib.parse_iso(e["ts"])
        if since_dt and (ts is None or ts < since_dt):
            continue
        if until_dt and (ts is None or ts > until_dt):
            continue
        out.append(e)
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out[:limit] if limit else out


def main():
    p = argparse.ArgumentParser(description="raw capture transcript (spec §5)")
    p.add_argument("--root", default=str(wmlib.wm_root()))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="append one capture, verbatim")
    a.add_argument("--text", required=True)
    a.add_argument("--at", help="capture time (ISO-8601); default now")
    a.add_argument("--force", action="store_true",
                   help="append even if an identical capture is within 24h")

    s = sub.add_parser("search", help="find captures by text or date")
    s.add_argument("--text")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    root = os.path.expanduser(args.root)

    if args.cmd == "add":
        entry, dup = add(root, args.text,
                         when=wmlib.parse_iso(args.at) if args.at else None,
                         force=args.force)
        print(json.dumps({"ts": wmlib.local_iso(entry["ts"]),
                          "text": entry["text"], "duplicate": dup},
                         ensure_ascii=False))
        if dup:
            print(f"rawlog: identical capture within {DEDUP_WINDOW_HOURS}h — not "
                  f"re-filed; use --force to override", file=sys.stderr)
    elif args.cmd == "search":
        for e in search(root, text=args.text, since=args.since,
                        until=args.until, limit=args.limit):
            print(json.dumps({"ts": wmlib.local_iso(e["ts"]), "text": e["text"]},
                             ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, wmlib.LockBusy) as exc:
        print(f"rawlog: {exc}", file=sys.stderr)
        sys.exit(2)
