#!/usr/bin/env bash
# Boundary lint — mechanical enforcement of the CHARTER.md litmus tests and
# the post-cut reference invariants (AUDIT-protocol-cut.md §9) over the
# canonical suite. Run from anywhere: scripts/boundary-lint.sh
#
# Checks:
#   1. required canonical layout files exist
#   2. no dead ai-coding-* doc references (legacy-transition mentions allowed:
#      lines carrying "legacy" or the /ai-coding*.md gitignore glob)
#   3. protocol purity: canonical/protocols/*.md carry no caller vocabulary
#      ("orchestrator", dispatch verbs, other-role session naming, hook
#      wiring, skill slash-invocations) and no `§` cross-references
#   3b. eager-channel purity (charter rule 12): the session-start EAGER_FILES
#      arrays and the CLAUDE.md import block carry no protocols/ contract
#      other than conduct.md
#   4. every `.ai-protocol/<path>.md` reference resolves to a canonical file
#   5. orchestrator prompt templates + postcheck contract validate
#      (prompts_error() startup check)

set -u
cd "$(cd "$(dirname "$0")/.." && pwd)"
fail=0
err() { printf 'boundary-lint: %s\n' "$*" >&2; fail=1; }

# --- 1. layout ---------------------------------------------------------------
for f in protocols/conduct.md protocols/dev-advancement.md \
         protocols/dev-remediation.md \
         protocols/review.md protocols/plan.md protocols/intake.md \
         meta/taskfile.md meta/memory.md meta/init.md \
         workflow/runbook.md workflow/rolemapping.md \
         workflow/skills/session-end.md workflow/skills/closeout.md \
         repo-root/CLAUDE.md; do
  [ -f "canonical/$f" ] || err "missing canonical/$f"
done
for gone in dev.md dev-base.md dev-add-advancement.md dev-add-remediation.md; do
  [ -f "canonical/protocols/$gone" ] && err "canonical/protocols/$gone must stay deleted (merged into per-mode dev contracts)"
done

# --- 2. dead doc names -------------------------------------------------------
dead=$(grep -rnI --exclude-dir=__pycache__ --exclude=boundary-lint.sh 'ai-coding-' \
         canonical ai_native_deployment tests scripts \
         skills-backup/ai-init skills-backup/ai-sync-v2 skills-backup/ai-load \
         skills-backup/ai-housekeeping skills-backup/intake-task 2>/dev/null \
       | grep -v 'HANDOFF-orch-hub.md' \
       | grep -v 'legacy' \
       | grep -v 'ai-coding\*\.md' || true)
if [ -n "$dead" ]; then
  err "dead ai-coding-* references:"
  printf '%s\n' "$dead" >&2
fi

# --- 3. protocol purity ------------------------------------------------------
purity() { # pattern, description
  hits=$(grep -rni "$1" canonical/protocols/*.md || true)
  if [ -n "$hits" ]; then
    err "protocols/ carry $2:"
    printf '%s\n' "$hits" >&2
  fi
}
purity 'orchestr'            '"orchestrator" (caller naming, charter litmus 1/2)'
purity '§'                   'section-symbol cross-references (post-cut invariant)'
purity 'dispatch'            'dispatch vocabulary (litmus 2)'
purity 'the next session[^-]' 'next-session prediction (litmus 2)'
purity 're-review waits\|hands\{0,1\} back\|hand the task back\|sends the task\|is reviewed next\|reviewed before the next' 'dispatch-predicting phrasing (litmus 2)'
purity 'stop.hook\|stophook' 'hook wiring (workflow-owned, charter rule 10)'
purity '/ai-sync-v2\|/intake-task\|/ai-init\b' 'skill slash-invocations (contracts reference contracts, not packagings)'
# other-role session naming inside a role contract (litmus 1)
for pair in "dev-advancement.md:review session" \
            "dev-remediation.md:review session" \
            "review.md:dev session" \
            "plan.md:dev session" "plan.md:review session" \
            "intake.md:dev session" "intake.md:review session"; do
  doc=${pair%%:*}; phrase=${pair#*:}
  hits=$(grep -ni "$phrase" "canonical/protocols/$doc" || true)
  if [ -n "$hits" ]; then
    err "protocols/$doc names another role ('$phrase', litmus 1):"
    printf '%s\n' "$hits" >&2
  fi
done

# --- 3b. eager-channel purity (charter rule 12) --------------------------------
# The ambient (eager) channel carries no role contract: the only protocols/
# file allowed in the session-start EAGER_FILES arrays (initial array + any
# += additions) and the CLAUDE.md @import block is conduct.md.
for hook in canonical/cursor/hooks/session-start.sh \
            canonical/codex/hooks/session-start.sh; do
  bad=$( { sed -n '/^EAGER_FILES=(/,/^)/p' "$hook"; grep 'EAGER_FILES+=' "$hook"; } \
         | grep 'protocols/' | grep -v 'protocols/conduct\.md' || true)
  if [ -n "$bad" ]; then
    err "$hook eager set carries a role contract (charter rule 12):"
    printf '%s\n' "$bad" >&2
  fi
done
bad=$(grep '^@' canonical/repo-root/CLAUDE.md \
      | grep 'protocols/' | grep -v 'protocols/conduct\.md' || true)
if [ -n "$bad" ]; then
  err "CLAUDE.md imports a role contract (charter rule 12):"
  printf '%s\n' "$bad" >&2
fi

# --- 4. .ai-protocol path references resolve ---------------------------------
refs=$(grep -rhoI --exclude-dir=__pycache__ '\.ai-protocol/[A-Za-z0-9/_-]*\.md' canonical skills-backup 2>/dev/null | sort -u)
for ref in $refs; do
  [ -f "canonical/${ref#.ai-protocol/}" ] || err "dangling reference: $ref"
done

# --- 5. prompt templates validate --------------------------------------------
if ! python3 - <<'EOF'
import sys
sys.path.insert(0, "canonical/orchestrator")
import orchestrator as o
err = o.prompts_error()
if err:
    print(err, file=sys.stderr)
    raise SystemExit(1)
EOF
then
  err "prompt template validation failed (prompts_error)"
fi

if [ "$fail" -eq 0 ]; then
  echo "boundary-lint: OK"
else
  exit 1
fi
