#!/usr/bin/env python3
"""Nightly backup push for the working-memory data repo (v3, Stage 4).

Watchdog pattern: prints NOTHING when the run is healthy; prints alert
lines when something needs attention. Registered as a Hermes no_agent
cron job (03:00 daily) so a healthy night is silent and a broken one
delivers exactly the problem — no tokens, no agent.

Run steps:
  1. Report anything that failed quietly in the last day, and prune old logs.
  2. Todoist export (JSONL of open tasks) -> todoist-export.jsonl, skipped
     silently when Todoist isn't configured. Since Todoist owns reminders
     outright, this export is their only off-box copy.
  3. git add -A + commit (only when something changed) in WM_ROOT.
  4. git push origin <branch> — the private remote (off-box copy <= 24h lag).
  5. Vault sync: git fetch + pull --ff-only when behind (devices push
     legitimately); alert only on unpushed local commits or a failed pull.

What it backs up is now just the raw transcript, meta/, and the Todoist
export: WM_ROOT holds no database and no reminder store (2026-08-29 cut).
The vault is backed up by its own remote, which step 4 verifies.

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
import todoist  # noqa: E402
import wmlib  # noqa: E402
import datetime as _dt  # noqa: E402

REMOTE = "origin"
BRANCH = "main"
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
    scripts = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(os.path.join(root, ".git")):
        print(f"WM backup: {root} is not a git repo — nothing to push.")
        return 0

    # 1. Anything failing quietly? (was the consolidation gate's one real job)
    alerts.extend(recent_failures(root))
    prune_logs(root)

    # 2. Todoist export — silent when Todoist simply isn't configured.
    #    (This used to alert on every healthy night for anyone not using it,
    #    which trains you to ignore the watchdog.)
    if todoist.enabled():
        r = run([sys.executable, os.path.join(scripts, "todoist.py"), "list"])
        if r.returncode == 0:
            with open(os.path.join(root, "todoist-export.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(r.stdout)
        else:
            alerts.append(f"WM backup: Todoist export failed (continuing): {_err(r)}")

    # 3. Commit any changes
    r = run(["git", "-C", root, "add", "-A"])
    if r.returncode != 0:
        alerts.append(f"WM backup: git add failed: {_err(r)}")
    elif run(["git", "-C", root, "diff", "--cached", "--quiet"]).returncode != 0:
        msg = f"backup: nightly snapshot {wmlib.now():%Y-%m-%d}"
        r = run(["git", "-C", root, "commit", "-q", "-m", msg])
        if r.returncode != 0:
            alerts.append(f"WM backup: git commit failed: {_err(r)}")

    # 4. Push to the private remote
    r = run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    branch = r.stdout.strip() or BRANCH
    if run(["git", "-C", root, "remote", "get-url", REMOTE]).returncode != 0:
        alerts.append(
            f"WM backup: no '{REMOTE}' remote in {root} — the off-box copy is "
            "not happening; add the private remote.")
    else:
        r = run(["git", "-C", root, "push", REMOTE, branch])
        if r.returncode != 0:
            alerts.append(
                "WM backup: git push failed — check the private remote exists "
                f"and the PAT covers it: {_err(r)}")

    # 5. Vault sync: devices push legitimately, so pull --ff-only when behind
    #    (silent); alert only on unpushed local commits or a failed pull.
    if os.path.isdir(os.path.join(vault, ".git")):
        r = run(["git", "-C", str(vault), "fetch", REMOTE])
        if r.returncode != 0:
            alerts.append(f"WM backup: vault sync check failed: {_err(r)}")
        else:
            r = run(["git", "-C", str(vault), "rev-list", "--left-right",
                     "--count", "HEAD...@{u}"])
            if r.returncode != 0:
                alerts.append(
                    f"WM backup: vault branch has no upstream — {vault} is not "
                    f"tracking a remote branch: {_err(r)}")
            elif r.stdout.strip():
                left, _, right = r.stdout.strip().partition("\t")
                ahead, behind = left.strip() or "0", right.strip() or "0"
                if behind != "0":
                    r = run(["git", "-C", str(vault), "pull", "--ff-only", "-q"])
                    if r.returncode != 0:
                        alerts.append(
                            "WM backup: vault pull --ff-only failed (dirty tree "
                            f"or diverged?): {_err(r)}")
                if ahead != "0":
                    alerts.append(
                        f"WM backup: vault has unpushed local commits — {vault} "
                        f"is ahead {ahead} vs origin (push needed).")
    elif os.environ.get("WM_VAULT_PATH") or env.get("WM_VAULT_PATH"):
        # Only complain when a vault was actually configured; an install that
        # doesn't use one should not be alerted at every night.
        alerts.append(f"WM backup: configured vault {vault} is not a git clone.")

    if alerts:
        print("\n".join(alerts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # watchdog must never die silently
        print(f"WM backup: unexpected error: {exc}")
        sys.exit(0)
