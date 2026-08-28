#!/usr/bin/env python3
"""Nightly backup push for the working-memory data repo (v3, Stage 4).

Watchdog pattern: prints NOTHING when the run is healthy; prints alert
lines when something needs attention. Registered as a Hermes no_agent
cron job (03:00 daily) so a healthy night is silent and a broken one
delivers exactly the problem — no tokens, no agent.

Run steps:
  1. Safe SQLite snapshot: records.py backup -> records.db.tmp, then
     os.replace over records.db — every committed DB file is a consistent
     point-in-time copy, never a torn live read.
  2. Best-effort Todoist export (JSONL of open tasks) -> todoist-export.json.
  3. git add -A + commit (only when something changed) in WM_ROOT.
  4. git push origin main — the private remote (off-box copy <= 24 h lag).
  5. Vault sync check: git fetch in ~/wiki; alert on drift (review-notes
     decision 7).

Exit 0 whenever the run completed; alerts are carried in stdout (the
no_agent scheduler delivers stdout verbatim). Exceptions are caught and
printed instead of propagating, so the scheduler's generic error alert
never duplicates the payload.
"""

import datetime
import os
import subprocess
import sys

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
ENV_PATH = os.path.join(HERMES_HOME, "working-memory.env")
DEFAULT_ROOT = os.path.expanduser("~/working-memory")
REMOTE = "origin"
BRANCH = "main"
WIKI = os.path.expanduser("~/wiki")


def load_env():
    env = {}
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    except OSError:
        pass
    return env


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def main():
    alerts = []
    root = os.path.expanduser(load_env().get("WM_ROOT", DEFAULT_ROOT))
    scripts = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(os.path.join(root, ".git")):
        print(f"WM backup: {root} is not a git repo — nothing to push.")
        return 0

    # 1. Consistent SQLite snapshot in the working tree
    tmp_db = os.path.join(root, "records.db.tmp")
    r = run([sys.executable, os.path.join(scripts, "records.py"), "--root", root,
             "backup", "--out", tmp_db])
    if r.returncode != 0:
        alerts.append(f"WM backup: SQLite snapshot failed: {(r.stderr or r.stdout).strip()}")
    else:
        os.replace(tmp_db, os.path.join(root, "records.db"))

    # 2. Best-effort Todoist export
    r = run([sys.executable, os.path.join(scripts, "todoist.py"), "list"])
    if r.returncode == 0 and r.stdout.strip():
        with open(os.path.join(root, "todoist-export.json"), "w") as f:
            f.write(r.stdout)
    else:
        alerts.append(f"WM backup: Todoist export failed (continuing): {(r.stderr or r.stdout).strip()}")

    # 3. Commit any changes
    r = run(["git", "-C", root, "add", "-A"])
    if r.returncode != 0:
        alerts.append(f"WM backup: git add failed: {r.stderr.strip()}")
    elif run(["git", "-C", root, "diff", "--cached", "--quiet"]).returncode != 0:
        msg = f"backup: nightly snapshot {datetime.date.today().isoformat()}"
        r = run(["git", "-C", root, "commit", "-q", "-m", msg])
        if r.returncode != 0:
            alerts.append(f"WM backup: git commit failed: {r.stderr.strip()}")

    # 4. Push to the private remote
    r = run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    branch = r.stdout.strip() or BRANCH
    r = run(["git", "-C", root, "push", REMOTE, branch])
    if r.returncode != 0:
        alerts.append(
            "WM backup: git push failed — check the private remote exists and the "
            f"PAT covers it: {(r.stderr or r.stdout).strip()}"
        )

    # 5. Vault sync check (review-notes decision 7)
    if os.path.isdir(os.path.join(WIKI, ".git")):
        r = run(["git", "-C", WIKI, "fetch", "origin"])
        if r.returncode != 0:
            alerts.append(f"WM backup: vault sync check failed: {(r.stderr or r.stdout).strip()}")
        else:
            r = run(["git", "-C", WIKI, "rev-list", "--left-right", "--count", "HEAD...@{u}"])
            if r.returncode == 0 and r.stdout.strip():
                left, _, right = r.stdout.strip().partition("\t")
                ahead = left.strip() or "0"
                behind = right.strip() or "0"
                if ahead != "0" or behind != "0":
                    alerts.append(
                        f"WM backup: vault out of sync — ~/wiki is ahead {ahead}, "
                        f"behind {behind} vs origin."
                    )
    else:
        alerts.append("WM backup: no ~/wiki clone — vault sync check skipped.")

    if alerts:
        print("\n".join(alerts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # watchdog must never die silently
        print(f"WM backup: unexpected error: {exc}")
        sys.exit(0)
