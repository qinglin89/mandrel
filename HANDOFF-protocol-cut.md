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
| P2 | Prompt externalization + postcheck-ID contract, byte-identical (`canonical/orchestrator/prompts/`) | **landed 2026-07-16** — 67 templates (`entry/` 26, `midflight/` 41) + `postcheck-contract.md` (9 check-ids); byte-identity PROVEN (AST-extracted templates; old-vs-new capture: 37 suite prompts + 4 banners + 41 probe texts all identical); zero existing-assertion edits; new scenario 30 (startup refusal); mock 30/30 + pytest 25/25 green |
| P3a | Doc cut + consumer re-point (new `protocols/ workflow/ meta/`; deploy umbrella `.ai-protocol/` at targets — user-selected; CLAUDE.md chain, hooks, `REVIEW_RULE`, deploy.py, tests, boundary-lint) | **landed 2026-07-16** — 12 new docs; 5 `ai-coding-*.md` + tasks-v2-tmp + automation-mode.md deleted; all consumers re-pointed; `scripts/boundary-lint.sh` added; mock 30/30 + pytest 25/25 + lint green; scratch-target deploy smoke verified; globals resynced |
| P3b | Prompt/hook content trim to instantiation (litmus 4; mock substring assertions churn here) | **landed 2026-07-16** — all listed surfaces trimmed; `wrapup-plan-remediation` template deleted; ZERO mock-assertion edits needed (assertions anchor on instantiation substrings, which survive); ORC-07's plan-prompt trim deliberately deferred (see landing facts); mock 30/30 + pytest 25/25 + lint green |
| P3c | Plan-contract injection + plan-prompt trim (ORC-07 second half; scenario 6 churns) | **approved 2026-07-16** (user ruling: Plan A — wrapper-inject plan.md, mirror the review pattern) — entry brief below; runs BEFORE P4; objective-gated ⇒ delegation-eligible |
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

### P2 landing facts (for the P3a session)

- Mechanism as briefed: `PROMPTS_DIR = ORCH_DIR/"prompts"`, strict `{{var}}`
  substitution, code-side manifests (`PROMPT_MANIFEST`, `POSTCHECK_MANIFEST`
  in orchestrator.py), `prompts_error()` startup refusal (wired into
  `main()`; also validates orphan files and exercises `check_specs` across
  the role/mode matrix for the ID↔callable 1:1 both ways).
  `check_specs` now returns `(check-id, requirement, check)` triples and is
  a `@staticmethod`; requirement lines render via `contract_line(id, ...)`.
- P3a's doc-name/§ re-points now happen in TEXT FILES: grep
  `canonical/orchestrator/prompts/` for `ai-coding-` and `§` (e.g.
  `entry/review-rule-wrapper.md` carries the `ai-coding-review-v2.md`
  banner; contract lines cite `tasks-v2 §3`). Composition and problem-
  message strings stay in code — P3b's trim churns templates + manifests +
  the ~75 substring assertions together.
- WHITESPACE IS BYTE-SIGNIFICANT in templates (several fragments carry
  leading/trailing spaces) — see `prompts/README.md` rules before editing.
- `DISCUSSION_HINT`/`CLEAN_HOWTO` remain module constants (mock suite
  references them) but load at import from their template files.
- Templates deploy with the existing orchestrator bucket (recursive walk —
  verified `iter_deployment_items` rglob; no deploy.py change).

Semantic guards for P3a (from the audit — do not drop silently): (1) §10 End step 5
runs remaining-task reconciliation at EVERY session end — the session-end boundary
skill keeps it (closeout additionally repeats it); the handoff's earlier
session-end list was abbreviated, not a ruled change. (2) The three stop hooks'
wrap-up texts have drifted (only cursor carries the remediation-marker
instruction, `AUDIT-protocol-cut.md` HK-01) — single-sourcing from the
session-end skill resolves it; claude/codex behavior gains the marker line, which
matches the protocol docs. (3) Log-line formats and all task-file data shapes are
frozen (orch-hub + mid-flight tasks).

### P3a landing facts (for the P3b session)

