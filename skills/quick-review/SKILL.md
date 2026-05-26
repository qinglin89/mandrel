---
name: quick-review
description: Review uncommitted code changes against an anti-pattern checklist and report findings. Report-only, no auto-fix. Auto-trigger after substantial code modification.
---

Code changes from this session are already in context. DO NOT re-read edited
files. Only Read additional code when:
- A referenced function/variable is not visible in current context
- The diff alone is insufficient to judge an anti-pattern

## Procedure

1. Run `git diff HEAD -- '*.go' '*.proto' '*.yaml'` to get the full uncommitted
   change set. If output is empty, output "SIMPLIFY: clean" and skip to step 4.
2. For each changed region, apply the checklist below using in-context code.
3. Output findings in the format shown below. REPORT ONLY — do not fix.
4. **Update marker** — after output, run:
   `.ai/scripts/sync-hash.sh > .ai/.simplify-hash`
   This records that review ran on the current code-diff hash.

## Checklist

### A. Duplication / Stacking
- Adjacent `if` blocks checking the same variable → should merge
- New code duplicating surrounding logic → should reuse
- New small function overlaps an existing one → extend existing instead
- Same condition evaluated multiple times in the same scope

### B. Dead Code
- Old code now unreachable due to the change
- Variables / fields / imports no longer used
- Stale comments referring to removed code
- Leftover `// removed X` / `// TODO: delete` markers

### C. Over-Defense
- Nil checks on internally-known non-nil values
- Validation of invariants guaranteed by callers
- Error handling for scenarios that cannot occur

### D. Premature Abstraction
- Helpers with a single call site
- Parameters / flags added for non-existent future use cases
- New interfaces with a single implementation

### E. Verbosity
- `if ... else { return }` that could be early return
- Multi-line logic with a clean one-liner equivalent
- Inaccurate or misleading variable names introduced by the change

## Output Format

If no issues:
```
SIMPLIFY: clean
```

If issues found (keep each item to 2 lines max):
```
SIMPLIFY: <N> issue(s)
1. [<category>] <file>:<line>
   <problem> → <suggestion>
2. ...
```

Categories: `dup`, `dead`, `defense`, `abstract`, `verbose`.

## Scope

- Changed lines plus their immediate surroundings (same function / block).
- Do NOT review unrelated files.
- Do NOT flag pre-existing issues untouched by the diff.
- Do NOT suggest style-only changes (gofmt territory).
- Do NOT propose refactors beyond the scope of the diff.

## After Output

Report-only. The user will decide whether to act. The marker update in
step 4 is unconditional — "review ran" is the completion criterion, not
"all issues resolved".
