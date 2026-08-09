#!/usr/bin/env bash
# Unified verification gate — the single entrypoint developers, agents, Git
# hooks, and CI all use to run this repository's deterministic checks.
# Run from anywhere: scripts/check.sh
#
# Contract:
#   * every check runs even after an earlier one fails; the summary lists each
#     result and the exit status is nonzero when any check failed
#   * a missing tool is a hard failure, never a silent skip (see preflight)
#   * the run leaves the working tree byte-identical (tree-unchanged check)
#
# Adding a check: write a `check_*` function and append one `run_check` line to
# the CHECKS section. CI (.github/workflows/ci.yml) and the optional Git hooks
# (scripts/install-git-hooks.sh) invoke this script and never re-list its
# steps, so one line here reaches every caller.
# tests/test_verification_gate.py asserts that the suites living outside pytest
# stay wired in.
#
# Deliberately excluded: canonical/orchestrator/smoke_hooks.py needs a live
# Cursor API key and network, so it cannot gate a merge. It runs from the
# manual .github/workflows/smoke.yml workflow instead.

set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd) || exit 2
cd "$ROOT" || exit 2

# --- preflight: resolve tools, or fail closed with one actionable message ----

PYTHON=${AII_PYTHON:-}
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON=$(command -v python3 || true)
  fi
fi

missing=()
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
  printf 'check: no Python interpreter found. Create one with:\n' >&2
  printf '  python3 -m venv .venv\n' >&2
  printf 'or point AII_PYTHON at an existing interpreter.\n' >&2
  exit 2
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  missing+=("Python >= 3.11 (AII_PYTHON=$PYTHON is $("$PYTHON" -V 2>&1))")
fi
for module in pytest ruff build setuptools wheel; do
  "$PYTHON" -c "import $module" >/dev/null 2>&1 || missing+=("Python module: $module")
done

# The shellcheck-py package installs the binary beside the interpreter; a system
# install is an equally good source, so prefer the venv copy, then fall back to
# PATH.
SHELLCHECK="$(dirname "$PYTHON")/shellcheck"
[ -x "$SHELLCHECK" ] || SHELLCHECK=$(command -v shellcheck || true)
[ -n "$SHELLCHECK" ] || missing+=("shellcheck (pip package shellcheck-py, or a system install)")

command -v git >/dev/null 2>&1 || missing+=("git")
if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  missing+=("a Git work tree at $ROOT (the file-scanning checks use git ls-files)")
fi

if [ ${#missing[@]} -gt 0 ]; then
  printf 'check: cannot run the verification gate. Missing:\n' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  printf '\nInstall everything the gate needs with:\n' >&2
  printf "  %s -m pip install -e '.[dev]'\n" "$PYTHON" >&2
  exit 2
fi

printf 'check: repository %s\n' "$ROOT"
printf 'check: interpreter %s (%s)\n' "$PYTHON" "$("$PYTHON" -V 2>&1)"
printf 'check: shellcheck  %s\n' "$SHELLCHECK"

TREE_BEFORE=$(git status --porcelain)

# --- check implementations ---------------------------------------------------

# Tracked text files carry no trailing whitespace and no CR line endings, and
# every one of them ends in a newline.
#
# canonical/orchestrator/prompts/ is excluded on purpose: those fragments are
# concatenated byte-for-byte into agent prompts and several end in a
# significant space — see _read_template in canonical/orchestrator/orchestrator.py.
WHITESPACE_EXCLUDE=(':!canonical/orchestrator/prompts')

check_whitespace() {
  local rc=0 hits file
  hits=$(git grep -nIE $'[ \t]+$' -- "${WHITESPACE_EXCLUDE[@]}" || true)
  if [ -n "$hits" ]; then
    printf 'trailing whitespace:\n%s\n' "$hits" >&2
    rc=1
  fi
  hits=$(git grep -nI $'\r$' || true)
  if [ -n "$hits" ]; then
    printf 'CR line endings:\n%s\n' "$hits" >&2
    rc=1
  fi
  while IFS= read -r file; do
    [ -s "$file" ] || continue
    if [ -n "$(tail -c 1 -- "$file")" ]; then
      printf 'no newline at end of file: %s\n' "$file" >&2
      rc=1
    fi
  done < <(git grep -I -l -e '' -- .)
  return $rc
}

check_ruff() {
  "$PYTHON" -m ruff check --no-cache .
}

# Every tracked shell script, found by extension or shebang so a new one is
# covered without editing this list.
shell_files() {
  local file first interp
  while IFS= read -r file; do
    case "$file" in
      *.sh) printf '%s\n' "$file"; continue ;;
      *.md|*.py|*.json|*.jsonl|*.toml|*.txt|*.yml|*.yaml|*.cfg|*.ini) continue ;;
    esac
    first=$(head -n 1 -- "$file" 2>/dev/null)
    case "$first" in
      '#!'*) ;;
      *) continue ;;
    esac
    # First shebang token that is not `env`, basename only: `#!/usr/bin/env sh`
    # and `#!/bin/bash` both reduce to the interpreter name.
    interp=$(printf '%s\n' "${first#\#!}" \
             | awk '{for (i = 1; i <= NF; i++) { t = $i; sub(/.*\//, "", t); if (t != "env") { print t; exit } } }')
    case "$interp" in
      sh|bash|dash|ksh) printf '%s\n' "$file" ;;
    esac
  done < <(git ls-files)
}

