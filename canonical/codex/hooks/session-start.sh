#!/usr/bin/env bash
# Codex CLI SessionStart hook for the ai-coding-v2 workflow.
#
# Port of .cursor/hooks/session-start.sh. Same eager-load semantics; only the
# Codex I/O surface differs:
#   - stdin JSON: session_id (not conversation_id), source, cwd, model, ...
#   - output: {"hookSpecificOutput":{"hookEventName":"SessionStart",
#              "additionalContext":"..."}}  (plain stdout would also work, but
#              JSON is explicit and future-proof).
#
# Isolation: this script self-gates on the ai-coding protocol marker
# (ai-coding-v2.md at the git root). It is a silent no-op in any repo that is
# not an ai-coding-v2 project, so it stays safe even if it is ever promoted
# from project scope (<repo>/.codex/) to user scope (~/.codex/hooks.json) — the
# documented fallback when project-scope hooks do not fire in the interactive
# TUI (see .codex/README.md).
#
# Injected via additionalContext:
#   1. Session ID line (used for claimed-by / session-log headings).
#   2. Codex adaptations preamble (mirrors .cursor/rules/protocol.mdc).
#   3. Protocol files: ai-coding-v2.md, ai-coding-memory-v2.md,
#      ai-coding-tasks-v2.md.
#   4. Eager memory set: .ai/index.md .ai/map.md .ai/overview.md
#      .ai/architecture.md .ai/design/index.md .ai/conventions/index.md
#      .ai-tasks/index.md.
#   5. Housekeeping reminder when .ai/.housekeeping-pending exists.
#
# Fail-open: missing files are skipped; jq failure exits 0 with no output.

set -euo pipefail

# Codex runs hooks with the session cwd, which may be a subdirectory. Resolve
# the git root so relative protocol/.ai paths always resolve.
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$root" 2>/dev/null || exit 0

LOG="${HOME}/.codex/ai-hooks.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') session-start sid=${session_id:-?} $*" >> "$LOG" 2>/dev/null || true; }

input=$(cat 2>/dev/null || true)
session_id=$(echo "$input" | jq -r '.session_id // empty' 2>/dev/null || true)
source=$(echo "$input" | jq -r '.source // empty' 2>/dev/null || true)

# Self-gate: only act in an ai-coding-v2 project.
if [ ! -f "ai-coding-v2.md" ]; then
  log "no-protocol-marker root=$root → skip"
  exit 0
fi

EAGER_FILES=(
  ai-coding-v2.md
  ai-coding-memory-v2.md
  ai-coding-tasks-v2.md
  .ai/index.md
  .ai/map.md
  .ai/overview.md
  .ai/architecture.md
  .ai/design/index.md
  .ai/conventions/index.md
  .ai-tasks/index.md
)

ctx="PROJECT PROTOCOL CONTEXT (ai-coding-v2) — injected by the Codex SessionStart hook.

Session ID for this conversation: ${session_id:-unknown}
Use this ID wherever the protocol calls for \$CLAUDE_CODE_SESSION_ID (the
\`claimed-by\` frontmatter field and \`## Session log\` entry headings).

== Codex adaptations (override wording inside the protocol files) ==
- Session ID: use the ID above wherever the protocol says
  \$CLAUDE_CODE_SESSION_ID; that env var is NOT set under Codex.
- \`@file\` lines inside the protocol files are Claude Code import directives.
  The referenced files are already included below; ignore the \`@file\` lines.
- Skill / slash-command invocations (\`/ai-sync-v2\`, \`/intake-task\`,
  \`/ai-init\`, \`/ai-housekeeping\`, \`/ctd-tasks\`) map to skills: read
  \`~/.claude/skills/<name>/SKILL.md\` and follow it. (Codex can also load
  these as native skills; see .codex/config.toml.)

== Cross-model review (verb = role, independent of model) ==
- \`task <id>\`  → dev role: develop or continue the task per §10.
- \`review <id>\` → review role: evaluate per §6; read and follow
  \`${root}/.codex/review-workflow.md\`.
- Status vocabulary extends the base enum with \`final_review\`:
  - A dev session sets only \`in_progress\` or \`final_review\`; never
    \`completed\`.
  - \`final_review\` = dev-complete, awaiting review. It does NOT trigger
    ai-sync-v2: the Stop hook treats every non-\`completed\` status as
    in-flight and still enforces the §10 End discipline (clean tree,
    session-log entry).
  - Only a review session sets \`completed\`, the sole trigger for the
    ai-sync-v2 close-out.

The protocol files and the eager memory set follow. Treat them as binding rules.
"

loaded=0
for f in "${EAGER_FILES[@]}"; do
  if [ -f "$f" ]; then
    ctx+=$'\n'"===== BEGIN ${f} ====="$'\n'
    ctx+="$(cat "$f")"
    ctx+=$'\n'"===== END ${f} ====="$'\n'
    loaded=$((loaded + 1))
  fi
done

# Housekeeping hint.
FLAG=".ai/.housekeeping-pending"
hk="absent"
if [ -f "$FLAG" ]; then
  hk="present"
  count=$(wc -l < "$FLAG" 2>/dev/null | tr -d ' ')
  issues=$(cat "$FLAG" 2>/dev/null)
  ctx+="
HOUSEKEEPING REMINDER: .ai/.housekeeping-pending exists with ${count} line(s).

Details:
${issues}

INSTRUCTION TO ASSISTANT: Before responding to the user's first request this session, prepend a one-line notice exactly: '⚠️  .ai/ housekeeping pending — run /ai-housekeeping when convenient.' Do not block the user's task; surface the reminder once and then proceed normally.
"
fi

log "source=${source:-?} files=${loaded}/${#EAGER_FILES[@]} housekeeping=${hk} → inject"

jq -n --arg ctx "$ctx" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}' \
  2>/dev/null || true
exit 0
