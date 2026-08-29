#!/usr/bin/env python3
"""Working-memory reminder checker — the firing cron tick.

Two delivery modes:

* Telegram mode (default): run from the crontab every few minutes (see
  crontab.example). Fires every pending reminder whose due_at has passed —
  including any that came due while the machine was down — and sends it via
  the existing Hermes Telegram bot into the reminder's origin chat (the chat
  where it was captured), falling back to the legacy working-memory lane
  (WM_TELEGRAM_CHAT_ID / THREAD_ID) when the origin is missing or not
  deliverable by this script. A failed send is never marked fired; it stays
  pending and retries on the next tick. Every attempt is logged to
  $WM_ROOT/logs/YYYY-MM.log (JSON lines).

* stdout mode (no Telegram): if TELEGRAM_BOT_TOKEN or WM_TELEGRAM_CHAT_ID is
  unset, the script prints each due reminder to stdout as one line and marks
  it fired. Wire it as a Hermes no_agent cron job (every few minutes,
  script=reminder-check.py, deliver to your home channel): the scheduler
  delivers non-empty stdout verbatim. In this mode delivery is the cron job's
  business — stdout carries only reminder lines; diagnostics go to stderr.

Todoist: the agent mirrors each reminder synchronously at capture time via
reminders.py. This tick runs the durable CATCH-UP (mirror anything still
missing a todoist_id) and completion reconciliation. Mirrored reminders are
NOT fired locally — Todoist's notification is the reminder — but stay as the
durable record and fallback. Without a token only the local layer runs.

Store access goes through reminders.py, which owns reminders.json and takes
the lock the agent also takes; this script no longer reads or writes that
file directly, and no longer carries its own copy of the Todoist client.

Stdlib only.
"""

import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reminders as rem  # noqa: E402
import todoist  # noqa: E402
import wmlib  # noqa: E402


def _send(token, chat_id, thread_id, text, retries=3):
    """Send via the existing bot. Returns (ok, attempts_or_error)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
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


def _git_commit(root, summary):
    """Commit the store change.

    Deliberately narrow: only reminders.json is staged. A blanket `git add
    -A` here also swept up whatever the agent happened to be part-way
    through writing in the same repo, attributing it to this tick.
    """
    try:
        subprocess.run(["git", "-C", str(root), "add", "reminders.json"],
                       check=False, capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m",
                        f"reminders: {summary}"],
                       check=False, capture_output=True)
    except Exception as exc:
        print(f"  git commit failed: {exc}", file=sys.stderr, flush=True)


def _resolve_target(reminder, default_chat, default_thread):
    """Pick the delivery address for a reminder.

    Origin (platform + chat_id + thread_id) is recorded at capture time.
    Telegram origins deliver directly; anything else (or a missing origin)
    falls back to the legacy lane / home channel. Returns
    (chat_id, thread_id, fell_back).
    """
    origin = reminder.get("origin") or {}
    platform = origin.get("platform") or "telegram"
    if platform != "telegram":
        # Non-Telegram origins are not deliverable by this standalone script.
        return default_chat, default_thread, True
    if origin.get("chat_id"):
        return origin.get("chat_id"), origin.get("thread_id") or default_thread, False
    return default_chat, default_thread, True


def _tick(root, env):
    """One firing pass. Assumes the tick lock is held."""
    chat_id = env.get("WM_TELEGRAM_CHAT_ID", "").strip()
    thread_id = env.get("WM_TELEGRAM_THREAD_ID", "").strip()
    token = wmlib.hermes_env().get("TELEGRAM_BOT_TOKEN", "").strip()

    telegram_mode = bool(token and chat_id)
    if not telegram_mode:
        print("reminder-check: Telegram not configured "
              "(TELEGRAM_BOT_TOKEN / WM_TELEGRAM_CHAT_ID) — stdout mode; "
              "deliver via a Hermes no_agent cron job (see README).",
              file=sys.stderr, flush=True)

    if not rem.store_path(root).exists():
        return 0

    # Todoist catch-up + completion sync. Both are no-ops when the mirror is
    # disabled, and both take the store lock internally.
    mirrored = aged = closed = []
    if todoist.enabled():
        # Mirroring is cheap and only runs when something is unmirrored;
        # reconciliation is rate-limited (see reminders.reconcile_due).
        mirrored = rem.mirror_pending(root)
        if rem.reconcile_due(root):
            closed, aged = rem.reconcile(root)

    fired, failed, fell_back = [], [], []
    for r in rem.due_now(root):
        print(f"  firing {r.get('id')} (due {r.get('due_at')}): "
              f"{(r.get('message') or '')[:80]}", file=sys.stderr, flush=True)
        if telegram_mode:
            t_chat, t_thread, fb = _resolve_target(r, chat_id, thread_id)
            if fb:
                fell_back.append(r["id"])
                wmlib.log(root, "reminder-cron", "fire", "origin-fallback",
                          reminder_id=r["id"], origin=r.get("origin") or "none")
            ok, info = _send(token, t_chat, t_thread, r.get("message", "Reminder"))
            if ok:
                extra = {"fired_at": wmlib.iso()}
                if fb:
                    extra["delivered_via"] = "fallback"
                rem.set_status(root, r["id"], "fired", **extra)
                fired.append(r["id"])
                wmlib.log(root, "reminder-cron", "fire", "sent",
                          reminder_id=r["id"], attempts=info,
                          origin=r.get("origin") or "legacy-lane")
            else:
                failed.append(r["id"])
                print(f"  send failed ({info}); leaving pending for next tick",
                      file=sys.stderr, flush=True)
                wmlib.log(root, "reminder-cron", "fire", "failed",
                          reminder_id=r["id"], error=str(info))
        else:
            # stdout mode: the delivery payload IS stdout (the cron scheduler
            # delivers it verbatim to the home channel).
            print(f"🔔 {r.get('message', 'Reminder')}", flush=True)
            rem.set_status(root, r["id"], "fired",
                           fired_at=wmlib.iso(), delivered_via="cron-stdout")
            fired.append(r["id"])
            wmlib.log(root, "reminder-cron", "fire", "stdout",
                      reminder_id=r["id"], origin=r.get("origin") or "legacy-lane")

    if fired or failed or mirrored or closed or aged:
        _git_commit(root, f"fired {len(fired)}, failed {len(failed)}, "
                          f"mirrored {len(mirrored)}, reconciled {len(closed)}")

    pending = sum(1 for r in rem.load(root) if r.get("status") == "pending")
    print(f"reminder-check: mode={'telegram' if telegram_mode else 'stdout'} "
          f"fired={len(fired)} failed={len(failed)} fallback={len(fell_back)} "
          f"pending={pending} mirrored={len(mirrored)} reconciled={len(closed)} "
          f"aged={len(aged)}", file=sys.stderr, flush=True)
    return 0


def main():
    env = wmlib.wm_env()
    root = wmlib.wm_root(env)
    # Single-flight across ticks. This is SEPARATE from the store lock that
    # reminders.py takes per mutation: the store lock keeps individual writes
    # consistent, but a whole pass is read-then-send-then-mark, and two
    # overlapping passes would both see the same entry as due and send it
    # twice. A slow Telegram retry makes overlap realistic on a 5-minute cron.
    try:
        with wmlib.FileLock(root / "meta" / "reminder-check.lock", blocking=False):
            return _tick(root, env)
    except wmlib.LockBusy:
        print("reminder-check: another tick in progress; skipping",
              file=sys.stderr, flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
