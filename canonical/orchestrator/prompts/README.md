# Orchestrator prompt templates

Single source for every prompt and banner text the orchestrator sends or
shows. `orchestrator.py` loads these files at use time; a human standing in
for the orchestrator (manual workflow execution) reads the SAME files — one
text, two executors, no drift. Extracted byte-identically from the previous
hardcoded strings (protocol-cut P2); content trims to
instantiation-plus-pointer happen in a later phase.

## Layout

- `entry/` — fragments composed into a session's FIRST prompt (invocations,
  checklist lines, plan-gate, close-out, checks-preview header) plus
  injected blocks (approved plan, rulings, doc wrappers).
- `midflight/` — texts sent into an already-running conversation
  (`[orchestrator]` prompts: wrap-up, violation fix, resume/answer relays,
  discussion turns) and every human-escalation banner (`banner-*`).
- `postcheck-contract.md` — the end-of-session discipline: each `##`
  heading is a check-id, its body the requirement line. `check_specs` binds
  every id to a verification callable; the same rendered lines are the
  prompt's POST-SESSION CHECKS preview and the human executor's on-return
  checklist.

## Rules

- Syntax is `{{var}}` (same as deploy.py's `{{REPO_ROOT}}`), lowercase
  names. Substitution is STRICT: the placeholders in a file must exactly
  match the code-side manifest (`PROMPT_MANIFEST` / `POSTCHECK_MANIFEST` in
  `orchestrator.py`) and the values passed at render time. Adding or
  removing a placeholder requires the matching manifest edit.
- Startup validates everything (`prompts_error()`): missing/malformed
  template, undeclared placeholder, orphan template file, or a
  contract↔check mapping that is not 1:1 in both directions refuses the
  run — same policy as the effort allowlist, never a silent fallback.
- Templates are text atoms. Composition — which fragments, in what order,
  separators, list joins like `"\n- ".join(problems)` — stays in builder
  code. Templates instantiate rules with current values; they must not
  restate protocol rules (charter litmus 4).
- WHITESPACE IS BYTE-SIGNIFICANT. Only the file's single trailing newline
  is stripped at load. Several fragments begin or end with a meaningful
  space (`wrapup-note-*`, `wrapup-plan-*`, `*-undershoot*`) — an editor
  that trims trailing whitespace will corrupt them.
- `checks-preview` content is GENERATED from the loaded contract at
  runtime; never freeze a checks list into a template.
- After ANY edit here, run the mock suite
  (`python3 -W error::DeprecationWarning ../test_loop_mock.py`) — it
  asserts prompt substrings and the startup validation.

These files deploy with the orchestrator bucket to
`.cursor/orchestrator/prompts/` (edit canonical only; never deployed
copies).
