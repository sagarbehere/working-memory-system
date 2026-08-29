#!/usr/bin/env bash
# Working-memory system installer (spec Section 15: setup.sh).
#
# Creates the data skeleton, initializes the backup git repo, installs the
# skill + debounce hook, and writes the runtime env file (never overwrites).
# Idempotent — safe to re-run after a package update.
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
WM_ROOT="${WM_ROOT:-$HOME/working-memory}"

echo "== Working memory setup =="
echo "Package: $PKG_DIR"
echo "Data:    $WM_ROOT"
echo "Hermes:  $HERMES_HOME"

# 1. Data skeleton (spec Section 3/4)
mkdir -p "$WM_ROOT/raw/archive" "$WM_ROOT/meta" "$WM_ROOT/logs"
if [ ! -f "$WM_ROOT/meta/lanes.json" ]; then
  echo "{}" > "$WM_ROOT/meta/lanes.json"
fi
# 2. Backup git repo (spec Section 3) — repo-local identity, no global config.
#    FRESH install: init + initial commit so the audit trail starts.
#    ESTABLISHED repo: identity config only, NEVER commit — pending changes
#    belong to whoever made them (capture pipeline, agent edits); a catch-all
#    commit here raced an in-flight edit and mislabeled it "init:" (2026-08-29).

