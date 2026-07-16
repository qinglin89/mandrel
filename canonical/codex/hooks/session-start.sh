#!/usr/bin/env bash
# Codex CLI SessionStart hook for the ai-protocol workflow.
#
# Port of .cursor/hooks/session-start.sh. Same eager-load semantics; only the
# Codex I/O surface differs:
#   - stdin JSON: session_id (not conversation_id), source, cwd, model, ...
#   - output: {"hookSpecificOutput":{"hookEventName":"SessionStart",
#              "additionalContext":"..."}}  (plain stdout would also work, but
#              JSON is explicit and future-proof).
#
# Isolation: this script self-gates on the deployed protocol marker
# (.ai-protocol/protocols/conduct.md at the git root). It is a silent no-op in
# any repo that is not an ai-protocol project, so it stays safe even if it is
# ever promoted from project scope (<repo>/.codex/) to user scope
# (~/.codex/hooks.json) — the documented fallback when project-scope hooks do
# not fire in the interactive TUI (see .codex/README.md).
#
# Injected via additionalContext:
#   1. Session ID line (used for claimed-by / session-log headings).
#   2. Codex adaptations preamble (mirrors .cursor/rules/protocol.mdc).
#   3. The loader (CLAUDE.md, carries the verb→contract mapping) and the
#      eager protocol set: .ai-protocol/protocols/conduct.md,
#      .ai-protocol/protocols/dev.md, .ai-protocol/meta/taskfile.md,
#      .ai-protocol/meta/memory.md.
#   4. Eager memory set: .ai/index.md .ai/map.md .ai/overview.md
#      .ai/architecture.md current design/conventions entrypoints
#      (resolved from .ai/index.md routing; .md vs /index.md fallback)
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

# Self-gate: only act in an ai-protocol project.
if [ ! -f ".ai-protocol/protocols/conduct.md" ]; then
  log "no-protocol-marker root=$root → skip"
  exit 0
fi

EAGER_FILES=(
  CLAUDE.md
  .ai-protocol/protocols/conduct.md
  .ai-protocol/protocols/dev.md
  .ai-protocol/meta/taskfile.md
  .ai-protocol/meta/memory.md
  .ai/index.md
  .ai/map.md
  .ai/overview.md
  .ai/architecture.md
)

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

add_eager_entrypoint() {
  local label="$1"
  local flat="$2"
  local dir_index="$3"
  local routed=""

  routed="$(resolve_ai_router_path "$label" || true)"
  if [ -n "$routed" ] && [ -f "$routed" ]; then
    EAGER_FILES+=("$routed")
  elif [ -f "$dir_index" ] && [ ! -f "$flat" ]; then
    EAGER_FILES+=("$dir_index")
  else
    EAGER_FILES+=("$flat")
  fi
}

add_eager_entrypoint "Design" ".ai/design.md" ".ai/design/index.md"
add_eager_entrypoint "Conventions" ".ai/conventions.md" ".ai/conventions/index.md"
EAGER_FILES+=(.ai-tasks/index.md)

ctx="PROJECT PROTOCOL CONTEXT (ai-protocol) — injected by the Codex SessionStart hook.

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
- The verb→contract mapping is in the loader (CLAUDE.md, included below).
  For \`review <id>\` sessions the review contract is also reachable via the
  pointer file \`${root}/.codex/review-workflow.md\`.

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
