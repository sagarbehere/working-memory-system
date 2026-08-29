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
  2. raw month files older than WM_RAW_RETENTION_DAYS (rotation due)
  3. log files older than 30 days (deletion due)
  4. refinement-log entries with STATUS: PENDING APPROVAL
  5. OPERATIONAL HEALTH (v3, added 2026-08-28): reminder-send failures,
     todoist mirror/reconcile failures, extraction fallbacks since the last
     consolidation; reminders.json anomalies (mirrored-without-id, unknown
     status); records.db integrity. Emitted as a separate "Health issues"
     block, only when something is actually off — a healthy system stays
     silent, so the nightly AI call is still skipped.
"""

import datetime as _dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402

ENV_PATH = str(wmlib.HERMES_HOME / "working-memory.env")
load_env = wmlib.load_env_file
# Timestamps are parsed in the CONFIGURED zone, not a hardcoded UTC+05:30.
# The gate used to bake in +05:30 while reminder-check used the system zone,
# so the two disagreed about "now" on any machine outside India — and this
# package ships an export.sh for installing on other machines.
parse_dt = wmlib.parse_iso


def read_log_entries(root: str) -> list:
    """Every parseable log line, once.

    last_consolidation_ts and health_issues each used to walk and json-parse
    the whole logs/ directory independently. One pass feeds both.
    """
    entries = []
    logs_dir = os.path.join(root, "logs")
    if not os.path.isdir(logs_dir):
        return entries
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
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            continue
    return entries


def last_consolidation_ts(entries: list):
    """Latest consolidation-event timestamp among already-read log entries."""
    latest = None
    for obj in entries:
        if obj.get("component") == "consolidation" and obj.get("event") == "consolidation":
            ts = parse_dt(str(obj.get("ts", "")))
            if ts and (latest is None or ts > latest):
                latest = ts
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


def health_issues(root: str, since, entries: list) -> list:
    """Operational health checks — return real problems only (silent when healthy).

    Local-only by design: the gate never calls external APIs. Mirror/reconcile
    failures surface here via the log lines the other components already write.
    """
    issues = []

    # 1. Failure log-lines since the last consolidation (component/event -> n)
    fail_counts = {}
    for obj in entries:
        ts = parse_dt(str(obj.get("ts", "")))
        if since is not None and ts and ts <= since:
            continue
        if obj.get("outcome") in ("failed", "retry", "unfiled-fallback"):
            key = (obj.get("component", "?"), obj.get("event", "?"))
            fail_counts[key] = fail_counts.get(key, 0) + 1
    for (comp, ev), n in sorted(fail_counts.items()):
        issues.append(f"{comp} {ev}: {n} failure(s) since last consolidation")

    # 2. reminders.json structural sanity
    rem_path = os.path.join(root, "reminders.json")
    if os.path.isfile(rem_path):
        try:
            with open(rem_path) as f:
                reminders = json.load(f)
            inconsistent = [
                r.get("id") for r in reminders
                if r.get("mirrored") and not r.get("todoist_id")
            ]
            # Kept in step with reminders.STATUSES — 'cancelled' was missing
            # here, so every cancelled reminder was reported as an anomaly.
            unknown_status = [
                r.get("id") for r in reminders
                if r.get("status") not in ("pending", "fired", "done", "cancelled")
            ]
            if inconsistent:
                issues.append("reminders marked mirrored but missing todoist_id: "
                              + ", ".join(map(str, inconsistent)))
            if unknown_status:
                issues.append("reminders with unknown status: "
                              + ", ".join(map(str, unknown_status)))
        except (OSError, ValueError) as exc:
            issues.append(f"reminders.json unreadable: {exc}")

    # 3. records.db integrity (stdlib sqlite3; missing DB = nothing to check)
    db_path = os.path.join(root, "records.db")
    if os.path.isfile(db_path):
        try:
            import sqlite3
            con = sqlite3.connect(db_path)
            try:
                row = con.execute("PRAGMA integrity_check").fetchone()
                if row and row[0] != "ok":
                    issues.append(f"records.db integrity check: {row[0]}")
            finally:
                con.close()
        except Exception as exc:
            issues.append(f"records.db check failed: {exc}")

    return issues


def main() -> int:
    env = load_env(ENV_PATH)
    # wmlib.wm_root honours the WM_ROOT process env var as well as the
    # config file; resolving it locally here ignored the env var, so the
    # gate could look at a different directory from everything else.
    root = str(wmlib.wm_root(env))
    try:
        retention = int(env.get("WM_RAW_RETENTION_DAYS", "90"))
    except ValueError:
        retention = 90

    now = wmlib.now(env)
    if not os.path.isdir(root):
        # Fail open: if we can't assess state, let the agent run and report.
        print("WM gate: WM_ROOT missing (%s) — consolidation cannot proceed; check the working-memory setup." % root)
        return 0

    entries = read_log_entries(root)
    since = last_consolidation_ts(entries)
    new_count, newest = raw_entries_since(root, since)
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
                (" (newest %s)" % wmlib.local_iso(newest)) if newest else "",
            )
        )
    for fn in rot:
        lines.append("- raw/%s older than %dd retention — rotate to raw/archive/" % (fn, retention))
    for fn in logs:
        lines.append("- logs/%s older than 30d — delete (diagnostic)" % fn)
    if pend:
        lines.append("- %d refinement entr%s with STATUS: PENDING APPROVAL" % (pend, "y" if pend == 1 else "ies"))

    if lines:
        print("Consolidation work detected:")
        print("\n".join(lines))

    health = health_issues(root, since, entries)
    if health:
        print("Health issues detected:")
        for h in health:
            print("- " + h)

    # else: print nothing → scheduler skips AI call, no session created.
    return 0


if __name__ == "__main__":
    sys.exit(main())
