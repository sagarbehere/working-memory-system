#!/usr/bin/env python3
"""Nightly backup push for the working-memory data repo (v3, Stage 4).

Watchdog pattern: prints NOTHING when the run is healthy; prints alert
lines when something needs attention. Registered as a Hermes no_agent
cron job (03:00 daily) so a healthy night is silent and a broken one
delivers exactly the problem — no tokens, no agent.

Run steps:
  1. Consistent SQLite snapshot -> records-snapshot.db (a SEPARATE file;
     the live records.db is never touched — see below).
  2. Todoist export (JSONL of open tasks) -> todoist-export.jsonl, skipped
     silently when Todoist isn't configured.
  3. git add -A + commit (only when something changed) in WM_ROOT.
  4. git push origin <branch> — the private remote (off-box copy <= 24h lag).
  5. Vault sync: git fetch + pull --ff-only when behind (devices push
     legitimately); alert only on unpushed local commits or a failed pull.

WHY THE SNAPSHOT IS A SEPARATE FILE. This script used to snapshot to
records.db.tmp and then os.replace it OVER the live records.db, so that the
git-tracked path held a consistent copy. That is unsafe: the database runs
in WAL mode, so a stale records.db-wal was left beside a swapped-out main
file, and any connection open across the replace kept an fd on the unlinked
inode — its committed writes vanished. Committing a distinct snapshot file
gets the same off-box copy with none of that: the live database is only ever
read. Restore with `cp records-snapshot.db records.db` (with the gateway
stopped), which is also why records.db* itself is gitignored.

Exit 0 whenever the run completed; alerts are carried in stdout (the
no_agent scheduler delivers stdout verbatim). Exceptions are caught and
printed instead of propagating, so the scheduler's generic error alert
never duplicates the payload.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import todoist  # noqa: E402
import wmlib  # noqa: E402

REMOTE = "origin"
BRANCH = "main"
SNAPSHOT = "records-snapshot.db"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _err(r):
    return (r.stderr or r.stdout).strip()


def main():
    alerts = []
    env = wmlib.wm_env()
    root = str(wmlib.wm_root(env))
    vault = wmlib.vault_path(env)
    scripts = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(os.path.join(root, ".git")):
        print(f"WM backup: {root} is not a git repo — nothing to push.")
        return 0

    # 1. Consistent SQLite snapshot, written beside the live DB (never over it)
    if os.path.isfile(os.path.join(root, "records.db")):
        snap = os.path.join(root, SNAPSHOT)
        tmp = snap + ".tmp"
        r = run([sys.executable, os.path.join(scripts, "records.py"),
                 "--root", root, "backup", "--out", tmp])
        if r.returncode != 0:
            alerts.append(f"WM backup: SQLite snapshot failed: {_err(r)}")
            if os.path.exists(tmp):
                os.unlink(tmp)  # never leave a partial snapshot for git to commit
        else:
            os.replace(tmp, snap)

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
