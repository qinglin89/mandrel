# Cross-model review workflow (Codex pointer)

The workflow text is single-sourced. Read and follow the canonical document:
`ai-coding-review-v2.md` (repo root, gitignored alongside the other
`ai-coding-*.md` protocol files).

This pointer exists because `.codex/hooks/session-start.sh` references this
path (editing that script would invalidate its recorded hook trust). All
content — procedure, review-entry shape (`Verdict:` / `Group:` / `Findings:`),
and the convergence rules (findings ledger, convergence groups, per-group
round budget) — lives in the canonical file, including the Codex-specific
native-reviewer note. Do not duplicate it here.