WM_IGNORES='meta/pending-buffer.json
meta/*.lock
meta/todoist-state.json
logs/
*.tmp'

if [ ! -d "$WM_ROOT/.git" ]; then
  git -C "$WM_ROOT" init -q
  git -C "$WM_ROOT" config user.name "Hermes Working Memory"
  git -C "$WM_ROOT" config user.email "hermes@working-memory.local"
  printf '%s\n' "$WM_IGNORES" > "$WM_ROOT/.gitignore"
  git -C "$WM_ROOT" add -A
  git -C "$WM_ROOT" commit -q -m "init: working memory skeleton" || true
  echo "Git repo initialized with initial commit."
else
  git -C "$WM_ROOT" config user.name "Hermes Working Memory"
  git -C "$WM_ROOT" config user.email "hermes@working-memory.local"
  # Add any MISSING ignore lines (idempotent, never rewrites the file, never
  # commits — an established repo's pending changes belong to whoever made
  # them). Existing installs predate the records.db entries.
  touch "$WM_ROOT/.gitignore"
  added=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if ! grep -qxF -- "$line" "$WM_ROOT/.gitignore"; then
      printf '%s\n' "$line" >> "$WM_ROOT/.gitignore"
      added=$((added + 1))
    fi
  done <<EOF
$WM_IGNORES
EOF
  if [ "$added" -gt 0 ]; then
    echo "Git repo already initialized; added $added missing .gitignore line(s)."
  else
    echo "Git repo already initialized (working tree left untouched)."
  fi
  # Upgrade cleanup: the 2026-08-29 cut removed the SQLite store and the
  # local reminder store. Their files linger in an existing WM_ROOT and, if
  # tracked, keep being committed. Flag them; never delete the user's data.
  for stale in records.db reminders.json records-snapshot.db; do
    if [ -e "$WM_ROOT/$stale" ]; then
      echo "  NOTE: $WM_ROOT/$stale is left over from before the 2026-08-29"
      echo "        cut and is no longer used. Reminders now live in Todoist."
      if git -C "$WM_ROOT" ls-files --error-unmatch "$stale" >/dev/null 2>&1; then
        echo "        It is still tracked by git. To retire it:"
        echo "            git -C $WM_ROOT rm --cached $stale"
      fi
      echo "        Delete it when you are satisfied nothing needs it."
    fi
  done
fi

# 3. Install the skill (SYMLINK, like the hook — the package is the single
#    source of truth; package edits apply immediately after /reload-skills
#    or a new session. The git-tracked copy is the canonical one, so every
#    accepted refinement is diffable/revertible, spec Section 17.)
SKILL_DEST="$HERMES_HOME/skills/note-taking/working-memory"
mkdir -p "$SKILL_DEST"
ln -sfn "$PKG_DIR/SKILL.md" "$SKILL_DEST/SKILL.md"
echo "Skill installed (symlink): $SKILL_DEST/SKILL.md"

# 4. Install the debounce hook (symlink, so package updates apply immediately)
mkdir -p "$HERMES_HOME/hooks"
ln -sfn "$PKG_DIR/hooks/working-memory-debounce" \
  "$HERMES_HOME/hooks/working-memory-debounce"
echo "Hook installed: $HERMES_HOME/hooks/working-memory-debounce (symlink)"

# 4b. Install the cron/helper scripts as WRAPPERS — real files inside
#     $HERMES_HOME/scripts that exec the package copy (single source of
#     truth). Real files (not symlinks) pass the Hermes cron guard, which
#     rejects scripts resolving outside ~/.hermes/scripts. Package edits
#     take effect immediately — no refresh step; re-run setup.sh only if
#     the package moves or a new helper script is added. The package path
#     is baked in at generation time (2026-08-29 wrapper model):
#       - wm-consolidation-gate.py  -> nightly consolidation gate (context
#         script: empty output = no work = scheduler skips the AI call)
#       - cron-session-prune.py     -> monthly cron-session cleanup (watchdog)
#       - wm-backup-push.py         -> nightly backup push to the private
#         remote (watchdog: silent when healthy, alerts on failure)
#       - rawlog.py                 -> raw capture log CLI (owns the entry format)
#       - todoist.py                -> Todoist client (the reminder layer)
#     wmlib.py is NOT wrapped: it is imported by the others from the package
#     directory, never executed on its own.
WRAPPED_SCRIPTS="wm-consolidation-gate.py cron-session-prune.py wm-backup-push.py rawlog.py todoist.py"
mkdir -p "$HERMES_HOME/scripts"
for s in $WRAPPED_SCRIPTS; do
  if [ -f "$PKG_DIR/$s" ]; then
    cat > "$HERMES_HOME/scripts/$s" <<WRAPPER_EOF
#!/usr/bin/env python3
"""Wrapper — execs the package copy (single source of truth).

Generated by setup.sh; package path baked in at install time. Re-run
setup.sh only if the package moves. Package script edits apply
immediately (no refresh step).
"""
import os
import sys

os.execv(sys.executable, [sys.executable, "$PKG_DIR/$s"] + sys.argv[1:])
WRAPPER_EOF
    chmod +x "$HERMES_HOME/scripts/$s"
  fi
done
echo "Wrapper scripts installed (exec package copies): $WRAPPED_SCRIPTS"

# 4c. Retire wrappers for scripts this package no longer ships. A wrapper left
#     behind execs a package file that no longer exists, and Hermes' cron
#     resolves scripts from this directory — that is how a superseded script
#     once stayed in the scheduler's path. Named explicitly, never a directory
#     sweep: other projects keep their own scripts here.
RETIRED_SCRIPTS="reminders.py records.py reminder-check.py"
for s in $RETIRED_SCRIPTS; do
  target="$HERMES_HOME/scripts/$s"
  if [ -f "$target" ]; then
    # Only remove something this installer generated; never touch a file
    # someone else put here that happens to share the name.
    if head -3 "$target" 2>/dev/null | grep -q "Wrapper — execs the package copy"; then
      rm -f "$target"
      echo "Retired stale wrapper: $target (script removed in the 2026-08-29 cut)"
    else
      echo "  NOTE: $target exists but was not generated by setup.sh — leaving it."
      echo "        The working-memory package no longer ships $s; if this is"
      echo "        a leftover copy, delete it (Hermes cron runs scripts here)."
    fi
  fi
done

# 5. Runtime env (never overwrite user edits)
if [ ! -f "$HERMES_HOME/working-memory.env" ]; then
  cp "$PKG_DIR/.env.example" "$HERMES_HOME/working-memory.env"
  echo "Config written: $HERMES_HOME/working-memory.env"
else
  echo "Config exists (kept): $HERMES_HOME/working-memory.env"
fi

# 7. Version-control the package (spec Section 17: SKILL.md tracked in git,
#    so every accepted refinement is diffable and revertible)
if [ ! -d "$PKG_DIR/.git" ]; then
  git -C "$PKG_DIR" init -q
  git -C "$PKG_DIR" config user.name "Hermes Working Memory"
  git -C "$PKG_DIR" config user.email "hermes@working-memory.local"
  if [ ! -f "$PKG_DIR/.gitignore" ]; then
    printf '__pycache__/\n*.pyc\n' > "$PKG_DIR/.gitignore"
  fi
  git -C "$PKG_DIR" add -A
  git -C "$PKG_DIR" commit -q -m "init: working-memory package" || true
  echo "Package git repo initialized with initial commit."
else
  echo "Package git repo already initialized."
fi

echo
echo "== Next steps =="
echo "1) Capture is marker-first: works anywhere with zero config — start a"
echo "   message with 'Hey memory' (see working-memory-system-spec-v3.md,"
echo "   Scope boundary)."
echo "2) Optional frictionless lane: set WM_TELEGRAM_CHAT_ID (+ THREAD_ID)"
echo "   in $HERMES_HOME/working-memory.env as a legacy seed, OR reserve a"
echo "   chat in-band with 'reserve for memory' (any platform; release"
echo "   with 'release for memory'). Markers are implied in reserved chats."
echo "3) No OS crontab entry is needed. If you are UPGRADING and have a"
echo "   reminder-check.py line in your crontab, remove it — that script is"
echo "   gone (crontab -e)."
echo "4) Restart the gateway so the hook loads (run from SSH, not from"
echo "   inside an agent session — it deadlocks there):"
echo "   hermes gateway restart"
echo "5) Send /reload-skills in the Telegram chat so the working-memory"
echo "   skill is visible to the agent."
