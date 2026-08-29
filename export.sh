#!/usr/bin/env bash
# export.sh — bundle the ENTIRE working-memory system into one tarball for
# copying to another machine (or backing up off-box).
#
# Includes:
#   - the package (working-memory-system/): source, SKILL.md, hook, scripts
#     (rawlog.py, todoist.py, wm-backup-push.py, cron-session-prune.py,
#     setup.sh, export.sh), spec, tests, backups/, and its .git history
#   - the data (working-memory/): the raw transcript, meta/,
#     refinement log, and its .git history (point-in-time recovery)
#   - INSTALL-NOTES.txt: the per-install wiring values from THIS machine
#
# Excludes (transient or machine-local, regenerable on the target):
#   __pycache__/, *.pyc, meta/pending-buffer.json (in-flight capture state),
#   meta/*.lock, logs/ (diagnostic trail)
#
# No secrets: the bot token lives in ~/.hermes/.env, which is NOT exported.
#
# Usage:
#   ./export.sh [output-path]     # default: ~/working-memory-export-<stamp>.tar.gz
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

WM_ROOT="$(grep -E '^WM_ROOT=' "$HERMES_HOME/working-memory.env" 2>/dev/null | cut -d= -f2- || true)"
WM_ROOT="${WM_ROOT:-$HOME/working-memory}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$HOME/working-memory-export-$STAMP.tar.gz}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "== Working-memory export =="
echo "Package: $PKG_DIR"
echo "Data:    $WM_ROOT"
echo "Output:  $OUT"

# 1. Package (with .git history)
tar -C "$(dirname "$PKG_DIR")" -cf - \
    --exclude='__pycache__' --exclude='*.pyc' \
    "$(basename "$PKG_DIR")" | tar -C "$STAGE" -xf -

# 2. Data (with .git history), minus transient state
tar -C "$(dirname "$WM_ROOT")" -cf - \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='meta/pending-buffer.json' \
    --exclude='meta/*.lock' \
    --exclude='logs' \
    "$(basename "$WM_ROOT")" | tar -C "$STAGE" -xf -

# 3. Install notes with THIS machine's wiring values
NOTES="$STAGE/INSTALL-NOTES.txt"
{
    echo "Working-Memory System — install notes (exported $(date -Iseconds))"
    echo "================================================================="
    echo
    echo "Contents of this archive:"
    echo "  working-memory-system/   the package (source + policy + tests, git-tracked)"
    echo "  working-memory/          the data (memories, git-tracked)"
    echo
    echo "Steps on the TARGET machine (Hermes + Telegram gateway already running):"
    echo
    echo "  1) Extract:  tar xzf $(basename "$OUT")"
    echo "  2) Install:  cd working-memory-system && ./setup.sh"
    echo "     (creates the skeleton if missing, symlinks the skill+hook,"
    echo "      writes wrapper scripts into ~/.hermes/scripts/ that exec the"
    echo "      package copies — real files, because Hermes' cron scheduler"
    echo "      refuses scripts resolving outside ~/.hermes/ — and writes"
    echo "      ~/.hermes/working-memory.env if absent. It will NOT overwrite"
    echo "      an existing env file.)"
    echo "  3) EDIT ~/.hermes/working-memory.env for the target machine."
    echo "     Values on the SOURCE machine were:"
    grep -E '^(WM_|#)' "$HERMES_HOME/working-memory.env" 2>/dev/null | sed 's/^/       /' || true
    echo "     Capture is marker-first: capture works ANYWHERE by"
    echo "     starting a message with 'Hey memory' — the env vars"
    echo "     are only a LEGACY seed for a frictionless lane and can be left"
    echo "     empty. To reserve a chat as a lane instead, say 'reserve for"
    echo "     memory' in it (release with 'release for memory')."
    echo "  4) Optional convenience lane: if you want a dedicated topic, either"
    echo "     set WM_TELEGRAM_CHAT_ID / WM_TELEGRAM_THREAD_ID in the env file"
    echo "     (legacy seed) or reserve the chat in-band (step 3). A DM-topic"
    echo "     skill binding in ~/.hermes/config.yaml is NOT required for"
    echo "     marker capture; only add it if you also want the topic to"
    echo "     auto-load the working-memory skill:"
    echo "       platforms:"
    echo "         telegram:"
    echo "           extra:"
    echo "             dm_topics:"
    echo "               - chat_id: <chat_id>"
    echo "                 topics:"
    echo "                   - name: Working Memory"
    echo "                     thread_id: <thread_id>"
    echo "                       skill: working-memory"
    echo "  5) No OS crontab entry is needed (see crontab.example)."
    echo "  6) Re-create the Hermes cron jobs — they live in Hermes\'s cron"
    echo "     store, not in this archive. Both are no_agent watchdogs; there"
    echo "     is deliberately NO job that invokes the agent. Ask your agent:"
    echo "     a) \"recreate the nightly working-memory backup push job\""
    echo "        (no_agent, 03:00 daily, script=wm-backup-push.py)."
    echo "     b) \"recreate the monthly cron-session prune job\""
    echo "        (no_agent, monthly, silent unless it pruned something)."
    echo "  7) Restart the gateway (from SSH):  hermes gateway restart"
    echo "  8) /reload-skills in Telegram so sessions pick up the skill."
    echo
    echo "Reminders fire into WM_TELEGRAM_CHAT_ID(+THREAD_ID) via the existing bot."
    echo "No bot token is included in this archive (it lives in ~/.hermes/.env)."
    echo "Capture policy ships in SKILL.md (in the package); only the cron"
    echo "registrations are per-install and re-created in step 6."
} > "$NOTES"

# 4. Bundle
tar -C "$STAGE" -czf "$OUT" .
echo
echo "Exported: $OUT"
echo
echo "Top-level contents:"
tar -tzf "$OUT" | awk -F/ 'NF<=3 {print "  " $0}' | sort | head -20
