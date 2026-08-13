# ai-native-deployment

This repository is the canonical, version-controlled home for the AI-native
coding protocol suite and deployment tooling.

Target repositories should receive copies from this repository. Deployed files
should not be hand-edited in target repos; edit canonical files here, redeploy,
and use `status` to verify drift.

For the whole chain — source of truth → deploy → target layout → the three IDE
surfaces → what actually ends up in the context window — see
[ARCHITECTURE.md](ARCHITECTURE.md). This README is the operational reference.

## Repository Layout

- `canonical/repo-root/` deploys to the target repo root.
- `canonical/protocols/` deploys to target `.ai-protocol/protocols/` (role contracts).
- `canonical/workflow/` deploys to target `.ai-protocol/workflow/` (runbook, rolemapping, boundary-skill specs).
- `canonical/meta/` deploys to target `.ai-protocol/meta/` (taskfile schema, memory protocol, init).
- `canonical/cursor/` deploys to target `.cursor/`.
- `canonical/codex/` deploys to target `.codex/`.
- `canonical/claude/` deploys to target `.claude/`, including the workflow
  skills under `canonical/claude/skills/`.
- `canonical/orchestrator/` deploys to target `.cursor/orchestrator/`.
- `ai_native_deployment/` contains deploy, manifest, registry, status, and CLI code.
- `.registry/repos.local.json` is a gitignored local inventory of managed repos.

The orchestrator is a canonical deployment payload in this repository.
Runtime changes are made and tested under `canonical/orchestrator/`, then
deployed to target repositories; deployed copies should not be hand-edited.

## Deploy

Run from this repository:

```bash
python -m ai_native_deployment.cli deploy ../some-target-repo
```

The repo-local wrapper is shorter and does not require installation:

```bash
./aii-2 deploy ../some-target-repo
```

To make the deployed orchestrator runtime-ready in the target repo, bootstrap
its local virtualenv during deploy:

```bash
./aii-2 deploy --bootstrap-orchestrator ../some-target-repo
```

This creates or updates `.cursor/orchestrator/.venv` with `python3.14 -m venv`,
upgrades `pip`, installs `.cursor/orchestrator/requirements.txt`, and creates
`.cursor/orchestrator/.env` from `.env.example` only if `.env` does not already
exist. Use `--orchestrator-python /path/to/python` if `python3.14` is not the
right executable on the machine. Credentials and CLI logins are still local:
log in to `claude` and `codex` for the default `cc-codex` backend, or set
`CURSOR_API_KEY` when explicitly using `--backend cursor`.

If installed in editable mode, the same command is exposed as:

```bash
aii-2 deploy ../some-target-repo
```

Deployment writes:

- canonical files into the target paths listed above
- rendered `.codex/config.toml` from `canonical/codex/config.toml.template`
- target `.ai-deploy-manifest.json` for local status checks
- target `.ai-deploy-lock.json` as a portable deploy receipt suitable for Git
- an idempotent `.gitignore` block in the target repo that ignores deployed
  payload files, `.ai-tasks/`, and the local manifest, but leaves the lockfile
  trackable
- a local registry entry in `.registry/repos.local.json`
- with `--bootstrap-orchestrator`: target `.cursor/orchestrator/.venv/` and a
  non-overwriting `.cursor/orchestrator/.env` scaffold

`canonical/codex/config.toml.template` may contain `{{REPO_ROOT}}`; deploy
renders that placeholder to the absolute target repo path.

Preview a deploy without writing the target repo:

```bash
./aii-2 deploy --dry-run ../some-target-repo
```

Dry-run reports which managed files would be added, updated, left unchanged, or
blocked by a non-file target path. It does not write payload files, manifests,
lockfiles, `.gitignore`, registry entries, or orchestrator bootstrap files.

## Status

Check one repo:

```bash
python -m ai_native_deployment.cli status ../some-target-repo
./aii-2 status ../some-target-repo
```

Check every locally registered repo:

```bash
python -m ai_native_deployment.cli status --all
```

Status reads the target `.ai-deploy-manifest.json` and reports:

- `in sync`
- `target modified`
- `canonical changed`
- `stale eager import`
- `ambiguous memory entrypoint`
- `shadowed skill`
- `missing target file`
- `extra deployed file`

Drift returns a nonzero exit code.

### Memory entrypoints in `CLAUDE.md`

