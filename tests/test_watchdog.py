#!/usr/bin/env python3
"""Integration tests for wm-watchdog.py against scratch repos. No network.

Builds a throwaway WM_ROOT and a fake vault with a file:// bare remote, so
the vault sync path runs for real.

The property that matters most is silence: a healthy night must print
nothing, or the watchdog trains you to ignore it. Everything this script
still does is in service of one thing — noticing that the VAULT, where the
notes actually are, is not backed up or that something has been failing
quietly.

Until 2026-08-31 this was test_backup.py and it also covered a Todoist
export, a WM_ROOT commit and a push to a private remote. Those steps are
gone (decisions.md, "The 2026-08-31 watchdog cut"), and so are their tests.

Run: python3 tests/test_watchdog.py   (from the package dir)
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

checks = 0


def check(cond, label):
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def init_bare(path):
    """A bare repo whose HEAD points at main, whatever git's default is.

    Without the symbolic-ref, a host with init.defaultBranch=master (the git
    default before 2.28, and still unset on many servers) leaves HEAD on a
    branch that never gets created. Cloning such a repo then checks nothing
    out and leaves the clone on an unborn branch — which silently made this
    fixture test nothing.
    """
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "symbolic-ref", "HEAD",
                    "refs/heads/main"], check=True)


def init_work(path):
    """A working repo on main with a deterministic identity."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "checkout", "-q", "-B", "main")
    git(path, "config", "user.name", "t")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "commit.gpgsign", "false")


def _fixture(with_vault=True, wm_is_git=True):
    """A WM_ROOT (git repo by default) plus an optional vault clone.

    WM_ROOT gets no remote: nothing pushes it any more. wm_is_git=False
    covers an install where it was never a git repo at all, which is now a
    perfectly ordinary configuration rather than something to alert about.
    """
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-watchdog-test-"))
    root = td / "wm"
    (root / "meta").mkdir(parents=True)
    if wm_is_git:
        init_work(root)
        (root / ".gitignore").write_text("meta/*.lock\n*.tmp\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "init")

    vault = td / "wiki"
    if with_vault:
        init_bare(td / "wiki.git")
        init_work(vault)
        (vault / "index.md").write_text("# vault\n")
        git(vault, "add", "-A")
        git(vault, "commit", "-q", "-m", "init")
        git(vault, "remote", "add", "origin", str(td / "wiki.git"))
        r = git(vault, "push", "-q", "-u", "origin", "main")
        assert r.returncode == 0, f"fixture vault push failed: {r.stderr}"

    hermes = td / "hermes"
    hermes.mkdir()
    (hermes / "working-memory.env").write_text(
        f"WM_ROOT={root}\n" + (f"WM_VAULT_PATH={vault}\n" if with_vault else ""))
    (hermes / ".env").write_text("")  # no tokens: Todoist is not configured
    return td, root, vault, hermes


def _run_watchdog(hermes, extra_env=None):
    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)
    env.pop("WM_VAULT_PATH", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(PKG / "wm-watchdog.py")],
                          capture_output=True, text=True, env=env)


def test_silent_when_healthy():
    """A healthy night must print nothing — including with Todoist unconfigured.

    Todoist absence and a missing vault used to each emit an alert on every
    healthy run, which trains you to ignore the watchdog.
    """
    td, root, _vault, hermes = _fixture()
    r = _run_watchdog(hermes)
    check(r.returncode == 0, "healthy run exits 0")
    check(r.stdout.strip() == "",
          f"healthy run is SILENT (got {r.stdout.strip()!r})")

    td2, root2, _v2, hermes2 = _fixture(with_vault=False)
    r = _run_watchdog(hermes2)
    check(r.stdout.strip() == "",
          f"no vault configured -> still silent (got {r.stdout.strip()!r})")


def test_wm_root_needs_no_git_repo_or_remote():
    """WM_ROOT is not backed up by this script, so its git state is not news.

    The old wm-backup-push.py refused to run at all unless WM_ROOT was a git
    repo, and alerted every night if it had no 'origin'. Both were correct
    then and would now be pure noise: the private remote is retired and
    nothing here commits or pushes WM_ROOT. Asserted rather than assumed,
    because "the watchdog got chattier" is exactly the regression that makes
    people stop reading it.
    """
    td, root, _vault, hermes = _fixture(wm_is_git=False)
    r = _run_watchdog(hermes)
    check(r.returncode == 0, "a non-git WM_ROOT still exits 0")
    check(r.stdout.strip() == "",
          f"a non-git WM_ROOT is SILENT (got {r.stdout.strip()!r})")

    # And a git WM_ROOT with no remote, which is the shape left behind after
    # the private remote is deleted.
    td2, root2, _v2, hermes2 = _fixture()
    check(git(root2, "remote").stdout.strip() == "", "fixture WM_ROOT has no remote")
    r = _run_watchdog(hermes2)
    check(r.stdout.strip() == "",
          f"WM_ROOT with no remote is SILENT (got {r.stdout.strip()!r})")


