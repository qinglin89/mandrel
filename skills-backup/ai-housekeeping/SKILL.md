---
name: ai-housekeeping
description: Scan `.ai/` snapshot for structural drift (oversize docs, sub-item dominance) and apply memory §4 upgrade procedure per user confirmation. Manually invoked when SessionStart hint flags drift or `.ai/.housekeeping-pending` exists. Clears the flag when scan-clean.
---

The memory protocol §4 (Size limits, Upgrade procedure, Layered routing invariant; deployed as `.ai-protocol/meta/memory.md`) is in baseline context. This skill applies §4 reactively, per doc.

## Invocation

Manual only. Triggers:
- SessionStart hook surfaced pending `.ai/.housekeeping-pending`
- User notices drift; invokes ad-hoc
- Periodic project hygiene

## Procedure

1. **Scan** all snapshot docs (`.ai/*.md`, `.ai/<topic>/index.md`, `.ai/<topic>/*.md`):
   - Approximate token count (`wc -c` / 4)
   - Flag if: content doc > 3000 tokens, index doc > 1500 tokens, or single section > 50% of parent
   - **Skip** docs with frontmatter `housekeeping-exempt: true` (project-level opt-out for intentionally large docs)

2. **Report** flagged docs to user, sorted by severity (most over limit first). For each: path, current size / limit, what upgrade is needed.

3. **Plan** the upgrade per doc (don't apply yet):
   - Source path
   - Proposed `<topic>/index.md` + sub-files names
   - Routing changes (`.ai/index.md` / `.ai/map.md`)

4. **Show plan + ask confirmation per doc**. User chooses: apply, skip, defer. Process docs largest-first by default.

5. **Apply per confirmed doc** (one doc at a time, separate commits):
   a. Rename / move files per §4 upgrade procedure.
   b. Spawn sub-files with frontmatter (`last-updated` today, `verified-against` from `git rev-parse HEAD`).
   c. Update parent / top-level routing as needed.
   d. Verify new sub-files within size limit; if any still over, flag for further user decision.
   e. Commit: `chore(.ai): split <doc> into directory form per §4`.

6. **Rescan** after applying all confirmed upgrades. If clean (no remaining flagged docs):
   - Remove `.ai/.housekeeping-pending` flag file.
   - Commit removal as part of last housekeeping commit, or as separate `chore: clear housekeeping flag`.

   If issues remain (user skipped some, or sub-files still over):
   - Rewrite `.ai/.housekeeping-pending` with remaining issues.

7. **Print summary**: docs processed, sub-files created, routing updates, deferred/skipped items.

## Flag file format

`.ai/.housekeeping-pending` (plain text, one issue per line):

```
.ai/modules/oracle.md SIZE 5850/3000
.ai/some-large-index.md INDEX_SIZE 2100/1500
.ai/big-feature.md DOMINANCE 65%
```

Format: `<path> <issue-type> <details>`. Written by `/ai-sync-v2` verify step; read by SessionStart hook + this skill.

## Edge cases

- **No issues found**: print "no maintenance needed"; if flag file exists, remove it (stale flag).
- **User skips all**: keep flag with remaining issues; SessionStart will continue hinting.
- **Sub-file still over after split**: surface for further decomposition decision; don't recurse silently.
- **`.ai-tasks/` issues**: not in scope (different file space, not snapshot).
- **Concurrent /ai-sync-v2 running**: don't interleave; assume serialized execution per user.

## Exempt mechanism

A doc that's intentionally large (e.g., a core module reference that doesn't decompose well) can opt out of housekeeping flags by adding to its frontmatter:

```
---
last-updated: YYYY-MM-DD
verified-against: <sha>
housekeeping-exempt: true
---
```

This skill and `/ai-sync-v2`'s scan should skip these. Use sparingly — exempt should be the exception, not the workaround.
