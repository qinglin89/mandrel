#!/usr/bin/env bash
# Cursor sessionStart hook for the ai-protocol workflow.
#
# Replicates Claude Code's eager-load semantics (CLAUDE.md @imports) plus the
# SessionStart housekeeping hint, and additionally injects this conversation's
# session ID (Cursor has no $CLAUDE_CODE_SESSION_ID equivalent in the agent
# shell, so the ID must be handed to the model here).
#
# Injected via additional_context:
#   1. Session ID line (used for claimed-by / session-log headings).
#   2. The loader (CLAUDE.md, carries the verb→contract mapping) and the
#      eager protocol substrate: .ai-protocol/protocols/conduct.md,
#      .ai-protocol/meta/taskfile.md, .ai-protocol/meta/memory.md.
#      Role contracts (protocols/dev-*, review, plan) are NOT eager — the
#      caller delivers them at invocation.
#   3. Eager memory set: .ai/index.md .ai/map.md, the current
#      overview/architecture/design/conventions entrypoints (resolved from
#      .ai/index.md routing; .md vs /index.md fallback), .ai-tasks/index.md.
#   4. Housekeeping reminder when .ai/.housekeeping-pending exists.
#
# Fail-open: missing files are skipped; jq failure exits 0 with no output.

set -euo pipefail

# Orchestrator guard: SDK-driven sessions (.cursor/orchestrator/) inject the
# protocol context themselves and own the lifecycle. AI_ORCH=1 is exported
# only by the orchestrator's process tree — never set interactively.
if [ "${AI_ORCH:-}" = "1" ]; then exit 0; fi

# Project hooks run from the project root; cd defensively anyway.
cd "${CURSOR_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"

LOG="${HOME}/.cursor/ai-hooks.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') session-start conv=${conversation_id:-?} $*" >> "$LOG" 2>/dev/null || true; }

input=$(cat)
conversation_id=$(echo "$input" | jq -r '.conversation_id // empty' 2>/dev/null || true)

EAGER_FILES=(
  CLAUDE.md
  .ai-protocol/protocols/conduct.md
  .ai-protocol/meta/taskfile.md
  .ai-protocol/meta/memory.md
  .ai/index.md
  .ai/map.md
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

# Every eager content doc the memory protocol allows to be upgraded to
# directory form. Kept in lockstep with CLAUDE_MD_MEMORY_IMPORT_TOPICS in
# mandrel/deploy.py (tests/test_hook_eager_set.py).
add_eager_entrypoint "Overview" ".ai/overview.md" ".ai/overview/index.md"
add_eager_entrypoint "Architecture" ".ai/architecture.md" ".ai/architecture/index.md"
add_eager_entrypoint "Design" ".ai/design.md" ".ai/design/index.md"
add_eager_entrypoint "Conventions" ".ai/conventions.md" ".ai/conventions/index.md"
EAGER_FILES+=(.ai-tasks/index.md)

ctx="PROJECT PROTOCOL CONTEXT (ai-protocol) — injected by the sessionStart hook.

Session ID for this conversation: ${conversation_id:-unknown}
Use this ID wherever the protocol calls for \$CLAUDE_CODE_SESSION_ID (the
\`claimed-by\` frontmatter field and \`## Session log\` entry headings).

The protocol files and the eager memory set follow. Treat them as binding
rules. \`@file\` lines inside them are Claude Code import directives — the
referenced files are already included below; ignore the \`@file\` lines.
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

# Housekeeping hint (ported from session-start-housekeeping-check.sh).
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

log "files=${loaded}/${#EAGER_FILES[@]} housekeeping=${hk} → inject"

jq -n --arg ctx "$ctx" '{additional_context: $ctx}' 2>/dev/null || true
exit 0
