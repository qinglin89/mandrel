# Handoff: protocol/workflow cut

> Active handoff for the protocol/workflow boundary workstream. It is separate
> from `canonical/orchestrator/HANDOFF-orch-hub.md` and the quantx orchestrator
> state-machine workstream. Read `canonical/orchestrator/README.md` for system
> orientation and `CHARTER.md` for the normative boundary rules.
>
> This file was compacted on 2026-07-19. The full design discussion, phase entry
> briefs, landing logs, smoke notes, and audit history remain in Git at commit
> `91600fc`:
>
> ```bash
> git show 91600fc:HANDOFF-protocol-cut.md
> ```

## Current state

| Work | State |
|---|---|
| P0–P3 | **Landed 2026-07-16** — boundary charter and audit; prompt externalization; protocol/workflow/meta doc cut; consumer re-point; prompt and plan-contract trim |
| P4 | **Validation complete; target wave remains** — deterministic smoke and a full live LLM drill passed after the three findings below were fixed |
| P5 | **Landed/closed 2026-07-17** — context assembly was made symmetric; eager substrate purified; dev contracts ended as separate per-mode contracts; broad voice editing deliberately stopped |
| P6 | **Defined, not implemented** — split the memory document along its read/write axis; implementation brief below |

The cut now has these stable properties:

- Canonical docs are layered under `canonical/protocols/`, `workflow/`, and
  `meta/`, and deploy under one `.ai-protocol/` target directory.
- Orchestrator prompts live in `canonical/orchestrator/prompts/`; strict
  `{{var}}` manifests and the postcheck-ID contract fail closed at startup.
- Role contracts are injected only for the selected role/mode. The eager channel
  contains shared substrate and conduct, not role contracts.
- Plan is a role; the plan report is its return-value contract.
- Task declarations used for dispatch are structurally parsed. Prose mentions are
  inert; incomplete remediation is represented by frontmatter `fix-set: open`.
- `/invoke` accepts an explicit role and verifies its legality against the task
  file before execution.

## Remaining work

### P4 target wave and cleanup

State recorded 2026-07-18: orch-hub-test was still at `f2ee85d`; hkchain,
orch-hub, and quantx were older. Run `mandrel status` rather than assuming this is
still current. Each lagging target needs one canonical `mandrel deploy` before the
next drill or rollout.

After all targets use the post-cut layout:

1. Remove legacy `ai-coding-*.md` files from the targets.
2. Drop the transitional `/ai-coding*.md` gitignore rule and remaining explicit
   legacy compatibility mentions.
3. Re-run a live drill on a deployed target.

### Small pending fixes

- Add an orchestrator cap for consecutive remediation-continuation dispatches;
  escalate to the human after the configured limit. Direction approved, design
  and implementation pending.
- Fix `canonical/protocols/review.md`: “You evaluates” → “You evaluate”.

### P6 implementation brief: memory read/write split

User ruling from 2026-07-17: split `meta/memory.md`; do **not** split
`meta/taskfile.md`. Revalidate this brief against the Snapshot upgrade backlog
before implementation.

- Keep `memory.md` as the reader-facing eager contract: invariants, loading,
  routing, and interpretation of `last-updated` / `verified-against`.
- Move admission, maintenance, size/upgrade, propagation, and authoring procedures
  to a write-side document such as `meta/memory-write.md` or `meta/absorption.md`.
  The final name and the exact split of mixed authoring/staleness text are design
  decisions.
- Activate the write-side document through closeout, housekeeping, and init
  skills. Do not add another ambient prompt wrapper.
- Re-point `workflow/skills/closeout.md`, `skills-backup/ai-sync-v2`,
  `ai-housekeeping`, and `ai-init`; resync installed/global skills as required.
- Update boundary lint, deployment layout/count assertions, mock repo fixtures,
  and smoke expectations. Keep `EAGER_FILES` and the `CLAUDE.md` import set
  unchanged.
- Expected gain: roughly 60% of the current memory document leaves ambient
  context while reader-facing trust semantics remain available.

