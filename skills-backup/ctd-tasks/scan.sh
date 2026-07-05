#!/usr/bin/env bash
# ctd-tasks scan: enumerate .ai/tasks/*.md frontmatter and print a concise
# status overview grouped by state. "Pending" = anything not done / obsolete /
# superseded.
#
# Usage:
#   ctd-tasks/scan.sh               # pending only (default)
#   ctd-tasks/scan.sh --all         # every entry
#   ctd-tasks/scan.sh --status done # filter by status
#
# Exit: 0 always. Output is plain text (one task per line, "  status | file | summary").

set -eo pipefail   # note: no -u; macOS bash 3.2 fights empty-array expansion

MODE="pending"
FILTER=""
for arg in "$@"; do
  case "$arg" in
    --all)        MODE="all" ;;
    --pending)    MODE="pending" ;;
    --status)     MODE="filter" ;;
    --status=*)   MODE="filter"; FILTER="${arg#*=}" ;;
    *)            if [ "$MODE" = "filter" ] && [ -z "$FILTER" ]; then FILTER="$arg"; fi ;;
  esac
done

TASKS_DIR=".ai/tasks"
if [ ! -d "$TASKS_DIR" ]; then
  echo "ctd-tasks: no $TASKS_DIR in $(pwd)" >&2
  exit 0
fi

# Use newline-delimited strings instead of arrays so empty groups are trivial.
active=""
handoff=""
proposal=""
deferred=""
done_entries=""
superseded=""
other=""

for f in "$TASKS_DIR"/*.md; do
  base=$(basename "$f")
  [ "$base" = "INDEX.md" ] && continue
  [ -f "$f" ] || continue

  status=$(awk '/^---$/{n++; next} n==1 && /^status:/ {sub(/^status:[[:space:]]*/,""); print; exit}' "$f")
  summary=$(awk '/^---$/{n++; next} n==1 && /^summary:/ {sub(/^summary:[[:space:]]*"?/,""); sub(/"$/,""); print; exit}' "$f")
  revisit=$(awk '/^---$/{n++; next} n==1 && /^revisit:/ {sub(/^revisit:[[:space:]]*/,""); print; exit}' "$f")

  status="${status:-(missing)}"
  summary="${summary:-(no summary)}"

  line=$(printf "  %-14s | %-55s | %s" "$status" "$base" "$summary")
  [ -n "$revisit" ] && line="$line  (revisit: $revisit)"

  case "$status" in
    active)           active="${active}${line}"$'\n' ;;
    handoff)          handoff="${handoff}${line}"$'\n' ;;
    proposal)         proposal="${proposal}${line}"$'\n' ;;
    deferred)         deferred="${deferred}${line}"$'\n' ;;
    done)             done_entries="${done_entries}${line}"$'\n' ;;
    superseded-by:*)  superseded="${superseded}${line}"$'\n' ;;
    *)                other="${other}${line}"$'\n' ;;
  esac
done

count_lines() {
  [ -z "$1" ] && { echo 0; return; }
  printf "%s" "$1" | grep -c '^'
}

print_group() {
  local title=$1
  local body=$2
  [ -z "$body" ] && return
  local n
  n=$(count_lines "$body")
  printf "\n%s  (%d)\n" "$title" "$n"
  printf "%s" "$body"
}

case "$MODE" in
  pending)
    print_group "🟢 Active"             "$active"
    print_group "⏸  Handoff"             "$handoff"
    print_group "📝 Proposal"           "$proposal"
    print_group "🟠 Deferred"           "$deferred"
    print_group "⚠  Other / no-status"   "$other"
    echo
    total=$(( $(count_lines "$active") + $(count_lines "$handoff") + $(count_lines "$proposal") + $(count_lines "$deferred") + $(count_lines "$other") ))
    echo "pending total: $total  (use --all to include done/superseded)"
    ;;
  all)
    print_group "🟢 Active"       "$active"
    print_group "⏸  Handoff"       "$handoff"
    print_group "📝 Proposal"     "$proposal"
    print_group "🟠 Deferred"     "$deferred"
    print_group "✅ Done"          "$done_entries"
    print_group "🔗 Superseded"   "$superseded"
    print_group "⚠  Other"         "$other"
    ;;
  filter)
    [ -z "$FILTER" ] && { echo "--status needs a value" >&2; exit 1; }
    case "$FILTER" in
      active)     print_group "🟢 Active"       "$active" ;;
      handoff)    print_group "⏸  Handoff"       "$handoff" ;;
      proposal)   print_group "📝 Proposal"     "$proposal" ;;
      deferred)   print_group "🟠 Deferred"     "$deferred" ;;
      done)       print_group "✅ Done"          "$done_entries" ;;
      superseded) print_group "🔗 Superseded"   "$superseded" ;;
      *)          echo "unknown status: $FILTER" >&2; exit 1 ;;
    esac
    ;;
esac
