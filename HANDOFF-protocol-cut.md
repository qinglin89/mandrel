# Handoff: protocol/workflow cut (boundary refactor across the suite)

> Created 2026-07-13 from a **design-only** discussion session ("workflow-
> mechanism&protocol cut"). SEPARATE workstream from `HANDOFF-orch-hub.md`
> (orch-hub service) and from the orchestrator state-machine handoff in
> quantx. This repo does no `.ai-tasks` tracking (human-interactive,
> handoff-driven) — this document anchors the workstream. The ENTIRE
> handoff is a preliminary study (user-ruled 2026-07-16): reference input,
> NOT binding constraints — the implementing session must understand it,
> evaluate it against the actual code/docs, and judge its own approach.
> Read `canonical/orchestrator/README.md` first if you don't know the
> system.

## Execution status (cut plan approved by user 2026-07-16)

The implementing session evaluated this handoff against the suite and the user
approved its execution plan (recorded in the landings below). Phase ladder — each
phase is one independent commit landing; the suite stays green at every landing:

| Phase | Content | Status |
|---|---|---|
| P0 | `CHARTER.md` (boundary law, ratified) | **landed 2026-07-16** |
| P1 | `AUDIT-protocol-cut.md` (classified leak inventory) | **landed 2026-07-16** — 38 findings (3A/14B/1C/9D/11R), all dispositions name their destination; §-ref fates classified; open questions §10 resolved |
| P2 | Prompt externalization + postcheck-ID contract, byte-identical (`canonical/orchestrator/prompts/`) | pending |
| P3a | Doc cut + consumer re-point (new `protocols/ workflow/ meta/`; deploy umbrella `.ai-protocol/` at targets — user-selected; CLAUDE.md chain, hooks, `REVIEW_RULE`, deploy.py, tests, boundary-lint) | pending |
| P3b | Prompt/hook content trim to instantiation (litmus 4; mock substring assertions churn here) | pending |
| P4 | Live smoke on a `quantx-bak-*` repo + one-wave target deploys (user-run) | pending |

Key plan decisions (full plan text approved 2026-07-16): externalization runs BEFORE
the doc cut (byte-identity proves the loader with zero assertion churn); templates +
postcheck contract live under `canonical/orchestrator/prompts/` deploying with the
existing orchestrator bucket; target docs deploy under a single `.ai-protocol/`
umbrella dir (one gitignore line, no root collisions); `{{var}}` template syntax
(deploy.py `{{REPO_ROOT}}` precedent); verb→contract mapping stays in target
`CLAUDE.md` (loader = dispatch surface, litmus 1 applies to role-doc internals);
plan is a ROLE with the plan-report as its return-value output contract (adopted);
`ai-coding-tasks-v2-tmp.md` is deleted in P3a (it deploys today — no `-tmp` filter
in deploy.py, the "excluded" was convention only).

### P2 entry brief (for the fresh implementing session)

Read first: `CHARTER.md`, `AUDIT-protocol-cut.md` (§6 is the prompt-site table),
and the approved plan `/Users/linqing/.claude/plans/cozy-moseying-harbor.md`
(full plan text; user session-strategy ruling 2026-07-16: one fresh hand session
per phase; wholesale subagent delegation acceptable only for objective-gated
phases — P2/P3b — never P3a/P4).

- **Scope** (closed inventory; orchestrator.py line refs at `4be85f2`): builders
  `dev_prompt` 1916–1963, `review_prompt` 1965–1985, plan-gate initial 1565–1591 +
  feedback 1681–1706, approved-plan append 1362–1376; nine midflight
  `[orchestrator]` strings — answered-continue 1392–94, run-error retry 1403–06,
  context wrap-up 1478–91 (+ role/remediation note variants 1445–73), followup
  relay 1511, violation fix 1516–24, discussion-turn 1213–19, blocked resume
  2035–41, blocked violation 2061–62, closeout-incomplete 2192–98; `close_out`
  2168–78; stall augmentation 2327–30; fragments `_sid_line` 1858–63, `_preamble`
  1865–77, `_entry_checklist` 1883–1914, `CLEAN_HOWTO` 199–204, `DISCUSSION_HINT`
  181–83; all `kind=` escalation banners; `check_specs` 1710–1834 →
  `postcheck-contract.md` (no ID system exists today — P2 introduces IDs).
- **Mechanism**: `PROMPTS_DIR = ORCH_DIR/"prompts"` (AUTOMATION_MD precedent:
  ORCH_DIR-relative survives repo-layout changes AND loads unpatched in the mock
  suite); `{{var}}` substitution, strict; code-side manifest (template name →
  placeholder set); startup validation = missing/malformed template, unknown
  placeholder, or postcheck ID↔callable mapping not 1:1 both directions →
  startup ERROR (same policy as effort validation). Composition (base +
  mode-add + injected fragments) stays in builder code; `checks_preview` stays
  GENERATED from the loaded contract.
- **Gates**: byte-identity ⇒ ZERO edits to the existing ~75 `in prompt` + ~25
  banner assertions (that is the proof); one NEW mock scenario (startup refusal:
  missing template + ID mismatch); `pytest tests/` green; log-line formats and
  `sessions.json` untouched (orch-hub).
- **Facts that shaped the design**: the mock suite already asserts through
  externally loaded files (`REVIEW_RULE.read_text()`, session-start.sh output,
  automation-mode.md) — the seam is proven; `make_repo()` copies real canonical
  docs; `patch_module()` overrides path constants (an ORCH_DIR-relative
  PROMPTS_DIR needs no override). Templates deploy automatically with the
  existing orchestrator bucket → `.cursor/orchestrator/prompts/` (no deploy.py
  change in P2).
- **Landing**: single commit; mark P2 landed in the status table above.

Semantic guards for P3a (from the audit — do not drop silently): (1) §10 End step 5
runs remaining-task reconciliation at EVERY session end — the session-end boundary
skill keeps it (closeout additionally repeats it); the handoff's earlier
session-end list was abbreviated, not a ruled change. (2) The three stop hooks'
wrap-up texts have drifted (only cursor carries the remediation-marker
instruction, `AUDIT-protocol-cut.md` HK-01) — single-sourcing from the
session-end skill resolves it; claude/codex behavior gains the marker line, which
matches the protocol docs. (3) Log-line formats and all task-file data shapes are
frozen (orch-hub + mid-flight tasks).

## Goal

Separate the **protocol** (what one session does) from the **workflow**
(how sessions chain). Today choreography leaks into role docs, prompts, and
session-log prose. After the cut: each agent session is a pure function —
`context/task-state → role function → declared outputs` — and the workflow
layer (the orchestrator, or a human executing the same runbook) is the ONLY
component that interprets those outputs as scheduling signals. Expected
gains: shorter role-local docs (less context per session), a workflow that
can change without re-teaching sessions, and mechanism guarantees moving
from LLM compliance into deterministic code.

## Charter seed (Phase 0 ratifies this, user-ruled 2026-07-13 in discussion)

- **Pure-function model**: a session faces only the protocol: inputs (task
  file, context, role knowledge) → its role's responsibility → declared
  outputs. It never needs to know other session kinds exist.
- **Melt rule**: any datum the workflow needs from a session is reified as
  a protocol field/marker with ROLE-LOCAL semantics ("declare
  `Handoff: continuation` when your fix set is incomplete"). Its scheduling
  consequence ("remediation re-dispatches before re-review") is documented
  ONLY in the workflow runbook. Same data, two interpretations, one owner
  each.
- **Caller owns sequencing**: no role section or prompt names another
  session/role or predicts dispatch.
- **Standalone = human-as-orchestrator**: one workflow runbook, two
  executors — the orchestrator implements it, a human can execute it
  manually. Single-sourced like tasks-v2 §3 (never restated elsewhere).
- **Ratified exception**: quality-shaping language STAYS ("end as one
  complete, coherent, reviewable unit"); choreography language GOES
  ("…which the next review session then examines"). Without this line the
  audit oscillates.
- **Litmus tests** (applied mechanically in Phase 1):
  1. Does a role section name another session/role?
  2. Does any prose predict what runs next?
  3. Is a marker documented by its consequence instead of its local
     meaning?
  4. Does an orchestrator prompt restate a rule instead of instantiating
     values and pointing at the protocol?

## Evidence / canonical specimens (2026-07-13)

- Live review entry (quantx-bak-0713-before-plangate, task
  `2026-07-04-risk-control-operator-notifications`):
  `- Next: Dev remediation for group ce967ff4…: keep the operator event
  contract/read model, but prevent risk-event persistence/publish latency
  from blocking manual or automatic kill execution.`
  First half = scheduling narration, **literally redundant** (dispatch
  derives dev-remediation from `Verdict` + `Group`; the prose is never
  parsed). Second half = genuine review output. This is the model case for
  re-scoped `Next:` semantics: "remaining work / required changes on this
  task" — never role/session naming.
- Precedents proving the direction (already done in past cleanups):
  reviewer-side escalation status signal REMOVED (orchestrator counts
  changes-requested rounds itself); transitions single-sourced in tasks-v2
  §3; orchestrator `checks_preview` is generated from `check_specs` so
  told-vs-verified cannot drift; convergence budgets counted by the
  orchestrator, never declared by the reviewer.

## Bounding insight (why this is safe)

The DATA contract is already correct: task file = ground truth; the dumb
scheduler derives all dispatch from parsed fields/markers (`status`,
`Verdict`, `Group`, `blockers`, `Handoff: continuation`,
`Dispute-unresolved:`), stateless attach. The refactor is **docs and
prompts only; data shapes stay stable** — archived and mid-flight tasks
keep parsing, and both execution modes (manual, orchestrated) keep working
at every step. orch-hub is expected to be untouched (it parses stable log
lines + task frontmatter only) — confirm, don't assume.

## Target-structure proposal (2026-07-16 discussion — REFERENCE, not binding)

> Produced in a follow-up discussion in the same workstream. It refines the
> Phase 2 target below. The next session should UNDERSTAND and EVALUATE this
> proposal against the audit findings, then confirm or adjust — do not
> inherit it blindly.

Three layers:

1. **protocols/** — roles: `intake`, `plan`, `dev` (advancement |
   remediation), `review`. Each self-contained and closed:
   `inputs (task file + assembled context) → role function → declared
   outputs`. Roles connect to the rest of the system only through declared
   data and through skills/hooks; anything cross-cutting is defined in the
   skill/hook, never in the role protocol.
2. **workflow/** — the caller: orchestrator (or a human executing the same
   runbook), hooks (session start/stop), core skills (closeout including
   remaining-task reconciliation, housekeeping, context-check). The
   **runbook spec** (dispatch table, budgets, escalation paths) stays an
   explicit single-source document implemented by both executors —
   hooks/skills are executables, not where the spec lives. Workflow shape:
   read taskfile → decide role → assemble entry prompt (eager + prefetch)
   → run session (starthook → work → stophook) → on return: post-check /
   close-out / escalation / wrap-up.
3. **meta/** — data + schemas + access contract: `.ai/` (shapes, admission,
   routing), `.ai-tasks/` (task-file schema, index, archive semantics).
   Proposal: `scripts/` belongs to workflow (pure executables); meta stays
   data+schema only — a naming choice for the next session to settle.

Supporting rules (proposed, to ratify or amend):

- **Role vs skill criterion**: dispatched by task state ⇒ role; triggered
  by a lifecycle event ⇒ skill. Closeout ⇒ skill (it also crosses tasks
  via remaining-task reconcile). A role may be *packaged* as a skill for
  invocation (intake today) — its definition (protocol) and its invocation
  (workflow) are classified separately.
- **Result dual-channel**: role outputs = task-file declarations ∪ a
  return value to the caller. Review persists (verdict/findings must
  survive across sessions); plan returns (the plan-report is consumed
  immediately by the caller and deliberately not persisted). This also
  answers the plan-report open question: **plan is a role; the plan-report
  is its output contract** (a protocol asset); whether/when a plan session
  runs stays a workflow decision (`--plan-gate`).
- **Meta read/write asymmetry**: roles READ meta through its access
  contract; WRITES go only through workflow-invoked skills (closeout
  absorb, housekeeping) — formalizes the existing "no mid-task `.ai/`
  edits" convention.
- **Context assembly by the caller**: static context (eager docs +
  frontmatter `prefetch:`) is assembled by the workflow into the entry
  prompt; dynamic mid-session retrieval remains role behavior through the
  meta read contract. The two backends are two implementations of ONE
  assembly spec (cursor: orchestrator injection; cc-codex: hooks/CLAUDE.md
  chain) — spec single-sourced.
- **automation-mode.md split**: scheduling content → runbook; conduct
  adjustments (no interactive asks, blocking rules) → a workflow-injected
  conduct annex; role protocols stay orchestration-unaware.

### Boundary bookkeeping (discussion continuation, 2026-07-16)

- **v2 §10 dissolves**: Entry (claim, est bump, checklist) dissolves into
  the caller-assembled entry prompt; End (clean-tree commit, session-log
  entry, status write) dissolves into a boundary skill; what remains of
  §10 is pure work conduct. Role names (dev/review) are workflow
  vocabulary — dispatch keys that select a contract; the contract text
  itself is a self-contained, anonymous work spec ("implement the required
  slice per the taskfile"; remediation stays role-local via
  findings-as-input). A session never needs to know it "is a dev session".
- **Two boundary skills, split by one criterion — does it need the working
  session's context?**
  - *Session-end bookkeeping* (every session, stophook-triggered, runs IN
    the same conversation): commit clean tree, write the session-log entry
    (shape from the meta schema), status declaration. Only the working
    session can author this content.
  - *Task-completion closeout* (fires only at completion): absorption
    (ai-sync), archive, remaining-task reconcile — context-independent,
    may run as a fresh session (today's orchestrated ai-sync-v2 path) or
    natively in-session (the cc-codex seam).
- **Content/format separation**: the role contract produces judgment
  content (what was done, findings, verdict conclusions, what remains,
  disputes); the boundary skill owns persistence format (taskfile entry
  shapes, status mechanics, commit discipline — shapes specified ONCE in
  meta). Role contracts contain zero file-format text. Today the entry
  shape is smeared across v2 §10 / review-v2 / hook texts / orchestrator
  prompts — after the cut it is one schema consumed by one skill.
  Post-checks keep verifying declared outputs unchanged; the enforcement
  layer does not move.
- **Hook as the single trigger — backstop arrangement**: after the move,
  bookkeeping instructions are carried ONLY by the stophook chain.
  Orchestrated mode: post-check followups remain the backstop. Manual
  mode: the human executing the runbook IS the post-check (the runbook's
  on-return step includes verifying declared outputs) — do NOT reintroduce
  End pointers into role contracts. Phase 2 entry precondition: Stop-hook
  firing verified on every tool in use (cc-codex verified per orchestrator
  README; cursor backend is orchestrator-injected, so the question is
  moot there).
- **`.ai/` read-capability optimization is OUT of this workstream's
  scope** — the audit records observations but changes nothing there
  (structure-first discipline; content/quality improvements come after
  the boundaries are done).

Mapping of current files (audit should verify, then Phase 2 executes):

| Current | Destination |
|---|---|
| ai-coding-v2.md §10/§11 | protocols/dev, protocols/review |
| ai-coding-v2.md §8/§9 (retrieval) | meta read contract + workflow context-assembly spec |
| ai-coding-tasks-v2.md | split: taskfile schema → meta; §3 transitions → workflow runbook; close-out section → closeout skill's reference spec |
| ai-coding-review-v2.md | protocols/review |
| ai-coding-memory-v2.md | meta (`.ai/` schema + admission) + closeout skill reference |
| ai-coding-init-v2.md | meta bootstrap (ai-init skill's reference spec) |
| automation-mode.md | split per rule above |
| orchestrator README §3/§5/§6 | machine-side implementation doc of the runbook |

### Prompt externalization (user-ruled direction, 2026-07-16)

Orchestrator prompt texts must not stay hardcoded in `orchestrator.py`:
extract them into single-source template files (workflow-layer artifacts)
that the orchestrator loads at runtime — the SAME files a human reads when
standing in for the orchestrator, making human/orchestrator equivalence
operational. The direction is ruled; the mechanism design belongs to the
implementing session.

Inventory to externalize (by name; code as of 2026-07-16): role entry
prompts (`dev_prompt`, `review_prompt`, shared `_preamble` / `_sid_line` /
`_entry_checklist` fragments); plan-gate prompts (initial PLAN GATE, PLAN
FEEDBACK two-shape, APPROVED PLAN GATE injection); mid-flight
`[orchestrator]` prompts (answered-continue, run-error retry, context
wrap-up, violation fix, blocked resume, close-out incomplete,
discussion-turn); close-out prompts; escalation banners.

Constraints to preserve:

- `checks_preview` is GENERATED from `check_specs` (told-vs-verified
  single-sourcing) — in templates it must remain a substitution variable,
  never frozen prose.
- Templates instantiate rules with current values (sid, task id, status
  menus, budget numbers) via variables; they must not restate protocol
  rules (litmus test 4 applies to templates too).
- The mock suite asserts prompt substrings — tests must assert through the
  same loaded templates (or load the same files), so a template edit can
  never silently diverge from scenario expectations.
- Templates ship with the deploy payload (`aii-2` alongside
  `orchestrator.py`); a missing/malformed template is a startup ERROR
  (refuse, like effort validation), never a silent fallback.

Sizing: fits Phase 3, or as its own pre-phase — next session judges.

### Concrete canonical layout (discussion continuation, 2026-07-16 — proposal)

Proposed literal directory target (REFERENCE — evaluate before adopting):

```
canonical/
  protocols/            # role contracts (session-facing, anonymous work
                        # specs, standalone-readable)
    dev.md (advancement/remediation as mode sections)
    review.md   plan.md   intake.md
  workflow/             # caller-layer SPEC (orchestrator = machine
                        # executor, human = manual executor)
    runbook.md          # dispatch narrative + budgets + escalation paths
                        # (human-executable)
    rolemapping.md      # condition(taskfile[durable] + run-state
                        # [ephemeral]) -> (contract, prompt composition,
                        # session mode)
    prompts/entry/      # *-base, *-add, conduct-annex, plan-report
                        # injection slot, checks-preview variable slot
    prompts/midflight/  # wrap-up, violation-fix, resume/answer prompts,
                        # discussion-turn, close-out, escalation banners
    postcheck-contract.md  # requirement lines + check-IDs
    skills/             # boundary-skill specs: session-end bookkeeping,
                        # task-completion closeout
  meta/                 # taskfile schema (frontmatter, entry shapes,
                        # index/archive semantics); .ai/ schema +
                        # admission + read contract
  claude/ codex/ cursor/  # tool adapters (hooks wiring the workflow
                          # trigger points)
  orchestrator/         # machine-executor code + mock suite (consumes
                        # workflow/prompts and postcheck-contract)
```

Notes that make this layout sound (the four corrections from discussion):

- **meta stays explicit** — taskfile/.ai/ schemas have MULTIPLE consumers
  (boundary skill writes entries, rolemapping parses conditions,
  post-checks verify shapes); without a single source the entry shape
  smears back into skills and contracts, recreating the disease.
- **Prompts are two families** — `entry/` AND `midflight/` (the ruled
  externalization inventory covers both). Entry composition formalized:
  `base + mode-add + injected fragments`, the fragments being the
  plan-report injection (dev-adv after a plan session), the automation
  conduct annex, and `checks-preview` — which stays a GENERATED variable,
  never file prose.
- **Post-check contract externalizes via ID binding**: the contract file
  holds requirement text + check-ID; code binds checks by ID; startup
  validates the mapping 1:1 in BOTH directions (mismatch = startup error,
  like a missing template). The contract file then doubles as the
  human-as-orchestrator on-return checklist — human and machine read the
  same source.
- **rolemapping marks input durability**: the ONLY cross-run durable
  dispatch input is the taskfile (this IS the stateless-attach
  invariant); run-local inputs (sessions.json sid→tool routing,
  pending_ruling, followup/group/max-sessions counters, control-dir
  signals) are listed but marked ephemeral. Session modes are FOUR, not
  three:

  | mode | today's mechanics |
  |---|---|
  | new (first-entry) | attach-table dispatch, fresh session |
  | continue-turn (iteration) | same open conversation: followup fixes, answered-continue, discussion rounds, plan feedback |
  | resume-by-sid | blocked-resume: reopen the SAME persisted conversation |
  | fresh-continuation (wrap-up) | new session, same role: remediation continuation marker only (advancement never continues — review comes first) |

This proposal answers the "literal layout vs logical layering" open
question in the affirmative; two deploy touchpoints must be confirmed at
audit time: the `aii-2` manifest/target-path mapping (today
`ai-coding-*.md` deploys flat at the target repo root) and the CLAUDE.md
import chain pointing at the new paths.

## Phase plan (each phase lands independently)

- **Phase 0 — charter**: write the cut rules above as a short normative
  doc; user RATIFIES before anything else moves. (Location: open question
  below.)
- **Phase 1 — audit**: sweep the whole suite against the litmus tests →
  classified leak inventory: (A) narrative leaks — delete/reword; (B)
  workflow rules living in role docs — relocate to runbook; (C) protocol
  data missing a role-local definition — write it; (D) orchestrator prompts
  restating rules — trim to instantiation. Deliverable: inventory appended
  to this handoff (or sibling doc). Phases 0+1 fit one session.
- **Phase 2 — restructure**: three layers — *role conduct* (dev / review /
  close-out, each readable alone), *data contract* (task-file schema:
  fields, markers, entry shapes — the declared-output alphabet), *workflow
  runbook* (dispatch table, budgets, escalations — human-executable,
  orchestrator-implemented). Re-scoped `Next:` semantics land here.
- **Phase 3 — mechanism alignment**: orchestrator prompt builders trimmed
  to instantiation + pointers; mock-suite assertions updated; live smoke on
  a test repo; `aii-2` deploy to targets.

## Audit scope (canonical file map)

- `canonical/repo-root/`: `ai-coding-v2.md` (§10 entry/preReEst/end, §11
  conduct — main leak surface), `ai-coding-tasks-v2.md` (§3 transitions —
  becomes the runbook core), `ai-coding-review-v2.md` (verdict semantics,
  dispute flow), `ai-coding-memory-v2.md` + `ai-coding-init-v2.md`
  (expected light), `CLAUDE.md`. `ai-coding-tasks-v2-tmp.md` is a draft —
  excluded per convention.
- `canonical/orchestrator/`: `orchestrator.py` prompt builders
  (`dev_prompt`, review prompt, plan-gate prompts, wrap-up/violation
  prompts, close-out prompts, `check_specs` requirement lines),
  `README.md` (§5/§6 are already runbook-shaped), `automation-mode.md`.
- `canonical/claude|codex|cursor/hooks/` (esp. `stop-context-check.sh`
  wrap-up texts).
- `skills-backup/`: `ai-sync-v2`, `intake-task` (session-adjacent; light).

## Guards / mechanics

- Mock suite must stay green after ANY orchestrator change:
  `python3 -W error::DeprecationWarning canonical/orchestrator/test_loop_mock.py`
  (28 scenarios as of 2026-07-13). It only guards the mechanism half —
  protocol semantic changes need human review; there is no automated guard.
- Edit canonical only; deploy via the user's `aii-2`; never hand-edit
  deployed copies. Live smoke pattern: a `quantx-bak-*` test repo (see
  Related work below for today's example).

## Related landed work (same day, same files — do not regress)

`6a0452b` "Improve orchestrator plan report handling": the `--plan-gate`
loop now revolves around a plan-report artifact (full-report revision /
`PLAN-REPORT: unchanged` sentinel / warn-and-keep; pointer banners with
rev/round; confirm delivers the CURRENT report + ruling, never last-turn
text; README §5.8 rewritten; mock scenario 6 rewritten). Live-validated on
`quantx-bak-0713-before-plangate` (2-round loop incl. a fold-in revision;
clean delivery; graceful stop.flag exit). Deployed to that test repo by the
user; run `aii-2 status` before assuming other targets are current.

## Open questions for the next session

- Charter + runbook location: new protocol doc vs extending
  `ai-coding-tasks-v2.md` §3 vs orchestrator README §6. Must end
  single-sourced with the orchestrator's actual behavior.
- Exact re-scoped `Next:` wording (proposal: "remaining work / required
  changes on this task; never name sessions or roles").
- Is the plan-gate plan-report contract protocol data or mechanism-local?
  (2026-07-16 proposal: plan is a ROLE and the report is its output
  contract via the return-value channel — evaluate against the audit,
  then decide.)
- Audit depth for memory-v2 / init-v2 / skills (expected light — confirm).
- Prompt templates (ruled direction, see above): file format and location
  (e.g. `canonical/orchestrator/prompts/`, one file per prompt vs a
  bundle), variable syntax, and how the mock suite consumes the same
  files.
- Does protocols/workflow/meta become a literal doc/directory layout, or a
  logical layering inside fewer files? (2026-07-16 proposal: literal —
  see "Concrete canonical layout". Deploy surface and CLAUDE.md import
  chain are affected — confirm with `aii-2` mechanics in view.)
- Confirm orch-hub's task-file/log parsers are unaffected (data shapes
  unchanged → should be a no-op).

## Key invariants to preserve

- Protocol works standalone; orchestrator = enforcement + attention
  amplification, never a replacement.
- Task file = ground truth; orchestrator NEVER writes it; all dispatch
  derives from it, re-parsed each iteration.
- Each rule lives once; prompts instantiate rules with current values,
  never restate them.
- Terminal output status-level; verbosity to log files.
- Canonical repo is the only edit surface; targets receive deploys.
