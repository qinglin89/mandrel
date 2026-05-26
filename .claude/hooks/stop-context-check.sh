#!/usr/bin/env bash
# Stop hook for ai-coding-v2 workflow.
#
# Logic:
#   Case 1: active task status == completed
#           → instruct model to invoke /ai-sync-v2 for absorption + archive.
#   Case 2a: active task status != completed
#            AND no session-log entry written for this session yet
#            AND transcript token count > THRESHOLD
#           → instruct model to wrap up: write session-log entry and prepare handoff.
#   Case 2b: active task status != completed AND session-log entry exists
#           → allow stop (handoff already done).
#   Else (no active task, or under threshold without log)
#           → allow stop.
#
# Logging: every decision appended to ~/.claude/stop-hook.log
#          (file write only; never stdout — won't pollute hook JSON output).

set -euo pipefail

# Anchor cwd to project root so relative paths (.ai-tasks/*.md) resolve correctly
# regardless of which subdirectory Claude Code was launched from.
cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

LOG="${HOME}/.claude/stop-hook.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') session=${session_id:-?} $*" >> "$LOG" 2>/dev/null || true; }

input=$(cat)
session_id=$(echo "$input" | jq -r '.session_id // empty')
transcript_path=$(echo "$input" | jq -r '.transcript_path // empty')

# Fail-open on missing inputs.
[ -z "$session_id" ] && { log "no-input session_id → allow"; exit 0; }
[ -z "$transcript_path" ] && { log "no-input transcript → allow"; exit 0; }

# Locate the active task: a file in .ai-tasks/ whose claimed-by matches.
shopt -s nullglob
task_files=(.ai-tasks/*.md)
shopt -u nullglob

task_file=""
for f in "${task_files[@]}"; do
  # Skip the index file.
  [ "$(basename "$f")" = "index.md" ] && continue
  if grep -q "claimed-by:.*${session_id}" "$f" 2>/dev/null; then
    task_file="$f"
    break
  fi
done

# No active task → allow stop.
[ -z "$task_file" ] && { log "no-active-task → allow"; exit 0; }

# Extract status from frontmatter.
status=$(awk '
  /^---$/ { count++; if (count == 2) exit; next }
  count == 1 && /^status:/ {
    sub(/^status:[[:space:]]*/, "")
    sub(/[[:space:]]*#.*/, "")
    sub(/[[:space:]]+$/, "")
    print
    exit
  }
' "$task_file")

# Case 1: completed → invoke /ai-sync-v2.
if [ "$status" = "completed" ]; then
  log "case1 task=$task_file status=completed → block (invoke /ai-sync-v2)"
  cat <<EOF
{
  "decision": "block",
  "reason": "Task '${task_file}' has status: completed. Invoke /ai-sync-v2 now to apply absorption and archive the task before ending the session."
}
EOF
  exit 0
fi

# Case 2: status != completed → check session-log for this session's entry.
session_log_section=$(awk '
  /^## Session log/ { f=1; next }
  f && /^## / { exit }
  f { print }
' "$task_file")

if echo "$session_log_section" | grep -q -F "$session_id"; then
  # Case 2b: handoff already written → allow stop.
  log "case2b task=$task_file status=$status log-entry-present → allow"
  exit 0
fi

# Case 2a candidate: no log entry yet. Check transcript token count.
# Approximate: 1 token ≈ 4 chars.
char_count=$(wc -c < "$transcript_path" 2>/dev/null || echo 0)
approx_tokens=$((char_count / 4))

THRESHOLD=200000

if [ "$approx_tokens" -gt "$THRESHOLD" ]; then
  log "case2a task=$task_file status=$status tokens=$approx_tokens > $THRESHOLD → block (wrap up)"
  cat <<EOF
{
  "decision": "block",
  "reason": "Context has grown to approximately ${approx_tokens} tokens (over the ${THRESHOLD} budget for reliable work). Wrap up this session: (1) append a '## Session log' entry to '${task_file}' (Done / Next / Open) describing what's been done and what the next session should pick up. (2) Raise session-est total upward to reflect the actual scope (e.g., if currently 1/1, change to 1/2 or higher) — this wrap-up means the original estimate undershot. (3) Keep status as in_progress. The user will resume in a fresh session."
}
EOF
  exit 0
fi

# Below threshold without log: allow stop (session may be ending naturally early).
log "case2c task=$task_file status=$status tokens=$approx_tokens ≤ $THRESHOLD → allow"
exit 0
