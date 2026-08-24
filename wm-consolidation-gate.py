#!/usr/bin/env python3
"""Gate for the nightly working-memory consolidation cron job.

Prints a compact work-digest to stdout when consolidation actually has
something to do; prints NOTHING when the pass would be a no-op.

The cron scheduler injects script stdout into the agent prompt as context.
When stdout is empty, the scheduler skips the AI call entirely (before the
session id is minted) — no session row, no tokens, no delivery. This kills
the "one cron session per night even when nothing happened" accumulation
while keeping full agent-driven consolidation on nights with real work.

Work signals checked (all file-based, read-only):
  1. raw entries newer than the last logged consolidation run
  2. topic files exceeding WM_CONDENSE_SIZE
  3. raw month files older than WM_RAW_RETENTION_DAYS (rotation due)
  4. log files older than 30 days (deletion due)
  5. refinement-log entries with STATUS: PENDING APPROVAL
"""

import datetime as _dt
import json
import os
import re
import sys

ENV_PATH = os.path.expanduser("~/.hermes/working-memory.env")
DEFAULT_ROOT = os.path.expanduser("~/working-memory")


def load_env(path: str) -> dict:
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def parse_dt(s: str):
    """Parse ISO timestamp from raw entries or log lines; tz-naive → local."""
    if not s:
        return None
    s = s.strip()
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        pass
    # tz-naive: assume local time (UTC+05:30 for this VPS)
    try:
        return _dt.datetime.fromisoformat(s + "+05:30")
    except ValueError:
        return None


def last_consolidation_ts(root: str):
    """Latest consolidation-event timestamp across logs/YYYY-MM.log."""
    latest = None
    logs_dir = os.path.join(root, "logs")
    if not os.path.isdir(logs_dir):
        return None
    for fn in sorted(os.listdir(logs_dir)):
        if not re.match(r"\d{4}-\d{2}\.log$", fn):
            continue
        try:
            with open(os.path.join(logs_dir, fn)) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("component") == "consolidation" and obj.get(
                        "event"
                    ) == "consolidation":
                        ts = parse_dt(str(obj.get("ts", "")))
                        if ts and (latest is None or ts > latest):
                            latest = ts
        except OSError:
            continue
    return latest


def raw_entries_since(root: str, since):
    """Count raw entries with timestamps newer than `since`."""
    count = 0
    newest = None
    raw_dir = os.path.join(root, "raw")
    if not os.path.isdir(raw_dir):
        return 0, None
    entry_re = re.compile(r"^##\s+(\S+)")
    for fn in sorted(os.listdir(raw_dir)):
        if not re.match(r"\d{4}-\d{2}\.md$", fn):
            continue
        try:
            with open(os.path.join(raw_dir, fn)) as f:
                for line in f:
                    m = entry_re.match(line.strip())
                    if not m:
                        continue
                    ts = parse_dt(m.group(1))
                    if ts and (since is None or ts > since):
                        count += 1
                        if newest is None or ts > newest:
                            newest = ts
        except OSError:
            continue
    return count, newest


def oversized_topics(root: str, limit: int):
    out = []
    topics_dir = os.path.join(root, "topics")
    if not os.path.isdir(topics_dir):
        return out
    for fn in sorted(os.listdir(topics_dir)):
        if not fn.endswith(".md"):
            continue
        try:
            size = os.path.getsize(os.path.join(topics_dir, fn))
        except OSError:
            continue
        if size > limit:
            out.append((fn, size))
    return out


def files_due_for_rotation(root: str, retention_days: int, now):
    """raw/YYYY-MM.md files whose month is older than retention."""
    out = []
    raw_dir = os.path.join(root, "raw")
    if not os.path.isdir(raw_dir):
        return out
    for fn in sorted(os.listdir(raw_dir)):
        m = re.match(r"(\d{4})-(\d{2})\.md$", fn)
        if not m:
            continue
        try:
            fmonth = _dt.date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            continue
        age_days = (now.date() - fmonth).days
        if age_days > retention_days:
            out.append(fn)
    return out


def logs_due_for_deletion(root: str, cutoff_days: int, now):
    """logs/YYYY-MM.log files older than cutoff (diagnostic, deletable)."""
    out = []
    logs_dir = os.path.join(root, "logs")
    if not os.path.isdir(logs_dir):
        return out
    for fn in sorted(os.listdir(logs_dir)):
        m = re.match(r"(\d{4})-(\d{2})\.log$", fn)
        if not m:
            continue
        try:
            fmonth = _dt.date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            continue
        if (now.date() - fmonth).days > cutoff_days:
            out.append(fn)
    return out


def pending_approvals(root: str) -> int:
    path = os.path.join(root, "meta", "refinement-log.md")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path) as f:
            # Anchor at line start: real status lines read "STATUS: PENDING
            # APPROVAL", while the header legend line is "- `STATUS: PENDING
            # APPROVAL` — ..." and must NOT count. (Bug fixed 2026-08-24:
            # previous bare substring match counted the legend as a pending
            # entry, making the gate fire on a false positive every run.)
            return sum(
                1
                for ln in f
                if ln.lstrip().startswith("STATUS: PENDING APPROVAL")
            )
    except OSError:
        return 0


def main() -> int:
    env = load_env(ENV_PATH)
    root = env.get("WM_ROOT", DEFAULT_ROOT)
    try:
        condense_size = int(env.get("WM_CONDENSE_SIZE", "2500"))
    except ValueError:
        condense_size = 2500
    try:
        retention = int(env.get("WM_RAW_RETENTION_DAYS", "90"))
    except ValueError:
        retention = 90

    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
    if not os.path.isdir(root):
        # Fail open: if we can't assess state, let the agent run and report.
        print("WM gate: WM_ROOT missing (%s) — consolidation cannot proceed; check the working-memory setup." % root)
        return 0

    since = last_consolidation_ts(root)
    new_count, newest = raw_entries_since(root, since)
    topics = oversized_topics(root, condense_size)
    rot = files_due_for_rotation(root, retention, now)
    logs = logs_due_for_deletion(root, 30, now)
    pend = pending_approvals(root)

    lines = []
    if new_count:
        lines.append(
            "- %d new raw entr%s since last consolidation%s"
            % (
                new_count,
                "y" if new_count == 1 else "ies",
                (" (newest %s)" % newest.isoformat()) if newest else "",
            )
        )
    for fn, size in topics:
        lines.append("- topic '%s' is %d bytes (limit %d) — condense" % (fn, size, condense_size))
    for fn in rot:
        lines.append("- raw/%s older than %dd retention — rotate to raw/archive/" % (fn, retention))
    for fn in logs:
        lines.append("- logs/%s older than 30d — delete (diagnostic)" % fn)
    if pend:
        lines.append("- %d refinement entr%s with STATUS: PENDING APPROVAL" % (pend, "y" if pend == 1 else "ies"))

    if lines:
        print("Consolidation work detected:")
        print("\n".join(lines))
    # else: print nothing → scheduler skips AI call, no session created.
    return 0


if __name__ == "__main__":
    sys.exit(main())