All three semantic guards honored: session-end skill keeps per-session
remaining-task reconciliation (step 5); the three stop-hook wrap-up texts are
now byte-identical (claude/codex gained the remediation-marker line — HK-01
drift fixed); no log-line or data-shape changes.

- **New layout**: `canonical/{protocols,meta,workflow}/` → target
  `.ai-protocol/{protocols,meta,workflow}/` (12 docs). `CLAUDE.md` is the
  loader (verb→contract mapping + `@.ai-protocol/...` + `@.ai/` imports).
  Deleted: the five `ai-coding-*.md`, tasks-v2-tmp, automation-mode.md.
- **automation-mode split**: conduct half → `prompts/entry/conduct-annex.md`
  (new template, rendered into `automation-wrapper` by `_preamble`;
  `AUTOMATION_MD` constant gone); scheduling half → runbook §4.4 + the
  existing `midflight/blocked-resume` template. The annex still carries the
  End-discipline restatement (AUTO-02) and never-edit list — P3b trims.
- **deploy.py**: PAYLOADS + 3 buckets; normalization re-keyed
  `AI_CODING_V2_TARGET` → `CLAUDE_MD_TARGET` and scans the WHOLE file (same 4
  topics; non-topic variants still rejected). DELIBERATE plan deviation:
  gitignore block gains `/.ai-protocol/` but KEEPS `/ai-coding*.md` — targets
  deployed before the cut carry legacy files untracked; dropping the line
  mid-transition would dirty every target tree and brick their stop hooks.
  Drop it after post-P4 legacy cleanup (follow-up recorded below).
- **Hooks**: EAGER_FILES = CLAUDE.md + conduct + dev + taskfile + memory +
  `.ai/` set; injection label now `PROJECT PROTOCOL CONTEXT (ai-protocol)`
  (protocol.mdc + smoke_hooks updated to match); codex self-gate marker →
  `.ai-protocol/protocols/conduct.md` (both codex hooks); codex session-start
  "Cross-model review" choreography block replaced by one pointer line
  (HK-04) — the mapping arrives via the injected loader.
- **Orchestrator**: `REVIEW_RULE` → `.ai-protocol/protocols/review.md`;
  README slimmed — §3/§5/§6 content moved to runbook/rolemapping, but §
  NUMBERING retained (stubs + machine mechanics) so test docstrings/§-refs
  stay valid. Template edits were doc-name re-points only:
  `review-rule-wrapper` banner (now `===== BEGIN
  .ai-protocol/protocols/review.md =====`), dev-invocation, dev-remediation
  ("(dev contract, remediation mode)"), checklist-dev-est(+unknown),
  wrapup-note-advancement, six postcheck-contract lines ("tasks-v2 §3" →
  "the taskfile transition table"). Mock churn was ONE assertion (scenario 1
  review-rule anchor → wrapper banner) + `make_repo`/`patch_module` fixtures.
- **boundary-lint.sh** (scripts/): layout presence, dead `ai-coding-`
  references (lines carrying "legacy" or the gitignore glob are allowed),
  protocols/ purity (no "orchestr", no `§`, no dispatch vocabulary, no other-
  role session naming, no hook wiring, no skill slash-invocations), dangling
  `.ai-protocol/*.md` reference check, and `prompts_error()` template
  validation. Review.md avoids litmus-1 hits by using taskfile vocabulary
  ("work entries/work sessions") for the entries it evaluates.
