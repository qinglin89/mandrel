# How the protocol reaches a session

This repo is the source of truth for one thing: the rules an AI coding session
in *another* repo must follow. Those rules are plain markdown. The work is
getting them, unchanged, into the context window of three different tools —
Claude Code, Cursor, and Codex CLI — and keeping them there across upgrades
without stepping on state the target repo owns.

Five layers, each with exactly one owner:

```
  canonical/                 layer 0   source of truth (this repo, git)
        │
        │  aii-2 deploy      layer 1   copy + render + resolve + record
        ▼
  <target repo>/             layer 2   deployed payload + target-owned memory
        │
        │  hooks / imports   layer 3   three IDE surfaces, one eager set
        ▼
  session context            layer 4   loading contract (eager / lazy / delivered)
```

---

## Layer 0 — `canonical/` is the only source of truth

Everything under `canonical/` is authored here and copied verbatim into target
repos. Nothing is authored in a target repo; a local edit there is drift, and
`aii-2 status` reports it as such.

| `canonical/` bucket | lands in target as | what it is |
|---|---|---|
| `repo-root/` | `/` (repo root) | `CLAUDE.md`, the **loader** |
| `protocols/` | `.ai-protocol/protocols/` | conduct + the role contracts (dev-advancement, dev-remediation, review, plan, intake) |
| `meta/` | `.ai-protocol/meta/` | data schemas: `taskfile.md`, `memory.md`, `init.md` |
| `workflow/` | `.ai-protocol/workflow/` | caller-side procedure: `runbook.md`, `rolemapping.md`, skills |
| `claude/` | `.claude/` | `settings.json` (hook wiring) + hooks + project skills |
| `cursor/` | `.cursor/` | `hooks.json` + hooks + `rules/*.mdc` |
| `codex/` | `.codex/` | `config.toml.template` + hooks + adaptation docs |
| `orchestrator/` | `.cursor/orchestrator/` | the SDK-driven multi-session caller |

Two boundary rules are mechanically enforced by `scripts/boundary-lint.sh`:

- **Contracts don't know about callers.** `protocols/*.md` may not mention the
  orchestrator, dispatch, hooks, other roles' sessions, or slash commands.
- **The ambient channel carries no role contract** (charter rule 12). The only
  `protocols/` file allowed in a session-start eager set or in the loader's
  import block is `conduct.md`.

---

## Layer 1 — `aii-2 deploy` writes the payload

`./aii-2 deploy <target>` walks `canonical/`, and for each file:

1. **Filters** forbidden paths — `.git`, `.venv`, `__pycache__`, `logs/`,
   `.env*` (except `.env.example`), `sessions.json`, `settings.local.json`,
   `*.pyc`. These never leave the source repo.
2. **Renders** templates. Only `codex/config.toml.template` is a template: it
   becomes `.codex/config.toml` with `{{REPO_ROOT}}` replaced by the target's
   absolute path. Codex CLI does not shell-evaluate hook commands, so the paths
   must be absolute and baked in at deploy time.
