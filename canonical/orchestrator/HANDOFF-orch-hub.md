# Handoff: orch-hub (remote-control service "x") + ai-native-deployment

> Created 2026-07-04 from a **design-only** session (no code written). This is a
> SEPARATE workstream from `HANDOFF.md` (orchestrator state-machine testing —
> still live, do not confuse or supersede). Read `README.md` in this directory
> first if you don't know the orchestrator.

## Goal

A resident daemon ("x", repo name **orch-hub**) on the MacBook Pro, remotely
reachable from iPhone/iPad, that drives the existing dev↔review orchestrator
across MULTIPLE repos: list repos/tasks, start/stop runs, stream logs, and —
the core loop — capture every orchestrator `HUMAN INPUT NEEDED` escalation,
push-notify the phone, collect the human answer remotely, and feed it back.
Plus **ai-native-deployment**: a sibling repo that becomes the canonical,
version-controlled home of the whole protocol suite, deployed per-repo via the
user's existing local `aii` command (to be extended).

## User-ruled decisions (2026-07-04, all confirmed)

- NO wall-clock watchdog and NO hang/log-silence detection anywhere (dropped
  entirely, including the x-side notification variant).
- orch-hub = independent git repo, sibling of quantx
  (`~/workplace/github.com/qinglin89/orch-hub`).
- x v1 feature set: repo/task list, start/stop, SSE log tail, escalation
  list + answer, push notification. UI = simplest mobile single page
  (beautify later).
- Network/deploy: Tailscale + bearer token + launchd.
- Protocol suite (`ai-coding-*.md`, `.cursor/`, `.codex/`, `.claude/`
  hooks/settings, scripts) centrally managed in sibling repo
  **ai-native-deployment**; per-repo deployment by extending the existing
  local `aii` command.
- IM bot: list menus (repos + status, per-repo pending tasks, active run per
  repo) via inline keyboards are in scope. Notification channel decision:
  **Bark push + web UI answering for v1**; Feishu long-connection bot as v2
  for in-IM interaction; Telegram adapter optional-only (needs proxy on both
  ends in CN); ntfy REJECTED for iOS (needs `upstream-base-url=ntfy.sh` APNs
  relay — foreign dependency, unreliable in CN); WeCom rejected (interactive
  callbacks require a public URL); DingTalk Stream = equivalent alternative to
  Feishu if ever preferred.

## Architecture (settled)

### Orchestrator change — the ONLY one, strictly non-invasive

**STATUS: IMPLEMENTED 2026-07-06** in the canonical repo (mock scenarios
23–27 cover the file channel; all pre-existing scenarios stayed green).
The settled contract below supersedes the earlier sketch.

- `ask_human` is the single human-IO choke point (verified: the only
  `input()` / stdin read in orchestrator.py — grep `def ask_human`; all
  §5.1–5.8 escalations, plan-gate, close-out go through it, each call site
  now passing an explicit `kind=`).
- `--control-dir <run-dir>` (dir auto-created; keep it OUTSIDE the repo
  working tree — an in-repo dir would dirty the tree and fail post-checks;
  the orchestrator warns at startup):
  - unset → current stdin path byte-for-byte unchanged (default; manual tty
    runs unaffected; all pre-existing mock scenarios unaffected);
  - set → `ask_human` atomically writes `NNN-question.json`
    `{seq, ts (ISO-8601 UTC), kind, banner, message}` — `message` ==
    `banner` in v1 (reserved to diverge later); `options` reserved, never
    written in v1 — then polls `NNN-answer.json` every 1s
    (`CONTROL_POLL_SECONDS`). Only a non-empty string `answer` is REQUIRED
    of the answer file; `seq` (checked against the filename, mismatch
    logged file-only), `ts`, `responder` (logged file-only) are optional
    extras the hub should still write. A malformed/partial answer is
    logged (once per distinct error) and re-read each tick — the hub may
    atomically rewrite the file. Answer `stop` still exits via the
    existing sys.exit path ("stopped by human"). Banner still goes to the
    log file in both modes (auditable, unchanged).
  - seq numbering starts after the max existing
    `NNN-(question|answer).json` in the dir — a reused dir never gets
    files overwritten and a stale answer is never consumed. Recommended
    hub practice stays one fresh run-dir per run.
  - `kind` enum maps README §5.1–5.8: `request | run-error |
    followups-exhausted | blocked | convergence-budget | dispute-unresolved |
    final-review-stall | closeout-incomplete | plan-gate`. v1 UI may render
    banner text only; `kind` is for icons/styling (plan-gate later gets
    markdown render + confirm/amend buttons).
