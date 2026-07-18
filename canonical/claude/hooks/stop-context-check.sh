#!/usr/bin/env bash
# Stop hook for the ai-protocol workflow.
#
# Invariant enforced: a task's "advancement" signals — a session-log entry and
# status=completed — must never run ahead of a clean working tree. The
# session-end procedure (workflow/skills/session-end.md) orders it "step1 make the tree clean → then write the log / set completed".
# The hook computes working-tree cleanliness ONCE on entry (STRICT: includes
# untracked files) and gates the advancement branches on it.
#
# Why strict (untracked counts): an untracked file is unhandled work that must
# be resolved before end — real work committed, an unwanted scratch removed, or
# a run-time artifact covered by a gitignore rule for its category. Run-time
# artifact classes are already gitignored (bin/ .logs/ .pids/ .captures/ gen/
# .claude/*.lock), and .ai-tasks/ is gitignored too, so writing the session-log
# / setting status never dirties the tree → wt_clean is an exact proxy for
# "all real work committed".
#
# Logic:
#   Entry: compute wt_clean (git status --porcelain, incl. untracked).
#   Case 1: status == completed
#           clean → block: invoke /ai-sync-v2 (absorption + archive).
#           dirty → block: protocol violation — clean the tree first, keep
#                   status=completed.
#   Case 2a: status != completed AND no session-log entry for this session
#            AND transcript > THRESHOLD → block: wrap up.
#   Case 2b: status != completed AND session-log entry exists
#            clean → allow (handoff done).
#            dirty → block: false handoff (log written, tree not clean).
#   Case 2c: status != completed, no entry, under threshold → allow.
#   Else (no active task, missing inputs, non-repo) → allow (fail-open).
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

# Working-tree cleanliness, computed once (STRICT — includes untracked files).
# Fail-open: non-repo or git error → treat as clean (allow), matching the
# missing-input guards above.
wt_clean=1
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  porcelain=$(git status --porcelain 2>/dev/null || true)
  [ -n "$porcelain" ] && wt_clean=0
fi

# Shared definition of "make the tree clean", stated as a classification frame
# (not an imperative action list) so the model judges each file by its nature.
# Single source: workflow/skills/session-end.md step 1 (mirrored here and in the
# orchestrator's midflight/clean-howto template).
CLEAN_HOWTO="make the working tree clean (\`git status --porcelain\` empty) — each modified file committed, and each untracked file handled by its nature: real work committed; an unwanted scratch file removed; a run-time artifact covered by a gitignore rule for its category (not ignored file-by-file)."

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

# Case 1: completed → close-out, but only if the tree is clean.
if [ "$status" = "completed" ]; then
  if [ "$wt_clean" -eq 0 ]; then
    log "case1-dirty task=$task_file status=completed wt=dirty → block (clean first)"
    cat <<EOF
{
  "decision": "block",
  "reason": "Protocol violation: task '${task_file}' is status: completed but the working tree is not clean. Do NOT change status back to in_progress. Read '.ai-protocol/workflow/skills/session-end.md' and execute the applicable recovery requirements in full. In particular, ${CLEAN_HOWTO} Then end."
}
EOF
    exit 0
  fi
  log "case1 task=$task_file status=completed wt=clean → block (invoke /ai-sync-v2)"
  cat <<EOF
{
  "decision": "block",
  "reason": "Task '${task_file}' has status: completed. Invoke /ai-sync-v2 now and follow it in full — the task-completion closeout (.ai-protocol/workflow/skills/closeout.md) — before ending the session. Your final response must include one line beginning \`Remaining-task audit:\`."
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
  # Case 2b: a session-log entry exists.
  if [ "$wt_clean" -eq 0 ]; then
    # False handoff: log written but tree not clean.
    log "case2b-dirty task=$task_file status=$status log-entry-present wt=dirty → block"
    cat <<EOF
{
  "decision": "block",
  "reason": "Protocol violation: a session-log entry for this session exists in '${task_file}', but the working tree is not clean — this is a false handoff. Read '.ai-protocol/workflow/skills/session-end.md' and execute the applicable recovery requirements in full. The session log is an end-of-session record: clean tree first, then write the log. Resolve one of: (a) ${CLEAN_HOWTO} Then end. Or (b) if the entry was written mid-task by mistake, remove that premature session-log entry."
}
EOF
    exit 0
  fi
  # Handoff complete: clean + entry present.
  log "case2b task=$task_file status=$status log-entry-present wt=clean → allow"
  exit 0
fi

# Case 2a candidate: no log entry yet. Check transcript token count.
# Approximate: 1 token ≈ 4 chars.
char_count=$(wc -c < "$transcript_path" 2>/dev/null || echo 0)
approx_tokens=$((char_count / 4))

THRESHOLD=200000

if [ "$approx_tokens" -gt "$THRESHOLD" ]; then
  log "case2a task=$task_file status=$status tokens=$approx_tokens > $THRESHOLD wt=$wt_clean → block (wrap up)"
  cat <<EOF
{
  "decision": "block",
  "reason": "Context has grown to approximately ${approx_tokens} tokens (over the ${THRESHOLD} budget for reliable work). Read '.ai-protocol/workflow/skills/session-end.md' and execute its wrap-up variant in full for session id '${session_id}'. In particular: (1) ${CLEAN_HOWTO} (2) append the '## Session log' entry to '${task_file}' — Next carries the handoff. (3) re-estimate the session-est total. (4) keep status unchanged unless restoring protocol legality requires otherwise. Start no new work. Then end."
}
EOF
  exit 0
fi

# Below threshold without log: allow stop (session may be ending naturally early).
log "case2c task=$task_file status=$status tokens=$approx_tokens wt=$wt_clean ≤ $THRESHOLD → allow"
exit 0
