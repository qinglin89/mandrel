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
| P3c | Plan-contract injection + plan-prompt trim (ORC-07 second half; scenario 6 churns) | **landed 2026-07-16** — `PLAN_RULE` + `entry/plan-rule-wrapper` injected ahead of the gate instruction (review-pattern mirror); three plan templates trimmed to instantiation + pointer; the two reply-shape caveats moved into plan.md's Revision protocol; scenario-6 churn: 4 assertions re-anchored + 1 wrapper-banner assertion added; mock 30/30 + pytest 25/25 + lint green |
| P4 | Live smoke on a `quantx-bak-*` repo + one-wave target deploys (user-run) | **deterministic half GREEN 2026-07-17** in an isolated sandbox (29/29 checks — see P4 sandbox smoke below); remaining: live LLM drill (optional) + the real-target wave (user-run) |
| P5 | Context-assembly symmetrization (P5a) + protocol-voice pass (P5b, interactive) | **P5a landed 2026-07-17** — landing facts below; dev.md split (base + 2 mode adds), eager substrate purified, two-slot dev wrapper injection, `/invoke` skill, charter rule 12, eager-purity lint; mock 30/30 + pytest 25/25 + lint + sandbox smoke 30/30 green. **P5b closed 2026-07-17** — conduct.md header re-voiced; **dev contracts re-merged per-mode, base retired** (single-slot injection; landing facts below); the remaining voice pass (review/plan/intake + meta) deliberately NOT scheduled — user ruling: the system works, protect product time; reopen only on felt friction. Deferred design annex below. Mock 30/30 + pytest 25/25 + lint + sandbox smoke 30/30 green |
| P6 | `meta/memory.md` read/write split (write side leaves the ambient channel) | **defined 2026-07-17** (user-ruled: memory ONLY; taskfile explicitly not split) — entry brief below; objective-gated ⇒ delegation-eligible; independent of P5 internals; recommended before the P4 wave |

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

### P3c landing facts (for the P4 session)

Executed as briefed (Plan A — wrapper injection mirroring the review rule):

