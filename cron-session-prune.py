#!/usr/bin/env python3
"""Monthly cron-session prune (watchdog pattern).

Runs `hermes sessions prune --older-than 30 --source cron --yes` — the
built-in cron-only prune. Watchdog semantics:

  * 0 pruned  -> empty stdout -> SILENT tick (nothing delivered)
  * N pruned  -> one-line summary delivered verbatim
  * failure   -> non-zero exit -> scheduler sends an error alert
"""

import shutil
import subprocess
import sys


def main() -> int:
    exe = shutil.which("hermes")
    if not exe:
        print("cron-prune: hermes CLI not found on PATH", file=sys.stderr)
        return 1
    try:
        proc = subprocess.run(
            [exe, "sessions", "prune",
             "--older-than", "30", "--source", "cron", "--yes"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("cron-prune: prune timed out", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        print(
            "cron-prune: prune failed: %s"
            % (proc.stderr.strip() or proc.stdout.strip()),
            file=sys.stderr,
        )
        return 1
    # Parse "Pruned N session(s)."
    count = 0
    for token in proc.stdout.split():
        if token.isdigit():
            count = int(token)
            break
    if count > 0:
        print("Pruned %d cron session(s) older than 30 days." % count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
