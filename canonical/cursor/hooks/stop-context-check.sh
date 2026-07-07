#!/usr/bin/env bash
# Cursor stop hook for the ai-coding-v2 workflow.
# Port of .claude/hooks/stop-context-check.sh — same invariants and case
# logic; only the I/O surface differs:
#   - stdin: conversation_id (instead of session_id), transcript_path.
#   - block: {"followup_message": "..."} (instead of {"decision":"block",...}).
#   - allow: no output, exit 0.
#   - /ai-sync-v2 is invoked by pointing the agent at the skill file.
#
# Invariant enforced: a task's "advancement" signals — a session-log entry and
# status=completed — must never run ahead of a clean working tree. §10 End
# orders it "step1 make the tree clean → then write the log / set completed".
# The hook computes working-tree cleanliness ONCE on entry (STRICT: includes
# untracked files) and gates the advancement branches on it.
#
# Logic:
#   Entry: compute wt_clean (git status --porcelain, incl. untracked).
#   Case 1: status == completed
#           clean → block: invoke ai-sync-v2 skill (absorption + archive).
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
# Logging: every decision appended to ~/.cursor/ai-hooks.log
#          (file write only; never stdout — won't pollute hook JSON output).

set -euo pipefail

# Orchestrator guard: SDK-driven sessions (.cursor/orchestrator/) run their
# own post-session checks; a followup_message here would fight the
# orchestrator's loop. AI_ORCH=1 is exported only by the orchestrator's
# process tree — never set interactively.
if [ "${AI_ORCH:-}" = "1" ]; then exit 0; fi

# Project hooks run from the project root; cd defensively anyway so relative
# paths (.ai-tasks/*.md) resolve correctly.
cd "${CURSOR_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"

LOG="${HOME}/.cursor/ai-hooks.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') stop conv=${session_id:-?} $*" >> "$LOG" 2>/dev/null || true; }

block() {
  jq -n --arg msg "$1" '{followup_message: $msg}'
  exit 0
}

input=$(cat)
session_id=$(echo "$input" | jq -r '.conversation_id // empty')
transcript_path=$(echo "$input" | jq -r '.transcript_path // empty')
[ -z "$transcript_path" ] && transcript_path="${CURSOR_TRANSCRIPT_PATH:-}"

# Fail-open on missing inputs.
[ -z "$session_id" ] && { log "no-input conversation_id → allow"; exit 0; }
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
CLEAN_HOWTO="make the working tree clean (\`git status --porcelain\` empty) — each modified file committed, and each untracked file handled by its nature: real work committed; an unwanted scratch file removed; a run-time artifact covered by a gitignore rule for its category (not ignored file-by-file)."

# How the agent invokes /ai-sync-v2 under Cursor (Agent Skill, not slash command).
SYNC_SKILL="${HOME}/.claude/skills/ai-sync-v2/SKILL.md"

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
    block "Protocol violation: task '${task_file}' is status: completed but the working tree is not clean. Do NOT change status back to in_progress. Per §10 End, ${CLEAN_HOWTO} Then end."
  fi
  log "case1 task=$task_file status=completed wt=clean → block (invoke ai-sync-v2)"
  block "Task '${task_file}' has status: completed. Invoke /ai-sync-v2 now — read '${SYNC_SKILL}' and follow it — to apply absorption and archive the task before ending the session. Close-out includes the remaining-task reconciliation; your final response must include one line beginning \`Remaining-task audit:\` (required even when no remaining task changes)."
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
    block "Protocol violation: a session-log entry for this session exists in '${task_file}', but the working tree is not clean — this is a false handoff. The session-log is an end-of-session record (§10 End: clean tree first, then write the log). Resolve one of: (a) ${CLEAN_HOWTO} Then end. Or (b) if the entry was written mid-task by mistake, remove that premature session-log entry."
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
  block "Context has grown to approximately ${approx_tokens} tokens (over the ${THRESHOLD} budget for reliable work). Wrap up this session, in this order: (1) ${CLEAN_HOWTO} The session-log must not be written ahead of a clean tree. (2) Append a '## Session log' entry to '${task_file}' (Done / Plan-slice if applicable / Next / Open) describing what's been done and what the next session should pick up. ONLY if you are a remediation session (fixing a changes-requested review) whose fix set is not yet complete, include the line '- Handoff: continuation' so remediation continues before re-review; an advancement session never writes that marker — its landed work is reviewed next (protocol §10). If this was a dev advancement session using a '## Session plan', update only the current and future unimplemented slices so Next points to one-session-sized work; prefer adding a continuation slice like 'session-2-cont' over renumbering later slices. If this was a remediation session, do not run preReEst or advance planned scope. (3) Re-estimate the session cost and update the session-est total accordingly — wrapping up early means the original estimate was inaccurate. (4) Do not advance lifecycle status just because of this context wrap-up; keep status unchanged unless restoring protocol legality requires otherwise. The user will resume in a fresh session."
fi

# Below threshold without log: allow stop (session may be ending naturally early).
log "case2c task=$task_file status=$status tokens=$approx_tokens wt=$wt_clean ≤ $THRESHOLD → allow"
exit 0
