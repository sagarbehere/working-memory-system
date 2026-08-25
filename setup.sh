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
mkdir -p "$WM_ROOT/raw/archive" "$WM_ROOT/topics" "$WM_ROOT/meta" "$WM_ROOT/logs"
if [ ! -f "$WM_ROOT/reminders.json" ]; then
  echo "[]" > "$WM_ROOT/reminders.json"
fi
if [ ! -f "$WM_ROOT/meta/lanes.json" ]; then
  echo "{}" > "$WM_ROOT/meta/lanes.json"
fi

# 2. Backup git repo (spec Section 3) — repo-local identity, no global config
if [ ! -d "$WM_ROOT/.git" ]; then
  git -C "$WM_ROOT" init -q
fi
git -C "$WM_ROOT" config user.name "Hermes Working Memory"
git -C "$WM_ROOT" config user.email "hermes@working-memory.local"
if [ ! -f "$WM_ROOT/.gitignore" ]; then
  printf 'meta/pending-buffer.json\nmeta/reminder-check.lock\nlogs/\n*.tmp\n' > "$WM_ROOT/.gitignore"
fi
git -C "$WM_ROOT" add -A
if ! git -C "$WM_ROOT" diff --cached --quiet; then
  git -C "$WM_ROOT" commit -q -m "init: working memory skeleton"
  echo "Git repo initialized with initial commit."
else
  echo "Git repo already up to date."
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

# 4b. Install the cron helper scripts (COPY, not symlink — Hermes' cron
#     scheduler refuses to execute scripts that resolve outside
#     ~/.hermes/scripts/ (guardrail), so a symlink into the package dir
#     would silently fail). The copies are refreshed on every setup.sh
#     run, so re-run setup.sh after a package update:
#       - wm-consolidation-gate.py  -> nightly consolidation gate (context
#         script: empty output = no work = scheduler skips the AI call)
#       - cron-session-prune.py     -> monthly cron-session cleanup (watchdog)
mkdir -p "$HERMES_HOME/scripts"
rm -f "$HERMES_HOME/scripts/wm-consolidation-gate.py" "$HERMES_HOME/scripts/cron-session-prune.py"
install -m 755 "$PKG_DIR/wm-consolidation-gate.py" "$HERMES_HOME/scripts/wm-consolidation-gate.py"
install -m 755 "$PKG_DIR/cron-session-prune.py" "$HERMES_HOME/scripts/cron-session-prune.py"
echo "Cron scripts installed: $HERMES_HOME/scripts/wm-consolidation-gate.py, cron-session-prune.py (copies)"

# 5. Runtime env (never overwrite user edits)
if [ ! -f "$HERMES_HOME/working-memory.env" ]; then
  cp "$PKG_DIR/.env.example" "$HERMES_HOME/working-memory.env"
  echo "Config written: $HERMES_HOME/working-memory.env"
else
  echo "Config exists (kept): $HERMES_HOME/working-memory.env"
fi

# 6. Make scripts executable
chmod +x "$PKG_DIR/reminder-check.py"

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
echo "1) v2 is marker-first (spec Section 18): capture works anywhere with"
echo "   zero config — start a message with 'Hey memory' or 'note'."
echo "2) Optional frictionless lane: set WM_TELEGRAM_CHAT_ID (+ THREAD_ID)"
echo "   in $HERMES_HOME/working-memory.env as a legacy seed, OR reserve a"
echo "   chat in-band with 'reserve for memory' (any platform; release"
echo "   with 'release for memory'). Markers are implied in reserved chats."
echo "3) Add the cron line (crontab -e):"
sed 's/^/   /' "$PKG_DIR/crontab.example"
echo "4) Restart the gateway so the hook loads (run from SSH, not from"
echo "   inside an agent session — it deadlocks there):"
echo "   hermes gateway restart"
echo "5) Send /reload-skills in the Telegram chat so the working-memory"
echo "   skill is visible to the agent."
