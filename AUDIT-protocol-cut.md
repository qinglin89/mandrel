# Audit: protocol/workflow leak inventory (cut P1)

Swept 2026-07-16 against the `CHARTER.md` litmus tests. This is the execution input
for P3a/P3b — every disposition names its destination in the new layout
(`protocols/ workflow/ meta/` canonical; `.ai-protocol/` umbrella at targets;
templates under `canonical/orchestrator/prompts/`). Line numbers are as of commit
`4be85f2`.

**Classes** — A: narrative leak (delete/reword) · B: workflow rule living in a role
doc (relocate to runbook/rolemapping) · C: protocol datum missing a role-local
definition (write it) · D: prompt/hook restating rules (trim to instantiation +
pointer). Items marked **R** are re-homes: content sound, wrong layer — moved, not
reworded. Melt = split one passage into a role-local half (stays) and a scheduling
half (workflow).

**Phase tags**: [P3a] = lands with the doc cut; [P3b] = prompt/hook content trim;
[P2] = externalization only (byte-identical — no content judgment applied in P2).

---

## 1. ai-coding-v2.md → protocols/conduct.md + protocols/dev.md + meta + workflow

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| V2-01 | §1–§5, §7 | R | Clean general conduct (preferences, reasoning, API safety, architecture, disagreement, authority tiers) → `protocols/conduct.md` verbatim. §6 review taxonomy → `protocols/review.md` (it is review-role vocabulary; dev cites it only via findings-as-input). [P3a] |
| V2-02 | §8 (62–75) | R | Memory-system pointer + eager `@.ai/` imports → import block moves to target `CLAUDE.md` (loader); the described contract lives in `meta/memory.md` §2. Deploy normalization re-keys to CLAUDE.md (deploy.py:129–164). [P3a] |
| V2-03 | §9 (76–84) | A/B | "Tasks are the pipeline by which new knowledge enters memory: `/ai-sync-v2` reviews completed tasks at the Stop hook, absorbs…then archives" — trigger choreography + skill naming in the loader doc. Datum ("completed tasks are absorbed then archived at close-out") → `meta/taskfile.md` lifecycle; trigger wiring → `workflow/runbook.md` + hook layer. [P3a] |
| V2-04 | §10 Entry (88–99) | B | Entire Entry block (pick task, claim shape, est bump, prefetch) = entry bookkeeping → dissolves into the caller-assembled entry prompt (template `entry-checklist`) per charter §Boundary. Claim/est data shapes → `meta/taskfile.md`. The no-ad-hoc-work rule ("If no existing task fits new work…ad-hoc work bypassing this is not allowed") is role conduct → `protocols/conduct.md`, referencing the intake CONTRACT (a protocol asset), not the skill invocation. [P3a] |
| V2-05 | §10 Work (103–104) | B | "Do not edit `.ai/` mid-task. Snapshot writes go only through `/ai-sync-v2` at close-out" — melt: role-local half ("no mid-task `.ai/` edits; a gap noticed is a truth learned → record in Done") stays in `protocols/dev.md`; who-writes-when → `meta/memory.md` access contract (charter rule 8). Routing-only reads stay role-local (meta read contract). [P3a] |
| V2-06 | §10 Work (106) | B | "New work discovered mid-task…spawn a pending task via `/intake-task`" → conduct rule references the intake contract; invocation mechanics → loader/workflow. [P3a] |
| V2-07 | §10 Work (118) | B melt | The load-bearing bullet. Splits four ways: (1) "Calibrate session-est to one effective context window" → `meta/taskfile.md` (est semantics). (2) "Stop hook prompts mid-session handoff on context overage" → workflow (hook wiring). (3) "One dev session is one reviewable unit — an advancement session's landed work is reviewed before the next dev session advances" — litmus 2; quality half ("end as one complete, coherent, reviewable unit; a wrap-up is an ordinary clean handoff") STAYS in dev.md per charter rule 5; the sequencing consequence → runbook. (4) "Sole exception: a remediation session that must wrap before its fix set is complete marks the entry with `- Handoff: continuation` — remediation resumes in a fresh session and re-review waits until the fix set completes. An advancement session never writes the marker." — litmus 3; role-local half ("declare `Handoff: continuation` when ending with your fix set incomplete; advancement never writes it") stays; consequences ("resumes in a fresh session", "re-review waits") → runbook. [P3a] |
| V2-08 | §10 End (120–136) | B | Whole End procedure → session-end boundary skill (`workflow/skills/session-end.md`); entry shapes → `meta/taskfile.md`. **Semantic guard**: End step 5 (remaining-task reconciliation at EVERY session end, 127–135) is heavier than the handoff's abbreviated session-end list — it is PRESERVED in the session-end skill spec; close-out additionally repeats it (ai-sync-v2 path). Do not silently drop the per-session reconcile. [P3a] |
| V2-09 | §10 End (137–143) | B | "On `task.status == completed`, the Stop hook fires `/ai-sync-v2`…`/ai-sync-v2` owns the entire close-out" + orchestrator-treats-as-incomplete note → trigger/verification → workflow (runbook + postcheck contract). Role-local negative space ("do not pre-archive, pre-edit `.ai/`, pre-remove the index row") → closeout skill spec + meta access contract. [P3a] |
| V2-10 | §11 (145–171) | B melt | Role section dissolves: verb→contract mapping → target `CLAUDE.md` (charter litmus note). Dispute conduct ("findings are claims to verify; a finding verified invalid is a dispute: record it, do not silently fix/skip") STAYS in dev.md. "disputed findings escalate to the user per the convergence rules…they do not loop" — litmus 3 consequence → runbook. "Remediation before advancement: while the latest review entry's verdict is `changes-requested`, a dev session only remediates…new scope resumes after a `pass` verdict" → data-driven MODE-SELECTION rule in dev.md ("your mode derives from the latest review verdict in the taskfile"), forward-predicting phrasing dropped; routing table → rolemapping. "`completed` is the sole trigger for the `/ai-sync-v2` close-out; per the table only a final-gate review session sets it" — litmus 2/3 → workflow. "Every non-`completed` status is in-flight: the §10 End discipline applies to review sessions unchanged" → session-end skill scope note. [P3a] |

