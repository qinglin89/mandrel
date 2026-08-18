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
--dev-agent claude|codex        # cc-codex only
--review-agent claude|codex     # cc-codex only
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

### Per-role CLI agent (`cc-codex`)

Both roles select a CLI agent the same way, and each agent carries its own
model namespace, environment variables, and effort vocabulary. The
model/effort FLAGS are per role (`--dev-model`/`--dev-effort`,
`--review-model`/`--review-effort`); which agent namespace they land in
follows that role's selected agent:

| Role | Agent selector (default) | Agent | Model / effort environment | Effort axis |
|---|---|---|---|---|
| dev | `--dev-agent` / `ORCH_CC_DEV_AGENT` (`claude`) | `claude` | `ORCH_CC_MODEL` / `ORCH_CC_EFFORT` | `low/medium/high/xhigh/max` |
| dev | (same) | `codex` | `ORCH_CODEX_DEV_MODEL` / `ORCH_CODEX_DEV_EFFORT` | `none/minimal/low/medium/high/xhigh` |
| review | `--review-agent` / `ORCH_CC_REVIEW_AGENT` (`codex`) | `claude` | `ORCH_CC_REVIEW_MODEL` / `ORCH_CC_REVIEW_EFFORT` | `low/medium/high/xhigh/max` |
| review | (same) | `codex` | `ORCH_CODEX_MODEL` / `ORCH_CODEX_EFFORT` | `none/minimal/low/medium/high/xhigh` |

Consequences for the Custom UI:

- The effort control for a role must follow that role's selected agent. An
  effort legal for one agent is refused for the other, with the axis named in
  stderr.
- Changing a profile's agent for a role also requires that role's explicit
  model and effort (`--dev-agent` needs `--dev-model` + `--dev-effort`;
  `--review-agent` needs `--review-model` + `--review-effort`). A profile is
  one complete agent+model+effort selection.
- Neither agent flag may be sent to `--backend cursor`.
- Selecting the same agent AND model for both roles is legal, and the run logs
  a `NOTICE:` line saying it has no cross-model independence. orch-hub can
  derive the same condition from the query response
  (`dev.agent`/`dev.model` versus `review.agent`/`review.model`) and warn
  before launch.

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
  "schema_version": 2,
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
  },
  "sources": {
    "dev.agent": "profile:standard",
    "review.agent": "profile:excellent"
  }
}
```

`review.agent` is now the selected CLI agent (`claude` or `codex`) on
`cc-codex`, exactly like `dev.agent`; on `--backend cursor` both roles report
the SDK and `dev.agent` stays null. `sources` gains `review.agent` beside the
existing `dev.agent`.

The legacy top-level `profile` reports only the run-wide `--profile`
selection. Keep accepting older deployed orchestrators that omit `profiles`;
for those, derive both role selections from top-level `profile`.

### Version compatibility

`schema_version` is `2` from this change on. A deployed orchestrator still
reporting `1` pins review to Codex: do not send `--review-agent` to it, and
treat its `review.agent` as fixed. Read the version from the query response
rather than from the target's deployment state.

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
`--dev-effort`; custom cc-codex review emits `--review-agent`,
`--review-model`, and `--review-effort`. For custom Cursor roles, omit both
agent flags and emit only model/effort.

## orch-hub implementation scope

1. Extend the config-query helper and API endpoint with `dev_profile` and
   `review_profile` query parameters.
2. Parse the new `profiles` object while retaining compatibility with the
   older single-profile response, and read `review.agent` (schema 2) instead
   of assuming Codex review.
3. Replace the single Mode control with independent DEV and REVIEW profile
   controls; keep Backend shared.
4. Query/repaint the full effective config whenever Backend or either role
   selection changes.
5. Update launch-argument construction for every preset/custom combination,
   including `--review-agent` and the review effort list that follows the
   selected review agent.
6. Persist and display both role selections in run snapshots/history.
7. Cover argument construction, API forwarding/parsing, old-orchestrator
   compatibility (`schema_version` 1 versus 2), preset/custom mixing,
   per-agent effort lists, and dialog keyboard behavior.