3. **Resolves** the loader's memory entrypoints against the target's own `.ai/`
   — see [Entrypoint resolution](#entrypoint-resolution) below.
4. **Writes** the bytes and the file mode. This is an unconditional overwrite:
   every deployed file is deploy-owned.

Then, once per deploy:

- **`.gitignore`** gets a managed block (`# BEGIN/END ai-native-deployment`)
  that ignores the whole deployed payload, so target repos don't commit it.
- **`.ai-deploy-manifest.json`** — target-local state: rendered file hashes,
  absolute paths, source commit, timestamp. Gitignored. This is what `status`
  compares against.
- **`.ai-deploy-lock.json`** — portable: canonical file hashes and source
  commit, no machine paths. Deliberately *not* gitignored, so a target repo can
  commit it and prove which protocol version it is running.
- **`.registry/repos.local.json`** in *this* repo — a machine-local inventory of
  deployed targets, so `aii-2 status --all` can sweep them.

`aii-2 deploy --dry-run` previews add/update/unchanged/blocked without writing.

### What `status` reports

`aii-2 status <target>` reads the manifest and reports drift, nonzero exit on
any finding:

| kind | meaning |
|---|---|
| `target modified` | a deployed file's bytes or mode no longer match what the deploy left |
| `canonical changed` | this repo moved ahead, in content or in mode; target needs a re-deploy |
| `missing target file` | a deployed file was deleted |
| `extra deployed file` | tracked in the manifest but gone from `canonical/` |
| `stale eager import` | the loader points at a memory doc that is no longer the current entrypoint, or routing points somewhere illegal |
| `ambiguous memory entrypoint` | both `x.md` and `x/index.md` exist for one topic |
| `shadowed skill` | a deployed skill name also exists personal-level, where it takes precedence |
| `invalid manifest entry` | a malformed manifest record, or a receipt that records no deployed mode (written before modes were recorded) |

---

## Layer 2 — what a target repo looks like

```
<target>/
  CLAUDE.md                    deploy-owned  loader: imports + verb→contract map
  .ai-protocol/                deploy-owned  contracts, schemas, caller procedure
  .claude/ .cursor/ .codex/    deploy-owned  hook wiring per tool
  .ai-deploy-manifest.json     deploy-owned  local state (gitignored)
  .ai-deploy-lock.json         deploy-owned  portable proof (committable)

  .ai/                         TARGET-OWNED  the project's memory snapshot
  .ai-tasks/                   TARGET-OWNED  one file per task + index
```

The split above is the thing to understand: **`.ai/` and `.ai-tasks/` are not
deployed.** They are created by `/ai-init` inside the target and evolve with the
project — `.ai/` version-controlled there (memory §1), `.ai-tasks/` covered by
the managed gitignore block so task files stay local. Deploy never writes
either, and never reads them except to answer one question: which file is each
eager memory topic currently in.

That single dependency is where this system used to leak, and it is what
[Entrypoint resolution](#entrypoint-resolution) covers.

---

## Layer 3 — three surfaces, one eager set

The same set of documents has to reach the model under three tools with three
different context-assembly mechanisms.

| | Claude Code | Cursor | Codex CLI |
|---|---|---|---|
| wiring | `.claude/settings.json` | `.cursor/hooks.json` | `.codex/config.toml` |
| how protocol text arrives | native `@import` in `CLAUDE.md` | `sessionStart` hook injects `additional_context` | `SessionStart` hook injects `additionalContext` |
| entrypoint resolution | **static** — resolved at deploy time | **dynamic** — resolved per session by the hook | **dynamic** — resolved per session by the hook |
| session id | `$CLAUDE_CODE_SESSION_ID` | injected by the hook (no env var exists) | injected by the hook (`session_id`) |
| fallback if hook fails | n/a (imports are native) | `rules/protocol.mdc` (`alwaysApply`) lists the read order | `.codex/README.md` documents user-scope promotion |
| session-end check | `Stop` hook | `stop` hook | `Stop` hook |

Notes that matter in practice:

- **The Cursor and Codex hooks are ports of each other.** Same eager set, same
  resolution order, same housekeeping hint; only the stdin/stdout JSON shape
  differs. The Codex hook additionally self-gates on the deployed protocol
  marker (`.ai-protocol/protocols/conduct.md`), so it stays a silent no-op in
  unrelated repos even when promoted to user scope — the documented fallback
  when project-scope hooks don't fire in the TUI.
- **`AI_ORCH=1` disables the Cursor hook.** Orchestrated sessions
  (`.cursor/orchestrator/`) assemble their own context and own the lifecycle;
  double injection would fight them.
- **Skills ship on the ordinary channel.** `canonical/claude/skills/` deploys
  to `<target>/.claude/skills/` with everything else, so the manifest and the
  lock cover them and each target's skills match its protocol revision. Claude
  Code finds them by native discovery; Cursor and Codex are pointed at
  `.claude/skills/<name>/SKILL.md` by their rule/injection text. A leftover
  `~/.claude/skills/<name>/` copy overrides the deployed one and no content
  hash can detect it, so `status` checks the personal skills root by name and
  reports `shadowed skill` — the same shape as `ambiguous memory entrypoint`:
  two legal locations present, the wrong one winning.

---

## Layer 4 — the loading contract

What ends up in the context window, per `.ai-protocol/meta/memory.md` §2:

**Eager — always present:**

- the loader (`CLAUDE.md`) — carries the verb→contract mapping
- `conduct.md`, `taskfile.md`, `memory.md` — behavior + the two data schemas
- `.ai/index.md`, `.ai/map.md` — routing spine, always single-file
- the current entrypoint of `overview`, `architecture`, `design`, `conventions`
- `.ai-tasks/index.md`

**Lazy — read on demand via `.ai/index.md` routing:** everything else in `.ai/`
(modules, apis, features, sub-indexes). A task may name 2–5 of these in its
`prefetch:` field.

**Delivered, never ambient — role contracts.** `dev-advancement`,
`dev-remediation`, `review`, `plan`, `intake` are *not* in the eager set. The
caller hands the contract text over at invocation: the `/invoke` skill, a paste
(runbook §6), or wrapper injection by the orchestrator. "Read it on demand" is
not a delivery channel — a contract that isn't in context isn't in force.

---

## Entrypoint resolution

The memory protocol lets a doc that outgrows its size limit be upgraded from a
single file to a directory: `.ai/design.md` becomes `.ai/design/index.md`, with
`.ai/index.md` routing re-pointed. It is a rename, so exactly one form exists at
a time — and which form is current is **target state**, while the loader that
imports it is **deploy-owned**.

That is the whole problem in one sentence: two owners, one line of text.

Every surface now answers "which file is topic X in?" the same way:

1. `.ai/index.md` routing table, if it names one of the two legal forms and that
   file exists;
2. else file shape — directory form if `x/index.md` exists and `x.md` does not;
3. else the single-file default.

The Cursor and Codex hooks run this per session, in shell. Deploy runs it in
Python when it renders `CLAUDE.md`, because Claude Code's `@import` is static
text and cannot resolve anything at load time.

**Why this needed fixing.** Deploy used to write the canonical single-file form
unconditionally, so a housekeeping upgrade was silently reverted on the next
deploy. Status was already tolerant of both forms for hashing — which meant the
revert was invisible from both directions. And Claude Code ignores a missing
`@import` with no error at all: the document simply disappears from the eager
set. Three silent failures stacked on one line.

**What now covers it, per layer:**

| layer | mechanism |
|---|---|
| deploy write path | `bytes_for_target()` resolves entrypoints per target, so a `@import` upgrade survives a re-deploy — and a loader left on the old path is repaired by one |
| deploy status | `stale eager import` / `ambiguous memory entrypoint` — explicit checks, because the content hash is deliberately blind to which form is deployed |
| session start (Claude) | the SessionStart hook re-runs the same check and warns in-session, covering the window between a housekeeping split and the next deploy |
| session start (Cursor/Codex) | nothing to cover — those hooks resolve dynamically every session |
| protocol | `memory.md` §2/§4 state that the forms are mutually exclusive and that a static loader must be re-pointed |
| tests | `tests/test_hook_eager_set.py` asserts the hooks and the deploy tool cover the same four topics, in the same order, with matching path forms |

The four upgradeable topics are `overview`, `architecture`, `design`,
`conventions`. `index.md` and `map.md` are the routing spine and stay
single-file — a directory-form `map/index.md` is drift, and status says so.

---

## Common operations

```bash
./aii-2 deploy ../target-repo            # deploy or upgrade
./aii-2 deploy ../target-repo --dry-run  # preview, writes nothing
./aii-2 status ../target-repo            # drift check, nonzero exit on drift
./aii-2 status --all                     # sweep every registered target
scripts/boundary-lint.sh                 # charter + reference invariants
python -m pytest tests/ -q               # deploy, hook-consistency, orchestrator
```

**After changing anything in `canonical/`**, every deployed target reports
`canonical changed` until it is re-deployed. That is the intended signal, not a
problem to suppress.

**After a housekeeping split in a target**, re-deploy that target. Cursor and
Codex sessions pick up the new entrypoint immediately; Claude sessions need the
loader rewritten, which is what the deploy does.