## 2. ai-coding-tasks-v2.md → meta/taskfile.md + workflow

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| TSK-01 | §3 preamble (30–37) | B | Kind-derivation ("A session's kind is derived from the invocation verb plus the task file…") → `workflow/rolemapping.md` (it is THE dispatch condition). "`Verdict` is the routing decision for the next dev session. `changes-requested`: the next dev session MUST be remediation…" — litmus 2 in a schema doc → runbook/rolemapping. Verdict VALUE semantics ("`pass` means the active convergence group has no unresolved findings, or all residual behavior was explicitly accepted") → `protocols/review.md` (output semantics) + enum in `meta/taskfile.md`. [P3a] |
| TSK-02 | §3 table (39–52) | R melt | Transition table = data contract → single source in `meta/taskfile.md` (status enum + per-kind legal transitions). Rolemapping references it for dispatch; postcheck contract instantiates the per-kind menus; role docs point, never restate. "A dev session never sets `completed`. A review session never advances the lifecycle forward." stays with the table (schema invariant). [P3a] |
| TSK-03 | §2 frontmatter (14–27) | R | Schema verbatim → `meta/taskfile.md`. `session-est` comment's "(part of the claim, §10 Entry)" re-points to the entry-bookkeeping spec; `claimed-by` comment's "supplied by the tool or orchestrator" → "supplied by the caller". [P3a] |
| TSK-04 | §4 body + session-log (56–100) | R | Entry shapes + Done/Plan-slice/Next/Open semantics → `meta/taskfile.md`. **Re-scoped `Next:` lands here**: "remaining work / required changes on this task; never name sessions or roles" (replaces current handoff-flavored wording; kills the quantx specimen's scheduling narration). Session-plan slicing rules → same file (they are plan-data shapes; preReEst conduct references them from dev.md). [P3a] |
| TSK-05 | §1/§5/§6/§7 | R/B | Invariants, index, archive → meta. §6 "executed by `/ai-sync-v2` at the Stop hook trigger" → trigger → workflow; close-out CONTENT (admission→absorb→archive; reconciliation + `Remaining-task audit:` report line shape) → `workflow/skills/closeout.md` + report-line shape in meta. Doc preamble "feed into memory via `/ai-sync-v2` at completion" → lifecycle-neutral wording. [P3a] |

## 3. ai-coding-review-v2.md → protocols/review.md (+ runbook)

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| REV-01 | header (1–8) | A | Loading/deployment narration ("Loaded lazily by every tool…the orchestrator injects it into review prompts. Rides the `ai-coding-*.md` gitignore pattern") → delete; loading lives in adapters/workflow. [P3a] |
| REV-02 | step 2 (20–24) | B/D | Claim step + "(The Stop hooks locate the active task through `claimed-by` — a review that sets `completed` without claiming never triggers the ai-sync close-out.)" — hook-mechanics rationale → runbook; role keeps "claim the task" as a data write per meta shape (instantiated by entry prompt). [P3a] |
| REV-03 | step 3 (25–30) | B melt | Pending-set derivation STAYS (role-local input semantics). Marker parenthetical melts: role-local half → "an entry carrying `Handoff: continuation` is an open fix set — not yet reviewable; exclude it from the pending set"; scheduling half ("re-review waits until the fix set completes; the review turn normally waits") → runbook. [P3a] |
| REV-04 | step 6 (49–58) | B melt | "A final gate that cannot pass leaves `final_review` in place — the `changes-requested` entry itself sends the task back to dev remediation" — litmus 2. Role-local: "record `changes-requested`; keep `final_review`; never downgrade status for findings; `in_progress` only if `final_review` was set in error (record why)". Dispatch consequence → runbook. [P3a] |
| REV-05 | Convergence (77–84) | B melt | Dispute flow: role-local = "evaluate the dispute on the merits; withdraw or hold; when still held valid write `Dispute-unresolved: <one line>` and do not re-request the same change". Consequence ("escalates to the user immediately; the orchestrator pauses on this marker; in a manual session raise it to the user") → runbook escalation path. [P3a] |
| REV-06 | round budget (106–113) | B melt | Reviewer-side budget counting ("count the `changes-requested` entries sharing the current `Group:`…once two exist…do not hand the task back…escalate to the user…the orchestrator pauses on the round count mechanically") → budgets are counted by the CALLER (runbook; precedent: convergence budgets already orchestrator-counted). Role-local residue: "record unresolved findings and your verdict; do not expand scope to force convergence". Manual mode: the human runbook-executor counts (charter rule 4). [P3a] |
| REV-07 | groups (85–105) | — | Group-anchoring rules are genuinely role-local (findings bookkeeping) — STAY. Cross-refs ("roles §11", "tasks-v2 §3") re-point. The §11-violation fallback stays (review-side input handling). [P3a] |
| REV-08 | ledger/severity (63–76) | — | Delta-only, findings ledger, severity gates: STAY. "carried to a new task via `/intake-task`" → "carried as a new pending task per the intake contract" (protocol-asset reference, not session naming — charter litmus interpretation). [P3a] |

## 4. memory-v2 / init-v2 (audit depth: light — CONFIRMED)

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| MEM-01 | §1 (close-out bullet) | B | "…are `/ai-sync-v2`'s exclusive domain. LLM session work stops at the End procedure (`ai-coding-v2.md` §10 End steps 1-5)" → access-contract wording ("snapshot writes only via the closeout skill"), End pointer → session-end skill. Rest of memory-v2 → `meta/memory.md` verbatim. [P3a] |
| MEM-02 | §2 loading contract | R | Tier table (WHAT is eager/lazy) stays meta; HOW it is assembled (imports / hook injection / orchestrator preamble) = the context-assembly spec → workflow (one spec, two backends). [P3a] |
| INIT-01 | whole file | R | → `meta/init.md`; re-point tasks-v2/intake refs. **Content edit**: excluded-paths list `ai-coding-*.md` (line 20) → `.ai-protocol/**` (+ keep `ai-coding-*.md` during transition). ai-init skill precondition check likewise (skills-backup/ai-init/SKILL.md:12–13, 35). [P3a] |

## 5. automation-mode.md → conduct annex (template) + runbook

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| AUTO-01 | block-and-end (8–34) | B melt | Never-ask-inline + the 5 block-and-end steps = genuine conduct adjustment → `prompts/entry/conduct-annex.md`. "The orchestrator polls the task file, surfaces the question to the human, and resumes this conversation with the answer" (29–30) + resume-restore mechanics (32–34) — litmus 2 → runbook (the resume prompt instantiates restore instructions at resume time; template `midflight/blocked-resume`). [P3a split; P2 externalizes verbatim first] |
| AUTO-02 | end discipline (36–50) | D | Full §10 End restatement + "The orchestrator verifies all three after the session and will send the violation back to you to fix" — litmus 4 + 2. Annex keeps one line ("no Stop-hook backstop here: satisfy the session-end procedure unprompted before your final message" + pointer); menus/checks arrive via the generated checks-preview. [P3b] |
| AUTO-03 | scope discipline (52–59) | D | "Do exactly the invoked role (`task <id>` = dev, `review <id>` = review)" → drop (loader owns the mapping; the prompt's role-invocation line already instantiates it). Never-edit list stays conduct; re-point filenames to `.ai-protocol/**` post-cut. ".ai/ writes happen only at close-out via /ai-sync-v2, when the orchestrator asks for it explicitly" → keep conduct half, drop orchestrator-asks half. [P3a/P3b] |

## 6. Orchestrator (prompts → templates [P2], content [P3b]; README → runbook [P3a])

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| ORC-01 | `_entry_checklist` 1883–1914 | — | Correct instantiation pattern (values, not rules) — becomes THE entry-bookkeeping carrier per charter. [P2 externalize; P3a re-point §10 refs] |
| ORC-02 | dev remediation block 1933–1942 | D | Mode declaration (caller-side, correct) + remediation conduct restatement (duplicates dev.md remediation mode) → trim to mode + group values + pointer. [P3b] |
| ORC-03 | preReEst block 1944–1955 | D | Restates §10 preReEst procedure → dev.md owns it; prompt trims to "advancement mode" + pointer. [P3b] |
| ORC-04 | review independence para 1975–1980 | — | Quality-shaping/cross-model framing — evaluate at P3b; likely stays as instantiated context (or moves into review.md if it is rule-like). [P3b judgment] |
| ORC-05 | wrap-up prompts 1445–1491 | D | Restate End/wrap-up procedure incl. marker rule (mirrors stop-hook texts) → instantiate (tokens, sid, mode) + point at session-end skill; mode-specific notes are caller-side instantiation of dev.md's marker rule, kept minimal. [P3b] |
| ORC-06 | close_out 2168–2178 | D | Restates closeout report-line shape (meta-owned post-cut) → instantiate task id/count + point at closeout skill. [P3b] |
| ORC-07 | plan-gate prompts 1565–1591, 1681–1706 | **C** | The plan ROLE's contract (read-only bounds, six report headings, revision shapes) exists ONLY in prompt text — the main class-C finding. Write `protocols/plan.md` [P3a]; prompts then instantiate + point [P3b]. Plan-report = the role's return-value output contract (charter rule 7). |
| ORC-08 | `_preamble`/automation injection 1865–1877 | R | Conduct annex becomes a first-class template (P2 verbatim; P3a swaps in the split annex). cc-codex "protocol arrives natively" note stays (context-assembly spec, workflow-owned). |
| ORC-09 | README §3/§5/§6 | R | Runbook-shaped content → `workflow/runbook.md` (dispatch narrative §3, escalations §5.1–5.8, attach table §6 → rolemapping). README slims to machine-executor doc (backends §0–§2, §4 session mechanics, logs §7, testing §8, limitations §9) and points at the runbook. [P3a] |
| ORC-10 | `check_specs` 1710–1834 | R | Requirement lines → `postcheck-contract.md` with check-IDs [P2 byte-identical]; per-kind status-menu lines become instantiations of the meta transition table (quote + source pointer) [P3a re-point]. No ID system exists today — P2 introduces it with 1:1 startup validation both directions. |

## 7. Hooks (single-sourcing fixes drift; texts → session-end skill + pointers)

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| HK-01 | cursor stop :146 | D + drift | Wrap-up text restates the full End procedure AND uniquely carries the remediation-marker instruction + "an advancement session never writes that marker — its landed work is reviewed next (protocol §10)" (litmus 2 in a hook). claude:154 / codex:149 wrap-ups LACK the marker line — live drift, the disease specimen. → all three wrap-up texts single-sourced from `workflow/skills/session-end.md` content; hooks instantiate values (tokens, task file) + pointer. [P3a source; P3b trim] |
| HK-02 | stop hooks case1 (cursor:115, codex:120, claude:111) | D | "Invoke /ai-sync-v2 now…your final response must include one line beginning `Remaining-task audit:`" — report shape restated (meta-owned post-cut) → invocation + pointer at closeout skill. Hook-as-trigger itself is CORRECT (charter rule 10). [P3b] |
| HK-03 | stop hooks case1-dirty/case2b (cursor:112,130; codex:117,134; claude:102,132) | D | "Per §10 End…" violation texts → re-point to session-end skill; `CLEAN_HOWTO` becomes shared single-source text (same fragment as orchestrator's — one definition, consumed by hooks and templates). [P3a re-point] |
| HK-04 | codex session-start :129–141 | A/B/D | "Cross-model review (verb = role)" block: verb mapping + status vocabulary + "Only a review session sets `completed`, the sole trigger for the ai-sync-v2 close-out" — litmus 1/2/4 in hook text. → replace with the review-workflow pointer line only; mapping lives in the injected loader content; status vocabulary in meta (injected). Adaptations block (:119–127) is legitimate tool-adapter content — stays. [P3a] |
| HK-05 | codex/cursor self-gates + EAGER_FILES (codex:46,52–54; cursor:38–40) | R | Path re-points: marker file → `.ai-protocol/` presence (or CLAUDE.md), EAGER_FILES → new eager set (conduct, dev, meta/taskfile, meta/memory + `.ai/` set + `.ai-tasks/index.md`). [P3a] |
| HK-06 | cursor session-start :99–108; claude housekeeping hook; settings.json | — | Clean (sid + injection + housekeeping reminder; no choreography). No change beyond paths. |

## 8. Adapters + skills (light — CONFIRMED)

| ID | Loc | Class | Finding → disposition |
|---|---|---|---|
| ADP-01 | cursor protocol.mdc | R/D | Fallback read-list :15 re-points; "Cross-model review" §34–39 stays pointer-level, re-pointed. Adaptations (sid, @file, skills mapping) stay — tool-adapter content. [P3a] |
| ADP-02 | review-workflow.mdc / codex review-workflow.md | R | Already correct single-source pointers — filename re-point only. [P3a] |
| ADP-03 | codex README + config.toml.template comments | R | Label/path mentions re-pointed; no boundary leaks (operational doc). [P3a] |
| SKL-01 | ai-sync-v2 SKILL.md | D/R | Becomes the closeout skill packaging (spec: `workflow/skills/closeout.md`). ":101–103 (the orchestrator greps the final response for it)" — caller-naming in skill text; report-line shape moves to meta, skill instantiates it; baseline-context refs :6–9 re-point. Procedure content is sound. [P3a] |
| SKL-02 | ai-init SKILL.md :12–13, :35 | R | Required-files + surface-exclusion lists → `.ai-protocol/**` paths. [P3a] |
| SKL-03 | intake-task SKILL.md | R/C | Source for `protocols/intake.md` (role contract written from it — the skill remains its packaging, charter rule 6); §10-Entry/tasks-v2 refs re-point. [P3a] |
| SKL-04 | ai-load SKILL.md :7 | R | "sections 8,9,10 in CLAUDE.md" — those sections dissolve → re-point to meta read contract + loader. [P3a] |
| SKL-05 | session-ai-audit (deployed) | — | Depends only on session-log heading shape + close-out divider = frozen data shapes. No change; verify at P3a gate. |
| SKL-06 | ai-sync (old names), ctd-tasks (old `.ai/tasks/` layout) | — | Pre-existing staleness, out of scope (plan: known follow-ups). |

## 9. `§` cross-reference classification (131 lines, 21 files)

| File (refs) | Fate |
|---|---|
| orchestrator README (22), orchestrator.py (21), test_loop_mock.py (11) | Re-point at P3a (runbook/protocol names replace §-anchors); test assertions churn at P3b with the trim |
| ai-coding-v2 (11), review-v2 (10), tasks-v2 (5), memory-v2 (2), init-v2 (1) | Die with the docs (content redistributed; new docs use file-scoped references, minimal §) |
| automation-mode (5) | Die with the split |
| ai-sync-v2 (9), intake-task (4), ai-housekeeping (4), ai-init (2) | Re-point to meta/protocol names [P3a] |
| cursor stop hook (4), codex stop hook (3), claude stop hook (3), codex session-start (3), protocol.mdc (1) | Re-point / dissolve with single-sourcing [P3a/P3b] |
| tasks-v2-tmp (4) | File deleted [P3a] |
| HANDOFF-orch-hub (3) | Historical doc — leave as-is |

Post-cut invariant (boundary-lint): `protocols/*.md` contain no `§`-references into
workflow docs, no role/session names of OTHER roles, no "orchestrator", no
dispatch-predicting verbs. Cross-doc references use doc names, not section symbols,
except within a single doc.

## 10. Confirmations of handoff open questions

- **Audit depth memory/init/skills**: light — confirmed (2/1/6 findings, mostly re-points).
- **orch-hub**: no-op confirmed at the parser level (registry + `.ai-tasks` frontmatter/index + stable log lines; verified against orchestrator.py emit sites). Guard: log-line formats frozen.
- **Charter + runbook location**: settled — `CHARTER.md` (canonical root, not deployed); `workflow/runbook.md` single-sourcing README §3/§5/§6 + tasks-v2 §3 routing + automation-mode scheduling.
- **`Next:` wording**: settled per TSK-04.
- **Plan-report**: protocol data — plan is a role, report is its return-value contract (ORC-07 is the class-C evidence).
- **Literal layout**: affirmed; deploy touchpoints enumerated (deploy.py:43 constant, :129–164 normalization re-key, :24–36 gitignore block, PAYLOADS additions; CLAUDE.md chain; EAGER_FILES; REVIEW_RULE; protocol.mdc:15; test fixtures both suites; `-tmp` deletion).

## 11. Counts

38 classified findings: 3×A, 14×B (8 melts), 1×C (plan contract), 9×D, 11×R —
plus 6 no-change confirmations. Main leak surfaces confirmed as predicted: v2
§10/§11 (10 findings), review-v2 (8), hooks (6, incl. one live three-way drift),
orchestrator prompts (10).