`CLAUDE.md` is deploy-owned, but which file each eager memory topic
(`overview`, `architecture`, `design`, `conventions`) lives in is target state:
the memory protocol upgrades a doc from `.ai/design.md` to
`.ai/design/index.md` once it outgrows the size limit. Deploy therefore
resolves each import against the target's own `.ai/` — `.ai/index.md` routing
first, then file shape — using the same order as the cursor and codex
session-start hooks, so all three surfaces load the same file. A directory-form
upgrade survives the next deploy instead of being reverted, and a loader left
on the pre-upgrade path is repaired by it.

For `CLAUDE.md` only, status treats the two entrypoint forms as equivalent for
hashing. Because that makes the hash blind to which form is deployed, the two
entrypoint checks are reported separately:

- `stale eager import` — the loader imports a path that is no longer the
  current entrypoint (Claude Code ignores missing imports silently, so the
  document just goes absent), or `.ai/index.md` routes a topic somewhere that
  is neither entrypoint form.
- `ambiguous memory entrypoint` — both `x.md` and `x/index.md` exist. The
  upgrade renames; it does not duplicate.

Other files and other edits remain exact hash checks.

### Shadowed skills

The same blind spot in the other direction: every deployed skill file can hash
correctly and still not be the one that runs. Agent tools resolve same-named
skills personal-level over project-level, so a leftover
`~/.claude/skills/<name>/SKILL.md` wins over the deployed copy — silently, and
no content comparison can see it.

- `shadowed skill` — a deployed skill name also exists as a personal-level
  skill. The detail names the file that takes precedence; removing it restores
  the deployed contract.

