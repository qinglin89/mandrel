# ai-native-deployment

This repository is the canonical, version-controlled home for the AI-native
coding protocol suite and deployment tooling.

Target repositories should receive copies from this repository. Deployed files
should not be hand-edited in target repos; edit canonical files here, redeploy,
and use `status` to verify drift.

## Repository Layout

- `canonical/repo-root/` deploys to the target repo root.
- `canonical/protocols/` deploys to target `.ai-protocol/protocols/` (role contracts).
- `canonical/workflow/` deploys to target `.ai-protocol/workflow/` (runbook, rolemapping, boundary-skill specs).
- `canonical/meta/` deploys to target `.ai-protocol/meta/` (taskfile schema, memory protocol, init).
- `canonical/cursor/` deploys to target `.cursor/`.
- `canonical/codex/` deploys to target `.codex/`.
- `canonical/claude/` deploys to target `.claude/`.
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
- `missing target file`
- `extra deployed file`

Drift returns a nonzero exit code.

For `CLAUDE.md` only, status treats memory topic imports as equivalent when
they differ only by single-file versus directory entrypoint form, e.g.
`@.ai/design.md` and `@.ai/design/index.md`. Other files and other edits
remain exact hash checks.

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
`.cursor/orchestrator/HANDOFF-orch-hub-config-profiles.md`.

## Temporary Global Skills Sync

The current tested Claude/Cursor/Codex workflow still uses global Claude skills
as the operational skill source:

```text
~/.claude/skills/<name>/SKILL.md
```

This repository keeps a parked copy in `skills-backup/`. That directory is not
part of target repo deployment. To refresh the global Claude skill directory
from the parked backup, use the temporary compatibility command:

```bash
./aii-2 skills sync-claude-global --dry-run
./aii-2 skills sync-claude-global
```

The command copies only the managed skills listed in `skills-backup/README.md`
from `skills-backup/` to `~/.claude/skills/`. It does not delete extra global
skills, does not write a manifest, and does not update target repo manifests or
lockfiles. It exists to preserve the current tested hook model until skills are
migrated to repo-local deployment in a separate task.

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

Run tests with:

```bash
python -m pytest
```

The test suite uses temporary source and target repos, so it does not modify
`../quantx`.
