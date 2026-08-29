#!/usr/bin/env python3
"""Integration tests for wm-backup-push.py against scratch repos. No network.

Builds a throwaway WM_ROOT git repo with a file:// bare remote and a fake
vault, so every step (snapshot, commit, push, vault sync) runs for real.

The headline test is test_live_db_survives_backup: the script used to write
its snapshot OVER the live records.db while that database was in WAL mode,
which discards the writes of any connection held open across the swap.

Run: python3 tests/test_backup.py   (from the package dir)
"""
import os
import pathlib
import sqlite3
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


def _fixture(with_vault=True):
    """A WM_ROOT git repo with a bare remote, plus an optional vault clone."""
    td = pathlib.Path(tempfile.mkdtemp(prefix="wm-backup-test-"))
    root, remote = td / "wm", td / "remote.git"
    (root / "meta").mkdir(parents=True)
    (root / "raw").mkdir()
    (root / "raw" / "2026-08.md").write_text("## 2026-08-01T00:00:00+05:30\n")
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("records.db\nrecords.db-*\nmeta/*.lock\n*.tmp\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "branch", "-M", "main")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "-u", "origin", "main")

    vault = td / "wiki"
    if with_vault:
        vremote = td / "wiki.git"
        subprocess.run(["git", "init", "-q", "--bare", str(vremote)], check=True)
        vault.mkdir()
        git(vault, "init", "-q")
        git(vault, "config", "user.name", "t")
        git(vault, "config", "user.email", "t@t")
        git(vault, "config", "commit.gpgsign", "false")
        (vault / "index.md").write_text("# vault\n")
        git(vault, "add", "-A")
        git(vault, "commit", "-q", "-m", "init")
        git(vault, "branch", "-M", "main")
        git(vault, "remote", "add", "origin", str(vremote))
        git(vault, "push", "-q", "-u", "origin", "main")

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


def test_live_db_survives_backup():
    """THE regression test: the live database is untouched by a backup.

    Old behaviour snapshotted to records.db.tmp then os.replace'd it OVER
    records.db while connections were open in WAL mode. That leaves a stale
    records.db-wal beside a different main file; the damage lands when that
    WAL is next checkpointed. Measured against the old code, this exact
    sequence produced `integrity_check` = "wrong # of entries in index
    idx_records_type" and silently dropped 201 committed rows.

    The checkpoint below is what makes the test discriminating — without it
    the old code often appeared to work, which is precisely what made the
    bug dangerous. SQLite triggers such a checkpoint on its own once the WAL
    passes ~1000 pages, so this is a normal event, not a contrived one.
    """
    td, root, _vault, hermes = _fixture()
    subprocess.run([sys.executable, str(PKG / "records.py"), "--root", str(root),
                    "init"], capture_output=True, check=True)

    live = sqlite3.connect(root / "records.db", timeout=30)
    live.execute("PRAGMA journal_mode=WAL")
    ins = ("INSERT INTO records(type,domain,occurred_at,entity,data_json) "
           "VALUES ('m','health','2026-08-20T09:00:00+00:00',?,'{}')")
    live.executemany(ins, [(f"before{i}",) for i in range(200)])
    live.commit()

    r = _run_backup(hermes)
    check(r.returncode == 0, f"backup exits 0 ({r.stdout}{r.stderr})")

    live.executemany(ins, [(f"during{i}",) for i in range(200)])
    live.commit()
    live.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the step that used to corrupt
    live.execute(ins, ("after",))
    live.commit()
    live.close()

    con = sqlite3.connect(root / "records.db")
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    n_before = con.execute("SELECT count(*) FROM records WHERE entity LIKE 'before%'").fetchone()[0]
    n_during = con.execute("SELECT count(*) FROM records WHERE entity LIKE 'during%'").fetchone()[0]
    n_after = con.execute("SELECT count(*) FROM records WHERE entity = 'after'").fetchone()[0]
    con.close()
    check(integrity == "ok", f"live db passes integrity_check (got {integrity!r})")
    check(n_before == 200, f"pre-backup writes intact (got {n_before}/200)")
    check(n_during == 200, f"writes during/after the backup intact (got {n_during}/200)")
    check(n_after == 1, f"post-checkpoint write intact (got {n_after}/1)")

    check((root / "records-snapshot.db").exists(), "snapshot written")
    snap = sqlite3.connect(root / "records-snapshot.db")
    snap_n = snap.execute("SELECT count(*) FROM records").fetchone()[0]
    snap_ok = snap.execute("PRAGMA integrity_check").fetchone()[0]
    snap.close()
    check(snap_ok == "ok", f"snapshot passes integrity_check (got {snap_ok!r})")
    check(snap_n == 200, f"snapshot is a consistent point-in-time copy (got {snap_n})")
    check(not (root / "records-snapshot.db.tmp").exists(), "no temp snapshot left")


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
    subprocess.run(["git", "clone", "-q", str(td / "wiki.git"), str(other)], check=True)
    git(other, "config", "user.name", "t")
    git(other, "config", "user.email", "t@t")
    git(other, "config", "commit.gpgsign", "false")
    (other / "from-phone.md").write_text("captured elsewhere")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "from another device")
    git(other, "push", "-q")

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
    test_live_db_survives_backup()
    test_silent_when_healthy()
    test_alerts_on_real_problems()
    test_vault_pull_when_behind()
    test_pushes_to_remote()
    print(f"ALL BACKUP TESTS PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
