# Handoff: orch-hub launch profiles and effective configuration

> Superseded for the Start run UI by
> `HANDOFF-orch-hub-split-role-profiles.md`. This document describes the
> original single-profile integration and remains only as compatibility
> background.

## Goal

Replace the current free-form start-run flag form with a preset selector:

- **Default** — show the orchestrator's effective inherited values; do not
  pass profile/model/effort flags.
- **Standard** — show the resolved `standard` profile and launch with
  `--profile standard`.
- **Excellent** — show the resolved `excellent` profile and launch with
  `--profile excellent`.
- **Custom** — let the user choose/type the applicable values and launch with
  explicit flags.

The orchestration policy remains owned by each target repo's deployed
orchestrator. orch-hub must query the orchestrator and must not parse
`orchestrator.toml` or duplicate profile model ids.

## Implemented orchestrator contract

The implementation is in ai-native-deployment's
`canonical/orchestrator/` payload and reaches targets through the normal
deployment command.

### Backward compatibility

`--profile` is optional. Every existing command without it remains valid.

Resolution precedence:

```text
explicit CLI flag > named --profile > environment > orchestrator.toml
```

Supported profiles are currently `standard` and `excellent`. `default` and
`custom` are orch-hub UI modes, not values passed to `--profile`.

### Effective-config query

Run the deployed orchestrator with `--print-config`. A task id is not
required, and this path does not require credentials, a clean tree, or a
provider model-catalog call.

```bash
<repo>/.cursor/orchestrator/.venv/bin/python \
  <repo>/.cursor/orchestrator/orchestrator.py \
  --print-config
```

Query a profile for a backend:

```bash
<python> <orchestrator.py> --print-config \
  --backend cursor --profile standard

<python> <orchestrator.py> --print-config \
  --backend cc-codex --profile excellent
```

Use the repo root as `cwd`, exactly as for a real run. Parse stdout as one JSON
object. A nonzero exit is a configuration/query failure and should be shown
to the user; do not silently substitute orch-hub defaults.

Representative response:

```json
{
  "schema_version": 1,
  "config_revision": "<sha256-of-orchestrator.toml>",
  "effective_revision": "<sha256-of-resolved-policy>",
  "profile": "default",
  "available_profiles": ["excellent", "standard"],
  "backend": "cc-codex",
  "dev": {
    "agent": "claude",
    "model": "claude-opus-4-8",
    "effort": "max"
  },
  "review": {
    "agent": "codex",
    "model": "gpt-5.5",
    "effort": "xhigh"
  },
  "max_sessions": 40,
  "context_budget": 200000,
  "codex_sandbox": "danger-full-access",
  "task_id": null,
  "once": false,
  "plan_gate": false,
  "control_dir": null,
  "sources": {
    "backend": "config",
    "codex_sandbox": "config",
    "context_budget": "config",
    "dev.agent": "config",
    "dev.model": "config",
    "dev.effort": "config",
    "max_sessions": "config",
    "review.model": "config",
    "review.effort": "config"
  }
}
```

The `sources` map is diagnostic provenance. Expected values are `cli`,
`profile:<name>`, `env:<NAME>`, and `config`.

### Current profile values

These are informational; orch-hub must use query results rather than hardcode
them.

| Profile/backend | Dev | Review |
|---|---|---|
| Standard / Cursor | `claude-opus-4-8 @ max` | `gpt-5.5 @ xhigh` |
| Standard / cc-codex | `claude:claude-opus-4-8 @ max` | `codex:gpt-5.5 @ xhigh` |
| Excellent / Cursor | `claude-fable-5 @ max` | `gpt-5.6-sol @ xhigh` |
| Excellent / cc-codex | `claude:claude-fable-5 @ max` | `codex:gpt-5.6-sol @ xhigh` |

Model namespaces differ by backend; this is why profiles are resolved in the
orchestrator.

### Actual-run snapshot

Every real run logs a stable startup line:

```text
effective-config: {<compact JSON>}
```

The JSON is the same shape as `--print-config`, including the actual task id,
launch booleans, control directory, resolved sources, and configuration
revision. orch-hub should persist this actual snapshot in run history. It is
authoritative if the preview and launch-time configuration differ.

## orch-hub implementation

### Start dialog

Add a required mode selector with:

1. `Default`
2. `Standard`
3. `Excellent`
4. `Custom`

Initialize the backend selector from the Default query. The committed
orchestrator default is `cc-codex`, so Default, Standard, Excellent, and
Custom all begin on `cc-codex`; Cursor remains an explicit user selection.

When the selected backend changes, re-query the selected mode:

- Default: `--print-config --backend <backend>`; omit `--backend` too if the
  user is inheriting the configured backend rather than selecting one.
- Standard: `--print-config --backend <backend> --profile standard`.
- Excellent: `--print-config --backend <backend> --profile excellent`.
- Custom: begin from the Default query, then make the applicable fields
  editable.

For Default, Standard, and Excellent, model/effort/dev-agent fields are
read-only and show the query result. Showing the `sources` provenance is
useful in an advanced/details section, especially when an environment
override changes Default.

For Custom, prefer selects for backend, dev agent, and effort. Model may stay
free-form if orch-hub has no provider catalog. Validate nonempty model ids
before launch; the orchestrator remains the final effort/config validator.

### Command construction

Continue passing task id and the hub-owned control directory for every run.
Pass `--once`, `--plan-gate`, and `--max-sessions` only according to the
existing UI semantics.

Default:

```bash
<python> <orchestrator.py> <task-id> \
  --control-dir <run-dir>
```

If the user explicitly selected a non-default backend, append
`--backend <backend>`. Do not append `--profile`, `--dev-model`,
`--dev-effort`, `--review-model`, or `--review-effort`.

Standard/Excellent:

```bash
<python> <orchestrator.py> <task-id> \
  --control-dir <run-dir> \
  --backend <backend> \
  --profile <standard|excellent>
```

Do not separately repeat the profile's model/effort flags.

Custom:

```bash
<python> <orchestrator.py> <task-id> \
  --control-dir <run-dir> \
  --backend <backend> \
  [--dev-agent <claude|codex>] \
  --dev-model <id> \
  --dev-effort <effort> \
  --review-model <id> \
  --review-effort <effort>
```

`--dev-agent` applies only to `cc-codex`; never pass it to Cursor.

The orchestrator permits explicit fields to override a named profile.
orch-hub should not mix profile and custom UI modes. One important guard in
the orchestrator: changing a cc-codex profile's `--dev-agent` also requires
explicit `--dev-model` and `--dev-effort`.

### Preview freshness

Cache query results only by:

```text
repo + backend + profile + effective_revision
```

`config_revision` tracks the TOML file alone; `effective_revision` also
changes when an environment override or selected resolution changes. It is
acceptable for Default to inherit a newer configuration at launch. Always
replace/store the preview with the JSON from the run's `effective-config:`
line. A future strict revision check can be added if needed; it is not part
of the current CLI contract.

### Failure behavior

- Query failure: disable Start and show stderr.
- Unknown/unavailable profile: refresh `available_profiles`; do not fall back
  to Default silently.
- Launch-time configuration failure: mark the run failed and surface stderr.
- Never expose `CURSOR_API_KEY` or other credentials. The query output
  intentionally contains no secret values.

## Acceptance checks

- Existing Default start still launches without `--profile`.
- Default fields exactly match `--print-config`.
- Environment overrides appear in Default with `env:<NAME>` provenance.
- Standard and Excellent fields exactly match profile queries for both
  backends.
- Custom emits explicit applicable flags and never sends `--dev-agent` to
  Cursor.
- Run history stores the actual `effective-config:` snapshot.
- A query error prevents launch rather than using duplicated UI defaults.
- Existing start/stop/control-dir/escalation behavior is unchanged.