The current reason not to split `taskfile.md`: it is a shared data plane. Its
frontmatter, transitions, entry shapes, and markers are the common alphabet read
and written by multiple roles. A read/write split would duplicate schema. A
smaller extraction of closeout-only lifecycle procedure may still be evaluated.

## TODO / refine backlog

These are discussion and design directions, not approved implementation plans.
Preserve current behavior until each has a concrete design, migration path, and
validation gates.

1. **`.ai/` Snapshot storage and I/O upgrade** — optimize Snapshot storage plus
   read/write paths. Use the Codex and Claude Code sessions titled
   `ai-snapshot upgrade` as inputs. Evaluate the proposals against the current
   memory schema, loaders, sync/housekeeping skills, deployment, and backward
   compatibility.
2. **Memory-document read/write separation (P6)** — implement the P6 direction
   above after reconciling it with item 1.
3. **Protocol-document refinement; reassess taskfile read/write separation** —
   refine the protocol docs and evaluate whether any taskfile content should move
   out. The current ruling remains **do not split `taskfile.md`**; changing it
   requires answering the shared-schema/data-plane objection.
4. **Plan-gate claimed-task context refresh** — during planning, explore
   optimizing or updating the claimed taskfile's `prefetch:` and touched-docs
   context. Ownership and mutation timing remain to be designed.
5. **Optional intake `touched docs` section** — define its semantics and its
   relationship to `prefetch:`, session-end backfill, Snapshot verification
   metadata, and plan-gate consumption before implementation.
6. **Protocol v3: parallel tasks within one repository** — support safe same-repo
   concurrency with workspace/worktree isolation, per-task branches,
   coordination and conflict rules, validation, merge/reconciliation, and an
   explicit v2 migration path.

## Constraints for future changes

`CHARTER.md` is authoritative. The operational summary is:

- A session sees inputs, performs one role contract, and emits declared outputs;
  the caller alone owns sequencing and dispatch.
- Protocol data has role-local meaning. Scheduling consequences live only in the
  workflow runbook.
- The protocol works without the orchestrator; a human can execute the same
  runbook. The orchestrator enforces and focuses attention but does not replace
  the protocol.
- The task file is ground truth. The orchestrator never writes it and re-parses it
  before every dispatch decision.
- Each rule has one owner. Prompts instantiate current values and point at the
  owning contract instead of restating rules.
- Shared context is eager; role-local context is injected or prefetched. Do not
  move write-only procedures back into the ambient channel.
- Preserve archived and mid-flight task compatibility unless a versioned
  migration explicitly changes the data contract.
- Edit canonical sources only. Targets receive changes through `mandrel deploy`;
  never hand-edit deployed protocol copies.

## Validation and delivery

Run gates in proportion to the change:

```bash
scripts/boundary-lint.sh
python -m pytest
cd canonical/orchestrator && .venv/bin/python test_loop_mock.py
```

The mock suite shells out to Git and may need to run outside a restricted
sandbox. Any orchestrator change requires the mock suite. Protocol semantic
changes also require human review because the mock suite primarily guards
mechanism. Changes affecting hooks or deployed layout additionally need an
isolated deploy smoke; rollout changes need a live target drill.

## Historical landing summary

The 2026-07-17/18 live drill found and fixed three issues:

1. Claim SID transcription drift → added the universal `claim-sid` postcheck.
2. Consumer-less per-session remaining-task audit prose → scoped reconciliation
   to work sessions and kept the report line in closeout only.
3. A prose mention of the old continuation marker triggered a runaway remediation
   loop → moved the signal to frontmatter `fix-set: open`, anchored remaining
   entry declarations to schema lines, and made prose structurally inert.

The subsequent entry-prompt slim pass removed duplicate loader narration,
hardened `/invoke`, simplified fix-set semantics to present iff open, and defined
batched review stamping on the last entry only. Detailed rationale, assertions,
commit-by-commit landing facts, and the retired proposals are intentionally kept
only in the Git history referenced at the top of this file.
