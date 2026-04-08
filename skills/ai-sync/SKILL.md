---
name: ai-sync
description: Sync .ai/ knowledge base after code changes. Auto-trigger when a code modification task is completed (not mid-task). Skip if no code was modified and no design decisions were made.
---

ai-coding-memory.md is already in context (loaded by CLAUDE.md). It is the
authoritative source for all constraints, content admission tests, and
maintenance procedures. Refer to it directly — do not re-read.

All .ai/ content docs (overview, architecture, design, modules, apis, features,
conventions, map, index) are also loaded into context via CLAUDE.md. Do NOT
re-read them — use the in-context versions directly when checking and editing.
Only Read a file if context has been compressed and its content is no longer
visible.

For incremental syncs (multiple syncs in one session): use the up-to-date
content already in context (including prior sync edits), but only assess
code changes made since the last sync to determine what needs updating.

Assess: did this session's changes since last sync make any .ai/ document
inaccurate or incomplete within its defined scope?

- NO  → output "No sync needed" and stop.
- YES → continue below.

Execute ai-coding-memory.md Section 3 → Consistency (Steps 1–3) exactly:

1. **Step 1** — List modules/features/APIs touched. Consult map.md + index.md
   to find ALL content docs that describe them — not just the single most
   obvious doc. A module dependency change affects both modules.md AND
   architecture.md; a new API affects both apis.md AND the feature's entry
   in features.md.
2. **Step 2** — For each affected content doc, consult map/index for
   horizontally related docs (same module, same feature chain, same API).
   Check each for inconsistency. Update inline, bump `last-updated`.
   If cannot verify → add `sync-todo` (cap 3 per doc).
3. **Step 3** — Update map.md / index.md ONLY if the relation graph changed.
4. Create a `.ai/tasks/` entry ONLY if a non-trivial decision/pitfall/tech-debt
   was encountered.
5. Output: "Sync complete: [updated docs]" or "No update needed"
6. **Update sync marker** — after completing (whether sync was needed or not),
   run: `.ai/scripts/sync-hash.sh > .ai/.sync-hash`
   This records the current code-diff hash so hooks know ai-sync has run.

All write constraints (content admission, size limits, format) from
ai-coding-memory.md apply. Do NOT paraphrase or condense those rules —
read and follow them from source.
