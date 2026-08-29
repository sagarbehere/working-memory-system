#!/usr/bin/env python3
"""Integration tests for wm-backup-push.py against scratch repos. No network.

Builds a throwaway WM_ROOT git repo with a file:// bare remote and a fake
vault, so every step (commit, push, vault sync) runs for real.

The property that matters most is silence: a healthy night must print
nothing, or the watchdog trains you to ignore it.

Run: python3 tests/test_backup.py   (from the package dir)
"""
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


def _fixture(with_vault=True):
    """A WM_ROOT git repo with a bare remote, plus an optional vault clone."""
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-backup-test-"))
    root, remote = td / "wm", td / "remote.git"
    (root / "meta").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "raw" / "2026-08.md").write_text("## 2026-08-01T00:00:00+05:30\n")
    init_bare(remote)
    init_work(root)
    (root / ".gitignore").write_text("meta/*.lock\n*.tmp\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(remote))
    r = git(root, "push", "-q", "-u", "origin", "main")
    assert r.returncode == 0, f"fixture push failed: {r.stderr}"

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


def _run_backup(hermes, extra_env=None):
    env = dict(os.environ, HERMES_HOME=str(hermes))
    env.pop("WM_ROOT", None)
    env.pop("WM_VAULT_PATH", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(PKG / "wm-backup-push.py")],
                          capture_output=True, text=True, env=env)


def test_silent_when_healthy():
    """A healthy night must print nothing — including with Todoist unconfigured.

    Todoist absence and a missing vault used to each emit an alert on every
    healthy run, which trains you to ignore the watchdog.
    """
    td, root, _vault, hermes = _fixture()
    r = _run_backup(hermes)
    check(r.returncode == 0, "healthy run exits 0")
    check(r.stdout.strip() == "",
          f"healthy run is SILENT (got {r.stdout.strip()!r})")

    td2, root2, _v2, hermes2 = _fixture(with_vault=False)
    r = _run_backup(hermes2)
    check(r.stdout.strip() == "",
          f"no vault configured -> still silent (got {r.stdout.strip()!r})")


def test_alerts_on_real_problems():
    """The watchdog must still speak up when something is genuinely wrong."""
    td, root, vault, hermes = _fixture()
    # Unpushed vault commits are a real problem: the vault is the primary store.
    (vault / "new.md").write_text("x")
    git(vault, "add", "-A")
    git(vault, "commit", "-q", "-m", "local only")
    r = _run_backup(hermes)
    check("unpushed local commits" in r.stdout,
          f"alerts on unpushed vault commits (got {r.stdout!r})")

    # A missing remote means the off-box copy silently is not happening.
    td2, root2, _v2, hermes2 = _fixture()
    git(root2, "remote", "remove", "origin")
    r = _run_backup(hermes2)
    check("no 'origin' remote" in r.stdout,
          f"alerts when the backup remote is gone (got {r.stdout!r})")


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

    r = _run_backup(hermes)
    check(r.stdout.strip() == "", f"being behind is silent (got {r.stdout.strip()!r})")
    check((vault / "from-phone.md").exists(), "vault fast-forwarded automatically")


def test_pushes_to_remote():
    td, root, _vault, hermes = _fixture()
    (root / "raw" / "2026-09.md").write_text("## 2026-09-01T00:00:00+05:30\n")
    r = _run_backup(hermes)
    check(r.stdout.strip() == "", f"push run is silent (got {r.stdout.strip()!r})")
    log = git(td / "remote.git", "log", "--oneline", "-1").stdout
    check("nightly snapshot" in log, f"commit reached the remote (got {log!r})")


def main():
    test_silent_when_healthy()
    test_alerts_on_real_problems()
    test_vault_pull_when_behind()
    test_pushes_to_remote()
    print(f"ALL BACKUP TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
