#!/usr/bin/env bash
# Optional: install a pre-push hook that runs the unified verification gate.
# Run from anywhere: scripts/install-git-hooks.sh
#
# The hook only calls scripts/check.sh — it never lists the individual checks,
# so a check added to the gate is enforced on push without touching this file.
# Skip a single push with `git push --no-verify`; uninstall by deleting
# .git/hooks/pre-push.
#
# An existing pre-push hook is replaced only when its contents are exactly what
# this installer writes. Calling the gate is not proof of ownership: a hook of
# your own may run scripts/check.sh alongside commands of its own, and
# rewriting it would delete them. Anything else is left untouched and reported.

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

HOOK_DIR=$(git rev-parse --git-path hooks)
HOOK="$HOOK_DIR/pre-push"

# The hook this installer owns, and the line that identifies it. Ownership is
# decided by full content equality with MANAGED; MARKER only sharpens the
# refusal message, telling "ours, since edited" apart from "yours".
MARKER='Installed by scripts/install-git-hooks.sh'
MANAGED=$(cat <<'HOOK_BODY'
#!/usr/bin/env bash
# Installed by scripts/install-git-hooks.sh. Runs the repository's one
# verification entrypoint; add new checks there, not here.
set -euo pipefail
exec "$(git rev-parse --show-toplevel)"/scripts/check.sh
HOOK_BODY
)

if [ -e "$HOOK" ] || [ -L "$HOOK" ]; then
  # Only a regular file can be compared; a symlink or directory is by
  # definition not ours and falls through to the refusal below.
  existing=
  if [ -f "$HOOK" ] && [ ! -L "$HOOK" ]; then
    existing=$(cat -- "$HOOK")
  fi

  if [ "$existing" = "$MANAGED" ]; then
    chmod +x "$HOOK"
    printf 'install-git-hooks: %s is already installed\n' "$HOOK"
    exit 0
  fi

  # Which advice to print — never whether to overwrite, which is settled above.
  printf 'install-git-hooks: %s exists and was not written by this installer.\n' "$HOOK" >&2
  printf 'Nothing was changed.\n' >&2
  if [ -n "$existing" ] && grep -qF "$MARKER" <<<"$existing"; then
    printf 'It carries the installer marker but its contents differ — edited since,\n' >&2
    printf 'or written by an earlier version.\n' >&2
  elif [ -n "$existing" ] && grep -qF 'scripts/check.sh' <<<"$existing"; then
    printf 'It already calls the gate, so the checks run on push either way; the\n' >&2
    printf 'rest of the file is yours and was left alone.\n' >&2
  else
    printf 'To run the gate on push, add this line to it:\n' >&2
    printf '  "$(git rev-parse --show-toplevel)"/scripts/check.sh\n' >&2
  fi
  printf 'To adopt the managed hook instead, move or delete that file and rerun.\n' >&2
  exit 1
fi

mkdir -p "$HOOK_DIR"
printf '%s\n' "$MANAGED" > "$HOOK"
chmod +x "$HOOK"

printf 'install-git-hooks: wrote %s\n' "$HOOK"
