#!/usr/bin/env python3
"""Nightly health watchdog for the working-memory system.

Watchdog pattern: prints NOTHING when the run is healthy; prints alert
lines when something needs attention. Registered as a Hermes no_agent
cron job (03:00 daily) so a healthy night is silent and a broken one
delivers exactly the problem — no tokens, no agent.

It watches two things, and both are about the VAULT, which is where the
user's notes actually live:

  1. Quiet failures — failure lines logged in the last day. The capture
     path logs them; nobody reads logs/. Without this, a persistent
     problem stays invisible.
  2. Vault sync — fetch, then pull --ff-only when behind (devices push
     legitimately, so being behind is normal and silent). Alerts only on
     unpushed local commits or a failed pull, because a commit that never
     reaches the remote is a note that is not backed up anywhere.

Plus log pruning, which is unrelated hygiene and stays silent.

WHAT IT NO LONGER DOES (2026-08-31, see decisions.md). This was
wm-backup-push.py and it also exported open Todoist tasks, committed
WM_ROOT, and pushed it to a private remote. Those three were the "backup"
half, and they protected WM_ROOT — which since the transcript cut holds
lanes.json, logs and a disposable cache, and nothing the user minds
losing. The private remote is retired. The name changed with the job: a
file called wm-backup-push.py that does not back anything up is how the
next reader gets misled.

**This script does not write to WM_ROOT's git repo at all.** WM_ROOT is
still a git repo; nothing automated commits to it any more.

Exit 0 whenever the run completed; alerts are carried in stdout (the
no_agent scheduler delivers stdout verbatim). Exceptions are caught and
printed instead of propagating, so the scheduler's generic error alert
never duplicates the payload.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wmlib  # noqa: E402
import datetime as _dt  # noqa: E402

# The vault's remote. WM_ROOT has no remote of interest here — nothing in
# this script pushes it.
VAULT_REMOTE = "origin"
# This job runs nightly, so "recent" is the last day. No state file needed:
# a window keyed to the schedule cannot drift out of step with it.
FAILURE_WINDOW_HOURS = 24
LOG_RETENTION_DAYS = 30


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _err(r):
    return (r.stderr or r.stdout).strip()


def recent_failures(root):
    """Failure lines logged in the last day, as 'component event: N'.

    Inherited from the consolidation gate when that was removed: the gate's
    only irreplaceable job was noticing that something had been failing
    quietly. This is already the watchdog that speaks when things are wrong,
    so it is the natural home — and it needs no separate script or cron entry.
    """
    logs = os.path.join(root, "logs")
    if not os.path.isdir(logs):
        return []
    cutoff = wmlib.now() - _dt.timedelta(hours=FAILURE_WINDOW_HOURS)
    counts = {}
    for fn in sorted(os.listdir(logs)):
        if not re.match(r"\d{4}-\d{2}\.log$", fn):
            continue
        try:
            with open(os.path.join(logs, fn), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("outcome") not in ("failed", "retry", "unfiled-fallback"):
                        continue
                    ts = wmlib.parse_iso(obj.get("ts"))
                    if ts is None or ts < cutoff:
                        continue
                    key = (obj.get("component", "?"), obj.get("event", "?"))
                    counts[key] = counts.get(key, 0) + 1
        except OSError:
            continue
    return [f"{c} {e}: {n} failure(s) in the last {FAILURE_WINDOW_HOURS}h"
            for (c, e), n in sorted(counts.items())]


def prune_logs(root):
    """Delete diagnostic logs older than the retention window. Silent."""
    logs = os.path.join(root, "logs")
    if not os.path.isdir(logs):
        return
    cutoff = (wmlib.now() - _dt.timedelta(days=LOG_RETENTION_DAYS)).date()
    for fn in sorted(os.listdir(logs)):
        m = re.match(r"(\d{4})-(\d{2})\.log$", fn)
        if not m:
            continue
        try:
            month_end = _dt.date(int(m.group(1)), int(m.group(2)), 28)
        except ValueError:
            continue
        if month_end < cutoff:
            try:
                os.unlink(os.path.join(logs, fn))
            except OSError:
                pass


def main():
    alerts = []
    env = wmlib.wm_env()
    root = str(wmlib.wm_root(env))
    vault = wmlib.vault_path(env)

    # 1. Anything failing quietly? (was the consolidation gate's one real job)
    #    WM_ROOT need not be a git repo for this: logs/ is a plain directory,
    #    and nothing here commits or pushes it.
    alerts.extend(recent_failures(root))
    prune_logs(root)

    # 2. Vault sync: devices push legitimately, so pull --ff-only when behind
    #    (silent); alert only on unpushed local commits or a failed pull.
    if os.path.isdir(os.path.join(vault, ".git")):
        r = run(["git", "-C", str(vault), "fetch", VAULT_REMOTE])
        if r.returncode != 0:
            alerts.append(f"WM watchdog: vault sync check failed: {_err(r)}")
        else:
            r = run(["git", "-C", str(vault), "rev-list", "--left-right",
                     "--count", "HEAD...@{u}"])
            if r.returncode != 0:
                alerts.append(
                    f"WM watchdog: vault branch has no upstream — {vault} is "
                    f"not tracking a remote branch: {_err(r)}")
            elif r.stdout.strip():
                left, _, right = r.stdout.strip().partition("\t")
                ahead, behind = left.strip() or "0", right.strip() or "0"
                if behind != "0":
                    r = run(["git", "-C", str(vault), "pull", "--ff-only", "-q"])
                    if r.returncode != 0:
                        alerts.append(
                            "WM watchdog: vault pull --ff-only failed (dirty "
                            f"tree or diverged?): {_err(r)}")
                if ahead != "0":
                    alerts.append(
                        f"WM watchdog: vault has unpushed local commits — "
                        f"{vault} is ahead {ahead} vs origin (push needed). "
                        "These notes exist on this machine only.")
    elif os.environ.get("WM_VAULT_PATH") or env.get("WM_VAULT_PATH"):
        # Only complain when a vault was actually configured; an install that
        # doesn't use one should not be alerted at every night.
        alerts.append(f"WM watchdog: configured vault {vault} is not a git clone.")

    if alerts:
        print("\n".join(alerts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # watchdog must never die silently
        print(f"WM watchdog: unexpected error: {exc}")
        sys.exit(0)