- **Files**: `orchestrator.py` (`PLAN_RULE` sibling of `REVIEW_RULE`;
  `entry/plan-rule-wrapper` manifest row; wrapper rendered inside
  `_plan_gate_turn` between the dev base prompt and the gate instruction);
  new `prompts/entry/plan-rule-wrapper.md` (same BEGIN/END banner shape as
  the review wrapper); `plan-gate` / `plan-feedback` / `approved-plan-gate`
  trimmed; `protocols/plan.md` Revision protocol gained the two reply-shape
  caveats ("do not start a line with `## Goal / Acceptance` except to
  deliver the full report"; "never include the `PLAN-REPORT: unchanged`
  line in a revision reply"); `workflow/rolemapping.md` plan-gate row now
  lists "plan contract text"; mock fixtures (`make_repo` copies plan.md,
  `patch_module` overrides `PLAN_RULE`).
- **Judgment calls**: (1) two scenario-6 contract substrings could not
  stand via injection because plan.md wraps/phrases them differently — the
  command list wraps mid-list, so "`rg`, `sed`, `ls`" re-anchored on the
  contiguous "`sed`, `ls`, `git show`", and "Do NOT run tests/builds"
  re-anchored on the contract's "run tests/builds, start services".
  (2) `approved-plan-gate` also dropped its closing "This is now the formal
  dev session: execute the normal entry checklist…" sentence — it restated
  the entry checklist the same composed prompt instantiates; the
  checklist/preReEst-ownership note stays. (3) `plan-feedback`'s bounds
  restatement became the pointer "the plan contract's read-only bounds
  still apply"; its two revise-turn assertions re-anchored on that pointer
  and on "REPLACES the current plan-report".
- **Assertion churn**: 5 lines, all in scenario 6 — 4 re-anchored (2 in
  propose_plan, 2 in revise_plan) + 1 added (plan wrapper banner, analogous
  to scenario 1's review-banner anchor). Flow-semantics assertions (rev-1
  capture, unchanged sentinel, warn-and-keep, report+ruling-only delivery
  with no history leakage, `Human ruling:\nconfirm` rendered shape) stand
  unchanged.
- **Gates**: mock 30/30, pytest 25/25, boundary-lint OK.

### P4 sandbox smoke (2026-07-17 — deterministic half, isolated copy)

User-ruled: verify everything automatable in an ISOLATED copy, never touching
the real targets. Sandbox: local clone of `quantx-bak-0713-before-
riskcontrolnotify` + its untracked `.ai-tasks/` + legacy `ai-coding-*.md`,
deployed with a THROWAWAY registry (`AI_NATIVE_DEPLOYMENT_REGISTRY` override —
real registry/targets/globals untouched). Re-runnable driver:
`<scratchpad>/p4-sandbox/p4_smoke.py`. 29/29 checks GREEN:

- **Deploy/layout**: `aii-2 deploy` + `status` in-sync; 12 `.ai-protocol/`
  docs; gitignore carries `/.ai-protocol/` + transitional `/ai-coding*.md`;
  CLAUDE.md is the loader; orchestrator bucket ships `prompts/` + postcheck
  contract; deploy diff confined to deploy-owned tracked paths.
- **Hook I/O on the deployed copies**: cursor+codex session-start inject the
  labeled context (new eager set; codex self-gate passes); stop-hook case
  matrix (3 tools × case1 / case1-dirty / case2a / case2b) emits the trimmed
  texts — closeout-skill pointer + `Remaining-task audit:`, session-end
  wrap-up pointer, no choreography tails — and allows a completed handoff.
- **Deployed-layout orchestrator (unpatched paths — what the mock patches
  away)**: `REPO` resolves to target root; `prompts_error()` None on the
  deployed template set; `REVIEW_RULE`/`PLAN_RULE` resolve under
  `.ai-protocol/protocols/`; real dev/review/plan-gate prompts compose on a
  real task (protocol block via the deployed session-start hook; review +
  plan contracts wrapper-injected).
- **orch-hub freeze proven end-to-end**: zero `self.log(` emit calls changed
  `4be85f2..HEAD`.

**Operational note for the wave**: on targets that TRACK deploy-owned files
(this bak tracks the lockfile, `.gitignore`, `.claude/` hooks) the deploy
legitimately modifies them — COMMIT the deploy diff at each target before
running any session, or the stop hooks will block on the dirty tree.

**Remaining for P4 (genuinely non-deterministic / user-run)**: (1) optional
live drill — orchestrator `--once` + one plan-gate round with a real
claude/codex CLI session against the sandbox (burns real tokens; sandbox is
ready for it); (2) the one-wave deploys to hkchain / orch-hub / quantx +
committing each deploy diff (user-run per repo convention; globals already
layout-neutral since P3a). **P5a re-run**: driver updated to P5a expectations
(A3 doc count 14 + new A3b split-files check, A6/B1 flipped to
substrate-only, C2 71 templates, C4 dev base+exactly-one-add wrappers) —
30/30 GREEN 2026-07-17. Wave note: deploy never prunes, so targets deployed
pre-P5a keep a stale `.ai-protocol/protocols/dev.md` — remove it once per
target (the driver encodes this as its A3 pre-step).

### P5 entry brief (defined 2026-07-17 — assembly symmetrization + protocol-voice pass)

Rulings from the 2026-07-17 discussion (user):

- **dev.md rides the wrong channel.** It is the only role contract in the
  eager set — second-person imperative text ambient in NON-dev sessions.
  Live specimen: a re-review session (latest verdict `changes-requested`)
  carries dev.md's "while the latest verdict is changes-requested, only
  remediate" — a condition-true, misdirected instruction, suppressed only by
  activation-layer specificity (a compliance reliance the cut eliminates
  elsewhere). Size for context: dev.md ≈1.2k tokens (~0.6% of budget) — the
  cost is awareness/purity, not attention.
- **Channel principle (to land in CHARTER.md §Rules at P5a)**: imperative /
  second-person text travels ONLY on the activation channel; the ambient
  (eager) channel carries only universally-applicable conduct and declarative
  substrate (schemas, memory contract, indexes, project knowledge).
- **Caller delivers contracts.** Manual mode's caller is the human
  (charter: human-as-orchestrator): pasting the contract or invoking a skill
  is byte-equivalent to orchestrated wrapper injection. Precedent: intake is
  already a role packaged as a skill for invocation (charter rule 6).
  Interactive lazy-read ("loaded on demand") is a weak compliance channel to
  be RETIRED, not a candidate.
- **Protocol docs still carry conversation/design residue** — not
  "protocol-voiced" enough. Calibration specimens (review.md): (a)
  "Self-contained: inputs → evaluation → declared outputs" — architecture
  self-description (the charter's pure-function model) fed to the session;
  design-model language belongs to the maintainer layer, not contract text.
  (b) "## Convergence — To keep the finding-remediation loop bounded:" —
  workflow-motive narration naming another role's mode. Nuance (user-ruled):
  cross-role naming is NOT absolutely banned — allowed where it genuinely
  helps describe THIS role's action (data input/output descriptors); avoided
  as narrative/rationale.
- **dev.md splits by mode** (user-proposed 2026-07-17; boundary verified
  clean): `dev-base` (inputs, work conduct, entry-shape outputs) +
  `dev-add-advancement` (preReEst, slice discipline, advancement status
  menu, no-marker) + `dev-add-remediation` (findings-as-claims, dispute
  recording, status-unchanged, continuation marker, no-preReEst). The
  operational layers are ALREADY mode-split — postchecks
  (`dev-advancement-status` / `dev-remediation-status` /
  `dev-no-continuation-marker`), entry templates (ADVANCEMENT/REMEDIATION
  blocks), wrap-up note variants — the contract file is the only unsplit
  layer; Declared outputs split along the postcheck contract's existing
  line. **Mode selection dissolves**: the caller certifies the mode
  (orchestrator already computes `was_remediation` and declares it in the
  entry prompt; predicates stay single-sourced in `meta/taskfile.md` §3 +
  rolemapping; `/invoke dev` encodes the same predicate) — the contract no
  longer teaches self-derivation, which also deletes the "Your mode
  derives…" ambient-imperative specimen. "Do not mix modes" survives as
  each add's own scope restriction (already present in both texts).
  Bonuses: the plan shadow sheds the remediation half entirely, and a
  DEMONSTRATED bleed disappears (the no-marker postcheck + mock scenario 12
  exist precisely because advancement sessions crib the ambient marker
  rule). Cost acknowledged: a human reading "the whole dev contract" now
  reads base + add (mitigate with pointer lines in base).

**P5a — context-assembly symmetrization** (objective-gated):

- Eager substrate := loader (CLAUDE.md) + `protocols/conduct.md` +
  `meta/taskfile.md` + `meta/memory.md` + `.ai/` eager tier +
  `.ai-tasks/index.md`. dev.md LEAVES the two session-start `EAGER_FILES`
  and the CLAUDE.md import block.
- The dev split lands here MECHANICALLY (P2 philosophy: content-preserving
  moves, no rewording — the voice rewrite is P5b's): `dev.md` →
  base + two mode adds (file layout — `protocols/dev/` dir vs `dev-*.md`
  siblings — is a P5a design point; path re-points ride the same files P5a
  touches anyway).
- Contract delivery = caller at activation, ALL roles: orchestrated dev gets
  a TWO-SLOT injection — dev-base wrapper + mode-add wrapper selected by the
  caller's existing `was_remediation` predicate (mirror of the review/plan
  wrappers). Plan-gate composition then carries dev-base +
  dev-add-advancement + plan automatically (base_prompt = dev_prompt;
  remediation never gates) — record the shadow's dev dependency in
  rolemapping. Review path unchanged (already injected). Interactive
  delivery = `/invoke <role>` skills reading the deployed contract files
  (intake pattern; `/invoke dev` applies the mode predicate; one
  parameterized skill vs separate skills = P5a design point); paste
  documented in runbook §6 as the equivalent fallback. Loader keeps the
  verb→contract mapping (dispatch surface) but points at caller delivery —
  "loaded on demand" retired.
- boundary-lint += eager-purity check (EAGER_FILES + CLAUDE.md imports carry
  no `protocols/` contract other than conduct.md); per-file role-naming
  purity pairs updated for the split dev filenames.
- Template wording follows (`dev-invocation`'s "in your protocol context" →
  "above"; `preamble-native-note` re-scoped to substrate). Mock churn
  expected small; re-run the P4 sandbox smoke (update its C4 to expect the
  dev base+add wrapper banners). Per-tool hardening (deterministic
  verb-detection prompt hooks where a tool supports them) = optional
  follow-up, NOT spec.

**P5b — protocol-voice pass** (interactive with the user; never delegated):

- Scope: the `protocols/` contracts (conduct; dev as its three post-split
  files — base + two mode adds; review; plan; intake), walked ONE AT A TIME
  with the user — present findings + proposed rewrite, user rules, land.
  `meta/` three swept with the same lens afterwards (expected lighter —
  schema register is already spec-like).
- Register target: an operative spec addressed to the working session —
  definitions, requirements, procedures, output shapes. Remove: architecture
  self-description, workflow-motive narration, design-provenance asides.
  Rationale lives in CHARTER/audit/handoff (maintainer layer, not deployed).
- Churn to expect: review.md/plan.md are wrapper-injected, so mock substring
  assertions re-anchor (scenario 6 asserts plan.md phrasing); boundary-lint
  purity patterns re-checked (patterns may need updating alongside
  rewrites); postcheck contract lines unaffected (they cite taskfile).

**Sequencing**: recommended P5a → P5b → P4 wave (one deploy ships final
assembly + final texts; the optional live drill is also better spent after
P5). The P4 sandbox smoke re-runs after each landing (cheap).

### P5a landing facts (2026-07-17 — for the P5b session)

Both design points settled and landed:

- **File layout = flat siblings**: `protocols/dev-base.md` +
  `protocols/dev-add-advancement.md` + `protocols/dev-add-remediation.md`;
  `protocols/dev.md` DELETED (not kept as the base's name — stale references
  fail loudly via lint check 4, and a new layout check errors if dev.md ever
  reappears). Matches the suite's flat convention (P6's memory-write sibling
  will follow the same shape).
- **Interactive delivery = ONE parameterized skill** `invoke`
  (`/invoke <role> <task-id>`, role ∈ dev|review|plan): reads the deployed
  contract files; for dev applies the taskfile §3 predicate and reads base +
  exactly one certified add. Lives in `skills-backup/invoke/` +
  `MANAGED_SKILLS` (skills.py); globals resynced (`aii-2 skills
  sync-claude-global` → "add invoke"). Paste fallback documented in runbook
  §6. Intake keeps its existing `/intake-task` packaging.

Split mechanics (content-preserving; the voice rewrite is still P5b's):

- The `## Mode selection` section DISSOLVED as ruled: the predicate lives
  only in taskfile §3 + rolemapping; the caller certifies the mode
  (orchestrator `was_remediation`; `/invoke dev` encodes the same predicate).
  Each mode's one-line definition became its add's opening line; "do not mix
  modes" survives as the adds' existing scope lines (advancement: never
  writes the marker; remediation: no preReEst / no scope advance) — no new
  rule text was needed. Declared outputs split along the postcheck line:
  base keeps the generic entry + status-declaration pointer; the advancement
  add carries the advancement status vocabulary; the remediation add already
  carried status-unchanged + dispute + marker lines.
- Orchestrator two-slot injection mirrors review/plan: constants
  `DEV_BASE_RULE` / `DEV_ADVANCEMENT_RULE` / `DEV_REMEDIATION_RULE`; three
  wrapper templates (`entry/dev-base-wrapper`,
  `entry/dev-add-{advancement,remediation}-wrapper`) injected in
  `dev_prompt` ahead of `entry/dev-invocation`. The plan gate's base prompt
  IS dev_prompt, so the gate carries base + advancement add automatically
  (remediation never gates) — dependency recorded in rolemapping's
  composition table.
- Template wording churn: `dev-invocation` → "per the dev contract above
  (base + mode add)" (self-derivation clause deleted);
  `preamble-native-note` re-scoped to substrate ("follow it together with
  the contract text delivered in this prompt"); `dev-pre-re-est` /
  `dev-remediation` now point at "the … add (above)". The
  ADVANCEMENT/REMEDIATION SESSION prefixes and `{{group}}` instantiation are
  unchanged.
- Eager substrate purified: dev import dropped from `repo-root/CLAUDE.md` +
  both session-start `EAGER_FILES`; `protocol.mdc` fallback list and both
  tools' skill lists updated (+`/invoke`); loader verb rows now state caller
  delivery — "loaded on demand" retired everywhere. Charter gained **rule 12
  (channel principle)** with the caller-certifies-mode corollary.
- boundary-lint: layout list carries the three dev files + errors if dev.md
  reappears; role-naming purity pairs cover all three; NEW check 3b
  eager-purity (session-start EAGER_FILES arrays incl. `+=` additions +
  CLAUDE.md `@`-imports may carry no `protocols/` file other than
  conduct.md) — negative-tested (a planted dev-base eager line fails the
  lint).
- Mock churn as predicted (small): `make_repo` copies the three files,
  `patch_module` patches the three constants, and scenarios 11/12 gained
  banner assertions (remediation prompt: base + remediation-add banners
  present, advancement-add banner ABSENT; scenario 12 the mirror). Zero
  edits to pre-existing assertions. NOTE: negative assertions must target
  the `===== BEGIN …` banner form — the base contract's pointer line names
  both add PATHS, so a bare-path `not in prompt` is always false.
- pytest untouched (its canonical tree is a synthetic fixture; its
  `dev.md` is fixture data, not a suite reference).
- Gates at landing: mock 30/30, pytest 25/25, boundary-lint OK, P4 sandbox
  smoke 30/30 (driver lives in the P4 session's scratchpad,
  `…/6c58ad49-…/scratchpad/p4-sandbox/p4_smoke.py`; /tmp is ephemeral — the
  rebuild recipe is in the P4 sandbox smoke section above).
- For P5b: the protocols walk is now over SEVEN files (conduct, dev-base,
  dev-add-advancement, dev-add-remediation, review, plan, intake); dev texts
  moved but were not re-voiced. Wrapper-injected docs whose rewrites churn
  mock substrings now include the three dev files (scenarios 11/12 banner
  asserts are path-anchored only — content rewrites churn nothing there;
  review.md/plan.md caveats from the P5 brief unchanged).

### P5b outcome + dev per-mode merge (landed 2026-07-17)

P5b ran interactively as briefed; after two files the register findings
escalated into structural design and the user cut scope to protect product
time (the full entry-prompt renders showed the suite self-consistent and
working — "the short pole is no longer in the system"). Outcomes:

- **conduct.md**: header re-voiced to precedence-only wording ("Contract
  text delivered at invocation and project-specific rules may extend
  them") — finding C1. The §7 intake path reference stays: the one
  sanctioned mid-session contract read (a data-conformance procedure,
  charter rule 6; intake is never in any session's assembled prompt).
- **Dev contracts re-merged, base retired** (user-ruled: base=common /
  add=unique means plain concatenation is the whole contract; the
  stitching sentences were caller vocabulary inside contract text):
  `protocols/dev-advancement.md` + `protocols/dev-remediation.md` are
  content-preserving fusions of base + each add. dev-base.md and both
  dev-add-*.md DELETED; lint errors if any of the four legacy dev
  filenames reappears. Ruled semantic deltas beyond the verbatim fusion:
  the three stitching sentences dropped; base's status-pointer bullet
  fused with each mode's own vocabulary (advancement: full menu inline;
  remediation: status-unchanged line); remediation does NOT inherit
  base's adjust-body/scope/est or est-calibration bullets (they
  contradict its no-scope-advance rule); the user's interim hand-edits
  to the old advancement add were reverted as redundant restatements.
- **Orchestrator**: single-slot injection — DEV_BASE_RULE gone,
  `DEV_{ADVANCEMENT,REMEDIATION}_RULE` re-pointed; new wrapper templates
  `entry/dev-{advancement,remediation}-wrapper` ({{dev_rule}}), the three
  old wrappers deleted; `dev-invocation` drops "(base + mode add)";
  pre-re-est / dev-remediation templates now say "(dev contract, above)".
  The plan gate composes dev-advancement + plan (rolemapping composition
  table updated; remediation still never gates).
- **Re-points**: loader verb row, rolemapping verb+composition tables,
  runbook §5/§6, codex README, orchestrator README, `/invoke` dev branch
  (predicate selects exactly one file; globals resynced), boundary-lint
  (layout list, legacy-name errors, purity pairs; check 4 gained
  `-I --exclude-dir=__pycache__` — a recompiled .pyc had started
  matching), mock (copy list, patched constants, scenarios 11/12
  single-banner asserts — the old banner-form workaround note is moot).
- **Deployed-target migration fact**: incremental deploy never removes
  files — pre-merge targets keep the three old contracts + three old
  wrapper templates until removed once per target (the stale templates
  make `prompts_error()` refuse startup, so the miss is loud, same
  pattern as the P5a dev.md note). The P4 smoke encodes this one-time
  cleanup; driver updated (A3 count 14→13, A3b/A6/A8/C4 re-anchored).
- Gates at landing: mock 30/30, pytest 25/25, boundary-lint OK, sandbox
  smoke 30/30.

### P5b design annex (user-ratified in-session; deliberately not executed)

Register rules for any future contract-text pass, ratified 2026-07-17:

1. **Channel register**: activation-channel contracts speak second-person
   operative ("You are invoked to…"); ambient substrate stays declarative.
   (Rule 12 quarantines imperatives to the activation channel; this fixes
   the register WITHIN it. The remediation contract already partly does.)
2. **Owner dedup**: contract text carries only role-unique content;
   anything owned by ambient substrate or caller instantiation is a
   restatement — bind by reference, never restate. Verified owner table
   for the old dev-base (every deleted sentence had a live owner):
   composition → loader + banners; est semantics / entry timing / field
   glosses → taskfile lines 27 / 83 / 104–116; `.ai/` write bans → memory
   §1; epistemics / tiers / scope → conduct §2/§6/§7; claim/est/prefetch
   → caller checklist. Two rules found homeless mid-analysis (would need
   owners before any such cut): "do not grep across `.ai/`" and the
   `.ai/`-gap→Done routing — natural owner memory.md §2/§1.
3. **Reference forms**: path references double as in-context anchors
   (banners label docs by path) — paths stay canonical everywhere; the
   title-reference experiment was rejected. Paths to non-ambient files
   (intake.md, `.ai-tasks/<id>.md`) are true read targets.
4. **Prompt-unification sketch (L3, not scheduled)**: the loader's
   workflow prose could leave the orchestrated channel's injection;
   contract + invocation could merge into one seamless second-person
   text. Load-bearing details: the invocation line carries the task-id
   datum (must survive any merge); banners are provenance + mock/path
   anchors (their removal churns tests for the first time).
5. **Chronological contract form (user sketch, the preferred end-state
   for a future pass)**: each mode contract as a temporally ordered
   walkthrough (enter → work → wrap), stating one core workflow + key
   emphases; review.md's Procedure is the existing precedent (claim
   already lives contract-side there). Open tensions if pursued:
   end-sequence ownership (charter rules 9/10 vs contract-carried
   chronology — the sequence already reaches every session via
   POST-SESSION CHECKS / stophook skill / annex) and preReEst-vs-claim
   ordering.

### P4 live drill findings (2026-07-17, orch-hub-test, hub-orchestrated)

First live run (task 2026-07-17-operations-overview-dashboard, cc-codex
backends, plan-gated) surfaced two findings:

1. **Claim-sid transcription drift** (fixed, landed): the dev session
   re-typed its 36-char session id into `claimed-by` and drifted 2 hex
   digits — despite the prompt carrying the exact instantiated claim line.
   The net caught it only INDIRECTLY (the entry heading shared the same
   wrong sid, so `session-log-entry` failed at wrap-up and the session
   corrected both); a wrong claim + correct entry would have passed
   everything. Landed: **`claim-sid` post-check** (universal, dev+review —
   `claimed-by`'s sid part must equal the session's id character-exact;
   backend-agnostic, rides the checks preview + the manual postcheck
   contract). Mock: `claim()` conformance helper (+`bump_est(p, agent)`
   claims), scripted behaviors updated, scenario 12 now exercises the
   violation→followup→fix loop for the claim too. All gates green (mock
   30/30, pytest 25/25, lint, sandbox smoke 30/30).
2. **`Remaining-task audit:` persisted in a review entry** (diagnosed;
   fix awaiting ruling): the codex reviewer faithfully obeyed
   taskfile.md §Markers ("emitted by session-end bookkeeping and by
   close-out") — wording ADDED AT P3a (commit 2b4ea70; the legacy system
   had no such line). But every consumer (3 stop hooks, closeout entry
   template, closeout-incomplete followup, runbook §4.7, mock) verifies
   the line ONLY in the close-out's final RESPONSE, and session-end.md
   requires the reconciliation ACT without any report line. Per-session
   persistence is therefore consumer-less ceremony from over-broad P3a
   wording. **Ruled + landed 2026-07-17**: review sessions change no facts
   (declarations only affect the current task), so (a) session-end §5
   reconciliation is scoped to WORK sessions — review skips it; (b) the
   taskfile §Markers bullet for the audit line is DELETED outright — it
   was never a task-file line; closeout.md owns the report line in the
   close-out response (rule 11 restored). Zero consumer churn (all
   consumers were closeout-side already); the closeout chain
   (closeout.md / ai-sync-v2 / entry template / scenario 22) is
   untouched.

3. **Marker prose false-positive → runaway remediation loop** (fixed,
   landed 2026-07-18): the remediation session narrated compliance in its
   Done — "…reviewable unit (no `Handoff: continuation`)" — and the
   orchestrator's UNANCHORED substring parse (pre-cut code imported with
   the orchestrator, predates P0) read the mention as the marker: fix set
   deemed open, re-review deferred, a spurious continuation dev session
   dispatched. The loop then SELF-REINFORCED: each spurious session read
   the growing log, imitated the previous entry's closing formula
   near-verbatim (7 entries / 7 copies of the phrase, sessions 3+ verbatim
   — in-context imitation, not independent protocol reasoning), and its
   own clarification ("definitely no continuation") re-armed the
   misparse. 6 spurious sessions before manual stop.flag; work output
   never corrupted. **User-ruled fix (both ends strict + channel move)**:
   the marker LEFT the entry body entirely — new frontmatter field
   `fix-set: open | complete` (absent = complete), declared only by
   remediation sessions; entry prose is now structurally inert for
   dispatch. Write matrix enforced deterministically: `fix-set-value`
   (universal — present ⇒ exactly open|complete) + `fix-set-closed`
   (advancement + review — never `open` at session end; replaces
   dev-no-continuation-marker) post-checks; orchestrator dispatches on
   `task.fix_set` (same WARNING guard for open-without-remediation);
   remaining entry fields (Verdict / Group / Dispute-unresolved)
   line-anchored to the schema's `- X:` list-line grammar, and taskfile
   §Markers now states the grammar ("a prose mention is not a
   declaration"). Re-points: dev contracts, review.md step 3 (open fix
   set ⇒ report and stop), session-end wrap-up variant, wrap-up note
   templates (advancement note re-voiced positive — the old "do NOT
   write X" phrasing was itself teaching sessions the fatal string),
   runbook §2/§3, rolemapping input+attach tables, orchestrator README,
   charter rule 2 example. Mock: set_fix_set helper; scenarios 10/11
   drive the flag (incl. a prose-mention-stays-inert entry); scenario 12
   exercises illegal-open → typo-value → clean across three followups.
   Data-shape note: first post-cut frontmatter addition (scope-guard
   freeze lifted by the user's ruling); absent = complete keeps every
   archived/mid-flight task parsing unchanged.

### P6 entry brief (defined 2026-07-17 — memory read/write split)

User ruling (2026-07-17 discussion): split `meta/memory.md` along its
read/write axis; **taskfile.md is explicitly NOT split** (see below). Third
application of the channel principle: charter rule 8 (meta read/write
asymmetry) is already law and the operational layer already matches (write
procedures are consumed only by skill invocations) — the doc layout follows.

- **Shape (minimal churn)**: `memory.md` KEEPS its name, eager lines, and
  imports — it retains §1 invariants (incl. the asymmetry statement) + §2
  loading contract + the staleness-interpretation semantics (readers judge
  trust by `last-updated`/`verified-against`). EXTRACTED:
  `meta/memory-write.md` (final name = execution design point; e.g.
  `absorption.md`) carrying §3 Admission tests + §4 Maintenance
  (trigger/propagation/size/upgrade) + §5's authoring rules
  (tables-over-prose, frontmatter authoring, bump rules). Line-level split
  of §5 is an execution judgment; moves are content-preserving (P2
  philosophy — the voice pass, if any, is P5b's business).
- **Delivery vehicle**: not a prompt wrapper — the closeout / housekeeping /
  init SKILLS are the activation channel; they Read the write doc at
  invocation (today they cite "the memory protocol §3/§4" layout-neutrally —
  re-point to the new file).
- **Re-point surface**: `workflow/skills/closeout.md` (§3/§4 refs),
  skills-backup `ai-sync-v2` / `ai-housekeeping` / `ai-init` (+ global
  skills resync), boundary-lint layout list (+1 file), deploy doc count
  13→14 (counts updated for the P5b per-mode merge; the brief predated
  it), mock
  `make_repo` copy list, P4 sandbox smoke A3 count. Zero
  EAGER_FILES / CLAUDE.md-import churn by construction.
- **Gain**: §3–§5 ≈ 60% of the file ≈ ~800 tokens leave every session's
  ambient context; absorber-facing procedural text exits the ambient
  channel.
- **taskfile.md — ruled NOT split**: the task file is the shared data
  plane — role declared-outputs ARE writes (claim, entries, status, body
  adjustments) and review reads what dev writes; the schema (frontmatter,
  transitions, entry shapes, markers) is the shared alphabet of both sides.
  A read/write split would duplicate shape definitions across two docs —
  re-creating the entry-shape smear P3a cured. Optional micro-cut recorded,
  NOT scheduled: §7 lifecycle + the closeout-operation semantics inside
  §6/§8 (~200 tokens) could move to the closeout skill spec.
- **Gate**: mock + pytest + boundary-lint + sandbox-smoke re-run; single
  commit; flip this row and append landing facts.

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