def test_does_not_write_to_wm_root_git():
    """It must not commit WM_ROOT — that job is gone, not merely quiet."""
    td, root, _vault, hermes = _fixture()
    before = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "meta" / "lanes.json").write_text('{"c1": true}')
    r = _run_watchdog(hermes)
    check(r.stdout.strip() == "", f"an uncommitted change is not news (got {r.stdout!r})")
    check(git(root, "rev-parse", "HEAD").stdout.strip() == before,
          "no new commit was made in WM_ROOT")
    check(git(root, "status", "--porcelain").stdout.strip() != "",
          "and the change is left uncommitted, not staged away")


def test_reports_quiet_failures():
    """The one job the consolidation gate did that nothing else did.

    Failures are logged by the capture path but nobody reads logs/. The
    watchdog surfaces recent ones so a persistent problem cannot stay silent.
    """
    td, root, _vault, hermes = _fixture()
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    import datetime as dt
    now = dt.datetime.now().astimezone()
    old = now - dt.timedelta(days=3)
    with (logs / f"{now:%Y-%m}.log").open("w") as f:
        for ts, outcome in ((now, "failed"), (now, "failed"), (now, "ok"), (old, "failed")):
            f.write(json.dumps({"ts": ts.isoformat(timespec="seconds"),
                                "component": "todoist", "event": "create",
                                "outcome": outcome}) + "\n")
        f.write("not json at all\n")

    r = _run_watchdog(hermes)
    check("todoist create: 2 failure(s)" in r.stdout,
          f"recent failures reported, older ones not (got {r.stdout!r})")
    check("ok" not in r.stdout.split("failure(s)")[0].split("todoist")[0],
          "successes are not reported")

    # And a healthy log stays silent.
    td2, root2, _v2, hermes2 = _fixture()
    (root2 / "logs").mkdir(exist_ok=True)
    (root2 / "logs" / f"{now:%Y-%m}.log").write_text(
        json.dumps({"ts": now.isoformat(timespec="seconds"), "component": "todoist",
                    "event": "create", "outcome": "ok"}) + "\n")
    r = _run_watchdog(hermes2)
    check(r.stdout.strip() == "", f"no failures -> still silent (got {r.stdout!r})")


def test_prunes_old_logs():
    td, root, _vault, hermes = _fixture()
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    import datetime as dt
    now = dt.datetime.now().astimezone()
    keep = logs / f"{now:%Y-%m}.log"
    drop = logs / f"{(now - dt.timedelta(days=200)):%Y-%m}.log"
    keep.write_text("")
    drop.write_text("")
    _run_watchdog(hermes)
    check(keep.exists(), "the current log is kept")
    check(not drop.exists(), "a log older than the retention window is pruned")


def test_alerts_on_real_problems():
    """The watchdog must still speak up when something is genuinely wrong."""
    td, root, vault, hermes = _fixture()
    # Unpushed vault commits are a real problem: the vault is the primary store.
    (vault / "new.md").write_text("x")
    git(vault, "add", "-A")
    git(vault, "commit", "-q", "-m", "local only")
    r = _run_watchdog(hermes)
    check("unpushed local commits" in r.stdout,
          f"alerts on unpushed vault commits (got {r.stdout!r})")


def test_vault_pull_when_behind():
    """Devices push to the vault legitimately; being behind is not an alert."""
    td, root, vault, hermes = _fixture()
    other = td / "other"
    # -b main explicitly: never rely on the remote's HEAD or on this host's
    # init.defaultBranch.
    subprocess.run(["git", "clone", "-q", "-b", "main", str(td / "wiki.git"),
                    str(other)], check=True)
    git(other, "config", "user.name", "t")
    git(other, "config", "user.email", "t@t")
    git(other, "config", "commit.gpgsign", "false")
    (other / "from-phone.md").write_text("captured elsewhere")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "from another device")
    r = git(other, "push", "-q")
    check(r.returncode == 0, f"the other device pushed ({r.stderr})")
    # Guard the premise: if the vault is not actually behind, the assertion
    # below would pass for the wrong reason.
    counts = git(vault, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    git(vault, "fetch", "origin")
    behind = git(vault, "rev-list", "--count", "HEAD..@{u}").stdout.strip()
    check(behind == "1", f"vault is genuinely 1 behind before the run (got {behind!r})")

    r = _run_watchdog(hermes)
    check(r.stdout.strip() == "", f"being behind is silent (got {r.stdout.strip()!r})")
    check((vault / "from-phone.md").exists(), "vault fast-forwarded automatically")


def main():
    test_silent_when_healthy()
    test_wm_root_needs_no_git_repo_or_remote()
    test_does_not_write_to_wm_root_git()
    test_reports_quiet_failures()
    test_prunes_old_logs()
    test_alerts_on_real_problems()
    test_vault_pull_when_behind()
    print(f"ALL WATCHDOG TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
