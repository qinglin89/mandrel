#!/usr/bin/env bash
# SessionStart hook: check for pending housekeeping flag and hint the LLM if found.
# Non-blocking: just injects a system reminder; user/LLM decides when to run /ai-housekeeping.

set -euo pipefail

# Anchor cwd to project root.
cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

LOG="${HOME}/.claude/stop-hook.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') session-start $*" >> "$LOG" 2>/dev/null || true; }

FLAG=".ai/.housekeeping-pending"

if [ ! -f "$FLAG" ]; then
  log "no-housekeeping-flag → no-op"
  exit 0
fi

count=$(wc -l < "$FLAG" 2>/dev/null | tr -d ' ')
issues=$(cat "$FLAG" 2>/dev/null)

log "housekeeping-flag-present count=$count → hint"

# Emit additionalContext to inject reminder into LLM's session start.
cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "HOUSEKEEPING REMINDER: .ai/.housekeeping-pending exists with ${count} line(s).\n\nDetails:\n${issues}\n\nINSTRUCTION TO ASSISTANT: Before responding to the user's first request this session, prepend a one-line notice exactly: '⚠️  .ai/ housekeeping pending — run /ai-housekeeping when convenient.' Do not block the user's task; surface the reminder once and then proceed normally."
  }
}
EOF

exit 0
