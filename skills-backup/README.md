# Skills Backup

This directory is a parked backup of the current AI-native workflow skills from:

```text
~/.claude/skills/
```

It is intentionally not part of the active deployment payload yet.

## Current Runtime Model

The currently tested runtime model still uses the global Claude skills as the
single operational source:

```text
~/.claude/skills/<name>/SKILL.md
```

Claude Code can use those skills directly. Cursor and Codex use the same files
because the deployed hooks/rules point agents at the global Claude skill path.
This is deliberate for now: the workflow has been tested with this arrangement,
and changing skill discovery paths would require another full validation pass.

## Why This Exists

The files here preserve the current skill definitions in version control so they
are not only stored in machine-local home directory state. They are useful for
reviewing, diffing, and future migration planning.

These files are not currently:

- deployed by `aii-2 deploy`
- referenced by target repo hooks
- treated as canonical runtime source
- installed into `.agents/skills`, `.cursor/skills`, or `.claude/skills`

## Temporary Sync Command

`aii-2` has a temporary compatibility command that can refresh the global Claude
skills from this backup:

```bash
./aii-2 skills sync-claude-global --dry-run
./aii-2 skills sync-claude-global
```

`--dry-run` reports which skills would be added, updated, or left unchanged
without writing `~/.claude/skills`.

The real sync copies the managed skill directories from `skills-backup/` into
`~/.claude/skills/`. It does not remove extra global skills, does not create an
ai-native manifest, and does not update any target repo manifest or lockfile.
This command is temporary and exists only for the current tested global-skill
hook model.

## Backed Up Skills

Current top-level skills copied from `~/.claude/skills/`:

- `ai-housekeeping`
- `ai-init`
- `ai-load`
- `ai-sync`
- `ai-sync-v2`
- `ctd-tasks`
- `intake-task`
- `invoke` (added at protocol-cut P5a — caller-side role-contract delivery
  for interactive sessions; not copied FROM `~/.claude/skills/`, canonical
  here first)

The existing `~/.claude/skills/bak/` directory was not copied because it is
already historical backup material rather than an active top-level skill.

## Future Migration Note

If these skills are later canonicalized as deployed payload, prefer doing that
as a separate task. The likely target model is:

```text
canonical/skills/<name>/SKILL.md
```

with deploy-time copies or generated wrappers for the agent-specific discovery
locations that are actually needed:

```text
target/.claude/skills/<name>/SKILL.md
target/.agents/skills/<name>/SKILL.md
target/.cursor/skills/<name>/SKILL.md
```

Before making that switch, update hooks/rules to reference repo-local skills and
re-test Claude, Cursor, Codex, and orchestrator close-out behavior end to end.
