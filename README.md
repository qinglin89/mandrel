# ai-native-deployment

This repository is the canonical, version-controlled home for the AI-native
coding protocol suite and deployment tooling.

Target repositories should receive copies from this repository. Deployed files
should not be hand-edited in target repos; edit canonical files here, redeploy,
and use `status` to verify drift.

## Repository Layout

- `canonical/repo-root/` deploys to the target repo root.
- `canonical/cursor/` deploys to target `.cursor/`.
- `canonical/codex/` deploys to target `.codex/`.
- `canonical/claude/` deploys to target `.claude/`.
- `canonical/orchestrator/` deploys to target `.cursor/orchestrator/`.
- `ai_native_deployment/` contains deploy, manifest, registry, status, and CLI code.
- `.registry/repos.local.json` is a gitignored local inventory of managed repos.

The orchestrator is treated as deployment payload only in this repository. Its
runtime behavior is intentionally not changed here.

## Deploy

Run from this repository:

```bash
python -m ai_native_deployment.cli deploy ../some-target-repo
```

The repo-local wrapper is shorter and does not require installation:

```bash
./aii-2 deploy ../some-target-repo
```

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

`canonical/codex/config.toml.template` may contain `{{REPO_ROOT}}`; deploy
renders that placeholder to the absolute target repo path.

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
not implement orch-hub.

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
