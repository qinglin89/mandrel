# Post-session check contract

Single source for the end-of-session discipline the orchestrator enforces and a
human standing in for it verifies on return. Each `##` heading is a check-id; the
body below it is that check's requirement line, templated with `{{var}}` values
instantiated per session. orchestrator.py binds every id to a verification
callable (`check_specs`) and validates the mapping 1:1 in both directions at
startup; the same lines render into each session prompt as its POST-SESSION
CHECKS preview, so what the agent is told and what gets verified cannot drift.

## tree-clean

working tree clean (`git status --porcelain` empty)

## session-log-entry

a `## Session log` entry for session id {{sid_disp}} (Done / Plan-slice if applicable / Next / Open)

## dev-remediation-status

status: keep `{{status_before}}` UNCHANGED — a remediation session never touches status (taskfile transition table; re-review is triggered by your session-log entry; `blocked` only for a genuine human question)

## dev-advancement-status

status per the taskfile transition table (dev advancement): `in_progress` (work remains) | `final_review` (ONLY when the whole scope is complete) | `blocked` (genuine human question) — never `completed`

## dev-no-continuation-marker

no `- Handoff: continuation` line in your entry (the marker is remediation-only — dev contract; advancement work is reviewed after every session)

## dev-est-increment

session-est incremented at claim: {{cur}}/{{tot}} → {{nxt}}/{{ntot}}{{undershoot}}

## review-status-final-gate

status per the taskfile transition table (FINAL GATE — entered at `final_review`): `completed` (pass — the sole ai-sync trigger) | `final_review` (changes required — your entry itself hands back to dev remediation) | `in_progress` (ONLY if final_review was set in error — verify apparent dev-completeness at entry, record why) | `blocked`

## review-status-interim

status per the taskfile transition table (interim review — entered at `{{status_before}}`): keep `in_progress` (findings never gate an interim review) | `blocked`

## review-entry-fields

the review entry carries `Verdict:` (pass | changes-requested) and `Group:` (convergence anchor, review contract)
