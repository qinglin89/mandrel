# Codex CLI adaptation of the ai-protocol workflow

This directory is the **Codex CLI** adaptation of the same ai-protocol workflow
that Claude Code and Cursor already run in this repo. It is fully isolated:
everything here lives in Codex-only locations, is gitignored (`.codex/`), and
touches none of the shared or other-tool files.

## What's here

| File | Role |
| --- | --- |
| `config.toml` | Project-layer Codex config; registers the two lifecycle hooks. |
| `hooks/session-start.sh` | `SessionStart` hook — injects session-id + protocol docs + eager `.ai/` set + housekeeping hint via `additionalContext`. Port of `.cursor/hooks/session-start.sh`. |
| `hooks/stop-context-check.sh` | `Stop` hook — enforces the session-end discipline (clean tree → session-log → status), triggers `ai-sync-v2` on `completed`. Port of `.cursor/hooks/stop-context-check.sh`. |
| `review-workflow.md` | Pointer to the canonical review contract `.ai-protocol/protocols/review.md` (single-sourced across Cursor/Codex/orchestrator). |

The workflow itself (protocol docs, `.ai/`, `.ai-tasks/`, skills) is shared and
unchanged. Skills are **bridged, not copied**: the SessionStart injection maps
slash-command names (`/ai-sync-v2`, `/intake-task`, `/ai-init`,
`/ai-housekeeping`, `/ctd-tasks`) to `~/.claude/skills/<name>/SKILL.md`, exactly
as the Cursor adaptation does.

## One-time setup (trust the hooks)

Codex will not run non-managed command hooks until you review and trust them:

- **Interactive TUI:** run `codex` in this repo, then `/hooks` → review and trust
  the two hooks (their trust is recorded against the current script hash; edit a
  script and you must re-trust).
- **Automation / `codex exec`:** pass `--dangerously-bypass-hook-trust` (this is
  how a Focus-1 orchestrator would drive dev/review runs).

## Verified vs. unverified (codex-cli 0.142.5)

- ✅ **`codex exec` fires both hooks** from this project-layer `config.toml`.
  Confirmed empirically; the exact stdin schema (`session_id`,
  `transcript_path`, `cwd`, `source`, `stop_hook_active`,
  `last_assistant_message`, …) matches what the scripts parse.
- ⚠️ **Absolute paths are mandatory.** On 0.142.5 the documented
  `command = '... "$(git rev-parse --show-toplevel)/..."'` form is **not**
  shell-evaluated — the hook silently never runs. `config.toml` therefore uses
  absolute paths. **If you move/clone this repo, update the two paths in
  `config.toml`** (or use the user-scope fallback below).
- ❓ **Interactive-TUI firing is unconfirmed** (GitHub issue #17532 reported
  project-layer hooks not firing in the TUI on older builds). This could not be
  settled from an automated harness. **Please verify once in a real terminal:**
  run `codex` here, trust the hooks via `/hooks`, start a session, and check that
  `~/.codex/ai-hooks.log` gets a `session-start … → inject` line. If it does,
  you're done. If it does **not**, activate the fallback below.

## Fallback: user-scope hooks (if project-layer hooks don't fire in the TUI)

The hook scripts self-gate on the protocol marker (`.ai-protocol/protocols/conduct.md`
at the git root), so they are a **silent no-op in every other repo**. That makes it safe to
register them once at user scope, where they fire in both the TUI and `exec`.

Create `~/.codex/hooks.json` (Codex-only; does not affect Claude Code or Cursor):

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "startup|resume|clear|compact",
        "hooks": [ { "type": "command",
          "command": "/absolute/path/to/target-repo/.codex/hooks/session-start.sh",
          "timeout": 15 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "/absolute/path/to/target-repo/.codex/hooks/stop-context-check.sh",
          "timeout": 30 } ] } ]
  }
}
```

Then remove (or keep — duplicates are merged with a warning) the `[hooks]`
entries in this project `config.toml`, and trust the user-scope hooks via
`/hooks`.

## Isolation (Constraint #0)

- Never edit `CLAUDE.md`, `.claude/**`, `~/.claude/skills/**`, the shared
  `.ai-protocol/**` docs, or `.cursor/**`. This adaptation touches none of them.
- No repo-root `AGENTS.md` is created: Codex **and** Cursor both read `AGENTS.md`
  (walking up to the repo root), so it would leak between tools. Context is
  injected via the `SessionStart` hook instead — the same choice made for Cursor.
- `.ai/` and `.ai-tasks/` remain shared cross-tool state. `claimed-by` is
  session-id-scoped, so each tool's Stop hook only reacts to its own sessions.
  The role vocabulary (dev/review, `final_review`) lives in the shared
  protocol suite (loader + `.ai-protocol/meta/taskfile.md`) — all three tools
  (Claude Code included) share it and can take either role.
- Hook logs go to `~/.codex/ai-hooks.log` (separate from Cursor's
  `~/.cursor/ai-hooks.log`).

## Cross-model dev/review (verb = role)

- `task <id>` → dev role (the dev contract, `.ai-protocol/protocols/dev.md`).
  Dev sets only `in_progress` or `final_review`, never `completed`.
- `review <id>` → review role (read `review-workflow.md`). Only a review session
  sets `completed`, which is the sole trigger for the ai-sync-v2 close-out.
