---
name: split-task
description: Transfer an in-progress task's remaining work into one independent pending task after a review pass at its dev-session limit, then close out the source administratively. Also resumes recorded Split transfers.
---

This is an administrative workflow, not a dev or review role. Use the taskfile
schema (`.ai-protocol/meta/taskfile.md`), intake contract
(`.ai-protocol/protocols/intake.md`), and existing closeout skill
(`.claude/skills/ai-sync-v2/SKILL.md`). No feature implementation, remediation,
or invented review verdict belongs in this session.

## Invocation and inputs

Invoke `/split-task <source-task-id>` after the caller reserves
`## Split` with `- Phase: preparing`, or to resume that transfer. The caller
uses its dev agent configuration without injecting a dev/review contract.
An authorized manual invocation follows the same procedure and reservation.

Read the source's full task file (its archive copy for recovery), active task
index, relevant code/commit evidence, and memory routing. The task file, not a
previous conversation, supplies all durable state. Claim only an active source
by refreshing `claimed-by`; leave archived source history intact on recovery.
Do not increment `session-est`, change `session-limit`, or invoke a plan gate.

Before reserving a new transfer, the caller requires `status: in_progress`,
the completed review's latest `Verdict: pass`, all landed work reviewed, no
`fix-set: open`, and current at least `session-limit`. An authorized manual
invocation without a reservation checks these conditions before reserving.
An existing reservation represents prior admission: recover it without
reapplying the new gate, including legacy `final_review`/failed-review cases.
Failed reviews keep normal remediation and group-budget escalation. Completed
tasks without a Split take ordinary closeout; recorded transfers still recover
and verify. `final_review` never admits a new transfer.

Before creating a successor, require a clean tree and inventory actual remaining
scope, acceptance, and the whole findings ledger. Status advancement, final
review, and closeout alone are not work to transfer; Plan-slice is not a gate.
If no actual remainder exists despite `in_progress`, report the inconsistent
lifecycle through the existing blocked mechanism. Preserve the reservation,
create no empty successor, and do not fabricate `completed` to resolve it.

Do not clear a genuine `blocked` state or settle an unresolved human dispute
by transferring it. Report the decision needed; the caller obtains the human
ruling and resumes this same split conversation. Preserve the claim so it can
do so. Where blocking is necessary, use the task schema's `blocked` transition
and a real blocker, preserving the Split reservation and any target already
created. Record split bookkeeping with `Kind: split` even when interrupted.

## Transfer procedure

1. **Inventory the remainder.** Compare Scope, Acceptance, Session plan,
   every work entry's Next/Open, and the whole findings ledger against landed
   code. Separate delivered work from unimplemented scope and every unresolved
   finding, including correctness issues, fix regressions, and valid deferred
   design/test work. Preserve severity, evidence, prior resolutions and human
   rulings; transfer is never proof that a finding was fixed or accepted.

2. **Reserve the successor before creating it.** Keep `Phase: preparing`.
   Choose one collision-free date-prefixed task id, checking active and
   archived tasks, and persist `- Target: <id>` plus the transfer plan in the
   source's Split section. Include the original requirements' disposition,
   retained acceptance evidence, every transferred item/finding, and active
   tasks whose dependencies move. A retry must reuse this Target. If a target
   already exists, compare it with the recorded plan before proceeding;
   never overwrite an unrelated, claimed, started, or archived task. Missing
   Target means selection was not yet completed, not permission to replace a
   previously recorded target.

3. **Intake one independent pending task.** Follow the intake schema with a
   fresh remainder estimate `0/<total>`, `session-limit: <total + 3>`, empty
   claim and session log, suitable prefetch, and its own Scope/Acceptance and
   session plan when applicable. The authorized split does not require a
   separate draft-confirmation round. Exclude the source from the overlap
   check, but do not silently duplicate work owned by other active tasks.

   Write the successor so it can be executed without reading the source:
   include relevant landed behavior, code locations/commit evidence, exact
   unresolved findings with reproduction/impact, and concrete verification
   criteria. Preserve source execution constraints and provenance-bearing
   instructions, including required branches/worktrees, experiment identity,
   prohibited edit destinations, and delivery requirements. Restate them
   directly in the successor so independence never drops a safety or placement
   constraint. Correctness repair belongs explicitly in its scope and plan.
   Do not import the source's completed session history as new dev work or
   require a source-task reference. The successor cannot depend on the source
   id, which will be archived. Keep other genuine prerequisites explicit.
   Write the task and its active index row; on retry repair only omissions
   consistent with the reserved plan, never append a duplicate row or task.

4. **Reconcile the source and dependent tasks.** Update the source's current
   Scope/Acceptance to the work it retains and the explicit transfer outcome;
   keep the original requirements and their destinations in `## Split`.
   Preserve historical session entries, review verdicts, finding severity,
   and completed/reviewed plan slices. Describe unimplemented plan work as
   transferred without claiming it was implemented. The source's completion
   means administrative closure after transfer, not that its original full
   acceptance criteria passed or its landed code has no outstanding defects.

   Audit other active tasks. Where a task still depends on transferred work,
   replace its source blocker with the successor id and retain its blocked
   state. Remove only dependencies actually satisfied by delivered work;
   restore an active status only when no genuine blocker remains. Preserve
   the dependency mapping in Split so closeout can verify or recover it.

5. **Verify before committing to closure.** Check coverage item by item:
   every original scope/acceptance obligation is either backed by retained
   completion evidence or owned by the successor; every unresolved finding
   has its severity, evidence, and required outcome in the successor; no
   dependency has been lost or falsely cleared. Confirm the successor is
   pending with its single index row, independent of the source, and the
   source's estimate and limit are unchanged. Do not rely on machine checks
   to prove this semantic coverage.

6. **Finish the administrative transfer.** Only after step 5 succeeds, set
   `- Phase: ready`. Append this session's source log entry with mandatory
   `- Kind: split`, Done describing the actual disposition, and Next/Open
   `none` because all remainder has a pending owner. Do not add `Verdict:
   pass` or rewrite a real `changes-requested` review. With a clean tree,
   set source `status: completed`, then invoke `/ai-sync-v2 <source-task-id>`.
   Absorption, source archival, and source index removal remain that skill's
   exclusive domain. Leave the successor pending; do not start it.

## Retry and recovery

- `preparing` or `ready` with an active source: read the persisted plan and
  Target, verify existing artifacts, and finish only missing steps. `ready`
  still requires its evidence to hold before completing the source. No new
  target, duplicated log entry for the same session, or repeated intake. Do
  not discard a recovery reservation because today's status/pass admission
  gate would reject a new transfer.
- Completed active source: verify its ready transfer, then invoke closeout.
- Source already archived with `Phase: ready`: reuse the archived Target and
  follow closeout's interrupted-split recovery. Verify the existing pending
  successor, reconcile remaining tasks and stale source index state, and
  report the required audit line. Never repeat intake or memory absorption.
- Missing source and no valid archive, conflicting or started successor, or
  insufficient evidence to recover the transfer: report the concrete issue
  for a human ruling. Preserve existing artifacts; do not overwrite or invent
  a successful transfer. A run interruption alone is not a new task.

## Report

Report the source and successor ids, the transferred work/findings, and
whether closeout finished. Include the normal closeout audit line exactly
once:

`Remaining-task audit: checked N active task(s); updated <ids|none>; unchanged <ids|none>`

Call out unresolved human decisions if closure could not finish. Never
describe transferred correctness findings as fixed or the source as having
passed review when it closed by split.
