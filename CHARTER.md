# Charter: protocol / workflow / meta boundaries

Normative rules for how the suite's documents, prompts, hooks, and code divide
responsibility. Charter seed user-ruled 2026-07-13 (discussion session); supporting
rules ruled 2026-07-16; ratified 2026-07-16 with the approved cut plan. Maintainer
law for the canonical repo — this file is NOT deployed to targets. The workstream
anchor is `HANDOFF-protocol-cut.md`; the leak inventory measured against this
charter is `AUDIT-protocol-cut.md`.

## Layers

| Layer | Owns | Artifacts |
|---|---|---|
| `protocols/` | What ONE session does: role contracts as self-contained, anonymous work specs | conduct, dev, review, plan, intake |
| `workflow/` | How sessions chain: dispatch, budgets, escalations, prompt assembly, boundary skills | runbook, rolemapping, prompt templates, postcheck contract, boundary-skill specs |
| `meta/` | Data + schemas + access contract | taskfile schema, `.ai/` schema + admission + read contract, init |

Two executors implement the workflow: the orchestrator (machine executor) and a
human running the same runbook (manual mode). Equivalence is operational, not
aspirational: both read the same single-source artifacts (runbook, postcheck
contract, prompt templates).

## Rules

1. **Pure-function model** — a session faces only its protocol: inputs (task file,
   assembled context, role knowledge) → its role's responsibility → declared
   outputs. It never needs to know other session kinds exist.
2. **Melt rule** — any datum the workflow needs from a session is reified as a
   protocol field/marker with ROLE-LOCAL semantics ("declare `Handoff: continuation`
   when your fix set is incomplete"). Its scheduling consequence ("remediation
   re-dispatches before re-review") is documented ONLY in the workflow layer. Same
   data, two interpretations, one owner each.
3. **Caller owns sequencing** — no role doc or prompt names another session/role or
   predicts dispatch.
4. **Standalone = human-as-orchestrator** — one workflow spec, two executors; the
   spec is single-sourced and never restated elsewhere.
5. **Quality vs choreography** (ratified exception) — quality-shaping language STAYS
   ("end as one complete, coherent, reviewable unit"); choreography language GOES
   ("…which the next review session then examines"). Without this line the audit
   oscillates.
6. **Role vs skill** — dispatched by task state ⇒ role; triggered by a lifecycle
   event ⇒ skill. A role may be *packaged* as a skill for invocation (intake); its
   definition (protocol) and its invocation (workflow) are classified separately.
7. **Result dual-channel** — role outputs = task-file declarations ∪ a return value
   to the caller. Review persists (verdict/findings survive across sessions); plan
   returns (the plan-report is consumed immediately by the caller and deliberately
   not persisted).
8. **Meta read/write asymmetry** — roles READ meta through its access contract;
   writes go only through workflow-invoked skills (closeout absorption,
   housekeeping). No mid-task `.ai/` edits.
9. **Content/format separation** — role contracts produce judgment content (what was
   done, findings, verdicts, what remains, disputes); boundary skills own
   persistence format (entry shapes, status mechanics, commit discipline). Shapes
   are specified ONCE, in meta. Role contracts contain zero file-format text.
10. **Hook as single trigger, checks as backstop** — session-boundary bookkeeping is
    carried only by the stophook chain. Orchestrated mode: post-check followups are
    the backstop. Manual mode: the human's on-return checklist (the postcheck
    contract) IS the post-check. No End pointers re-enter role contracts.
11. **Each rule lives once** — prompts, hooks, and templates instantiate rules with
    current values (sid, task id, status menus, budgets); they never restate them.

## Litmus tests

Applied mechanically in audits; `scripts/boundary-lint.sh` automates the greppable
parts once the new layout exists.

1. Does a role section name another session/role?
2. Does any prose predict what runs next?
3. Is a marker documented by its consequence instead of its local meaning?
4. Does an orchestrator prompt (or hook text) restate a rule instead of
   instantiating values and pointing at the protocol?

Interpretation note: the loader (target `CLAUDE.md`) carries the verb→contract
mapping (`task <id>` → dev contract; `review <id>` → review contract). That mapping
is the workflow's dispatch surface, not a role doc — litmus 1 applies to role-doc
internals.

## Boundary bookkeeping

- **Session-end bookkeeping** (every session; needs the working session's context):
  clean-tree commit, session-log entry, status declaration — a stophook-triggered
  boundary skill running in the same conversation. Only the working session can
  author this content.
- **Task-completion closeout** (fires only at completion; context-independent):
  absorption, archive, remaining-task reconcile — may run as a fresh session
  (orchestrated path) or natively in-session (stophook chain).
- **Entry bookkeeping** (claim, est bump, checklist) is assembled by the caller into
  the entry prompt; it is not role-contract text.
- Role names (dev/review) are workflow vocabulary — dispatch keys that select a
  contract. Contract text itself is a self-contained, anonymous work spec; a session
  never needs to know it "is a dev session".

## Scope guards

- Data shapes are FROZEN across this boundary work: task frontmatter fields,
  session-log heading format, markers, index/archive semantics, and orchestrator
  log-line formats (orch-hub consumes them). Archived and mid-flight tasks keep
  parsing at every landing.
- `.ai/` read-capability optimization is OUT of scope — audits record observations,
  change nothing there (structure first; content/quality after boundaries).