- `stop.flag` in the control dir (honored only when `--control-dir` is
  set; the orchestrator never deletes it):
  - at loop top → graceful stop at the next session boundary (tree is
    clean between sessions); logs `control-dir stop request (stop.flag) —
    stopping at session boundary` and exits 0 via the normal
    "orchestrator done" path.
  - while awaiting an answer (ruled 2026-07-06; supersedes the earlier
    "loop top ONLY") → also honored, semantically identical to answering
    `stop`: sys.exit (nonzero), may interrupt an open session, tree
    possibly dirty. Without this a pending question would block graceful
    stop forever.
  - Hard kill = kill the process group; UI must warn: tree may be dirty,
    a CLI child may be orphaned.
- Deferred (not v1): structured `state.json` / `events.jsonl` — x parses the
  existing stable log lines instead (`state:`, `--- <role> session start`,
  `context≈N tokens`, `[heartbeat]`; new stable lines: `control-dir
  question NNN written (kind=…)`, `human answered: …`, `control-dir stop
  request (stop.flag)`).
- State-machine / post-check / prompt / backend code untouched (held).
  File-channel scenarios added to the mock suite (23–27); all pre-existing
  scenarios green.

### orch-hub (service x)

- FastAPI + uvicorn, Python (same stack as orchestrator), launchd agent
  (`KeepAlive`). Never in-process imports the orchestrator (it is full of
  `sys.exit` + blocking loops) — always a subprocess.
- Spawn per run: each repo's OWN
  `.cursor/orchestrator/.venv/bin/python .cursor/orchestrator/orchestrator.py
  <task-id> --control-dir <run-dir> [flags]`, cwd=repo,
  `start_new_session=True` (detached: x restart never kills runs; re-attach =
  scan run-dirs, check pid, resume tailing; pending questions persist as
  files), wrapped in `caffeinate -i` (laptop sleep kills multi-hour runs).
- Per-repo mutex + serial queue (same working tree must never run twice);
  cross-repo parallel OK.