The check reads the personal skills root and writes nothing. It looks under
`~/.claude/skills`; set `AI_NATIVE_DEPLOYMENT_CLAUDE_SKILLS_ROOT` if the agent
home is relocated. See [One-time operator cleanup](#one-time-operator-cleanup).

The manifest is target-local state: it includes rendered file hashes and local
absolute paths, so it should remain ignored. The lockfile is portable: it
records canonical file hashes and source commit information without target
machine paths, so target repos may commit it for auditability.

## Registry

The registry is local machine inventory, not GitHub truth:

```bash
python -m ai_native_deployment.cli registry list --json
./aii-2 registry list --json
python -m ai_native_deployment.cli registry add ../some-target-repo
python -m ai_native_deployment.cli registry remove some-target-repo
```

`registry add` requires the target repo to already have a readable
`.ai-deploy-manifest.json`; otherwise run `deploy` first. `registry remove`
only removes local tracking. It does not delete deployed files, manifests,
hooks, or repo contents.

Future `orch-hub` tooling can consume `.registry/repos.local.json` to discover
managed repos on this machine, then start each target repo's deployed
`.cursor/orchestrator/orchestrator.py` as a subprocess. This repository does
not implement orch-hub. The deployed orchestrator exposes its effective
defaults and named model/effort profiles as machine-readable JSON:

```bash
.cursor/orchestrator/.venv/bin/python \
  .cursor/orchestrator/orchestrator.py --print-config
```

See `.cursor/orchestrator/README.md` for the resolution precedence and profile
contract. A focused orch-hub implementation handoff is deployed as
`.cursor/orchestrator/HANDOFF-orch-hub-split-role-profiles.md`.

## Skills

Workflow skills are ordinary canonical payload. They live in
`canonical/claude/skills/<name>/` and deploy to:

```text
<target>/.claude/skills/<name>/SKILL.md
```

Like every other deployed file they are hashed into `.ai-deploy-manifest.json`
and `.ai-deploy-lock.json`, so a target's protocol revision covers its skills
too. Claude Code discovers them natively; the Cursor rule and the Codex
SessionStart injection point at the same repo-relative path.

Deployed set: `ai-housekeeping`, `ai-init`, `ai-load`, `ai-sync-v2`,
`ctd-tasks`, `intake-task`, `invoke`.

`ai-sync` and `session-ai-audit` were retired on 2026-08-09. `ai-sync`
duplicated `ai-sync-v2`'s job against a task layout this protocol no longer
uses, and its description advertised an auto-trigger for `.ai/` writes that
the memory contract's closeout-only invariant forbids; `session-ai-audit`
drove a `scripts/session_ai_audit.py` that this repository has never carried,
so it could not run in any target. Deploy does not prune, so targets deployed
before that date keep both copies — see the cleanup below.

### One-time operator cleanup

These skills used to be installed machine-globally under `~/.claude/skills/` by
an `aii-2 skills sync-claude-global` command that no longer exists. **Remove the
leftover global copies**, because personal-level skills override project-level
ones ([skill precedence](https://code.claude.com/docs/en/skills)): a stale
global copy silently wins over the deployed one, and no content hash can see
it — deploy succeeds, the manifest and lock record the skills as deployed, and
`status` reports `in sync`.

**Redeploy every managed target first.** A target still on a pre-migration
payload has no project-level copy, so deleting the global one leaves it with
neither — and the stop hooks on all three backends point at `ai-sync-v2` for
task closeout. Order matters:

```bash
./aii-2 status --all                      # find targets reporting canonical changed
./aii-2 deploy <each-target>              # project-level copies land first

ls ~/.claude/skills                       # review before deleting
rm -rf ~/.claude/skills/{ai-housekeeping,ai-init,ai-load,ai-sync,ai-sync-v2}
rm -rf ~/.claude/skills/{ctd-tasks,intake-task,invoke,session-ai-audit}

# the retired pair also has project-level copies to remove
rm -rf <each-target>/.claude/skills/{ai-sync,session-ai-audit}
```

The retired `ai-sync` and `session-ai-audit` stay in the personal-level
deletion above and get a project-level deletion of their own: they must go
from **both** roots. Redeploying replaces the other seven project-level
copies, but writes nothing for a name that has left the payload, and the
fresh manifest simply stops mentioning it — so `status` reports `in sync`
over a target that still has the orphan. Claude Code finds skills by scanning
`.claude/skills/`, not by reading the manifest, so an orphaned copy keeps
running.

Skills you added yourself that are not in the deployed or retired sets are
unaffected; delete only the names above.

`status` reports any name still shadowing a deployed skill as `shadowed skill`,
so a target that has not had this cleanup done says so instead of reporting
`in sync`.

## Evolution

`evolution/README.md` is the normative contract for changing the canonical
protocol suite from evidence. `aii-2 evolution` is the mechanical half of it:
it discovers already-complete evaluation reports, stages them, freezes an
immutable cohort when the admission policy allows, and prepares that cohort's
pending analysis task. It runs against this repository, not a target.

Nothing here starts or schedules an evaluation, and nothing here decides
policy: batch formation, change admission, and canary promotion stay human
gates.

```bash
./aii-2 evolution status                 # lifecycle phase; writes nothing
./aii-2 evolution list  --feed-dir ...   # inspect candidates; writes nothing
./aii-2 evolution sync  --feed-dir ...   # import eligible reports into the pool
./aii-2 evolution start --feed-dir ...   # sync, then freeze a batch if policy allows
```

Every subcommand takes `--repo <path>` to work on another checkout, and
`status` takes `--json` for the same shape a script can read. Exit status is 0
for any completed run — including a `start` that formed no batch, which is the
contract's normal outcome when evidence is still thin — and 2 for a refusal,
corrupt local state, lock contention, or an unusable feed.

`status` names a lifecycle phase — `idle`, `pool N/<target>`, `batch-frozen`,
`dispositions-ready`, `proposals-pending`, `implementing`, `candidate-ready`,
`supersede-pending`, `conclusion-pending` — and then the facts behind it:

```text
$ ./aii-2 evolution status
evolution: pool 4/20
  pool         4 unique completed task(s); target 20, minimum 10
               oldest pending report imported 5 day(s) ago, max wait 30
  admission    no batch — pool-below-minimum
  batches      0 frozen, none current
  revisions    none in play — no experiment has frozen a base
```

One label is chosen by what blocks the next action: an open batch first (it is
what stops another cohort forming), then an admitted change task in flight,
then drafts waiting at the admission gate, then the pool. Every fact is printed
regardless of which label won, so nothing is hidden by the choice.

Nothing is stored to produce that: the phase is re-derived on every call from
the manifests, the closure records and drafts beside them, the runtime pool,
`.ai-tasks/`, and git. `--json` emits the same facts, with a `schema_version`.
Two of them are machine-local — the admitted change tasks and an open batch's
analysis lifecycle both come from gitignored `.ai-tasks/` — so another clone
reads the batch's committed closure record instead.

### Report source

Reports come from orch-hub's protected global feed. Point the client at it with
two environment variables, named by `[source]` in `evolution/config.toml` and
never committed:

```bash
export ORCH_HUB_URL=https://orch-hub.example
export ORCH_HUB_TOKEN=...
```

Without those variables, `list`, `sync`, and `start` fail with one message
naming both of them and the offline path — they do not hang, and they do not
silently import nothing. The offline path is a local report bundle:

```text
<feed-dir>/reports/*.json                          one import record per file
<feed-dir>/artifacts/<report_key>/<artifact-name>  the four L1+L2 bodies
```

`--feed-dir` stays the way to run a deterministic cohort offline and to replay
a fixed one afterwards.

The wire contract, as orch-hub publishes it and the client was reconciled
against on 2026-08-12 (details in `ai_native_deployment/evolution/hub.py`):

```text
GET <ORCH_HUB_URL><report_feed_path>?limit=<n>[&after=<watermark>]
    -> {"enabled": true, "reports": [<catalog entry>...],
        "cursor": <int>, "next_cursor": <int>, "has_more": <bool>}
GET <ORCH_HUB_URL><report_feed_path>/<report_key>/artifacts/<wire name>
    -> the artifact bytes exactly as published
       404 unknown report key or artifact name   410 published, bytes pruned
       409 incoherent stored identity            500 unreadable artifact
```

Both requests carry `Authorization: Bearer $ORCH_HUB_TOKEN`. `has_more` is
required: its negation is what tells a later `freeze` that the pool is the whole
eligible set rather than a prefix, so it is read from the feed and never
inferred from a short page. The cursor is an integer watermark on the wire and
opaque above the client, which converts at that boundary.

A catalog entry is orch-hub's own shape, not this repository's import record:
the client translates one into the other so the offline feed stays
interchangeable. Two fields are derived rather than copied — `completed` from
the entry's `archived` (orch-hub catalogs only reports whose task was archived
at publication, and this protocol archives only at completion close-out), and
each artifact's `media_type` from its published filename. orch-hub publishes
none of this repository's provenance fields, so translated records carry them
null, and a release assessment reads that as provenance it never got rather
than inventing a revision.

Artifact bytes are served byte for byte and verified here against the digests
the feed itself published. The route reports a failed integrity check in a
header instead of hiding the body, and the client deliberately ignores that
header for decisions: the check belongs to the side that did not publish the
bytes.

Two bounds are the client's own rather than the feed's, and both refuse with a
message naming what to change. Both were confirmed against the published feed
rather than assumed — nothing it serves redirects, and its largest artifact is
three orders of magnitude under the bound:

- **No redirect is followed.** `urllib` would copy the `Authorization` header
  onto whatever host a `Location` named, so a checked URL would say nothing
  about where the token ends up. Point `ORCH_HUB_URL` at the final URL instead.
- **Every response body is read under a fixed 32 MiB bound**, not the
  `size_bytes` the record declares. A body that reaches the bound is rejected by
  the usual size/hash check rather than truncated into the pool.

`scripts/probe-orch-hub.py` checks all of that against the running service —
envelope, reported exhaustion, one entry translated into a record the real
import schema admits, and one report's artifact bytes hashed against the digests
the feed published. It is read-only and credentialed, so it is not in
`scripts/check.sh` and no CI runner can reach the feed; run it by hand with both
variables exported after either side changes.

### What lands where

| Path | Owner | Committed |
|---|---|---|
| `.ai-evolution/state.json` | controller runtime | no — machine-local |
| `.ai-evolution/imported-artifacts/` | raw fetched bundles | no — raw evidence |
| `.ai-evolution/lock` | single-writer guard | no |
| `evolution/batches/<id>/manifest.json` | frozen membership | yes, immutable |
| `evolution/batches/<id>/findings.md` | analysis dispositions | yes |
| `evolution/batches/<id>/analysis-complete.json` | closure record | yes |
| `evolution/batches/<id>/proposed-tasks/` | change-task drafts | yes, inert |
| `evolution/ledger.jsonl` | sanitized audit | yes, append-only |
| `.ai-tasks/<id>-analysis.md` | generated analysis task | no — machine-local |

Privacy follows that split. Raw report content and any diagnostic quoting a
feed value stay under ignored `.ai-evolution/`; the ledger carries identities,
hashes, and a bounded vocabulary of reason codes only. Credentials live in the
environment and appear in no file, URL, or error message this tool writes.

The closure record and the manifests are written by the controller but
committed by you, like any other versioned artifact. Until that commit lands, a
finished analysis reads as finished only on this machine.

### Recovery

- **Interrupted run.** Re-run the same command. A freeze commits manifest →
  state → task → ledger and each step is redoable, so the next `start` finishes
  whatever remains and reports what it repaired. Repair runs before the feed is
  contacted, so an outage never blocks it.
- **Lock held.** `evolution lock held: ... (pid N on HOST since TIME)` means
  another run holds `.ai-evolution/lock`. It is never broken automatically — a
  crashed holder and a slow one look identical. Remove the file once you have
  confirmed no run is active.
- **Corrupt runtime state.** `state.json` failures are reported, never
  repaired: a silent reset would rewind the discovery cursor and drop pending
  evidence. Restore the file, or delete `.ai-evolution/` and re-run `start` to
  rebuild the pool from the feed. The committed batches are what protect you
  there: discovery skips every report a frozen manifest already names, so a
  wipe re-imports only what no cohort has analyzed, and an open batch's claims
  and analysis task are recreated from its manifest. Raw bundles that were
  staged before the wipe are gone; `status` reports the batch's evidence as
  absent from this machine rather than as damage.
- **`pool-incomplete`.** No discovery pass has reported the feed drained, so
  the pool may be a prefix and no batch may form from it. Run `sync` until it
  reports the feed drained.
- **A generated analysis task that no longer looks like one.** The controller
  identifies it by its id line, its `# Batch analysis — <batch-id>` heading, and
  the manifest path it cites, then reads its lifecycle. Keep those three intact
  while working the task; if the file is damaged, restore them or remove the
  file so the next run can write it again.

## Not Copied

Import and deploy intentionally skip local or sensitive files:

- `.venv/`
- `logs/` and `.logs/`
- `sessions.json`
- `.env`
- `.env.*` except `.env.example`
- `.claude/settings.local.json`
- `.claude/projects/`
- Python cache files
- Git metadata

Do not place credentials or machine-local state in `canonical/`.

## Development

### Bootstrap

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

The `dev` extra is the complete tool set the verification gate needs — pytest,
ruff, shellcheck (via `shellcheck-py`), and the build backend. Nothing else has
to be installed by hand; the gate refuses to run rather than skip a check whose
tool is missing, and its error names the command above.

### One command

```bash
scripts/check.sh
```

This is the repository's only verification entrypoint. It runs from any working
directory, runs every check even after one fails, exits nonzero if any failed,
and leaves the working tree byte-identical (its last check asserts exactly
that). It runs:

| Check | What it covers |
|---|---|
| `whitespace` | trailing whitespace, CR line endings, final newline in tracked text files |
| `ruff` | Python lint (pyflakes plus the pycodestyle error rules) |
| `shellcheck` | every tracked shell script, found by extension or shebang |
| `boundary-lint` | `scripts/boundary-lint.sh` — canonical protocol boundaries |
| `pytest` | the `tests/` suite |
| `orchestrator-mock-loop` | `canonical/orchestrator/test_loop_mock.py` scenarios |
| `package-build` | wheel build, offline install into a throwaway venv, CLI starts |
| `tree-unchanged` | the run mutated no tracked file |

The interpreter is `.venv/bin/python` when present, otherwise `python3`; set
`AII_PYTHON` to override.

`canonical/orchestrator/test_loop_mock.py` is deliberately outside pytest's
`testpaths` — it is a standalone script with its own `main()`, and the gate is
the only thing that runs it. The same is true of the two shell checks. That is
what `tests/test_verification_gate.py` guards: it asserts the gate still
carries every required check, that every mock-loop scenario is wired into
`main()`, and that CI has not grown a copy of any check.

The suites use temporary source and target repos, so they do not modify
`../quantx`.

### Optional Git hook

```bash
scripts/install-git-hooks.sh
```

Installs a `pre-push` hook that calls `scripts/check.sh` and nothing else.
`git push --no-verify` skips it; deleting `.git/hooks/pre-push` uninstalls it.

Rerunning the installer over the hook it wrote changes nothing. Any other
`pre-push` hook is left untouched and reported, including one of your own that
calls the gate alongside its own commands — ownership is decided by the file's
full contents, so a hook is replaced only when it is exactly what the installer
writes. To adopt the managed hook, move the existing one aside first.

### CI

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, and manual
dispatch. It installs the `dev` extra and calls `scripts/check.sh` on Python
3.11 (the `requires-python` floor) and 3.14 (the orchestrator runtime). It
needs no repository secrets and requests only `contents: read`.

**Branch protection is a repository setting, not a file in this repo.** Until
the `gate (Python 3.11)` and `gate (Python 3.14)` checks are marked required
for `main` under Settings → Branches, a red run can still be merged. Adding the
workflow does not enable the gate; marking the checks required does.

`.github/workflows/smoke.yml` is the non-default counterpart: a manual
`workflow_dispatch` run of `canonical/orchestrator/smoke_hooks.py`, which drives
a live Cursor SDK agent to observe whether the deployed hooks fire. It needs
`CURSOR_API_KEY`, costs money, and reports observations rather than a verdict,
so it is classified out of the merge gate on purpose.

### Adding a check

Add a `check_*` function and one `run_check` line to the CHECKS section of
`scripts/check.sh`. CI and the Git hook call that script and never re-list its
steps, so a check added there is enforced everywhere at once. Do not add a
second entrypoint; if a new check needs a tool, declare it in the `dev` extra
so the preflight can fail closed on it.
