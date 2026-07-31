#!/usr/bin/env bash
# SessionStart hook: surface .ai/ maintenance state that Claude cannot see from
# the loaded context itself. Non-blocking — it injects a reminder and the model
# decides when to act.
#
# Two independent checks, both fail-open:
#   1. Housekeeping flag (.ai/.housekeeping-pending) → hint /ai-housekeeping.
#   2. Eager entrypoint consistency. Claude loads the eager memory set through
#      static CLAUDE.md @imports, which cannot resolve entrypoints dynamically
#      the way the cursor/codex session-start hooks do. A memory §4
#      directory-form upgrade therefore leaves the loader pointing at the
#      pre-upgrade path until the next deploy re-derives it, and a missing
#      @import is silently ignored by Claude Code — the eager doc just goes
#      absent with no error. This check names that gap out loud.
#
# The deployment tool performs the same check as `stale eager import` /
# `ambiguous memory entrypoint` drift; this hook covers the window between a
# housekeeping split and the next deploy.

set -euo pipefail

# Anchor cwd to project root.
cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

LOG="${HOME}/.claude/stop-hook.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') session-start $*" >> "$LOG" 2>/dev/null || true; }

FLAG=".ai/.housekeeping-pending"
LOADER="CLAUDE.md"
TOPICS=(overview architecture design conventions)

# --- eager entrypoint checks -------------------------------------------------

# Path the .ai/index.md routing table assigns to a topic label, normalized to a
# repo-relative .ai/ path. Empty when unrouted or when the entry escapes .ai/.
# Mirrors resolve_ai_router_path in the cursor/codex session-start hooks.
resolve_ai_router_path() {
  local label="$1"
  local routed=""

  if [ -f ".ai/index.md" ]; then
    routed="$(awk -F'|' -v want="$label" '
      function trim(s) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", s)
        return s
      }
      /^[[:space:]]*\|/ {
        name = tolower(trim($2))
        file = trim($3)
        gsub(/`/, "", file)
        if (name == tolower(want)) {
          print file
          exit
        }
      }
    ' .ai/index.md 2>/dev/null || true)"
  fi

  routed="${routed#./}"
  if [ -n "$routed" ]; then
    case "$routed" in
      .ai/*) printf '%s\n' "$routed" ;;
      /*|../*|*/../*) return 1 ;;
      *) printf '.ai/%s\n' "$routed" ;;
    esac
  fi
}

warnings=""
warn() { warnings+="- $1"$'\n'; }

if [ -d ".ai" ]; then
  for topic in "${TOPICS[@]}"; do
    flat=".ai/${topic}.md"
    dir_index=".ai/${topic}/index.md"

    routed="$(resolve_ai_router_path "${topic}" || true)"
    current=""
    if [ -n "$routed" ] && { [ "$routed" = "$flat" ] || [ "$routed" = "$dir_index" ]; } && [ -f "$routed" ]; then
      current="$routed"
    elif [ -f "$dir_index" ] && [ ! -f "$flat" ]; then
      current="$dir_index"
    else
      current="$flat"
    fi

    if [ -f "$flat" ] && [ -f "$dir_index" ]; then
      warn "${topic}: both ${flat} and ${dir_index} exist. The directory-form upgrade renames, it does not duplicate — one of them is stale."
    fi

    if [ -n "$routed" ] && [ "$routed" != "$flat" ] && [ "$routed" != "$dir_index" ]; then
      warn "${topic}: .ai/index.md routes to ${routed}; the eager entrypoint must be ${flat} or ${dir_index}."
    elif [ -n "$routed" ] && [ ! -f "$routed" ]; then
      warn "${topic}: .ai/index.md routes to ${routed}, which does not exist."
    fi

    if [ -f "$LOADER" ]; then
      imported="$(grep -oE "^@\.ai/${topic}(\.md|/index\.md)[[:space:]]*$" "$LOADER" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
      imported="${imported#@}"
      if [ -n "$imported" ] && [ "$imported" != "$current" ]; then
        if [ -f "$imported" ]; then
          warn "${topic}: CLAUDE.md imports @${imported} but the current entrypoint is ${current}. The eager set is loading the stale document — Read ${current} before relying on it, and re-deploy to repair the loader."
        else
          warn "${topic}: CLAUDE.md imports @${imported}, which does not exist (current entrypoint: ${current}). Claude Code ignores missing imports silently, so ${topic} is ABSENT from this session's eager set — Read ${current} before relying on it, and re-deploy to repair the loader."
        fi
      fi
    fi
  done
fi

# --- housekeeping flag -------------------------------------------------------

hk_count=0
hk_issues=""
if [ -f "$FLAG" ]; then
  hk_count=$(wc -l < "$FLAG" 2>/dev/null | tr -d ' ')
  hk_issues=$(cat "$FLAG" 2>/dev/null || true)
fi

if [ ! -f "$FLAG" ] && [ -z "$warnings" ]; then
  log "no-housekeeping-flag no-entrypoint-drift → no-op"
  exit 0
fi

# --- compose + emit ----------------------------------------------------------

ctx=""
if [ -f "$FLAG" ]; then
  ctx+="HOUSEKEEPING REMINDER: .ai/.housekeeping-pending exists with ${hk_count} line(s).

Details:
${hk_issues}

INSTRUCTION TO ASSISTANT: Before responding to the user's first request this session, prepend a one-line notice exactly: '⚠️  .ai/ housekeeping pending — run /ai-housekeeping when convenient.' Do not block the user's task; surface the reminder once and then proceed normally.
"
fi

if [ -n "$warnings" ]; then
  if [ -n "$ctx" ]; then ctx+=$'\n'; fi
  ctx+="EAGER ENTRYPOINT WARNING: the .ai/ snapshot and the CLAUDE.md eager import set disagree.

${warnings}
INSTRUCTION TO ASSISTANT: Before responding to the user's first request this session, prepend a one-line notice exactly: '⚠️  .ai/ eager import out of sync — see session-start warning.' Do not block the user's task; surface it once, then proceed normally.
"
fi

emit() {
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg ctx "$1" \
      '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' 2>/dev/null || true
  elif command -v python3 >/dev/null 2>&1; then
    CTX="$1" python3 -c 'import json, os; print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": os.environ["CTX"]}}))' 2>/dev/null || true
  else
    log "no jq or python3 → cannot emit JSON safely"
  fi
}

log "housekeeping=${hk_count} entrypoint-warnings=$(printf '%s' "$warnings" | grep -c '^-' || true) → hint"
emit "$ctx"

exit 0