- API surface: repos (from aii registry — x does NOT keep its own repo
  config), tasks (x-local small parser of `.ai-tasks/index.md` + task
  frontmatter; do not import orchestrator code), runs (start/stop/list/
  status), escalations (list pending / answer), log stream (SSE tail of the
  orchestrator's own `logs/<utc-ts>-<task-id>.log`), health.
- Pre-start checks (P0, surface in UI before spawn): tree clean (orchestrator
  refuses otherwise), task exists + status legal, `blocked` with foreign
  `claimed-by` sid → warn "unblock manually first" (README §5.4 crash
  caveat), credentials probe (cursor: `CURSOR_API_KEY` in repo's
  `.cursor/orchestrator/.env`; cc-codex: claude/codex login state — per the
  other handoff claude login was still PENDING, codex verified).
- Security: this service = remote arbitrary-code execution on the dev machine
  (dev sessions run `--dangerously-skip-permissions`). Tailnet-only, bearer
  token on top, audit log of every answer (who/when/what). Never public.
  Tailscale Serve for HTTPS if PWA features are wanted; plain http over
  tailnet acceptable for v1.
- Notifications: `Notifier` interface, fan-out to configurable backends.
  v1 = **Bark** (self-hosted bark-server on the Mac, may bind tailnet-only;
  pushes go Mac →(outbound 443)→ APNs → phone, CN-reliable, zero proxy/public
  exposure; device registration once over tailnet; notification tap →
  deep-link URL to x web UI over tailnet; iPhone+iPad universal app).
  Triggers: escalation created, run ended/errored, close-out done. Nothing
  else (no silence detection — ruled out).
- Answer intake: web UI and (later) IM bot both POST to the SAME x endpoint —
  dual channel, single control plane, one audit trail.
- v2 IM bot (Feishu long-connection): lark-oapi WebSocket client inside x's
  asyncio loop — outbound-only, no public callback, no proxy; inline-keyboard
  menus: repo list w/ ● active / ○ idle badge → per-repo task list
  (pending/in_progress/blocked, active run annotated e.g.
  `review · session 3/40 · context≈120k`); callback_data ≤64 bytes → short-id
  map inside x; edit-message-in-place navigation; paginate ~8–10 rows. Same
  design works on Telegram cards if the TG adapter is ever enabled.

### ai-native-deployment + aii

- Motivation: protocol files are gitignored in quantx → currently ZERO
  version history; multi-repo manual `cp` sync already painful (see the other
  handoff's sync-discipline section).
- Canonical home of: `ai-coding-*.md` (excl. `-tmp` drafts), `CLAUDE.md`,
  `.cursor/{hooks,hooks.json,rules}`, `.codex/` (config.toml as template),
  `.claude/{hooks,settings.json}`, orchestrator CODE
  (`orchestrator.py`, `automation-mode.md`, `README.md`, `test_loop_mock.py`,
  `requirements.txt`, `.env.example`), repo-level `.claude/skills/` +
  `scripts/` agent-infra, AND the user-level `~/.claude/skills/` set
  (ai-sync-v2, intake-task, ai-init, ai-housekeeping) deployed to home —
  completes the cross-machine story. (User listed `.cursor/` wholesale;
  treating orchestrator code as in-scope is an inference — confirm.)
- `aii` (user's EXISTING local command — next session: ask where it lives,
  read it before extending):
  - `aii deploy <repo>`: copy + render `.codex/config.toml` `{{REPO_ROOT}}`
    template (absolute paths required — codex-cli 0.142.5 exec's the command
    string as argv, no shell eval) + idempotent `.gitignore` block append +
    venv bootstrap (`python3.14 -m venv` + `pip install -r requirements.txt`)
    + write `.ai-deploy-manifest.json` {source commit, per-file hashes};
  - `aii status [--all]`: hash-compare manifest both directions (local edits
    in target → warn; newer canonical → prompt sync);
  - `aii sync --all`; registry file of deployed repos (consumed by x as its
    repo list).
- Edit discipline: canonical repo ONLY; never hand-edit deployed copies.
- Per-repo init (NOT copied): `.ai/` via `/ai-init` (brownfield scan),
  fresh `.ai-tasks/index.md`, per-repo `.env` (secrets never in the
  deployment repo). NEVER copy: `.claude/projects/`,
  `.claude/settings.local.json`, `.venv/`, `logs/`, `sessions.json`.
- Portability audit (verified 2026-07-04): zero `quantx`/user hardcodes in
  `ai-coding-*.md` + `CLAUDE.md` (grep-verified); all six hook scripts
  resolve repo root dynamically (`CURSOR_PROJECT_DIR` / `CLAUDE_PROJECT_DIR`
  / `git rev-parse --show-toplevel`); `.claude/settings.json` uses
  `${CLAUDE_PROJECT_DIR}`; SOLE exception = two absolute hook paths in
  `.codex/config.toml` (documented in its header comment).
- Machine-level prereqs stay outside repos: CLI logins, `jq`, python3.14.

## Feature priorities

- **v1 slice**: orchestrator `--control-dir` + `stop.flag` (DONE
  2026-07-06); x core (repo/task list, start/stop, SSE tail, escalation
  list+answer, Bark push, token auth, mobile single page); launchd +
  Tailscale.
- **P1**: task-file rendering (ground truth beats logs on the phone), git
  log/show per run (needed to make informed rulings remotely), remote
  intake-task (phone types a request → x runs a one-shot agent session
  invoking `/intake-task` → new pending task → start orchestrator on it).
- **P2**: run history/audit UI, full-flag start form, multi-repo dashboard,
  IM start-with-preset buttons, TG adapter.
- **Dropped by ruling**: any watchdog/hang detection.

## Suggested build order (next session may reorder with user)

1. **ai-native-deployment + aii first**: move suite to canonical repo, deploy
   back to quantx, verify byte-identical + hooks still fire. Rationale: gives
   version history BEFORE further edits, and the orchestrator change then
   lands once in the canonical home instead of being migrated later.
2. Orchestrator `--control-dir` + `stop.flag` — DONE 2026-07-06 (landed in
   canonical; mock suite extended with scenarios 23–27, all pre-existing
   scenarios green; redeploy to targets via `aii-2 deploy`).
3. orch-hub FastAPI core + Bark notifier + single page.
4. launchd plist + Tailscale (Serve for HTTPS if wanted).
5. v2: Feishu long-connection bot, remote intake-task, git diff view.

## Open questions for the next session

- Where do x's run-dirs live (orch-hub data dir, e.g. `~/orch-hub/var/` vs
  `~/.orch-hub/`)? Not discussed.
- `aii`: where is it, what does it do today? (Must read before extending.)
- Confirm orchestrator code in ai-native-deployment scope (assumed yes).
- Bark server form: bare binary vs docker on the Mac.
- Task-tracking style for this workstream: orchestrator/agent-infra work has
  historically been handoff-driven OUTSIDE `.ai-tasks/` (all files
  gitignored). orch-hub + ai-native-deployment are their own repos → their
  work naturally lives there; keep quantx `.ai-tasks/` out of it unless the
  user says otherwise.

## Key invariants to preserve (unchanged from the orchestrator workstream)

- Orchestrator stays a dumb scheduler; task file = ground truth; orchestrator
  never writes the task file; ALL new intelligence lives in x.
- No web/remote logic inside the orchestrator or the protocol docs; the
  protocol must keep working standalone for manual interactive sessions.
- Terminal output stays status-level; verbosity goes to log files.
- Never edit `~/.claude/skills/**` in place… until ai-native-deployment
  becomes their canonical home (then: edit there, deploy home).

## Reference paths

- Orchestrator payload in each managed target repo:
  `.cursor/orchestrator/{orchestrator.py,README.md,automation-mode.md,test_loop_mock.py}`
- Protocol files in each managed target repo root: `ai-coding-*.md`, `CLAUDE.md`
- Hooks/configs: `.cursor/hooks{,.json}`, `.cursor/rules/`, `.codex/hooks` +
  `.codex/config.toml`, `.claude/hooks` + `.claude/settings.json`
- User-level skills remain outside target repos unless a future canonical deploy
  path explicitly manages them.
