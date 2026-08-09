#!/usr/bin/env bash
# ctd-tasks scan: enumerate .ai-tasks/*.md frontmatter and print a concise
# overview grouped by lifecycle status (taskfile schema: status, session-est,
# blockers). "Active" = every in-flight status: pending / in_progress /
# final_review / blocked. Completed tasks leave the active directory for
# .ai-tasks/archive/ at close-out, so they only appear under --all or
# --status archived.
#
# Usage:
#   ctd-tasks/scan.sh                     # active only (default)
#   ctd-tasks/scan.sh --all               # active + archived
#   ctd-tasks/scan.sh --status blocked    # filter to one group
#
# Exit: 0, except 1 when --status is given a missing or unknown value.
# Output is plain text, one task per line:
#   "  status | file | session-est | title".

set -eo pipefail   # note: no -u; macOS bash 3.2 fights empty-array expansion

MODE="active"
FILTER=""
for arg in "$@"; do
  case "$arg" in
    --all)        MODE="all" ;;
    --active)     MODE="active" ;;
    --status)     MODE="filter" ;;
    --status=*)   MODE="filter"; FILTER="${arg#*=}" ;;
    *)            if [ "$MODE" = "filter" ] && [ -z "$FILTER" ]; then FILTER="$arg"; fi ;;
  esac
done

TASKS_DIR=".ai-tasks"
ARCHIVE_DIR="$TASKS_DIR/archive"
if [ ! -d "$TASKS_DIR" ]; then
  echo "ctd-tasks: no $TASKS_DIR in $(pwd)" >&2
  exit 0
fi

# Use newline-delimited strings instead of arrays so empty groups are trivial.
pending=""
in_progress=""
final_review=""
blocked=""
completed=""
other=""
archived=""

# One awk pass per task file: the three frontmatter fields worth showing plus
# the body's H1 (task files carry no `summary:` field — the title is it).
# Fields are US-separated, not tab-separated: tab is IFS whitespace, so `read`
# would collapse the run of two delimiters a missing key produces and shift
# every later value one column left.
SEP=$'\037'

parse_task() { # <file> -> "status<US>session-est<US>blockers<US>title"
  awk '
    function val(line,   v) {
      v = line
      sub(/^[^:]*:[[:space:]]*/, "", v)
      sub(/[[:space:]]+$/, "", v)
      return v
    }
    /^---$/ { n++; next }
    n == 1 {
      if (index($0, "status:") == 1)           { status = val($0) }
      else if (index($0, "session-est:") == 1) { est = val($0) }
      else if (index($0, "blockers:") == 1)    { blockers = val($0) }
      next
    }
    n >= 2 && title == "" && /^# / { title = $0; sub(/^#[[:space:]]+/, "", title) }
    END { printf "%s\037%s\037%s\037%s\n", status, est, blockers, title }
  ' "$1"
}

collect() { # <dir> <as-archived: yes|no>
  local dir=$1 as_archived=$2
  local f base status est blockers title line
  for f in "$dir"/*.md; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    if [ "$base" = "index.md" ]; then
      continue
    fi

    status=""; est=""; blockers=""; title=""
    IFS="$SEP" read -r status est blockers title < <(parse_task "$f") || true

    status="${status:-(missing)}"
    est="${est:-?}"
    title="${title:-(no title)}"
    case "$blockers" in
      "["*"]") blockers="${blockers#\[}"; blockers="${blockers%\]}" ;;
    esac

    line=$(printf "  %-14s | %-46s | %-5s | %s" "$status" "$base" "$est" "$title")
    if [ -n "$blockers" ]; then
      line="$line  (blockers: $blockers)"
    fi

    if [ "$as_archived" = "yes" ]; then
      archived="${archived}${line}"$'\n'
      continue
    fi

    case "$status" in
      pending)       pending="${pending}${line}"$'\n' ;;
      in_progress)   in_progress="${in_progress}${line}"$'\n' ;;
      final_review)  final_review="${final_review}${line}"$'\n' ;;
      blocked)       blocked="${blocked}${line}"$'\n' ;;
      completed)     completed="${completed}${line}"$'\n' ;;
      *)             other="${other}${line}"$'\n' ;;
    esac
  done
}

count_lines() {
  [ -z "$1" ] && { echo 0; return; }
  printf "%s" "$1" | grep -c '^'
}

count_archived_files() {
  local n=0 f
  [ -d "$ARCHIVE_DIR" ] || { echo 0; return; }
  for f in "$ARCHIVE_DIR"/*.md; do
    [ -f "$f" ] || continue
    n=$((n + 1))
  done
  echo "$n"
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

print_active_groups() {
  print_group "📋 Pending"                "$pending"
  print_group "🟢 In progress"            "$in_progress"
  print_group "🔎 Final review"           "$final_review"
  print_group "⛔ Blocked"                "$blocked"
  print_group "⚠  Completed (unarchived)" "$completed"
  print_group "⚠  Other / no status"      "$other"
}

collect "$TASKS_DIR" no

case "$MODE" in
  active)
    print_active_groups
    echo
    total=$(( $(count_lines "$pending") + $(count_lines "$in_progress") \
            + $(count_lines "$final_review") + $(count_lines "$blocked") \
            + $(count_lines "$other") ))
    echo "active total: $total  ($(count_archived_files) archived; use --all to include them)"
    ;;
  all)
    collect "$ARCHIVE_DIR" yes
    print_active_groups
    print_group "✅ Archived" "$archived"
    ;;
  filter)
    [ -z "$FILTER" ] && { echo "--status needs a value" >&2; exit 1; }
    case "$FILTER" in
      pending)       print_group "📋 Pending"                "$pending" ;;
      in_progress)   print_group "🟢 In progress"            "$in_progress" ;;
      final_review)  print_group "🔎 Final review"           "$final_review" ;;
      blocked)       print_group "⛔ Blocked"                "$blocked" ;;
      completed)     print_group "⚠  Completed (unarchived)" "$completed" ;;
      other)         print_group "⚠  Other / no status"      "$other" ;;
      archived)      collect "$ARCHIVE_DIR" yes
                     print_group "✅ Archived" "$archived" ;;
      *)             echo "unknown status: $FILTER" >&2; exit 1 ;;
    esac
    ;;
esac
