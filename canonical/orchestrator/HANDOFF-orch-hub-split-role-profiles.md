# Handoff: split dev/review launch profiles in orch-hub

## Outcome

Replace Start run's single Mode selector with independent DEV and REVIEW
profile selectors. Keep Backend shared.

Each role selector has `Default`, `Standard`, `Excellent`, and `Custom`.
For a preset, show the role's resolved agent/model/effort read-only. Selecting
Custom reveals editable fields only for that role.

## Orchestrator contract

The deployed orchestrator now accepts:

```text
--dev-profile default|standard|excellent
--review-profile default|standard|excellent
```

The existing `--profile standard|excellent` remains supported as a
backward-compatible shorthand for selecting the same profile for both roles.
An omitted role-specific flag inherits `--profile`; an explicit
role-specific `default` clears the run-wide profile for that role and restores
environment/config inheritance.

Resolution is independent per role:

```text
explicit role model/effort flag
  > role-specific profile
  > run-wide --profile
  > environment
  > orchestrator.toml
```

`Custom` is an orch-hub UI mode, not an orchestrator profile.

### Effective-config query

Query both role selections in one subprocess:

```bash
<python> <orchestrator.py> --print-config \
  --backend cc-codex \
  --dev-profile standard \
  --review-profile excellent
```

The additive `profiles` object is authoritative:

```json
{
  "schema_version": 1,
  "profile": "default",
  "profiles": {
    "dev": "standard",
    "review": "excellent"
  },
  "available_profiles": ["excellent", "standard"],
  "dev": {
    "agent": "claude",
    "model": "claude-opus-4-8",
    "effort": "max"
  },
  "review": {
    "agent": "codex",
    "model": "gpt-5.6-sol",
    "effort": "xhigh"
  }
}
```

The legacy top-level `profile` reports only the run-wide `--profile`
selection. Keep accepting older deployed orchestrators that omit `profiles`;
for those, derive both role selections from top-level `profile`.

## Launch argument mapping

Do not pass a role-profile flag for Default unless orch-hub also passes the
legacy `--profile` flag. Normal split-mode launches should not need the
legacy flag.

| DEV | REVIEW | Arguments |
|---|---|---|
| Default | Default | no profile/model/effort flags |
| Excellent | Default | `--dev-profile excellent` |
| Default | Standard | `--review-profile standard` |
| Standard | Excellent | both role-profile flags |
| Custom | Excellent | explicit dev flags plus `--review-profile excellent` |
| Standard | Custom | `--dev-profile standard` plus explicit review flags |

For custom cc-codex dev, emit `--dev-agent`, `--dev-model`, and
`--dev-effort`. For custom Cursor dev, omit `--dev-agent`. Custom review emits
`--review-model` and `--review-effort`.

## orch-hub implementation scope

1. Extend the config-query helper and API endpoint with `dev_profile` and
   `review_profile` query parameters.
2. Parse the new `profiles` object while retaining compatibility with the
   older single-profile response.
3. Replace the single Mode control with independent DEV and REVIEW profile
   controls; keep Backend shared.
4. Query/repaint the full effective config whenever Backend or either role
   selection changes.
5. Update launch-argument construction for every preset/custom combination.
6. Persist and display both role selections in run snapshots/history.
7. Cover argument construction, API forwarding/parsing, old-orchestrator
   compatibility, preset/custom mixing, and dialog keyboard behavior.
