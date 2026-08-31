#!/usr/bin/env python3
"""Shared primitives for the working-memory deterministic layer.

Every component used to carry its own copy of the env-file parser, its own
timestamp handling, and its own idea of where the vault lives. They drifted:
only the consolidation gate stripped quotes from env values (so
``WM_ROOT="~/wm"`` resolved to a different directory in different scripts),
the gate hardcoded UTC+05:30 while another script used the system zone, and
``~/wiki`` was baked into the backup script. This module is the single
definition of all of it.

Stdlib only, no imports from sibling scripts — every other module in the
package may import this one, and this one imports none of them.

Contents:
  * env      -- load_env_file, wm_env, hermes_env, wm_root, vault_path
  * time     -- tz, now, parse_iso, local_iso  (always timezone-aware)
  * logging  -- log (one JSON line per event, appended to logs/YYYY-MM.log)
  * files    -- FileLock

Nothing is kept here "in case it is useful": to_utc, iso, write_json_atomic
and load_lanes were removed with their last callers in the 2026-08-29 cut.
"""

import datetime as _dt
import fcntl
import json
import os
import pathlib

__all__ = [
    "HERMES_HOME", "load_env_file", "wm_env", "hermes_env", "wm_root",
    "vault_path", "tz", "now", "parse_iso", "local_iso", "log",
    "FileLock", "LockBusy",
]

HERMES_HOME = pathlib.Path(
    os.environ.get("HERMES_HOME") or str(pathlib.Path.home() / ".hermes")
)


# ------------------------------------------------------------------ env

def load_env_file(path) -> dict:
    """Parse a KEY=VALUE env file. Missing file -> {}.

    Surrounding single/double quotes are stripped, so WM_ROOT=~/wm and
    WM_ROOT="~/wm" resolve identically. (Before this was centralised only
    the consolidation gate stripped them, so a quoted value silently sent
    the gate and the capture path to different roots.)
    """
    env = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError):
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def wm_env() -> dict:
    """The working-memory runtime config (~/.hermes/working-memory.env)."""
    return load_env_file(HERMES_HOME / "working-memory.env")


def hermes_env() -> dict:
    """Hermes' own secrets file (~/.hermes/.env) — bot and API tokens."""
    return load_env_file(HERMES_HOME / ".env")


def wm_root(env=None) -> pathlib.Path:
    """Resolve WM_ROOT: process env wins (tests), then the config file."""
    env = wm_env() if env is None else env
    raw = os.environ.get("WM_ROOT") or env.get("WM_ROOT") or "~/working-memory"
    return pathlib.Path(os.path.expanduser(raw))


def vault_path(env=None) -> pathlib.Path:
    """Resolve the Obsidian vault (WM_VAULT_PATH, default ~/wiki).

    Configurable because the backup watchdog checks it every night: a
    hardcoded ~/wiki meant anyone with the vault elsewhere got an alert
    on every healthy run.
    """
    env = wm_env() if env is None else env
    raw = os.environ.get("WM_VAULT_PATH") or env.get("WM_VAULT_PATH") or "~/wiki"
    return pathlib.Path(os.path.expanduser(raw))


# ----------------------------------------------------------------- time

def tz(env=None):
    """The system timezone, or WM_TZ when set (IANA name, e.g. Asia/Kolkata).

    Everything user-visible is stamped in this zone; everything stored for
    comparison is normalised to UTC. Falls back to the system zone if the
    name is unknown, so a typo degrades rather than crashes.
    """
    env = wm_env() if env is None else env
    name = (os.environ.get("WM_TZ") or env.get("WM_TZ") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return _dt.datetime.now().astimezone().tzinfo


def now(env=None) -> _dt.datetime:
    """Timezone-aware current time."""
    return _dt.datetime.now(tz(env))


def parse_iso(value, env=None):
    """Parse an ISO-8601 string to an aware datetime; None if unparseable.

    A naive value is interpreted in the configured zone rather than being
    left naive, so a comparison against it can never raise.
    """
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz(env))
    return dt




def local_iso(value, env=None):
    """Render a timestamp in the CONFIGURED zone, for display.

    Storage and display are deliberately different jobs: timestamps are
    stored in UTC so string comparison matches chronological order, but a
    person should never be shown UTC. Every user-facing timestamp goes
    through here. Returns the input unchanged if it cannot be parsed, so a
    display path can never lose data or raise.
    """
    dt = parse_iso(value, env)
    if dt is None:
        return value
    return dt.astimezone(tz(env)).isoformat(timespec="seconds")


# -------------------------------------------------------------- logging

def log(root, component, event, outcome, **extra) -> None:
    """Append one JSON line to <root>/logs/YYYY-MM.log.

    Timestamps carry an offset (they used to be naive local time, which
    made log lines incomparable with the offset-aware timestamps in raw
    entries — the consolidation gate compares exactly those two).
    Never raises: logging must not break a capture or a cron tick.
    """
    try:
        stamp = now()
        log_dir = pathlib.Path(root) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": stamp.isoformat(timespec="seconds"),
            "component": component,
            "event": event,
            "outcome": outcome,
            **extra,
        }
        with (log_dir / f"{stamp:%Y-%m}.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        print(f"wm log failed: {exc}", flush=True)


# ---------------------------------------------------------------- files


class LockBusy(Exception):
    """Raised by FileLock(blocking=False) when another holder has the lock."""


class FileLock:
    """flock-based mutual exclusion around a read-modify-write.

    NOTE: no caller in this package uses it as of the 2026-08-31 transcript
    cut — its only user was the raw-log append. It is kept because the rule
    below is the kind that gets rediscovered expensively, and because the
    next read-modify-write on a shared file will need exactly this.

    It exists because an earlier design had two processes writing one store
    while only one of them took a lock: the unlocked writer's captures were
    silently erased by the other's write-back. If you add a second writer to
    any file, both sides take the lock or neither is protected.

    Usage:
        with FileLock(root / "meta" / "some.lock"):
            ...read, modify, write...
    """

    def __init__(self, path, blocking: bool = True, timeout: float = 30.0):
        self.path = pathlib.Path(path)
        self.blocking = blocking
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        if self.blocking:
            # Bounded wait: SIGALRM interrupts a blocking flock so a stuck
            # holder degrades to an error instead of wedging cron forever.
            import signal

            def _timeout(*_args):
                raise LockBusy(f"timed out waiting for {self.path}")

            old = signal.signal(signal.SIGALRM, _timeout)
            signal.setitimer(signal.ITIMER_REAL, self.timeout)
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except (LockBusy, OSError):
                os.close(self._fd)
                self._fd = None
                raise
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old)
        else:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(self._fd)
                self._fd = None
                raise LockBusy(f"{self.path} is held by another process")
        return self

    def __exit__(self, *_exc):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return False
