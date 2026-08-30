#!/usr/bin/env bash
# Verification for the VPS, where Hermes actually runs.
#
#   cd /path/to/working-memory-system && git pull && ./verify-on-vps.sh
#
# Paste the whole output back. Everything here is READ-ONLY with respect to
# your live data: the test suites build their own temporary roots, and the
# checks against the real WM_ROOT only read. Nothing is installed, no cron
# entry is touched, and the gateway is not restarted.
#
# Exit status is 0 only if every REQUIRED check passed. Checks that depend on
# optional configuration (Todoist, a vault clone) report SKIP, not failure.

set -uo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
VENV_PY="$HERMES_HOME/hermes-agent/venv/bin/python"
PY="$(command -v python3 || true)"
FAILED=0
SKIPPED=0

hr() { printf '%s\n' "------------------------------------------------------------"; }
sec() { hr; printf '== %s\n' "$1"; hr; }
ok() { printf '  [PASS] %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; FAILED=$((FAILED + 1)); }
skip() { printf '  [SKIP] %s\n' "$1"; SKIPPED=$((SKIPPED + 1)); }

sec "0. Environment"
printf '  date          : %s\n' "$(date -Is)"
printf '  host / user   : %s / %s\n' "$(hostname)" "$(whoami)"
printf '  package dir   : %s\n' "$PKG_DIR"
printf '  git HEAD      : %s\n' "$(git -C "$PKG_DIR" log --oneline -1 2>&1)"
printf '  git status    : %s\n' "$(git -C "$PKG_DIR" status --porcelain | wc -l) modified file(s)"
printf '  python3       : %s (%s)\n' "${PY:-MISSING}" "$($PY -V 2>&1)"
if [ -x "$VENV_PY" ]; then
  printf '  hermes venv   : %s (%s)\n' "$VENV_PY" "$($VENV_PY -V 2>&1)"
else
  printf '  hermes venv   : NOT FOUND at %s\n' "$VENV_PY"
fi
printf '  WM_ROOT (env) : %s\n' "$(grep -E '^WM_ROOT=' "$HERMES_HOME/working-memory.env" 2>/dev/null || echo '(not set in working-memory.env)')"

sec "1. Full test suite (temporary roots — your data is not touched)"
# Prefer the Hermes venv: it is the only interpreter that can import the real
# gateway package, so the capture-gate suites run against the real
# MessageEvent/SessionSource rather than the stubs.
if [ -x "$VENV_PY" ]; then
  SUITE_PY="$VENV_PY"
  printf '  using the Hermes venv python (real gateway classes)\n'
else
  SUITE_PY="$PY"
  printf '  using system python (gateway stubs)\n'
fi
"$SUITE_PY" "$PKG_DIR/tests/run_all.py"
if [ $? -eq 0 ]; then ok "all offline suites"; else bad "offline suites — see output above"; fi

sec "2. Patch install against the REAL gateway classes"
# The one check a stub cannot stand in for: it exists because a Hermes rename
# (BaseAdapter -> BasePlatformAdapter) silently disabled the hook.
if [ -x "$VENV_PY" ]; then
  WM_SKIP_PATCH= "$VENV_PY" "$PKG_DIR/tests/test_patch_install.py"
  if [ $? -eq 0 ]; then ok "hook patches the real BasePlatformAdapter"; else bad "patch install"; fi
else
  skip "no Hermes venv at $VENV_PY — cannot verify against real gateway classes"
fi

sec "3. Every script still imports and runs --help"
for s in wmlib.py rawlog.py todoist.py wm-backup-push.py; do
  if "$PY" -c "import py_compile,sys; py_compile.compile('$PKG_DIR/$s', doraise=True)" 2>/dev/null; then
    ok "compiles: $s"
  else
    bad "compiles: $s"
  fi
done
for s in rawlog.py todoist.py; do
  if "$PY" "$PKG_DIR/$s" --help >/dev/null 2>&1; then ok "CLI responds: $s --help"; else bad "CLI responds: $s --help"; fi
done

sec "4. Live WM_ROOT — read-only inspection"
WM_ROOT_LIVE="$("$PY" -c "import sys; sys.path.insert(0,'$PKG_DIR'); import wmlib; print(wmlib.wm_root())" 2>/dev/null)"
printf '  resolved WM_ROOT: %s\n' "$WM_ROOT_LIVE"
if [ -d "$WM_ROOT_LIVE" ]; then
  ok "WM_ROOT exists"
  printf '  raw files       : %s\n' "$(ls -1 "$WM_ROOT_LIVE/raw"/*.md 2>/dev/null | wc -l)"
  printf '  git status      : %s uncommitted file(s)\n' "$(git -C "$WM_ROOT_LIVE" status --porcelain 2>/dev/null | wc -l)"

  # Does the live transcript still parse under the new reader? Entries
  # captured before the cut carry [id: ...] headers and field lines.
  if "$PY" "$PKG_DIR/rawlog.py" --root "$WM_ROOT_LIVE" search --limit 3 >/dev/null 2>&1; then
    ok "live raw transcript parses (including pre-cut entries)"
    printf '  transcript      : %s entries readable\n' \
      "$("$PY" "$PKG_DIR/rawlog.py" --root "$WM_ROOT_LIVE" search --limit 0 2>/dev/null | wc -l)"
  else
    bad "live raw transcript does NOT parse — paste the error:"
    "$PY" "$PKG_DIR/rawlog.py" --root "$WM_ROOT_LIVE" search --limit 3 2>&1 | head -5
  fi

  # Files left behind by the 2026-08-29 cut (harmless, but flag them).
  for stale in records.db reminders.json records-snapshot.db meta/tag-index.json; do
    [ -e "$WM_ROOT_LIVE/$stale" ] && printf '  leftover        : %s (unused since the cut)\n' "$stale"
  done

else
  bad "WM_ROOT $WM_ROOT_LIVE does not exist"
fi

sec "5. Todoist connectivity (optional)"
if "$PY" -c "import sys; sys.path.insert(0,'$PKG_DIR'); import todoist; sys.exit(0 if todoist.enabled() else 1)" 2>/dev/null; then
  if "$PY" "$PKG_DIR/todoist.py" list >/dev/null 2>&1; then
    ok "Todoist reachable ($("$PY" "$PKG_DIR/todoist.py" list 2>/dev/null | wc -l) open tasks)"
  else
    bad "Todoist enabled but the API call failed:"
    "$PY" "$PKG_DIR/todoist.py" list 2>&1 | head -3 | sed 's/^/    /'
  fi
else
  skip "Todoist not enabled (TODOIST_MIRROR_ENABLED / TODOIST_API_TOKEN)"
fi

sec "6. Installed wiring (symlinks, wrappers, cron)"
for p in "$HERMES_HOME/hooks/working-memory-debounce" \
         "$HERMES_HOME/skills/note-taking/working-memory/SKILL.md"; do
  if [ -e "$p" ]; then ok "present: $p -> $(readlink -f "$p" 2>/dev/null || echo "$p")"; else bad "missing: $p"; fi
done
echo "  wrapper scripts in $HERMES_HOME/scripts:"
ls -1 "$HERMES_HOME/scripts" 2>/dev/null | sed 's/^/    /' || echo "    (none)"
for w in rawlog.py todoist.py wm-backup-push.py; do
  if [ -f "$HERMES_HOME/scripts/$w" ] && grep -q "Wrapper" "$HERMES_HOME/scripts/$w" 2>/dev/null; then
    ok "wrapper installed: $w"
  else
    bad "$w is missing or is a stale COPY, not a wrapper — re-run setup.sh"
  fi
done
for gone in reminders.py records.py reminder-check.py wm-consolidation-gate.py cron-session-prune.py; do
  if [ -e "$HERMES_HOME/scripts/$gone" ]; then
    bad "$gone still present in ~/.hermes/scripts — DELETE it; it is a stale copy of removed code"
  else
    ok "removed script absent: $gone"
  fi
done
echo "  Hermes cron jobs (there must be NO job invoking the agent):"
hermes cron list 2>/dev/null | sed 's/^/    /' || echo "    (could not list — check manually)"
echo "  crontab lines mentioning the removed tick (should be EMPTY):"
crontab -l 2>/dev/null | grep -i 'reminder-check' | sed 's/^/    /' && bad "remove that crontab line — the script no longer exists" || ok "no stale crontab entry"
echo "  crontab lines mentioning working-memory:"
crontab -l 2>/dev/null | grep -iE 'reminder-check|working-memory|wm-' | sed 's/^/    /' || echo "    (none)"

hr
if [ "$FAILED" -eq 0 ]; then
  printf 'RESULT: OK — 0 failures, %s skipped\n' "$SKIPPED"
else
  printf 'RESULT: %s CHECK(S) FAILED, %s skipped\n' "$FAILED" "$SKIPPED"
fi
hr
exit "$FAILED"