- **Skills** re-pointed LAYOUT-NEUTRALLY (procedure text cites "the memory
  protocol §3/§4" etc. with the `.ai-protocol/` path as deployment note) so
  old-layout targets keep working until the P4 wave; globals synced
  (`aii-2 skills sync-claude-global`: 5 updated, ai-sync/ctd-tasks untouched).
- **Verified**: mock 30/30, pytest 25/25, boundary-lint OK; real-payload
  deploy to a scratch target (104 files; 12 `.ai-protocol/` docs; status
  in-sync; gitignore both lines; cursor+codex session-start hooks inject the
  new eager set and label; codex self-gate passes).
- **P3b trim surfaces** (litmus 4, from the audit): conduct-annex End section
  (AUTO-02) + scope-discipline residue (AUTO-03); ORC-02..06 templates
  (dev-remediation conduct restatement, dev-pre-re-est, wrapup* variants,
  violation-fix, closeout); postcheck-contract scheduling parentheticals
  ("your entry itself hands back to dev remediation", "the sole ai-sync
  trigger"); `wrapup-note-review`'s "the next review session continues the
  set"; hook texts trim to instantiation + pointer (HK-01/02/03); ~75 mock
  substring assertions churn in lockstep.

### P3b landing facts (for the P4 session)

Trim rule applied: a prompt/hook text carries the session's concrete values
and points at the owning doc; rules, procedures, entry shapes, and scheduling
consequences live only in their owners. Surfaces trimmed:

- **Templates**: `conduct-annex` End section → one line + session-end pointer
  (AUTO-02; round-trip warning already lives in `checks-preview-header`) and
  scope-discipline residue → conduct pointer (AUTO-03); `dev-invocation`
  trailing dispute/remediation restatement dropped (mode now cited as
  taskfile-derived per the contract); `dev-remediation` → mode declaration +
  group value (ORC-02); `dev-pre-re-est` → mode declaration + preReEst
  pointer (ORC-03); `review-independence` → run-fact only (separate
  conversations, possibly different model; no transcript) — procedure/budget
  restatement dropped, budgets are caller-counted (ORC-04 judgment);
  `wrapup` → instantiated tokens/budget/sid + wrap-up-procedure pointer, the
  three `wrapup-note-*` variants keep only role-local marker semantics
  (choreography tails dropped: "reviewed next", "before re-review", "the next
  review session continues the set"); `wrapup-plan-advancement` → slice-split
  pointer; **`wrapup-plan-remediation` DELETED** (pure dev.md restatement —
  manifest row removed, builder passes an empty plan note in the remediation
  branch); `violation-fix` → problems + session-end pointer + clean-howto
  (numbered End restatement dropped); `closeout` + `closeout-incomplete` →
  invocation, skill path, active count, audit-line requirement (procedure
  fields dropped) (ORC-05/06).
- **Postcheck contract**: scheduling parentheticals dropped — "re-review is
  triggered by your session-log entry", "advancement work is reviewed after
  every session", "the sole ai-sync trigger", "your entry itself hands back
  to dev remediation". Status menus stay (instantiations of the transition
  table).
- **Code problem messages** (`check_specs`): same two choreography clauses
  dropped; message content otherwise unchanged.
- **Hooks (HK-01/02/03)**: case1 close-out text → invocation + closeout-skill
  pointer + audit-line requirement (absorption/reconciliation description and
  "(required even when...)" dropped); case2a wrap-up → instantiated
  tokens/threshold/task-file + wrap-up-procedure pointer (marker/plan-slice
  rules and "The user will resume in a fresh session" dropped). All three
  wrap-up texts remain byte-identical; case1 differs only by the
  cursor/codex `SYNC_SKILL` read clause (claude has native slash commands).
  case1-dirty/case2b texts were already instantiation + pointer (P3a).
- **Zero mock-assertion churn**: the predicted ~75-assertion lockstep churn
  did not materialize — the suite's substrings anchor on instantiation-level
  text ("Wrap up NOW", "REMEDIATION SESSION", marker lines, menu values),
  which is exactly what a litmus-4 trim preserves. Gates: mock 30/30, pytest
  25/25, boundary-lint OK; hooks `bash -n` clean.
- **Deliberate deferral — ORC-07's prompt-trim half**: `plan-gate`,
  `plan-feedback`, and `approved-plan-gate` still restate `protocols/plan.md`
  (bounds, headings, revision shapes). Excluded here because (a) the ruled
  P3b surface list and the approved plan both omit them, (b) unlike
  review.md (wrapper-injected) plan.md is NOT in the assembled session
  context, so a trim needs an injection-mechanism decision first (mirror the
  review-rule wrapper?), and (c) scenario 6 guards the freshly-validated
  plan-gate flow. (RULED 2026-07-16: Plan A, wrapper injection — executed as
  P3c, entry brief below.)

### P3c entry brief (approved 2026-07-16 — plan-contract injection + trim)

User ruling: Plan A. The plan contract gets INJECTED into planning prompts the
same way the review contract is injected into review prompts; the three plan
templates then trim to caller-side instantiation + pointer. Resolves the
ORC-07 deferral recorded in the P3b landing facts.

- **Mechanism**: `PLAN_RULE = REPO / ".ai-protocol" / "protocols" / "plan.md"`
  (sibling of `REVIEW_RULE`); new template `entry/plan-rule-wrapper.md`
  mirroring `entry/review-rule-wrapper.md` exactly in shape (BEGIN/END banner
  naming `.ai-protocol/protocols/plan.md`, `{{plan_rule}}` placeholder,
  manifest row); rendered into the plan-gate composition AHEAD of the gate
  instruction. `make_repo()` already copies canonical protocol docs into the
  mock repo; `patch_module()` must override `PLAN_RULE` like `REVIEW_RULE`.
  No deploy.py change (templates ship with the orchestrator bucket; plan.md
  ships with protocols).
- **Trims**:
  - `entry/plan-gate.md` → caller-side only: the PLANNING-ONLY gate
    declaration, the read-only-shadow framing (naming the upcoming dev
    session is caller vocabulary), "do not execute the normal entry
    checklist yet", a pointer at the injected contract, and the rev-1
    capture note ("everything from `## Goal / Acceptance` … captured as
    plan-report rev 1 … the only planning output delivered once the human
    confirms"). Bounds detail, command list, heading list, `None identified`
    arrive via the injected plan.md.
  - `midflight/plan-feedback.md` → feedback value + still-PLANNING-ONLY +
    the two revision shapes BY NAME (revise = complete report from
    `## Goal / Acceptance`; unchanged = exact `PLAN-REPORT: unchanged`
    line) + "only the report is delivered onward" + stop-and-wait. Shape
    detail lives in the contract.
  - `entry/approved-plan-gate.md` → slim the caller framing (the
    guidance-and-constraints sentence is duplicated today); keep
    "APPROVED PLAN GATE", the `Human ruling:` / `Approved plan-report:`
    labels, and the checklist/preReEst-applies note (caller-side).
- **Contract addition**: the two parser-critical reply-shape caveats move
  INTO plan.md §Revision protocol — "do not start a line with
  `## Goal / Acceptance` except to deliver the full report" and "never
  include the `PLAN-REPORT: unchanged` line in a revision". They are output-
  contract properties (charter rule 7), not per-session values.
- **Guards**: do NOT regress the plan-gate flow semantics (scenario 6 is the
  guard): rev capture from `## Goal / Acceptance`, unchanged sentinel,
  warn-and-keep, confirm delivering ONLY the current report + ruling (no
  conversation history). Scenario 6 assertions churn in lockstep where they
  anchored on restated text (e.g. "Do NOT run tests/builds" → plan.md's own
  phrasing); assertions on contract substrings keep passing via the injected
  text. Log-line formats and `sessions.json` frozen (orch-hub).
- **Docs**: `workflow/rolemapping.md` plan-gate composition row gains the
  injected plan contract; prompts/README's layout bullet already covers doc
  wrappers.
- **Gate**: mock suite + pytest + boundary-lint green; single commit; flip
  the P3c status row and append P3c landing facts.

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
- Post-P4 cleanup (after every target is on the new layout): delete the
  legacy `ai-coding-*.md` files at each target, then drop the transitional
  `/ai-coding*.md` gitignore line, the conduct-annex/init "(legacy)"
  mentions, and the ai-init exclusion — one small landing.
- ~~Plan-prompt trim (ORC-07 second half, deferred at P3b)~~ — RULED
  2026-07-16 (Plan A: wrapper-inject plan.md like review, then trim) and
  scheduled as P3c before P4; see the P3c entry brief.

## Key invariants to preserve

- Protocol works standalone; orchestrator = enforcement + attention
  amplification, never a replacement.
- Task file = ground truth; orchestrator NEVER writes it; all dispatch
  derives from it, re-parsed each iteration.
- Each rule lives once; prompts instantiate rules with current values,
  never restate them.
- Terminal output status-level; verbosity to log files.
- Canonical repo is the only edit surface; targets receive deploys.