check_shellcheck() {
  local files
  files=$(shell_files)
  if [ -z "$files" ]; then
    printf 'no shell scripts found — the discovery in shell_files is broken\n' >&2
    return 1
  fi
  printf '%s\n' "$files" | sed 's/^/  /'
  # shellcheck disable=SC2086  # tracked paths, no spaces; deliberate word split
  "$SHELLCHECK" --severity=warning --external-sources $files
}

check_boundary_lint() {
  AII_PYTHON="$PYTHON" scripts/boundary-lint.sh
}

check_pytest() {
  "$PYTHON" -m pytest -q
}

# The orchestrator's mock-loop scenarios live outside `testpaths`, so pytest
# never collects them: this line is the only thing that runs them.
check_orchestrator_mock_loop() {
  "$PYTHON" canonical/orchestrator/test_loop_mock.py
}

# The wheel builds, installs offline into a throwaway venv, and its console
# script starts. Everything lands in a temp directory; the setuptools scratch
# `build/` is removed again unless it was already there.
check_package_build() {
  local tmp rc=0 had_build=0
  [ -d "$ROOT/build" ] && had_build=1
  tmp=$(mktemp -d) || return 1
  if "$PYTHON" -m build --wheel --no-isolation --outdir "$tmp/dist" . \
    && "$PYTHON" -m venv "$tmp/venv" \
    && "$tmp/venv/bin/python" -m pip install --quiet --no-index --no-deps "$tmp"/dist/*.whl \
    && "$tmp/venv/bin/aii-2" --help >/dev/null; then
    rc=0
  else
    rc=1
  fi
  rm -rf "$tmp"
  if [ "$had_build" -eq 0 ]; then
    rm -rf "$ROOT/build"
  fi
  return $rc
}

check_tree_unchanged() {
  local after
  after=$(git status --porcelain)
  if [ "$after" = "$TREE_BEFORE" ]; then
    return 0
  fi
  printf 'the verification run changed the working tree.\n' >&2
  printf -- '--- before\n%s\n--- after\n%s\n' "$TREE_BEFORE" "$after" >&2
  return 1
}

# --- runner ------------------------------------------------------------------

PASSED=()
FAILED=()

run_check() { # <id> <description> <command...>
  local id=$1 desc=$2
  shift 2
  printf '\n===== %s: %s\n' "$id" "$desc"
  if "$@"; then
    printf '===== %s: OK\n' "$id"
    PASSED+=("$id")
  else
    printf '===== %s: FAILED\n' "$id" >&2
    FAILED+=("$id")
  fi
}

# --- CHECKS ------------------------------------------------------------------

run_check whitespace              "tracked text file hygiene"            check_whitespace
run_check ruff                    "Python lint"                          check_ruff
run_check shellcheck              "shell lint"                           check_shellcheck
run_check boundary-lint           "canonical protocol boundaries"        check_boundary_lint
run_check pytest                  "deployment/hook/config test suite"    check_pytest
run_check orchestrator-mock-loop  "orchestrator loop scenarios"          check_orchestrator_mock_loop
run_check package-build           "wheel build, offline install, CLI"    check_package_build
run_check tree-unchanged          "the gate mutated no tracked file"     check_tree_unchanged

# --- summary -----------------------------------------------------------------

printf '\n===== summary\n'
if [ ${#PASSED[@]} -gt 0 ]; then
  printf '  PASS  %s\n' "${PASSED[@]}"
fi
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\ncheck: OK (%d checks)\n' "${#PASSED[@]}"
  exit 0
fi
printf '  FAIL  %s\n' "${FAILED[@]}" >&2
printf '\ncheck: %d of %d checks failed\n' \
  "${#FAILED[@]}" "$((${#FAILED[@]} + ${#PASSED[@]}))" >&2
exit 1
