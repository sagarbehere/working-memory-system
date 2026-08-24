#!/usr/bin/env python3
"""Working-memory reminder checker.

Run from the VPS crontab every few minutes (see crontab.example). Reads
$WM_ROOT/reminders.json, fires every pending reminder whose due_at has
passed — including any that came due while the VPS was down — sends it via
the existing Hermes Telegram bot into the dedicated working-memory chat
(WM_TELEGRAM_CHAT_ID, spec Section 2/9), and marks it fired. A failed
send is never marked fired; it stays pending and retries on the next
tick (spec Section 11). Every fire attempt is logged to
$WM_ROOT/logs/YYYY-MM.log (JSON lines).

Stdlib only. Safe to run concurrently (flock single-flight guard).
"""

import datetime as _dt
import fcntl
import json
import os
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request

HOME = pathlib.Path.home()
HERMES_HOME = pathlib.Path(
    os.environ.get("HERMES_HOME") or str(HOME / ".hermes")
)


def _load_env(path):
    env = {}
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


def _parse_iso(value):
    """Parse ISO-8601 due_at; naive values are assumed to be local time."""
    try:
        dt = _dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return dt


def _log(wm_root, component, event, outcome, **extra):
    """Append one JSON line to logs/YYYY-MM.log (spec Section 11)."""
    try:
        log_dir = wm_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "component": component,
            "event": event,
            "outcome": outcome,
            **extra,
        }
        log_file = log_dir / f"{_dt.datetime.now():%Y-%m}.log"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception as exc:
        print(f"  log failed: {exc}", flush=True)


def _send(token, chat_id, thread_id, text, retries=3):
    """Send via the existing bot. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if thread_id:
        data["message_thread_id"] = thread_id
    body = urllib.parse.urlencode(data).encode()
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, data=body, timeout=30) as resp:
                resp.read()
            return True, attempt + 1
        except Exception as exc:  # network blip / rate limit
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    return False, last_err


def _git_commit(wm_root, fired, failed):
    try:
        subprocess.run(
            ["git", "-C", str(wm_root), "add", "-A"],
            check=False, capture_output=True,
        )
        subprocess.run(
            [
                "git", "-C", str(wm_root), "commit", "-q",
                "-m", f"reminders: fired {len(fired)}, failed {len(failed)}",
            ],
            check=False, capture_output=True,
        )
    except Exception as exc:
        print(f"  git commit failed: {exc}", flush=True)


def main():
    wm_env = _load_env(HERMES_HOME / "working-memory.env")
    wm_root = pathlib.Path(wm_env.get("WM_ROOT") or str(HOME / "working-memory"))
    chat_id = wm_env.get("WM_TELEGRAM_CHAT_ID", "").strip()
    thread_id = wm_env.get("WM_TELEGRAM_THREAD_ID", "").strip()
    token = _load_env(HERMES_HOME / ".env").get("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        print("reminder-check: no TELEGRAM_BOT_TOKEN in ~/.hermes/.env", flush=True)
        return 1
    if not chat_id:
        print("reminder-check: WM_TELEGRAM_CHAT_ID not set in working-memory.env", flush=True)
        return 1

    reminders_path = wm_root / "reminders.json"
    if not reminders_path.exists():
        return 0

    # Single-flight guard — never run two ticks concurrently.
    lock_path = wm_root / "meta" / "reminder-check.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    except OSError:
        return 0
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("reminder-check: another run in progress; skipping", flush=True)
        os.close(lock_fd)
        return 0

    try:
        now = _dt.datetime.now().astimezone()
        with reminders_path.open(encoding="utf-8") as fh:
            reminders = json.load(fh)

        fired, failed = [], []
        for r in reminders:
            if r.get("status") != "pending":
                continue
            due = _parse_iso(r.get("due_at", ""))
            if due is None:
                print(
                    f"  reminder {r.get('id')}: unparseable due_at, "
                    "leaving pending",
                    flush=True,
                )
                continue
            if due > now:
                continue  # not due yet
            print(
                f"  firing {r.get('id')} (due {r.get('due_at')}): "
                f"{(r.get('message') or '')[:80]}",
                flush=True,
            )
            ok, info = _send(token, chat_id, thread_id, r.get("message", "Reminder"))
            if ok:
                r["status"] = "fired"
                r["fired_at"] = now.isoformat()
                fired.append(r.get("id"))
                _log(
                    wm_root, "reminder-cron", "fire", "sent",
                    reminder_id=r.get("id"), attempts=info,
                )
            else:
                failed.append(r.get("id"))
                print(f"  send failed ({info}); leaving pending for next tick", flush=True)
                _log(
                    wm_root, "reminder-cron", "fire", "failed",
                    reminder_id=r.get("id"), error=str(info),
                )

        if fired or failed:
            tmp = reminders_path.with_name("reminders.json.tmp")
            tmp.write_text(json.dumps(reminders, indent=2), encoding="utf-8")
            tmp.replace(reminders_path)
            _git_commit(wm_root, fired, failed)

        pending = sum(1 for r in reminders if r.get("status") == "pending")
        print(
            f"reminder-check: fired={len(fired)} failed={len(failed)} "
            f"pending={pending}",
            flush=True,
        )
        return 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
