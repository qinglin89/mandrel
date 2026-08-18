#!/usr/bin/env python3
"""Dev↔review orchestrator for the ai-protocol workflow (local,
single-machine, status-driven state machine, pluggable execution backend).

Runs the `dev → review → dev → …` loop over one `.ai-tasks/` task, with a
different model per role. The orchestrator is a dumb scheduler: it reads task
status + session log, advances or pauses, and never decides. Any
human-decision event — Confirm-tier, load-bearing uncertainty, disputed
finding, over-budget convergence group — pauses the loop and pulls the human
in on stdin.

Backends (--backend):
  cursor   fresh Cursor SDK agent per session (dev=Opus-4.8,
           review=GPT-5.5 by default). Hooks do NOT run under the SDK, so the
           orchestrator injects protocol context itself and exports AI_ORCH=1
           to keep the .cursor hooks quiet. Needs CURSOR_API_KEY.
  cc-codex (default) each role picks its own CLI agent: dev defaults to
           Claude Code headless (`claude -p`, opus-4.8 @ max effort) and
           review to Codex CLI (`codex exec`, gpt-5.5 @ xhigh effort);
           `--dev-agent` / `--review-agent` swap either one. No Cursor
           dependency; each tool's own hook/import chain loads the protocol
           natively, so the orchestrator does NOT inject it. Post-checks stay
           on as an end-discipline backstop.

Lifecycle the orchestrator owns (both backends):
  - post-session checks: clean tree, session-log entry, legal status per role
  - convergence-group budget counting (Group: field in review entries)
  - blocked → surface question → resume with answer
  - completed → ai-sync-v2 close-out via the review agent → verify archive
  - optional --plan-gate: every dev session is preceded by a read-only
    planning session that iterates a plan-report with the human and blocks
    until confirmation; only the confirmed plan-report (never conversation
    history) is injected into the fresh dev session

Usage:
    .venv/bin/python orchestrator.py <task-id> [--once] [--backend B]
        [--profile standard|excellent]
        [--dev-profile default|standard|excellent]
        [--review-profile default|standard|excellent]
        [--plan-gate] [--dev-agent claude|codex]
        [--review-agent claude|codex]
        [--dev-model ID] [--review-model ID]
        [--dev-effort E] [--review-effort E] [--max-sessions N]
    .venv/bin/python orchestrator.py --print-config [the same selection flags]
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from pathlib import Path

# --- paths -----------------------------------------------------------------

ORCH_DIR = Path(__file__).resolve().parent
REPO = ORCH_DIR.parents[1]  # the target repo root
TASKS_DIR = REPO / ".ai-tasks"
ARCHIVE_DIR = TASKS_DIR / "archive"
INDEX_FILE = TASKS_DIR / "index.md"
SESSION_START_SH = REPO / ".cursor" / "hooks" / "session-start.sh"
# Canonical review contract (single source; the .cursor/.codex files are
# pointers to it — inject the real text, not a pointer).
REVIEW_RULE = REPO / ".ai-protocol" / "protocols" / "review.md"
# Canonical plan contract — injected into the plan-gate prompt the same way
# (plan-rule wrapper ahead of the gate instruction).
PLAN_RULE = REPO / ".ai-protocol" / "protocols" / "plan.md"
# Canonical dev contracts, one self-contained file per mode. The caller
# certifies the mode: the was_remediation predicate selects which single
# contract is injected (review/plan mirror).
DEV_ADVANCEMENT_RULE = (REPO / ".ai-protocol" / "protocols"
                        / "dev-advancement.md")
DEV_REMEDIATION_RULE = (REPO / ".ai-protocol" / "protocols"
                        / "dev-remediation.md")
# Single-source prompt/banner templates + the postcheck contract (see
# prompts/README.md). ORCH_DIR-relative: survives repo layout changes and
# needs no override in the mock suite. The headless conduct annex is the
# entry/conduct-annex template (the automation-mode rules).
PROMPTS_DIR = ORCH_DIR / "prompts"
POSTCHECK_CONTRACT = PROMPTS_DIR / "postcheck-contract.md"
# Close-out skill, rendered into the close-out prompt for the session to read.
# REPO-anchored like the other deployed protocol paths: the skills ship as
# canonical payload into .claude/skills/, and the personal-level (home) skill
# set they replaced is retired.
SYNC_SKILL = REPO / ".claude" / "skills" / "ai-sync-v2" / "SKILL.md"
LOG_DIR = ORCH_DIR / "logs"
SESSION_MAP = LOG_DIR / "sessions.json"  # sid -> {tool, native_id} (cli backend)

# --- env file --------------------------------------------------------------

def _load_env_file() -> None:
    """Load KEY=VALUE pairs from .cursor/orchestrator/.env (gitignored with
    the rest of the directory). File values take precedence; a key absent or
    empty in the file falls back to the exported environment."""
    env_file = ORCH_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and val:
            os.environ[key] = val


_load_env_file()

# --- policy ----------------------------------------------------------------

# The parameter axis differs per model family (and so does the value
# vocabulary: claude family low..max, gpt none..extra-high).
EFFORT_AXIS = {"claude": "effort", "fable": "effort", "gpt": "reasoning"}
# Canonical top-tier spelling at the flag level is codex's `xhigh`; Cursor's
# gpt `reasoning` axis calls the same tier `extra-high`. Translate per
# consumer so either spelling works everywhere.
CURSOR_REASONING_ALIASES = {"xhigh": "extra-high"}
CODEX_EFFORT_ALIASES = {"extra-high": "xhigh"}

# Both cc-codex roles select from the same CLI agent set; each agent
# carries its own model namespace and its own effort axis.
CLI_AGENTS = {"claude", "codex"}
AGENT_EFFORT_AXIS = {"claude": "effort", "codex": "reasoning"}

# Startup effort allowlists, keyed by parameter axis. The server accepts
# unknown effort values SILENTLY (verified 2026-07-03 with a bogus value) and
# falls back to the default effort — so a typo would silently downgrade the
# run. Reject client-side instead.
EFFORT_ALLOWED = {
    # claude/fable axis (cursor `effort` param, claude CLI --effort)
    "effort": {"low", "medium", "high", "xhigh", "max"},
    # gpt/codex axis (cursor `reasoning` param, codex model_reasoning_effort);
    # extra-high = cursor's spelling of codex's xhigh (aliased both ways)
    "reasoning": {"none", "minimal", "low", "medium", "high", "xhigh",
                  "extra-high"},
}


def effort_error(axis: str, value: str | None) -> str | None:
    """None if the effort value is valid for the axis (or unset), else a
    refusal message. Called at startup for both backends."""
    if not value:
        return None
    allowed = EFFORT_ALLOWED.get(axis, EFFORT_ALLOWED["effort"])
    if value in allowed:
        return None
    return (f"invalid effort '{value}' for the {axis} axis — allowed: "
            + "/".join(sorted(allowed))
            + " (the server accepts unknown values silently and falls back "
              "to the default effort, so a typo would silently downgrade "
              "the run — refusing)")


CONFIG_FILE = ORCH_DIR / "orchestrator.toml"
CONFIG_SCHEMA_VERSION = 2
BACKENDS = {"cursor", "cc-codex"}
CODEX_SANDBOX_ALLOWED = {"read-only", "workspace-write",
                         "danger-full-access"}


class OrchestratorConfigError(ValueError):
    """The committed orchestrator configuration is missing or malformed."""


def _table(parent: dict, key: str, where: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise OrchestratorConfigError(f"{where}.{key} must be a table")
    return value


def _string(parent: dict, key: str, where: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorConfigError(
            f"{where}.{key} must be a non-empty string")
    return value


def _positive_int(parent: dict, key: str, where: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OrchestratorConfigError(
            f"{where}.{key} must be a positive integer")
    return value


def _effort_axis(model: str) -> str:
    for prefix, axis in EFFORT_AXIS.items():
        if model.startswith(prefix):
            return axis
    return "effort"


def _validate_role(where: str, table: dict, *, axis: str | None = None,
                   review_axis: str | None = None,
                   flat: bool = True) -> None:
    """Validate a role table. `flat` = a both-roles table (dev_* / review_*),
    where `axis` is the dev axis; otherwise a single agent's model/effort
    table, where `axis` is that agent's axis whichever role owns it."""
    if flat:
        dev_model = _string(table, "dev_model", where)
        dev_effort = _string(table, "dev_effort", where)
        review_model = _string(table, "review_model", where)
        review_effort = _string(table, "review_effort", where)
        axes = (axis or _effort_axis(dev_model),
                review_axis or _effort_axis(review_model))
        values = ((f"{where}.dev_effort", axes[0], dev_effort),
                  (f"{where}.review_effort", axes[1], review_effort))
    else:
        model = _string(table, "model", where)
        effort = _string(table, "effort", where)
        values = ((f"{where}.effort", axis or _effort_axis(model),
                   effort),)
    for label, value_axis, effort in values:
        error = effort_error(value_axis, effort)
        if error:
            raise OrchestratorConfigError(f"{label}: {error}")


def load_orchestrator_config(
        path: Path = CONFIG_FILE) -> tuple[dict, str]:
    """Load and validate the deployed TOML policy plus its content hash."""
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise OrchestratorConfigError(
            f"cannot read orchestrator config {path}: {err}") from err
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as err:
        raise OrchestratorConfigError(
            f"cannot parse orchestrator config {path}: {err}") from err

    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise OrchestratorConfigError(
            f"{path}: schema_version must be {CONFIG_SCHEMA_VERSION}")
    defaults = _table(config, "defaults", "config")
    backend = _string(defaults, "backend", "defaults")
    if backend not in BACKENDS:
        raise OrchestratorConfigError(
            f"defaults.backend must be one of {sorted(BACKENDS)}")
    _positive_int(defaults, "max_sessions", "defaults")
    _positive_int(defaults, "context_budget", "defaults")
    sandbox = _string(defaults, "codex_sandbox", "defaults")
    if sandbox not in CODEX_SANDBOX_ALLOWED:
        raise OrchestratorConfigError(
            "defaults.codex_sandbox must be one of "
            f"{sorted(CODEX_SANDBOX_ALLOWED)}")

    cursor = _table(defaults, "cursor", "defaults")
    _validate_role("defaults.cursor", cursor)
    cc = _table(defaults, "cc-codex", "defaults")
    for role in ("dev", "review"):
        agent = _string(cc, f"{role}_agent", "defaults.cc-codex")
        if agent not in CLI_AGENTS:
            raise OrchestratorConfigError(
                f"defaults.cc-codex.{role}_agent must be claude or codex")
        cc_role = _table(cc, role, "defaults.cc-codex")
        for agent_name, axis in sorted(AGENT_EFFORT_AXIS.items()):
            _validate_role(f"defaults.cc-codex.{role}.{agent_name}",
                           _table(cc_role, agent_name,
                                  f"defaults.cc-codex.{role}"),
                           axis=axis, flat=False)

    profiles = _table(config, "profiles", "config")
    if not profiles:
        raise OrchestratorConfigError("config.profiles must not be empty")
    for name, profile in profiles.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise OrchestratorConfigError(
                f"invalid profile name {name!r}")
        if not isinstance(profile, dict):
            raise OrchestratorConfigError(
                f"profiles.{name} must be a table")
        _validate_role(f"profiles.{name}.cursor",
                       _table(profile, "cursor", f"profiles.{name}"))
        profile_cc = _table(profile, "cc-codex", f"profiles.{name}")
        agents = {}
        for role in ("dev", "review"):
            agents[role] = _string(profile_cc, f"{role}_agent",
                                   f"profiles.{name}.cc-codex")
            if agents[role] not in CLI_AGENTS:
                raise OrchestratorConfigError(
                    f"profiles.{name}.cc-codex.{role}_agent must be claude "
                    "or codex")
        _validate_role(f"profiles.{name}.cc-codex", profile_cc,
                       axis=AGENT_EFFORT_AXIS[agents["dev"]],
                       review_axis=AGENT_EFFORT_AXIS[agents["review"]])

    revision = hashlib.sha256(raw).hexdigest()
    return config, revision


ORCH_CONFIG, ORCH_CONFIG_REVISION = load_orchestrator_config()
CONFIG_DEFAULTS = ORCH_CONFIG["defaults"]
CONFIG_CURSOR = CONFIG_DEFAULTS["cursor"]
CONFIG_CC = CONFIG_DEFAULTS["cc-codex"]


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _env_int(name: str, fallback: int) -> int:
    value = _env(name)
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except ValueError as err:
        raise OrchestratorConfigError(
            f"{name} must be a positive integer, got {value!r}") from err
    if parsed <= 0:
        raise OrchestratorConfigError(
            f"{name} must be a positive integer, got {value!r}")
    return parsed


# Compatibility names retained for imports, help text, and existing tests.
# Their fallback values now come from orchestrator.toml instead of literals.
DEFAULT_BACKEND = _env("ORCH_BACKEND") or CONFIG_DEFAULTS["backend"]
DEFAULT_DEV_MODEL = _env("ORCH_DEV_MODEL") or CONFIG_CURSOR["dev_model"]
DEFAULT_REVIEW_MODEL = (_env("ORCH_REVIEW_MODEL")
                        or CONFIG_CURSOR["review_model"])
DEFAULT_CURSOR_DEV_EFFORT = (_env("ORCH_CURSOR_DEV_EFFORT")
                             or CONFIG_CURSOR["dev_effort"])
DEFAULT_CURSOR_REVIEW_EFFORT = (_env("ORCH_CURSOR_REVIEW_EFFORT")
                                or CONFIG_CURSOR["review_effort"])
DEFAULT_CC_DEV_AGENT = (_env("ORCH_CC_DEV_AGENT")
                        or CONFIG_CC["dev_agent"])
DEFAULT_CC_REVIEW_AGENT = (_env("ORCH_CC_REVIEW_AGENT")
                           or CONFIG_CC["review_agent"])
DEFAULT_CC_MODEL = (_env("ORCH_CC_MODEL")
                    or CONFIG_CC["dev"]["claude"]["model"])
DEFAULT_CC_EFFORT = (_env("ORCH_CC_EFFORT")
                     or CONFIG_CC["dev"]["claude"]["effort"])
DEFAULT_CODEX_DEV_MODEL = (_env("ORCH_CODEX_DEV_MODEL")
                           or CONFIG_CC["dev"]["codex"]["model"])
DEFAULT_CODEX_DEV_EFFORT = (_env("ORCH_CODEX_DEV_EFFORT")
                            or CONFIG_CC["dev"]["codex"]["effort"])
DEFAULT_CC_REVIEW_MODEL = (_env("ORCH_CC_REVIEW_MODEL")
                           or CONFIG_CC["review"]["claude"]["model"])
DEFAULT_CC_REVIEW_EFFORT = (_env("ORCH_CC_REVIEW_EFFORT")
                            or CONFIG_CC["review"]["claude"]["effort"])
DEFAULT_CODEX_MODEL = (_env("ORCH_CODEX_MODEL")
                       or CONFIG_CC["review"]["codex"]["model"])
DEFAULT_CODEX_EFFORT = (_env("ORCH_CODEX_EFFORT")
                        or CONFIG_CC["review"]["codex"]["effort"])
# Per-agent environment names, keyed by (role, agent). The codex review pair
# keeps its historical unqualified names — codex was review-only when they
# were introduced.
AGENT_ENV_NAMES = {
    ("dev", "claude"): ("ORCH_CC_MODEL", "ORCH_CC_EFFORT"),
    ("dev", "codex"): ("ORCH_CODEX_DEV_MODEL", "ORCH_CODEX_DEV_EFFORT"),
    ("review", "claude"): ("ORCH_CC_REVIEW_MODEL", "ORCH_CC_REVIEW_EFFORT"),
    ("review", "codex"): ("ORCH_CODEX_MODEL", "ORCH_CODEX_EFFORT"),
}
# Display/backward-compatibility constant. ORCH_MAX_SESSIONS is parsed by the
# resolver only when no CLI value supersedes it, so a stale invalid env value
# cannot block an explicit --max-sessions override.
DEFAULT_MAX_SESSIONS = CONFIG_DEFAULTS["max_sessions"]
CONTEXT_BUDGET = _env_int(
    "ORCH_CONTEXT_BUDGET", CONFIG_DEFAULTS["context_budget"])
CONTEXT_BUDGET_SOURCE = ("env:ORCH_CONTEXT_BUDGET"
                         if _env("ORCH_CONTEXT_BUDGET") else "config")
CODEX_SANDBOX = (_env("ORCH_CODEX_SANDBOX")
                 or CONFIG_DEFAULTS["codex_sandbox"])
CODEX_SANDBOX_SOURCE = ("env:ORCH_CODEX_SANDBOX"
                        if _env("ORCH_CODEX_SANDBOX") else "config")


@dataclasses.dataclass(frozen=True)
class ResolvedLaunchConfig:
    # `profile` is the backward-compatible run-wide selector. The role
    # profiles are authoritative after applying role-specific overrides.
    profile: str | None
    dev_profile: str | None
    review_profile: str | None
    backend: str
    dev_agent: str | None
    dev_model: str
    dev_effort: str
    review_agent: str | None
    review_model: str
    review_effort: str
    max_sessions: int
    context_budget: int
    codex_sandbox: str
    sources: dict[str, str]

    def public_dict(self) -> dict:
        result = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "config_revision": ORCH_CONFIG_REVISION,
            "profile": self.profile or "default",
            "profiles": {
                "dev": self.dev_profile or "default",
                "review": self.review_profile or "default",
            },
            "available_profiles": sorted(ORCH_CONFIG["profiles"]),
            "backend": self.backend,
            "dev": {
                "agent": self.dev_agent,
                "model": self.dev_model,
                "effort": self.dev_effort,
            },
            "review": {
                "agent": ("cursor" if self.backend == "cursor"
                          else self.review_agent),
                "model": self.review_model,
                "effort": self.review_effort,
            },
            "max_sessions": self.max_sessions,
            "context_budget": self.context_budget,
            "codex_sandbox": (
                self.codex_sandbox if self.backend == "cc-codex" else None),
            "sources": dict(sorted(self.sources.items())),
        }
        effective_bytes = json.dumps(
            result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        result["effective_revision"] = hashlib.sha256(
            effective_bytes).hexdigest()
        return result


def launch_config_dict(resolved: ResolvedLaunchConfig,
                       args: argparse.Namespace) -> dict:
    """Public, serializable snapshot of every launch-relevant selection."""
    result = resolved.public_dict()
    result.update({
        "task_id": args.task_id,
        "once": bool(args.once),
        "plan_gate": bool(args.plan_gate),
        "control_dir": (
            str(args.control_dir.expanduser().resolve())
            if args.control_dir is not None else None),
    })
    return result


def same_model_notice(config: dict) -> str | None:
    """Launch notice when both roles resolved to the same agent AND model.

    Legal — the roles still run in separate conversations — but the review
    prompt states cross-model independence, so a run that reviews its own
    model's work should say so out loud. Derived from the public launch
    configuration, so a supervisor can compute the same condition from
    `--print-config` instead of parsing the log."""
    dev, review = config["dev"], config["review"]
    # Only cc-codex names a per-role CLI agent; on cursor both roles are the
    # one SDK (dev.agent stays null there for response compatibility).
    agents = [role["agent"] or config["backend"] for role in (dev, review)]
    if (agents[0], dev["model"]) != (agents[1], review["model"]):
        return None
    return (f"NOTICE: dev and review both resolved to {agents[1]}:"
            f"{review['model']} — the review prompt states cross-model "
            "independence, and this run has none: same model, separate "
            "conversations only.")


def _resolved_value(*, cli_value, profile_table: dict | None,
                    profile_key: str, env_name: str | None,
                    config_value, profile_name: str | None) -> tuple[object, str]:
    if cli_value is not None:
        return cli_value, "cli"
    if profile_table is not None and profile_key in profile_table:
        return profile_table[profile_key], f"profile:{profile_name}"
    if env_name and (value := _env(env_name)) is not None:
        return value, f"env:{env_name}"
    return config_value, "config"


def resolve_launch_config(
        *, backend: str | None = None, profile: str | None = None,
        dev_profile: str | None = None,
        review_profile: str | None = None,
        dev_agent: str | None = None, dev_model: str | None = None,
        review_agent: str | None = None,
        review_model: str | None = None, dev_effort: str | None = None,
        review_effort: str | None = None,
        max_sessions: int | None = None) -> ResolvedLaunchConfig:
    """Resolve CLI > role profile > run profile > env > TOML defaults."""
    profiles = ORCH_CONFIG["profiles"]
    for label, selected, allow_default in (
            ("profile", profile, False),
            ("dev profile", dev_profile, True),
            ("review profile", review_profile, True)):
        if (selected is not None
                and selected not in profiles
                and not (allow_default and selected == "default")):
            available = sorted(profiles)
            if allow_default:
                available.insert(0, "default")
            raise OrchestratorConfigError(
                f"unknown {label} {selected!r}; available: "
                + ", ".join(available))

    # An omitted role flag inherits the legacy run-wide profile. Explicit
    # `default` clears it for that role and restores env/config inheritance.
    effective_dev_profile = (
        profile if dev_profile is None
        else None if dev_profile == "default"
        else dev_profile)
    effective_review_profile = (
        profile if review_profile is None
        else None if review_profile == "default"
        else review_profile)

    effective_backend, backend_source = _resolved_value(
        cli_value=backend, profile_table=None, profile_key="backend",
        env_name="ORCH_BACKEND", config_value=CONFIG_DEFAULTS["backend"],
        profile_name=profile)
    if effective_backend not in BACKENDS:
        raise OrchestratorConfigError(
            f"invalid backend {effective_backend!r}; available: "
            + ", ".join(sorted(BACKENDS)))
    if (effective_backend == "cc-codex"
            and CODEX_SANDBOX not in CODEX_SANDBOX_ALLOWED):
        raise OrchestratorConfigError(
            "ORCH_CODEX_SANDBOX must be one of "
            f"{sorted(CODEX_SANDBOX_ALLOWED)}")
    dev_profile_table = (
        profiles[effective_dev_profile][effective_backend]
        if effective_dev_profile is not None else None)
    review_profile_table = (
        profiles[effective_review_profile][effective_backend]
        if effective_review_profile is not None else None)
    sources = {"backend": backend_source}
    sources["context_budget"] = CONTEXT_BUDGET_SOURCE
    sources["codex_sandbox"] = CODEX_SANDBOX_SOURCE

    effective_max, max_source = _resolved_value(
        cli_value=max_sessions, profile_table=None, profile_key="max_sessions",
        env_name="ORCH_MAX_SESSIONS",
        config_value=CONFIG_DEFAULTS["max_sessions"],
        profile_name=profile)
    try:
        effective_max = int(effective_max)
    except (TypeError, ValueError) as err:
        raise OrchestratorConfigError(
            f"max_sessions must be a positive integer, got "
            f"{effective_max!r}") from err
    if effective_max <= 0:
        raise OrchestratorConfigError(
            f"max_sessions must be a positive integer, got {effective_max!r}")
    sources["max_sessions"] = max_source

    if effective_backend == "cursor":
        for flag, selected in (("--dev-agent", dev_agent),
                               ("--review-agent", review_agent)):
            if selected is not None:
                raise OrchestratorConfigError(
                    f"{flag} is only supported with --backend cc-codex")
        fields = {}
        for key, cli_value, env_name in (
                ("dev_model", dev_model, "ORCH_DEV_MODEL"),
                ("dev_effort", dev_effort, "ORCH_CURSOR_DEV_EFFORT"),
                ("review_model", review_model, "ORCH_REVIEW_MODEL"),
                ("review_effort", review_effort,
                 "ORCH_CURSOR_REVIEW_EFFORT")):
            role_profile = (
                effective_dev_profile if key.startswith("dev_")
                else effective_review_profile)
            role_profile_table = (
                dev_profile_table if key.startswith("dev_")
                else review_profile_table)
            fields[key], sources[key.replace("_", ".", 1)] = _resolved_value(
                cli_value=cli_value, profile_table=role_profile_table,
                profile_key=key, env_name=env_name,
                config_value=CONFIG_CURSOR[key], profile_name=role_profile)
        effective_dev_agent = effective_review_agent = None
    else:
        # Both roles resolve the same way: agent first, then that agent's own
        # model/effort namespace.
        agents: dict[str, str] = {}
        field_spec = []
        for role, cli_agent, cli_model, cli_effort in (
                ("dev", dev_agent, dev_model, dev_effort),
                ("review", review_agent, review_model, review_effort)):
            role_profile = (effective_dev_profile if role == "dev"
                            else effective_review_profile)
            role_profile_table = (dev_profile_table if role == "dev"
                                  else review_profile_table)
            agent, agent_source = _resolved_value(
                cli_value=cli_agent, profile_table=role_profile_table,
                profile_key=f"{role}_agent",
                env_name=f"ORCH_CC_{role.upper()}_AGENT",
                config_value=CONFIG_CC[f"{role}_agent"],
                profile_name=role_profile)
            if agent not in CLI_AGENTS:
                raise OrchestratorConfigError(
                    f"invalid cc-codex {role}_agent {agent!r}; "
                    "available: claude, codex")
            sources[f"{role}.agent"] = agent_source
            # A profile states one complete agent+model+effort selection;
            # swapping only the agent would silently keep the other agent's
            # model.
            if (role_profile_table is not None and cli_agent is not None
                    and cli_agent != role_profile_table[f"{role}_agent"]
                    and (cli_model is None or cli_effort is None)):
                raise OrchestratorConfigError(
                    f"overriding a profile's --{role}-agent also requires "
                    f"explicit --{role}-model and --{role}-effort")
            agents[role] = str(agent)
            agent_defaults = CONFIG_CC[role][agent]
            model_env, effort_env = AGENT_ENV_NAMES[(role, agent)]
            field_spec.extend((
                (f"{role}_model", cli_model, model_env,
                 agent_defaults["model"], role_profile, role_profile_table),
                (f"{role}_effort", cli_effort, effort_env,
                 agent_defaults["effort"], role_profile, role_profile_table),
            ))
        fields = {}
        for (key, cli_value, env_name, config_value, role_profile,
             role_profile_table) in field_spec:
            fields[key], sources[key.replace("_", ".", 1)] = _resolved_value(
                cli_value=cli_value, profile_table=role_profile_table,
                profile_key=key, env_name=env_name,
                config_value=config_value, profile_name=role_profile)
        effective_dev_agent, effective_review_agent = (agents["dev"],
                                                       agents["review"])

    dev_axis = (_effort_axis(str(fields["dev_model"]))
                if effective_backend == "cursor"
                else AGENT_EFFORT_AXIS[str(effective_dev_agent)])
    review_axis = (_effort_axis(str(fields["review_model"]))
                   if effective_backend == "cursor"
                   else AGENT_EFFORT_AXIS[str(effective_review_agent)])
    for label, axis, value in (
            ("dev_effort", dev_axis, str(fields["dev_effort"])),
            ("review_effort", review_axis,
             str(fields["review_effort"]))):
        error = effort_error(axis, value)
        if error:
            raise OrchestratorConfigError(f"{label}: {error}")

    return ResolvedLaunchConfig(
        profile=profile,
        dev_profile=effective_dev_profile,
        review_profile=effective_review_profile,
        backend=str(effective_backend),
        dev_agent=(str(effective_dev_agent)
                   if effective_dev_agent is not None else None),
        dev_model=str(fields["dev_model"]),
        dev_effort=str(fields["dev_effort"]),
        review_agent=(str(effective_review_agent)
                      if effective_review_agent is not None else None),
        review_model=str(fields["review_model"]),
        review_effort=str(fields["review_effort"]),
        max_sessions=effective_max,
        context_budget=CONTEXT_BUDGET,
        codex_sandbox=CODEX_SANDBOX,
        sources=sources,
    )


MAX_FOLLOWUPS = 3     # post-check violation round-trips per session
GROUP_BUDGET = 2      # changes-requested RE-reviews per group (so 3 entries total)
PLAN_GATE_BANNER_CHARS = 12_000
# --plan-gate report artifact: a reply revises the plan-report by restating
# it in full from this heading on; a reply that changes nothing must end
# with the unchanged sentinel line instead. Anything else keeps the current
# report (warn-and-keep).
PLAN_REPORT_START_RE = re.compile(
    r"^##\s*Goal\s*/\s*Acceptance\s*:?\s*$", re.MULTILINE | re.IGNORECASE)
PLAN_REPORT_UNCHANGED_RE = re.compile(
    r"^\s*PLAN-REPORT:\s*unchanged\s*[.。]?\s*$", re.MULTILINE | re.IGNORECASE)
HEARTBEAT_SILENCE = 30  # seconds without stream events before a log-file beat
GEN_WINDOW = 30       # seconds — [gen] aggregation window for text/thinking
CONTROL_POLL_SECONDS = 1.0  # --control-dir: answer / stop.flag poll interval
DISCUSSION_MARKER_RE = re.compile(r"^(?:[?？]|discuss\s*:)", re.IGNORECASE)
# Session context budget (tokens) — port of stop-context-check.sh. Its
# effective value is resolved from env/config above and exposed in the
# machine-readable launch configuration.

DEV_LEGAL_STATUSES = {"in_progress", "final_review", "blocked"}
# reviewer transitions, keyed by status found at entry (status-transition
# table: .ai-protocol/meta/taskfile.md; in_progress from final_review is legal
# only for the "final_review set in error" revert):
REVIEW_LEGAL = {
    "in_progress": {"in_progress", "blocked"},
    "final_review": {"completed", "in_progress", "final_review", "blocked"},
}

# --- prompt templates --------------------------------------------------------
#
# Every prompt/banner text the orchestrator sends or shows lives under
# prompts/ as a single-source template file ({{var}} placeholders, one file
# per prompt/fragment) — the same files a human standing in for the
# orchestrator reads, so the two executors cannot drift. Composition (which
# fragments, in what order, separators, list joins) stays in builder code;
# templates are text atoms, loaded per use. The manifests
# declare each template's exact placeholder set; prompts_error() refuses
# startup on a missing/malformed template, an undeclared placeholder, or a
# postcheck contract that doesn't map 1:1 onto the code-side checks — same
# policy as the effort allowlist, never a silent fallback.

PROMPT_PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

PROMPT_MANIFEST: dict[str, frozenset[str]] = {
    "entry/approved-plan-gate": frozenset({"ruling", "plan"}),
    "entry/automation-wrapper": frozenset({"automation_md"}),
    "entry/checklist-claim": frozenset({"sid_disp", "now"}),
    "entry/checklist-dev-claim-status": frozenset(),
    "entry/checklist-dev-est": frozenset(
        {"cur", "tot", "nxt", "ntot", "undershoot"}),
    "entry/checklist-dev-est-unknown": frozenset(),
    "entry/checklist-dev-prefetch": frozenset(),
    "entry/checklist-est-undershoot": frozenset(),
    "entry/checklist-header": frozenset(),
    "entry/checklist-review-est": frozenset(),
    "entry/checklist-review-pending": frozenset({"pending"}),
    "entry/checks-preview-header": frozenset(),
    "entry/conduct-annex": frozenset(),
    "entry/closeout": frozenset({"task_id", "sync_skill", "active_count"}),
    "entry/dev-advancement-wrapper": frozenset({"dev_rule"}),
    "entry/dev-remediation-wrapper": frozenset({"dev_rule"}),
    "entry/dev-invocation": frozenset({"task_id", "sid_line"}),
    "entry/dev-pre-re-est": frozenset(),
    "entry/dev-remediation": frozenset({"group"}),
    "entry/est-undershoot-note": frozenset(),
    "entry/human-ruling": frozenset({"ruling"}),
    "entry/plan-gate": frozenset(),
    "entry/plan-rule-wrapper": frozenset({"plan_rule"}),
    "entry/preamble-native-note": frozenset(),
    "entry/review-independence": frozenset(),
    "entry/review-invocation": frozenset({"task_id", "sid_line"}),
    "entry/review-rule-wrapper": frozenset({"review_rule"}),
    "entry/sid-line": frozenset({"sid"}),
    "entry/sid-line-from-hook": frozenset(),
    "entry/stall-ruling": frozenset({"ruling"}),
    "midflight/answered-continue": frozenset({"answer"}),
    "midflight/banner-agent-replied": frozenset({"reply"}),
    "midflight/banner-blocked": frozenset(
        {"role", "sid", "blockers", "open_context"}),
    "midflight/banner-closeout-incomplete": frozenset({"problems"}),
    "midflight/banner-convergence": frozenset(
        {"group", "rounds", "findings"}),
    "midflight/banner-discussion-unavailable": frozenset(),
    "midflight/banner-dispute": frozenset({"reviewer_line", "review_entry"}),
    "midflight/banner-final-review-stall": frozenset(),
    "midflight/banner-followups-exhausted": frozenset(
        {"max_followups", "problems"}),
    "midflight/banner-native-closeout-incomplete": frozenset({"problems"}),
    "midflight/banner-no-reply": frozenset(),
    "midflight/banner-plan-gate": frozenset(
        {"headline", "shown", "warning"}),
    "midflight/banner-request": frozenset({"role", "sid", "log_file"}),
    "midflight/banner-run-error": frozenset({"role", "sid"}),
    "midflight/banner-wrapup-exhausted": frozenset({"problems"}),
    "midflight/blocked-resume": frozenset({"answer"}),
    "midflight/blocked-violation": frozenset({"problems"}),
    "midflight/clean-howto": frozenset(),
    "midflight/closeout-incomplete": frozenset({"problems"}),
    "midflight/discussion-hint": frozenset(),
    "midflight/discussion-turn": frozenset({"question"}),
    "midflight/human-instruction": frozenset({"answer"}),
    "midflight/plan-confirm-instruction": frozenset(),
    "midflight/plan-feedback": frozenset({"answer"}),
    "midflight/plan-headline-proposes": frozenset({"sid"}),
    "midflight/plan-headline-replied": frozenset({"sid"}),
    "midflight/plan-headline-revised": frozenset({"sid", "report_rev"}),
    "midflight/plan-headline-unchanged": frozenset(
        {"sid", "report_rev", "report_round", "log_file"}),
    "midflight/plan-no-reply": frozenset({"log_file"}),
    "midflight/plan-truncation-note": frozenset({"chars", "log_file"}),
    "midflight/plan-warning-dirty-tree": frozenset(),
    "midflight/plan-warning-keep": frozenset(
        {"report_rev", "report_round"}),
    "midflight/plan-warning-no-heading": frozenset(),
    "midflight/run-error-retry": frozenset({"answer", "role", "task_id"}),
    "midflight/violation-fix": frozenset({"problems", "clean_howto", "sid"}),
    "midflight/wrapup": frozenset(
        {"context_tokens", "context_budget", "clean_howto", "sid",
         "handoff_note", "plan_note"}),
    "midflight/wrapup-note-advancement": frozenset(),
    "midflight/wrapup-note-remediation": frozenset(),
    "midflight/wrapup-note-review": frozenset(),
    "midflight/wrapup-plan-advancement": frozenset(),
}

POSTCHECK_MANIFEST: dict[str, frozenset[str]] = {
    "tree-clean": frozenset(),
    "session-log-entry": frozenset({"sid_disp"}),
    "claim-sid": frozenset({"sid_disp"}),
    "fix-set-value": frozenset(),
    "fix-set-closed": frozenset(),
    "dev-remediation-status": frozenset({"status_before"}),
    "dev-advancement-status": frozenset(),
    "dev-est-increment": frozenset(
        {"cur", "tot", "nxt", "ntot", "undershoot"}),
    "review-status-final-gate": frozenset(),
    "review-status-interim": frozenset({"status_before"}),
    "review-entry-fields": frozenset(),
}


def _template_path(name: str) -> Path:
    return PROMPTS_DIR / f"{name}.md"


def _read_template(path: Path) -> str:
    """Template file bytes minus the single trailing newline of the
    text-file format. Everything else — including leading/trailing SPACES
    on several fragments — is byte-significant."""
    text = path.read_text()
    return text[:-1] if text.endswith("\n") else text


def _substitute(text: str, source: str, allowed: frozenset[str],
                values: dict) -> str:
    """Strict {{var}} substitution: the placeholders found in the text, the
    manifest set, and the provided values must all agree — any mismatch is
    a hard error, never a silent fallback. Values are never re-scanned."""
    found = set(PROMPT_PLACEHOLDER_RE.findall(text))
    if found != set(values) or found != allowed:
        raise RuntimeError(
            f"{source}: placeholder mismatch — template has "
            f"{sorted(found)}, manifest declares {sorted(allowed)}, caller "
            f"passed {sorted(values)}")
    return PROMPT_PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), text)


def render_prompt(name: str, **values) -> str:
    if name not in PROMPT_MANIFEST:
        raise KeyError(f"unknown prompt template: {name}")
    return _substitute(_read_template(_template_path(name)), name,
                       PROMPT_MANIFEST[name], values)


def _parse_postcheck_contract() -> dict[str, str]:
    """postcheck-contract.md → {check-id: requirement-line template}. Every
    `## <id>` heading opens an entry; its body is the requirement line."""
    entries: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []

    def flush() -> None:
        if current is not None:
            entries[current] = "\n".join(body).strip("\n")

    for line in POSTCHECK_CONTRACT.read_text().splitlines():
        m = re.fullmatch(r"##\s+(\S+)\s*", line)
        if not m:
            if current is not None:
                body.append(line)
            continue
        flush()
        current, body = m.group(1), []
        if current in entries:
            raise RuntimeError(
                f"postcheck contract: duplicate check-id `{current}`")
    flush()
    return entries


def contract_line(check_id: str, **values) -> str:
    """The requirement line for one check-id, instantiated with this
    session's values. check_specs pairs it with the verification callable;
    checks_preview shows the identical line — told and verified cannot
    drift."""
    if check_id not in POSTCHECK_MANIFEST:
        raise KeyError(f"check-id not bound by the code manifest: {check_id}")
    entries = _parse_postcheck_contract()
    if check_id not in entries:
        raise RuntimeError(
            f"postcheck contract: no requirement line for `{check_id}` in "
            f"{POSTCHECK_CONTRACT}")
    return _substitute(entries[check_id], f"postcheck:{check_id}",
                       POSTCHECK_MANIFEST[check_id], values)


def _exercised_postcheck_ids() -> set[str]:
    """Every check-id check_specs actually binds across the role/mode
    matrix — the callable side of the contract's 1:1 startup validation."""
    ids: set[str] = set()
    for role, status_before, est_before, was_rem in (
            ("dev", "in_progress", (1, 2), False),
            ("dev", "in_progress", None, False),
            ("dev", "final_review", (1, 2), True),
            ("review", "in_progress", None, False),
            ("review", "final_review", None, False)):
        specs = Orchestrator.check_specs(role, "startup-probe", status_before,
                                         est_before=est_before,
                                         was_remediation=was_rem)
        ids.update(check_id for check_id, _, _ in specs)
    return ids


def prompts_error() -> str | None:
    """None when every template and the postcheck contract validate, else a
    refusal message. Called at startup for both backends (same policy as
    effort_error): a missing/malformed template, an undeclared placeholder,
    or a check-id↔callable mapping that is not 1:1 in both directions
    refuses the run rather than degrading it silently."""
    if not PROMPTS_DIR.is_dir():
        return f"prompt templates: directory missing: {PROMPTS_DIR}"
    problems: list[str] = []
    for name, allowed in sorted(PROMPT_MANIFEST.items()):
        path = _template_path(name)
        if not path.is_file():
            problems.append(f"missing template: {name} ({path})")
            continue
        found = set(PROMPT_PLACEHOLDER_RE.findall(_read_template(path)))
        for var in sorted(found - allowed):
            problems.append(f"{name}: unknown placeholder {{{{{var}}}}}")
        for var in sorted(allowed - found):
            problems.append(f"{name}: declared placeholder {{{{{var}}}}} "
                            "missing from the template")
    for family in ("entry", "midflight"):
        family_dir = PROMPTS_DIR / family
        if not family_dir.is_dir():
            continue  # already reported as missing templates above
        for path in sorted(family_dir.glob("*.md")):
            if f"{family}/{path.stem}" not in PROMPT_MANIFEST:
                problems.append("template file not in the code manifest: "
                                f"{family}/{path.stem}")
    if not POSTCHECK_CONTRACT.is_file():
        problems.append(f"missing postcheck contract: {POSTCHECK_CONTRACT}")
    else:
        try:
            entries = _parse_postcheck_contract()
        except RuntimeError as err:
            entries = None
            problems.append(str(err))
        if entries is not None:
            contract_ids, code_ids = set(entries), set(POSTCHECK_MANIFEST)
            for cid in sorted(contract_ids - code_ids):
                problems.append(f"postcheck contract: id `{cid}` has no "
                                "code-side check")
            for cid in sorted(code_ids - contract_ids):
                problems.append("postcheck contract: no requirement line "
                                f"for code-side check `{cid}`")
            for cid in sorted(contract_ids & code_ids):
                line = entries[cid]
                if not line or "\n" in line:
                    problems.append(f"postcheck contract: `{cid}` must be "
                                    "exactly one requirement line")
                    continue
                found = set(PROMPT_PLACEHOLDER_RE.findall(line))
                allowed = POSTCHECK_MANIFEST[cid]
                for var in sorted(found - allowed):
                    problems.append(f"postcheck contract: `{cid}` has "
                                    f"unknown placeholder {{{{{var}}}}}")
                for var in sorted(allowed - found):
                    problems.append(f"postcheck contract: `{cid}` lacks "
                                    f"declared placeholder {{{{{var}}}}}")
            if contract_ids == code_ids:
                try:
                    exercised = _exercised_postcheck_ids()
                except (RuntimeError, KeyError) as err:
                    problems.append(f"postcheck binding exercise failed: "
                                    f"{err}")
                else:
                    for cid in sorted(code_ids - exercised):
                        problems.append(f"postcheck contract: id `{cid}` "
                                        "is never bound by check_specs")
                    for cid in sorted(exercised - code_ids):
                        problems.append(f"check_specs binds `{cid}` which "
                                        "the contract does not declare")
    if not problems:
        return None
    return ("prompt templates invalid — refusing to start "
            "(fix canonical prompts/ and redeploy):\n- "
            + "\n- ".join(problems))


# Module-level constants: referenced directly across the code (wrap-up and
# violation prompts, ask_human) and by the mock suite; loaded once at import
# from their single-source templates.
DISCUSSION_HINT = _read_template(_template_path("midflight/discussion-hint"))
CLEAN_HOWTO = _read_template(_template_path("midflight/clean-howto"))


# --- task file parsing -----------------------------------------------------

@dataclasses.dataclass
class LogEntry:
    heading: str
    session_id: str
    is_review: bool
    reviewed_sid: str | None
    body: str

    @property
    def verdict(self) -> str | None:
        # Entry fields are machine-parsed as exact `- X:` list lines
        # (taskfile schema); anchored so a prose MENTION of a field name
        # never parses as the field (live-drill incident: a Done narrating
        # "(no `Handoff: continuation`)" flipped dispatch for 6 sessions).
        m = re.search(r"^\s*-\s*Verdict:\s*([a-z-]+)\s*$", self.body,
                      re.MULTILINE)
        return m.group(1) if m else None

    @property
    def group(self) -> str | None:
        m = re.search(r"^\s*-\s*Group:\s*(\S+)\s*$", self.body,
                      re.MULTILINE)
        return m.group(1) if m else None



@dataclasses.dataclass
class TaskState:
    path: Path
    status: str
    blockers: str
    claimed_by: str
    est: str
    entries: list[LogEntry]
    # Frontmatter `fix-set`: "open" = a remediation fix set is incomplete
    # (not yet a reviewable unit — re-review deferred); absent ("") = no
    # open fix set. Declared only by remediation sessions (dev contract).
    fix_set: str = ""

    @property
    def est_tuple(self) -> tuple[int, int] | None:
        m = re.match(r"(\d+)\s*/\s*(\d+)", self.est)
        return (int(m.group(1)), int(m.group(2))) if m else None

    @property
    def review_entries(self) -> list[LogEntry]:
        return [e for e in self.entries if e.is_review]

    def unreviewed_dev_sids(self) -> list[str]:
        reviewed = {e.reviewed_sid for e in self.review_entries}
        seen: list[str] = []
        for e in self.entries:
            if not e.is_review and e.session_id not in reviewed \
                    and e.session_id not in seen:
                seen.append(e.session_id)
        return seen


def parse_task(path: Path) -> TaskState:
    text = path.read_text()

    def fm(key: str) -> str:
        m = re.search(rf"^{key}:\s*(.*?)\s*(?:#.*)?$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    log_m = re.search(r"^## Session log\s*$(.*)", text, re.MULTILINE | re.DOTALL)
    log = log_m.group(1) if log_m else ""
    entries: list[LogEntry] = []
    parts = re.split(r"^### ", log, flags=re.MULTILINE)
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        fields = [p.strip() for p in heading.split(" / ")]
        sid = fields[1] if len(fields) > 1 else ""
        reviewed = None
        for f in fields:
            if f.startswith("review of "):
                reviewed = f[len("review of "):].strip()
        entries.append(LogEntry(
            heading=heading.strip(), session_id=sid,
            is_review=reviewed is not None, reviewed_sid=reviewed, body=body))
    return TaskState(path=path, status=fm("status"), blockers=fm("blockers"),
                     claimed_by=fm("claimed-by"), est=fm("session-est"),
                     entries=entries, fix_set=fm("fix-set"))


# --- shell helpers ----------------------------------------------------------

def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, check=False).stdout


def tree_clean() -> bool:
    return git("status", "--porcelain").strip() == ""


def protocol_block(session_id: str) -> str:
    """Assemble the per-session protocol context by running the existing
    sessionStart hook script with a synthetic conversation_id (cursor backend
    only — the SDK does not deliver hook context to the model). AI_ORCH must
    be stripped for this one call — the guard inside the script would
    otherwise make it exit empty."""
    env = dict(os.environ)
    env.pop("AI_ORCH", None)
    env["CURSOR_PROJECT_DIR"] = str(REPO)
    proc = subprocess.run(
        ["bash", str(SESSION_START_SH)],
        input=json.dumps({"conversation_id": session_id}),
        capture_output=True, text=True, cwd=REPO, env=env, check=False)
    data = json.loads(proc.stdout)
    ctx = data.get("additional_context", "")
    if "PROJECT PROTOCOL CONTEXT" not in ctx:
        raise RuntimeError("session-start.sh produced no protocol context")
    return ctx


def summarize(val: object, limit: int = 80) -> str:
    try:
        s = json.dumps(val, ensure_ascii=False, default=str)
    except Exception:
        s = repr(val)
    return s if len(s) <= limit else s[:limit] + "…"


def usage_summary(usage: dict | None) -> str:
    """Small, non-content usage summary for observability logs."""
    if not usage:
        return "usage=?"
    keys = (
        ("input_tokens", "input"),
        ("cached_input_tokens", "cached"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_create"),
        ("output_tokens", "output"),
        ("reasoning_output_tokens", "reasoning"),
        ("total_tokens", "total"),
    )
    parts = [f"{label}={usage[k]}" for k, label in keys if usage.get(k)]
    return "usage=" + (",".join(parts) if parts else "?")


# --- execution backends ------------------------------------------------------

@dataclasses.dataclass
class TurnResult:
    status: str               # "finished" | "error"
    saw_request: bool = False
    text: str = ""            # assistant text of this turn


@dataclasses.dataclass
class PlanGateResult:
    plan: str
    ruling: str


class SessionStartError(Exception):
    """The session could not be started at all (auth, bad model, ...)."""


class BackendSession:
    """One protocol session (a conversation that can take several turns:
    first prompt + followups). Subclasses own sid discovery and streaming."""

    sid: str | None = None
    # Best-effort estimate of the conversation's context size in tokens,
    # refreshed after every turn (0 = unknown). Feeds the CONTEXT_BUDGET
    # wrap-up check (port of stop-context-check.sh).
    context_tokens: int = 0

    def turn(self, prompt: str) -> TurnResult:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _fmt_chars(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class Heartbeat:
    """Log-file-only stream monitor with two jobs:

    (a) liveness — a [heartbeat] line per HEARTBEAT_SILENCE seconds of
        stream silence (call .tick() on every stream event);
    (b) [gen] aggregation — high-rate text/thinking char events accumulate
        (gen_text/gen_thinking) and flush as ONE line like
        `[gen] text +234 chars, thinking +4.1k chars (28 events / 30s)`
        on any immediate event (emit() flushes BEFORE its line so the file
        reads in stream order), on GEN_WINDOW expiry (checked on the beat
        thread's 5s wakeup), or at stream end (__exit__).
    """

    def __init__(self, flog) -> None:
        self._flog = flog
        self._last = [time.monotonic()]
        self._done = threading.Event()
        self._lock = threading.Lock()  # accumulator is touched cross-thread
        self._win_start = 0.0
        self._text = 0
        self._thinking = 0
        self._events = 0

    def tick(self) -> None:
        self._last[0] = time.monotonic()

    def _add(self, text: int = 0, thinking: int = 0) -> None:
        with self._lock:
            if not self._events:
                self._win_start = time.monotonic()
            self._text += text
            self._thinking += thinking
            self._events += 1
            expired = time.monotonic() - self._win_start >= GEN_WINDOW
        if expired:
            self.flush()

    def gen_text(self, n: int) -> None:
        if n:
            self._add(text=n)

    def gen_thinking(self, n: int) -> None:
        if n:
            self._add(thinking=n)

    def _drain(self) -> str | None:
        with self._lock:
            if not self._events:
                return None
            span = int(round(time.monotonic() - self._win_start))
            parts = []
            if self._text:
                parts.append(f"text +{_fmt_chars(self._text)} chars")
            if self._thinking:
                parts.append(f"thinking +{_fmt_chars(self._thinking)} chars")
            line = (f"[gen] {', '.join(parts)} "
                    f"({self._events} events / {span}s)")
            self._text = self._thinking = self._events = 0
            return line

    def flush(self) -> None:
        line = self._drain()
        if line:
            self._flog(line)

    def emit(self, line: str) -> None:
        """Immediate log line; flushes the pending [gen] window first so
        the log file reads in stream order."""
        self.flush()
        self._flog(line)

    def __enter__(self) -> "Heartbeat":
        def beat() -> None:
            next_beat = HEARTBEAT_SILENCE
            while not self._done.wait(5):
                with self._lock:
                    expired = self._events and (
                        time.monotonic() - self._win_start >= GEN_WINDOW)
                if expired:
                    self.flush()
                silence = time.monotonic() - self._last[0]
                if silence < HEARTBEAT_SILENCE:
                    next_beat = HEARTBEAT_SILENCE
                elif silence >= next_beat:
                    self._flog(f"[heartbeat] still running: {int(silence)}s "
                               "since last stream event")
                    next_beat += HEARTBEAT_SILENCE
        threading.Thread(target=beat, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._done.set()
        self.flush()


# -- cursor backend (Cursor SDK) --

class CursorSession(BackendSession):
    def __init__(self, orch: "Orchestrator", agent) -> None:
        self.orch = orch
        self.agent = agent
        self.sid = agent.agent_id

    def turn(self, prompt: str) -> TurnResult:
        o = self.orch
        try:
            run = self.agent.send(prompt)
        except Exception as err:
            if CursorAgentError is not None and isinstance(err, CursorAgentError):
                raise SessionStartError(
                    f"startup failure (never ran): {err} "
                    f"retryable={err.is_retryable}") from err
            raise
        o.log(f"run started: run_id={run.id} agent={self.sid}")
        saw_request = False
        chunks: list[str] = []
        with Heartbeat(o.flog) as hb:
            try:
                for msg in run.messages():
                    hb.tick()
                    mtype = getattr(msg, "type", "")
                    if mtype == "assistant":
                        content = getattr(getattr(msg, "message", None),
                                          "content", []) or []
                        n = 0
                        for block in content:
                            if getattr(block, "type", "") == "text":
                                text = getattr(block, "text", "")
                                chunks.append(text)
                                n += len(text)
                        hb.gen_text(n)
                    elif mtype == "thinking":
                        ms = getattr(msg, "thinking_duration_ms", None)
                        if ms:
                            hb.emit(f"[thinking] {ms / 1000:.0f}s")
                        else:
                            hb.gen_thinking(
                                len(getattr(msg, "text", "") or ""))
                    elif mtype == "tool_call":
                        status = getattr(msg, "status", "")
                        name = getattr(msg, "name", "?")
                        if status == "running":
                            hb.emit(f"[tool] {name} "
                                    f"{summarize(getattr(msg, 'args', None))}")
                        elif status == "error":
                            hb.emit(f"[tool-error] {name} "
                                    f"{summarize(getattr(msg, 'result', None))}")
                    elif mtype == "status":
                        hb.emit(f"[status] {getattr(msg, 'status', '')} "
                                f"{getattr(msg, 'message', '')}".rstrip())
                    elif mtype == "request":
                        # Headless backstop: agent awaits input/approval.
                        # Cancel and escalate — automation-mode.md tells
                        # agents not to do this, so reaching here is a signal.
                        saw_request = True
                        hb.flush()
                        o.log("stream: `request` event — cancelling run")
                        with contextlib.suppress(Exception):
                            if run.supports("cancel"):
                                run.cancel()
                        break
            except Exception as err:  # stream hiccups must not lose the result
                hb.flush()
                o.log(f"stream error (continuing to wait): {err}")
            result = run.wait()
        text = "".join(chunks)
        if text:
            o.transcript(self.sid, text)
        # Same approximation as stop-context-check.sh: 1 token ≈ 4 chars of
        # the full conversation (the SDK exposes no usage counters).
        with contextlib.suppress(Exception):
            self.context_tokens = len(run.conversation_json()) // 4
        o.log(f"run finished: status={result.status} "
              f"context≈{self.context_tokens} tokens")
        return TurnResult(status=getattr(result, "status", "") or "finished",
                          saw_request=saw_request, text=text)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.agent.__exit__(None, None, None)


class CursorBackend:
    name = "cursor"
    injects_protocol = True   # orchestrator must assemble protocol context

    def __init__(self, orch: "Orchestrator", dev_model: str,
                 review_model: str, api_key: str | None,
                 dev_effort: str | None = None,
                 review_effort: str | None = None) -> None:
        self.orch = orch
        self.models = {"dev": dev_model, "review": review_model}
        self.efforts = {"dev": dev_effort, "review": review_effort}
        self.api_key = api_key

    def describe(self, role: str) -> str:
        effort = self.efforts[role]
        return f"cursor:{self.models[role]}" + (f"@{effort}" if effort else "")

    @staticmethod
    def _effort_axis(model: str) -> str:
        return _effort_axis(model)

    def _model_selection(self, role: str):
        model = self.models[role]
        effort = self.efforts[role]
        if not effort:
            return model  # bare id → the catalog's default variant
        axis = self._effort_axis(model)
        if axis == "reasoning":
            effort = CURSOR_REASONING_ALIASES.get(effort, effort)
        return {"id": model,
                "params": [{"id": axis, "value": effort}]}

    def _options(self, role: str):
        from cursor_sdk import AgentOptions, LocalAgentOptions
        return AgentOptions(model=self._model_selection(role),
                            api_key=self.api_key,
                            local=LocalAgentOptions(cwd=str(REPO)))

    def new_session(self, role: str) -> CursorSession:
        agent = Agent.create(self._options(role))
        agent.__enter__()
        return CursorSession(self.orch, agent)

    def resume_session(self, sid: str, role: str) -> CursorSession:
        agent = Agent.resume(sid, self._options(role))
        agent.__enter__()
        return CursorSession(self.orch, agent)


# -- cc-codex backend (Claude/Codex dev + Codex review, subprocess CLIs) --

def _session_map_load() -> dict:
    if SESSION_MAP.exists():
        with contextlib.suppress(Exception):
            return json.loads(SESSION_MAP.read_text())
    return {}


def _session_map_register(sid: str, tool: str, native_id: str) -> None:
    m = _session_map_load()
    m[sid] = {"tool": tool, "native_id": native_id}
    SESSION_MAP.parent.mkdir(parents=True, exist_ok=True)
    SESSION_MAP.write_text(json.dumps(m, indent=1))


class CliSession(BackendSession):
    """Shared subprocess plumbing: build argv per turn, stream JSONL from
    stdout, map events to the [tool]/[gen]/... log-file lines."""

    tool = "?"
    _hb: Heartbeat | None = None

    def __init__(self, orch: "Orchestrator") -> None:
        self.orch = orch
        self.first_turn = True

    def _emit(self, line: str) -> None:
        """Immediate log line, ordered after a [gen] flush when a stream
        monitor is attached (direct flog otherwise — e.g. unit tests)."""
        if self._hb:
            self._hb.emit(line)
        else:
            self.orch.flog(line)

    def _gen_text(self, n: int) -> None:
        if not n:
            return
        if self._hb:
            self._hb.gen_text(n)
        else:
            self.orch.flog(f"[text] {n} chars")

    def _argv(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _handle_event(self, ev: dict, chunks: list[str]) -> str | None:
        """Digest one JSONL event; return 'error' to flag run failure."""
        raise NotImplementedError

    def _update_context(self) -> None:
        """Refresh self.context_tokens after a turn, for backends whose
        stream carries no per-request usage (see CodexSession)."""

    def turn(self, prompt: str) -> TurnResult:
        o = self.orch
        argv = self._argv(prompt)
        o.flog(f"[exec] {' '.join(argv[:6])} …")
        try:
            proc = subprocess.Popen(
                argv, cwd=REPO, text=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as err:
            raise SessionStartError(f"{self.tool} spawn failed: {err}") from err
        o.log(f"run started: tool={self.tool} pid={proc.pid} "
              f"sid={self.sid or '(assigned by tool)'}")
        # Drain stderr on a thread — a full stderr pipe would deadlock the
        # stdout read loop.
        stderr_parts: list[str] = []
        t = threading.Thread(
            target=lambda: stderr_parts.append(proc.stderr.read() or ""),
            daemon=True)
        t.start()
        chunks: list[str] = []
        errored = False
        with Heartbeat(o.flog) as hb:
            self._hb = hb
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    hb.tick()
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        self._emit(f"[raw] {line[:200]}")
                        continue
                    try:
                        if self._handle_event(ev, chunks) == "error":
                            errored = True
                    except Exception as err:  # parser must never kill the run
                        self._emit(f"[event-parse-error] {err}: {line[:200]}")
                proc.wait()
            finally:
                self._hb = None
        t.join(timeout=10)
        self._update_context()
        stderr_tail = "".join(stderr_parts)[-2000:]
        if stderr_tail.strip():
            o.flog(f"[stderr] {stderr_tail.strip()}")
        text = "".join(chunks)
        if text:
            o.transcript(self.sid or self.tool, text)
        self.first_turn = False
        status = "finished" if proc.returncode == 0 and not errored else "error"
        o.log(f"run finished: tool={self.tool} exit={proc.returncode} "
              f"status={status} context≈{self.context_tokens} tokens")
        if self.sid is None:
            o.log("WARNING: no session id captured from the stream")
        return TurnResult(status=status, text=text)


class ClaudeSession(CliSession):
    """Claude Code headless (`claude -p --output-format stream-json`).
    The session id is chosen by the orchestrator (--session-id) so prompts
    and post-checks can name it before the process starts."""

    tool = "claude"

    def __init__(self, orch: "Orchestrator", model: str, effort: str,
                 sid: str | None = None, resume: bool = False) -> None:
        super().__init__(orch)
        self.model = model
        self.effort = effort
        self.sid = sid or str(uuid.uuid4())
        self.resume = resume
        _session_map_register(self.sid, "claude", self.sid)

    def _argv(self, prompt: str) -> list[str]:
        argv = ["claude", "-p", "--output-format", "stream-json", "--verbose",
                "--model", self.model, "--effort", self.effort,
                "--dangerously-skip-permissions"]
        if self.resume or not self.first_turn:
            argv += ["--resume", self.sid]
        else:
            argv += ["--session-id", self.sid]
        argv.append(prompt)
        return argv

    _in_thinking_burst = False

    def _handle_event(self, ev: dict, chunks: list[str]) -> str | None:
        etype = ev.get("type", "")
        # claude 2.1.199 emits a system/thinking_tokens event every ~1.5s
        # while the model thinks (observed 237 in one resumed session) —
        # collapse each burst to ONE line; any other event ends the burst.
        if etype == "system" and ev.get("subtype") == "thinking_tokens":
            if not self._in_thinking_burst:
                self._in_thinking_burst = True
                self._emit("[status] thinking_tokens (burst — repeats "
                           "suppressed until the next event)")
            return None
        self._in_thinking_burst = False
        if etype == "system":
            self._emit(f"[status] {ev.get('subtype', 'system')}")
        elif etype == "assistant":
            msg = ev.get("message") or {}
            content = msg.get("content") or []
            n = 0
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    chunks.append(block.get("text", ""))
                    n += len(block.get("text", ""))
                elif btype == "tool_use":
                    self._emit(f"[tool] {block.get('name', '?')} "
                               f"{summarize(block.get('input'))}")
            self._gen_text(n)
            # Context size = the PER-REQUEST usage of the response that just
            # streamed (input + cache reads/creation + output ≈ what the next
            # request will carry). Main thread only — subagent events carry
            # parent_tool_use_id and reflect the subagent's own context. The
            # `result` event's usage is CUMULATIVE across every request of
            # the run (measured 1.89M for a 7.8-min session) and must NOT be
            # used as a context signal.
            u = msg.get("usage") or {}
            if u and not ev.get("parent_tool_use_id"):
                self.context_tokens = (
                    (u.get("input_tokens") or 0)
                    + (u.get("cache_read_input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0)
                    + (u.get("output_tokens") or 0))
            observed_model = msg.get("model")
            if (observed_model and not ev.get("parent_tool_use_id")
                    and not getattr(self, "_observed_response_logged",
                                    False)):
                self._observed_response_logged = True
                self._emit(
                    "[status] claude observed response "
                    f"model={observed_model} "
                    f"requested_model={getattr(self, 'model', '?')} "
                    f"requested_effort={getattr(self, 'effort', '?')} "
                    f"{usage_summary(u)}")
        elif etype == "result":
            self._emit(f"[status] result {ev.get('subtype', '')}".rstrip())
            if ev.get("is_error"):
                return "error"
        return None


class CodexSession(CliSession):
    """Codex CLI (`codex exec --json`). Codex assigns the session id itself;
    it is captured from the thread/session start event on the first turn."""

    tool = "codex"

    def __init__(self, orch: "Orchestrator", model: str, effort: str,
                 sid: str | None = None) -> None:
        super().__init__(orch)
        self.model = model
        self.effort = CODEX_EFFORT_ALIASES.get(effort, effort)
        self.sid = sid  # None until the first stream event names it
        self._latest_token_usage: str | None = None
        self._observed_contexts: set[tuple[str, str, str]] = set()
        self._codex_reconnect_in_progress = False

    def _argv(self, prompt: str) -> list[str]:
        argv = ["codex", "exec", "--json", "-m", self.model,
                "-c", f"model_reasoning_effort={self.effort}",
                "-s", CODEX_SANDBOX, "--skip-git-repo-check"]
        if self.sid and not self.first_turn:
            # resume subcommand: codex exec resume <id> <prompt> [opts].
            # 0.142.5 rejects `-s` here ("unexpected argument") — the
            # sandbox must ride the config override instead (`-c
            # sandbox_mode=…`, validated against --strict-config). No `-m`
            # either: the resumed thread keeps its model.
            argv = ["codex", "exec", "resume", self.sid, prompt,
                    "--json", "-c", f"model_reasoning_effort={self.effort}",
                    "-c", f"sandbox_mode={CODEX_SANDBOX}",
                    "--skip-git-repo-check"]
            return argv
        argv.append(prompt)
        return argv

    def _log_observed_context(self, payload: dict, source: str) -> None:
        settings = payload.get("collaboration_mode")
        settings = settings if isinstance(settings, dict) else {}
        settings = settings.get("settings")
        settings = settings if isinstance(settings, dict) else {}
        observed_model = payload.get("model") or settings.get("model")
        observed_effort = (
            payload.get("effort")
            or payload.get("model_reasoning_effort")
            or settings.get("reasoning_effort"))
        sandbox = payload.get("sandbox_policy") or payload.get("sandbox")
        sandbox_type = (sandbox.get("type") if isinstance(sandbox, dict)
                        else sandbox)
        if not (observed_model or observed_effort or sandbox_type):
            return
        key = (observed_model or "?", observed_effort or "?",
               sandbox_type or "?")
        seen = getattr(self, "_observed_contexts", set())
        if key in seen:
            return
        seen.add(key)
        self._observed_contexts = seen
        self._emit(
            "[status] codex observed context "
            f"model={observed_model or '?'} "
            f"effort={observed_effort or '?'} "
            f"requested_model={getattr(self, 'model', '?')} "
            f"requested_effort={getattr(self, 'effort', '?')} "
            f"sandbox={sandbox_type or '?'} "
            f"source={source}")

    def _log_rollout_context(self, rollout: Path) -> None:
        """codex exec --json omits turn_context on stdout in 0.143.0, but
        the local rollout JSONL still records it. Log the latest one."""
        latest: dict | None = None
        with contextlib.suppress(OSError):
            for line in rollout.open():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "turn_context":
                    continue
                payload = ev.get("payload")
                if isinstance(payload, dict):
                    latest = payload
        if latest:
            self._log_observed_context(latest, "rollout")

    def _handle_event(self, ev: dict, chunks: list[str]) -> str | None:
        o = self.orch
        etype = ev.get("type", "")
        # session id discovery (schema varies across codex versions)
        if not self.sid:
            val = (ev.get("thread_id") or ev.get("session_id")
                   or ev.get("conversation_id"))
            payload = ev.get("payload")
            if not val and isinstance(payload, dict):
                val = payload.get("session_id") or payload.get("id")
            thread = ev.get("thread")
            if not val and isinstance(thread, dict):
                val = thread.get("id")
            if val:
                self.sid = str(val)
                _session_map_register(self.sid, "codex", self.sid)
                o.log(f"codex session id: {self.sid}")
        item = ev.get("item") or {}
        itype = item.get("type") or item.get("item_type") or ""
        if etype.startswith("item.") and itype:
            if itype in ("command_execution", "local_shell_call"):
                if etype == "item.started":
                    self._emit(f"[tool] shell "
                               f"{summarize(item.get('command'))}")
                elif item.get("exit_code") not in (None, 0):
                    self._emit(
                        f"[tool-error] shell exit={item.get('exit_code')}")
            elif itype in ("agent_message", "assistant_message"):
                if etype == "item.completed":
                    text = item.get("text", "") or ""
                    chunks.append(text)
                    self._gen_text(len(text))
            elif itype == "reasoning":
                if etype == "item.completed":
                    self._emit("[thinking] block")
            elif etype == "item.completed":
                self._emit(f"[status] {itype}")
        elif etype == "turn_context":
            payload = ev.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            self._log_observed_context(payload, "stream")
        elif etype == "event_msg":
            payload = ev.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            ptype = payload.get("type")
            if ptype == "token_count":
                info = payload.get("info")
                info = info if isinstance(info, dict) else {}
                last = info.get("last_token_usage")
                window = info.get("model_context_window")
                usage = last if isinstance(last, dict) else None
                self._latest_token_usage = (
                    f"{usage_summary(usage)} "
                    f"context_window={window or '?'}")
            elif (ptype == "task_complete"
                  and getattr(self, "_latest_token_usage", None)):
                self._emit(f"[status] codex token usage "
                           f"{self._latest_token_usage}")
                self._latest_token_usage = None
            elif ptype:
                self._emit(f"[status] event_msg {ptype}")
        elif etype == "turn.completed":
            # usage here is CUMULATIVE across every request of the turn
            # (measured 2.9M for an 8-min review) — useless as a context
            # signal; the estimate comes from _update_context() instead.
            if getattr(self, "_latest_token_usage", None):
                self._emit(f"[status] codex token usage "
                           f"{self._latest_token_usage}")
                self._latest_token_usage = None
            pass
        elif etype == "turn.failed":
            if getattr(self, "_latest_token_usage", None):
                self._emit(f"[status] codex token usage "
                           f"{self._latest_token_usage}")
                self._latest_token_usage = None
            self._emit(f"[status] {etype} {summarize(ev.get('message') or ev)}")
            return "error"
        elif etype == "error":
            # Codex emits transient transport errors such as websocket
            # reconnect notices before recovering and completing the turn. A
            # bare error can immediately follow that reconnect sequence, so
            # keep that diagnostic nonfatal too; isolated/unknown errors still
            # fail the turn and trigger the normal run-error escalation.
            if getattr(self, "_latest_token_usage", None):
                self._emit(f"[status] codex token usage "
                           f"{self._latest_token_usage}")
                self._latest_token_usage = None
            self._emit(f"[status] {etype} {summarize(ev.get('message') or ev)}")
            msg = ev.get("message")
            msg = msg if isinstance(msg, str) else ""
            reconnecting = msg.startswith("Reconnecting...")
            if reconnecting or (
                    not msg
                    and getattr(self, "_codex_reconnect_in_progress", False)):
                self._codex_reconnect_in_progress = True
            else:
                return "error"
        elif etype and not etype.startswith(("item.", "turn.", "thread.")):
            self._emit(f"[status] {etype}")
        if etype != "error":
            self._codex_reconnect_in_progress = False
        return None

    def _update_context(self) -> None:
        """codex --json exposes no per-request usage, so approximate the
        conversation size the way codex's own Stop hook does with its
        transcript: rollout-file chars / 4. The rollout lives at
        $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<thread-id>.jsonl
        (layout verified on 0.142.5). Not found → estimate stays as-is
        (0 = unknown → the budget check is skipped, fail-safe)."""
        if not self.sid:
            return
        base = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        with contextlib.suppress(OSError):
            rollouts = sorted(
                (base / "sessions").rglob(f"*{self.sid}*.jsonl"),
                key=lambda p: p.stat().st_mtime)
            if rollouts:
                rollout = rollouts[-1]
                self.context_tokens = rollout.stat().st_size // 4
                self._log_rollout_context(rollout)
            else:
                self.orch.flog(f"[status] no rollout file found for "
                               f"{self.sid} — context estimate unavailable")


class CliBackend:
    name = "cc-codex"
    injects_protocol = False  # native hook/import chains load the protocol

    def __init__(self, orch: "Orchestrator", dev_agent: str, dev_model: str,
                 dev_effort: str, review_agent: str, review_model: str,
                 review_effort: str) -> None:
        for role, agent in (("dev", dev_agent), ("review", review_agent)):
            if agent not in CLI_AGENTS:
                raise ValueError(f"invalid cc-codex {role}_agent: {agent}")
        self.orch = orch
        self.dev_agent, self.review_agent = dev_agent, review_agent
        self.dev_model, self.dev_effort = dev_model, dev_effort
        self.review_model, self.review_effort = review_model, review_effort

    def _agent(self, role: str) -> str:
        return self.dev_agent if role == "dev" else self.review_agent

    def _params(self, role: str) -> tuple[str, str]:
        if role == "dev":
            return self.dev_model, self.dev_effort
        return self.review_model, self.review_effort

    def describe(self, role: str) -> str:
        model, effort = self._params(role)
        return f"{self._agent(role)}:{model}@{effort}"

    def new_session(self, role: str) -> CliSession:
        model, effort = self._params(role)
        if self._agent(role) == "codex":
            return CodexSession(self.orch, model, effort)
        return ClaudeSession(self.orch, model, effort)

    def resume_session(self, sid: str, role: str) -> CliSession:
        # The recorded tool is authoritative: a session can only be resumed
        # in the CLI that created it. The role supplies the model/effort and
        # the fallback tool when the sid is unknown.
        info = _session_map_load().get(sid)
        tool = (info or {}).get("tool") or self._agent(role)
        if info is None:
            self.orch.log(f"WARNING: sid {sid} not in {SESSION_MAP} — "
                          f"assuming tool={tool} by role. If this session "
                          "was created manually in another tool, resume may "
                          "fail; unblock manually in that tool instead.")
        elif tool != self._agent(role):
            self.orch.log(f"NOTE: sid {sid} was created in {tool}, not this "
                          f"run's {role} agent {self._agent(role)} — "
                          f"resuming in {tool} with the resolved {role} "
                          "model/effort.")
        model, effort = self._params(role)
        if tool == "claude":
            return ClaudeSession(self.orch, model, effort,
                                 sid=sid, resume=True)
        if tool != "codex":
            raise SessionStartError(f"unknown CLI session tool '{tool}' for {sid}")
        s = CodexSession(self.orch, model, effort, sid=sid)
        s.first_turn = False  # force the resume argv shape
        return s


# --- orchestrator -----------------------------------------------------------

class Orchestrator:
    def __init__(self, task_id: str, dev_model: str, review_model: str,
                 api_key: str | None, once: bool, max_sessions: int,
                 backend: object | None = None,
                 plan_gate: bool = False,
                 control_dir: Path | None = None) -> None:
        self.task_id = task_id
        self.task_path = TASKS_DIR / f"{task_id}.md"
        self.once = once
        self.max_sessions = max_sessions
        self.plan_gate = plan_gate
        self.control_dir = (Path(control_dir).expanduser().resolve()
                            if control_dir else None)
        if self.control_dir:
            self.control_dir.mkdir(parents=True, exist_ok=True)
            # A reused dir may hold question/answer files from a previous
            # run — continue numbering after them; existing files are never
            # overwritten and a stale answer is never consumed.
            taken = [int(m.group(1)) for p in self.control_dir.iterdir()
                     if (m := re.fullmatch(r"(\d+)-(question|answer)\.json",
                                           p.name))]
            self._control_seq = max(taken, default=0) + 1
        self.last_review_agent: str | None = None
        self.pending_ruling: str | None = None
        self._native_closeout_text: str | None = None
        self._human_discuss_turn = None
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.log_file = LOG_DIR / f"{ts}-{task_id}.log"
        # flog is written from the stream thread AND the heartbeat/aggregator
        # thread — serialize appends so lines never interleave.
        self._log_lock = threading.Lock()
        self.backend = backend or CursorBackend(self, dev_model,
                                                review_model, api_key)

    # -- logging / human IO --

    def log(self, msg: str) -> None:
        line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with self._log_lock, self.log_file.open("a") as f:
            f.write(line + "\n")

    def flog(self, msg: str) -> None:
        """File-only log line. The terminal stays at status-level verbosity;
        `tail -f` the run log to watch the live event stream."""
        line = f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}"
        with self._log_lock, self.log_file.open("a") as f:
            f.write(line + "\n")

    def _exit_session_start_error(self, err: SessionStartError) -> None:
        self.log(f"ERROR: {err}")
        sys.exit(1)

    def transcript(self, agent_id: str, text: str) -> None:
        with self._log_lock, self.log_file.open("a") as f:
            f.write(f"--- {agent_id} ---\n{text}\n")

    @staticmethod
    def _discussion_text(answer: str) -> str | None:
        """Return marker-stripped discussion text, or None for a binding
        answer. Plan-gate bypasses this parser because all of its non-confirm
        answers already go back to its planning session."""
        m = DISCUSSION_MARKER_RE.match(answer)
        return answer[m.end():].strip() if m else None

    def _ask_human_once(self, banner: str, kind: str) -> str:
        """One stdin/control-dir question-answer pair."""
        # Every round's banner must be auditable after the fact.
        with self.log_file.open("a") as f:
            f.write(f"--- HUMAN INPUT NEEDED ---\n{banner}\n--- end banner ---\n")
        if self.control_dir:
            answer = self._control_ask(banner, kind)
        else:
            # Drain stray input buffered during the (possibly hour-long)
            # run — a queued Enter must not silently answer the question.
            drained = 0
            with contextlib.suppress(Exception):
                while select.select([sys.stdin], [], [], 0)[0]:
                    if not sys.stdin.readline():
                        break
                    drained += 1
            if drained:
                self.log(f"discarded {drained} stale buffered stdin line(s)")
            print("\n" + "=" * 72)
            print("HUMAN INPUT NEEDED")
            print("=" * 72)
            print(banner)
            print("(type your answer; 'stop' aborts the orchestrator)")
            while True:
                answer = input("answer> ").strip()
                if answer:
                    break
                print("(empty answer ignored — type an answer, or 'stop')")
        return answer

    def ask_human(self, banner: str, kind: str = "question") -> str:
        """Ask until a binding answer arrives.

        Callers attach a live/resumable session with
        ``ask_session_human``. Marked answers become read-only discussion
        turns in that session; its reply is surfaced as a new ordinary
        question of the same kind. Without a session, markers are recognized
        and rejected rather than accidentally becoming binding rulings.
        """
        discuss_turn = self._human_discuss_turn
        question = banner
        while True:
            shown = question
            if discuss_turn and kind != "plan-gate":
                shown += f"\n\n{DISCUSSION_HINT}"
            answer = self._ask_human_once(shown, kind)
            self.log(f"human answered: {answer!r}")
            if answer.lower() == "stop":
                sys.exit("stopped by human")
            if kind == "plan-gate":
                return answer
            discussion = self._discussion_text(answer)
            if discussion is None:
                return answer
            if not discuss_turn:
                question = render_prompt(
                    "midflight/banner-discussion-unavailable")
                continue
            result = discuss_turn(render_prompt(
                "midflight/discussion-turn", question=discussion))
            reply = (result.text or "").strip()
            if not reply:
                reply = render_prompt("midflight/banner-no-reply")
            question = render_prompt("midflight/banner-agent-replied",
                                     reply=reply)

    def ask_session_human(self, session: BackendSession, banner: str,
                          kind: str) -> str:
        """Attach ``session`` to one escalation without changing the public
        ask_human call shape used by integrations and mock overrides."""
        previous = self._human_discuss_turn
        self._human_discuss_turn = session.turn
        try:
            return self.ask_human(banner, kind)
        finally:
            self._human_discuss_turn = previous

    def ask_resumable_human(self, banner: str, kind: str, sid: str,
                            role: str) -> str:
        """Discussion-capable ruling backed by a lazily resumed session.
        A plain answer never opens the session."""
        session: BackendSession | None = None

        def turn(prompt: str) -> TurnResult:
            nonlocal session
            if session is None:
                session = self.backend.resume_session(sid, role)
            return session.turn(prompt)

        previous = self._human_discuss_turn
        self._human_discuss_turn = turn
        try:
            return self.ask_human(banner, kind)
        finally:
            self._human_discuss_turn = previous
            if session is not None:
                session.close()

    def _control_ask(self, banner: str, kind: str) -> str:
        """File-channel ask_human (--control-dir): write NNN-question.json,
        poll for NNN-answer.json. Only a non-empty string `answer` is
        required of the answer file (`seq`/`ts`/`responder` are optional
        extras); a malformed or partial file is logged and re-read next
        tick — the hub may rewrite it — never silently swallowed. stop.flag
        is honored while waiting: semantically a human answering 'stop'
        (may interrupt an open session, tree possibly dirty)."""
        while ((self.control_dir / f"{self._control_seq:03d}-question.json")
               .exists()
               or (self.control_dir / f"{self._control_seq:03d}-answer.json")
               .exists()):
            self._control_seq += 1
        seq = self._control_seq
        self._control_seq += 1
        q_path = self.control_dir / f"{seq:03d}-question.json"
        a_path = self.control_dir / f"{seq:03d}-answer.json"
        payload = {
            "seq": seq,
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "kind": kind,
            "banner": banner,
            # Hub-side display alias; identical to banner until they diverge.
            "message": banner,
        }
        # Same-dir tmp + atomic rename: the hub must never read a partial
        # question (the `$`-less name keeps it out of the seq scan).
        tmp = q_path.with_name(q_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, q_path)
        self.log(f"control-dir question {seq:03d} written (kind={kind}) — "
                 f"awaiting {a_path.name}")
        last_error: str | None = None
        while True:
            if (self.control_dir / "stop.flag").exists():
                self.log(f"control-dir stop request (stop.flag) while "
                         f"awaiting answer {seq:03d} — aborting")
                sys.exit(f"stopped by control-dir stop.flag while awaiting "
                         f"answer {seq:03d}")
            if a_path.exists():
                error = None
                try:
                    data = json.loads(a_path.read_text())
                except (OSError, ValueError) as err:
                    error = f"unreadable JSON ({err})"
                else:
                    if not isinstance(data, dict):
                        error = "not a JSON object"
                    elif (not isinstance(data.get("answer"), str)
                          or not data["answer"].strip()):
                        error = "missing/empty `answer` string"
                if error is None:
                    if data.get("seq") not in (None, seq):
                        self.flog(f"control-dir answer {a_path.name}: seq "
                                  f"field {data['seq']!r} != {seq} "
                                  "(filename wins)")
                    if data.get("responder"):
                        self.flog(f"control-dir answer {seq:03d} responder: "
                                  f"{data['responder']}")
                    return data["answer"].strip()
                if error != last_error:
                    self.log(f"control-dir answer {a_path.name} malformed: "
                             f"{error} — waiting for a valid answer")
                    last_error = error
            time.sleep(CONTROL_POLL_SECONDS)

    # -- session driving --

    def run_session(self, role: str, first_prompt_fn) -> str:
        """One protocol session = one fresh conversation on the backend.
        Handles the request-event backstop and the post-check followup loop
        inside the same conversation. Returns the session id."""
        task_before = parse_task(self.task_path)
        status_before = task_before.status
        est_before = task_before.est_tuple if role == "dev" else None
        was_remediation = (
            role == "dev" and bool(task_before.review_entries)
            and task_before.review_entries[-1].verdict == "changes-requested")
        plan_gate: PlanGateResult | None = None
        if self.plan_gate and role == "dev" and not was_remediation:
            try:
                plan_session = self.backend.new_session(role)
            except SessionStartError as err:
                self._exit_session_start_error(err)
            try:
                self.log(f"--- {role} plan-gate start: "
                         f"sid={plan_session.sid or '(pending)'} "
                         f"backend={self.backend.describe(role)} "
                         f"status_before={status_before}")
                plan_gate = self._plan_gate_turn(
                    plan_session, first_prompt_fn(plan_session.sid))
                self.log(f"--- {role} plan-gate end: "
                         f"sid={plan_session.sid or '(pending)'}")
            finally:
                plan_session.close()
        try:
            session = self.backend.new_session(role)
        except SessionStartError as err:
            self._exit_session_start_error(err)
        try:
            sid = session.sid
            self.log(f"--- {role} session start: sid={sid or '(pending)'} "
                     f"backend={self.backend.describe(role)} "
                     f"status_before={status_before}")
            prompt = first_prompt_fn(sid)
            if plan_gate:
                prompt += "\n\n" + render_prompt(
                    "entry/approved-plan-gate",
                    ruling=plan_gate.ruling, plan=plan_gate.plan)
            followups = 0
            while True:
                try:
                    result = session.turn(prompt)
                except SessionStartError as err:
                    self._exit_session_start_error(err)
                if session.sid != sid:
                    sid = session.sid  # codex names its id on first turn
                if result.saw_request:
                    answer = self.ask_session_human(
                        session,
                        render_prompt("midflight/banner-request", role=role,
                                      sid=sid, log_file=self.log_file),
                        kind="request")
                    prompt = render_prompt("midflight/answered-continue",
                                           answer=answer)
                    continue
                if result.status == "error":
                    answer = self.ask_session_human(
                        session,
                        render_prompt("midflight/banner-run-error",
                                      role=role, sid=sid),
                        kind="run-error")
                    prompt = render_prompt("midflight/run-error-retry",
                                           answer=answer, role=role,
                                           task_id=self.task_id)
                    continue
                if not self.task_path.exists():
                    # cc-codex seam (seen live 2026-07-04): a final-gate pass
                    # trips the session's NATIVE Stop hook, which runs the
                    # /ai-sync-v2 close-out INSIDE the review session — the
                    # task is already archived when we get control back.
                    # Recognize the fait accompli instead of fighting it.
                    if (ARCHIVE_DIR / self.task_path.name).exists():
                        self._native_closeout_text = result.text
                        self.log("task file archived mid-session — the "
                                 "native hook chain ran the close-out "
                                 "inside the session; skipping post-checks")
                        break
                    sys.exit(f"task file vanished without an archive copy "
                             f"mid-session: {self.task_path} — investigate "
                             "manually")
                problems = self.post_checks(role, sid, status_before,
                                            est_before=est_before,
                                            was_remediation=was_remediation)
                if not problems:
                    # Context-budget check (port of stop-context-check.sh):
                    # discipline satisfied, but if the conversation outgrew
                    # the budget, the session-log entry must reflect a
                    # handoff, and above all we must NOT send further work
                    # into this conversation. run_session ends here; the
                    # next session is a fresh one.
                    if session.context_tokens > CONTEXT_BUDGET:
                        self.log(
                            f"context budget: ≈{session.context_tokens} > "
                            f"{CONTEXT_BUDGET} tokens — session must not "
                            "take more turns; handing off to a fresh session")
                    break
                followups += 1
                if session.context_tokens > CONTEXT_BUDGET:
                    # Over budget AND discipline unmet: one wrap-up turn.
                    # The handoff note depends on the session kind:
                    # only an incomplete REMEDIATION fix set may defer its
                    # re-review via the continuation marker.
                    if role == "review":
                        handoff_note = render_prompt(
                            "midflight/wrapup-note-review")
                        plan_note = ""
                    elif was_remediation:
                        handoff_note = render_prompt(
                            "midflight/wrapup-note-remediation")
                        plan_note = ""
                    else:
                        handoff_note = render_prompt(
                            "midflight/wrapup-note-advancement")
                        plan_note = render_prompt(
                            "midflight/wrapup-plan-advancement")
                    self.log(f"context budget exceeded "
                             f"(≈{session.context_tokens} tokens) with "
                             "violations — sending wrap-up instruction "
                             f"(followup {followups})")
                    prompt = render_prompt(
                        "midflight/wrapup",
                        context_tokens=session.context_tokens,
                        context_budget=CONTEXT_BUDGET,
                        clean_howto=CLEAN_HOWTO, sid=sid,
                        handoff_note=handoff_note, plan_note=plan_note)
                    if followups > MAX_FOLLOWUPS:
                        self.ask_session_human(
                            session,
                            render_prompt(
                                "midflight/banner-wrapup-exhausted",
                                problems="\n- ".join(problems)),
                            kind="followups-exhausted")
                        break
                    continue
                if followups > MAX_FOLLOWUPS:
                    answer = self.ask_session_human(
                        session,
                        render_prompt(
                            "midflight/banner-followups-exhausted",
                            max_followups=MAX_FOLLOWUPS,
                            problems="\n- ".join(problems)),
                        kind="followups-exhausted")
                    prompt = render_prompt("midflight/human-instruction",
                                           answer=answer)
                    followups = 0
                    continue
                self.log(f"post-check violations (followup {followups}): "
                         + "; ".join(problems))
                prompt = render_prompt(
                    "midflight/violation-fix",
                    problems="\n- ".join(problems),
                    clean_howto=CLEAN_HOWTO, sid=sid)
            end_status = ("completed+archived"
                          if not self.task_path.exists()
                          else parse_task(self.task_path).status)
            self.log(f"--- {role} session end: sid={sid} "
                     f"status={end_status}")
            return sid
        finally:
            session.close()

    @staticmethod
    def _plan_gate_confirmed(answer: str) -> bool:
        return answer.strip().lower() in {
            "confirm", "confirmed", "approve", "approved", "proceed",
            "yes", "y", "ok", "okay",
            "确认", "同意", "批准", "通过", "继续", "可以", "开始", "开工",
            "执行", "实施",
        }

    @staticmethod
    def _extract_plan_report(text: str) -> str | None:
        """Everything from the first `## Goal / Acceptance` heading line to
        the end of the reply — the plan-report artifact. None when the
        reply does not carry the heading."""
        m = PLAN_REPORT_START_RE.search(text)
        return text[m.start():].strip() if m else None

    def _plan_gate_turn(self, session: BackendSession,
                        base_prompt: str) -> PlanGateResult:
        """--plan-gate: extra conversational turns BEFORE any work, looping
        around a plan-report artifact rather than raw turn text. Each round
        either replaces the report wholesale (a reply restating it from the
        `## Goal / Acceptance` heading on) or explicitly keeps it (a purely
        clarifying reply ending `PLAN-REPORT: unchanged`); anything else
        keeps the current report with a WARNING in the banner
        (warn-and-keep). Rounds that keep the report show a rev/round
        pointer instead of re-attaching it. On confirm, the CURRENT report
        — never the last turn's raw text, never conversation history — is
        delivered with the human ruling to a fresh dev session. The plan
        lives only in the conversation and the orchestrator log — no
        task-file writes, no session-log entry, no status change. The plan
        contract text is injected (plan-rule wrapper) ahead of the gate
        instruction, mirroring the review-rule injection; base_prompt is the
        dev prompt, so the gate composition already carries the dev base +
        advancement-add wrappers (remediation never gates)."""
        prompt = (base_prompt + "\n\n"
                  + render_prompt("entry/plan-rule-wrapper",
                                  plan_rule=PLAN_RULE.read_text())
                  + "\n\n" + render_prompt("entry/plan-gate"))
        report, report_rev, report_round = "", 0, 0
        round_no = 0
        while True:
            round_no += 1
            result = session.turn(prompt)
            reply = (result.text or "").strip()
            # Banners are display-truncated; the log keeps every round in
            # full so pointer/truncation notes always have a target.
            self.flog(f"plan-gate round {round_no} full reply:\n{reply}")
            extracted = self._extract_plan_report(reply)
            unchanged = bool(PLAN_REPORT_UNCHANGED_RE.search(reply))
            warning = ""
            if unchanged and report:
                # The sentinel wins over a heading-shaped quote: an answer
                # may cite report sections; only an explicit restatement
                # (no sentinel) replaces the report.
                shown = PLAN_REPORT_UNCHANGED_RE.sub("", reply).strip() or reply
                headline = render_prompt(
                    "midflight/plan-headline-unchanged", sid=session.sid,
                    report_rev=report_rev, report_round=report_round,
                    log_file=self.log_file)
                self.log(f"plan-gate round {round_no}: plan-report unchanged "
                         f"(rev {report_rev} from round {report_round})")
            elif extracted and not unchanged:
                report = extracted
                report_rev += 1
                report_round = round_no
                shown = reply
                headline = render_prompt(
                    "midflight/plan-headline-revised", sid=session.sid,
                    report_rev=report_rev)
                self.log(f"plan-gate round {round_no}: plan-report revised "
                         f"→ rev {report_rev}")
            elif report:
                shown = reply
                headline = render_prompt(
                    "midflight/plan-headline-replied", sid=session.sid)
                warning = "\n\n" + render_prompt(
                    "midflight/plan-warning-keep", report_rev=report_rev,
                    report_round=report_round)
                self.log(f"plan-gate round {round_no}: reply matched no "
                         f"report shape — keeping rev {report_rev}")
            else:
                # First usable reply came in the wrong shape: adopt it
                # wholesale as rev 1 — there is nothing older to keep.
                report = reply
                report_rev, report_round = (1, round_no) if reply else (0, 0)
                shown = reply
                headline = render_prompt(
                    "midflight/plan-headline-proposes", sid=session.sid)
                if reply:
                    warning = "\n\n" + render_prompt(
                        "midflight/plan-warning-no-heading")
                    self.log(f"plan-gate round {round_no}: reply lacked the "
                             "report heading — adopted whole reply as rev 1")
            if shown:
                if len(shown) > PLAN_GATE_BANNER_CHARS:
                    shown = (shown[:PLAN_GATE_BANNER_CHARS] + "\n\n"
                             + render_prompt(
                                 "midflight/plan-truncation-note",
                                 chars=PLAN_GATE_BANNER_CHARS,
                                 log_file=self.log_file))
            else:
                shown = render_prompt("midflight/plan-no-reply",
                                      log_file=self.log_file)
            banner = render_prompt("midflight/banner-plan-gate",
                                   headline=headline, shown=shown,
                                   warning=warning)
            if not tree_clean():
                self.log("plan-gate violation: the planning turn modified the "
                         "tree — surfacing to human")
                banner += "\n\n" + render_prompt(
                    "midflight/plan-warning-dirty-tree")
            answer = self.ask_human(
                banner + "\n\n"
                + render_prompt("midflight/plan-confirm-instruction"),
                kind="plan-gate")
            if self._plan_gate_confirmed(answer):
                self.log(f"plan-gate confirmed (round {round_no}): "
                         f"delivering plan-report rev {report_rev} from "
                         f"round {report_round}")
                self.flog(f"plan-gate delivered plan-report rev "
                          f"{report_rev}:\n{report}")
                return PlanGateResult(plan=report, ruling=answer)
            prompt = render_prompt("midflight/plan-feedback", answer=answer)

    # -- post-session checks (Stop-hook replica / backstop) --

    @staticmethod
    def check_specs(role: str, sid: str | None, status_before: str,
                    est_before: tuple[int, int] | None = None,
                    was_remediation: bool = False):
        """Single source for the end-of-session discipline. Each spec is
        (check-id, requirement line, check(task) -> problem | None): the
        requirement line comes from the postcheck contract by check-id
        (contract_line, validated 1:1 against these bindings at startup),
        post_checks RUNS the checks, and the same rendered lines become the
        session prompt's POST-SESSION CHECKS preview — so what the agent is
        told and what the orchestrator verifies cannot drift.

        est_before: session-est at session start for a FRESH dev session
        (None skips — review sessions don't consume the estimate; a resumed
        blocked session already counted). was_remediation: latest review
        verdict was changes-requested at dev entry → status must not change
        (taskfile transition table)."""
        sid_disp = sid or "<this session's id>"
        specs: list[tuple[str, str, object]] = []
        specs.append((
            "tree-clean",
            contract_line("tree-clean"),
            lambda task: None if tree_clean() else
            "working tree is not clean (git status --porcelain is "
            "non-empty)"))
        specs.append((
            "session-log-entry",
            contract_line("session-log-entry", sid_disp=sid_disp),
            lambda task: None
            if sid and any(e.session_id == sid for e in task.entries) else
            f"no `## Session log` entry for session id {sid}"))

        def _claim_sid(task):
            # Character-exact claim check: a session re-typing its id (LLM
            # transcription drift) breaks the task↔transcript join even when
            # every other declaration is right.
            if not sid:
                return None
            got = (task.claimed_by or "").split("@")[0].strip()
            if got == sid:
                return None
            return (f"frontmatter `claimed-by` sid `{got or '(empty)'}` "
                    f"does not match this session's id `{sid}` — re-claim "
                    "with the exact id (taskfile schema claim rules)")
        specs.append((
            "claim-sid",
            contract_line("claim-sid", sid_disp=sid_disp),
            _claim_sid))

        def _fix_set_value(task):
            if task.fix_set and task.fix_set != "open":
                return (f"frontmatter `fix-set: {task.fix_set}` is not a "
                        "legal value — the only legal value is `open`; "
                        "remove the line when the fix set is complete")
            return None

        def _fix_set_closed(task):
            if task.fix_set:
                return ("frontmatter `fix-set` is set after this session — "
                        "the flag is declared only by a remediation "
                        "session with an incomplete fix set; remove the "
                        "line")
            return None
        if role == "dev":
            if was_remediation:
                specs.append((
                    "dev-remediation-status",
                    contract_line("dev-remediation-status",
                                  status_before=status_before),
                    lambda task: None
                    if task.status in {status_before, "blocked"} else
                    f"remediation session changed status `{status_before}` "
                    f"→ `{task.status}` — a remediation session never "
                    "touches status (taskfile transition table); restore "
                    "it"))
                specs.append((
                    "fix-set-value",
                    contract_line("fix-set-value"),
                    _fix_set_value))
            else:
                specs.append((
                    "dev-advancement-status",
                    contract_line("dev-advancement-status"),
                    lambda task: None
                    if task.status in DEV_LEGAL_STATUSES else
                    f"status `{task.status}` is illegal for a dev session "
                    f"(allowed: {sorted(DEV_LEGAL_STATUSES)}; a dev session "
                    "never sets completed)"))

                specs.append((
                    "fix-set-closed",
                    contract_line("fix-set-closed"),
                    _fix_set_closed))
            if est_before:
                cur, tot = est_before
                nxt, ntot = cur + 1, max(tot, cur + 1)
                undershoot = (render_prompt("entry/est-undershoot-note")
                              if ntot > tot else "")

                def _est_check(task, cur=cur, tot=tot):
                    after = task.est_tuple
                    if after and after[0] > cur:
                        return None
                    return (f"session-est not incremented: still {task.est} "
                            f"(was {cur}/{tot}) — a dev session increments "
                            "<current> as part of the claim; raise "
                            "<total> too if the estimate "
                            "undershot")
                specs.append((
                    "dev-est-increment",
                    contract_line("dev-est-increment", cur=cur, tot=tot,
                                  nxt=nxt, ntot=ntot, undershoot=undershoot),
                    _est_check))
        else:
            allowed = REVIEW_LEGAL.get(status_before,
                                       {"in_progress", "blocked"})
            if status_before == "final_review":
                menu_id = "review-status-final-gate"
                menu = contract_line(menu_id)
            else:
                menu_id = "review-status-interim"
                menu = contract_line(menu_id, status_before=status_before)

            def _entry_check(task):
                latest = (task.review_entries[-1]
                          if task.review_entries else None)
                if not latest or latest.session_id != sid:
                    return None
                probs = []
                if not latest.verdict:
                    probs.append("review entry lacks a `Verdict:` line")
                if not latest.group:
                    probs.append("review entry lacks a `Group:` line "
                                 "(convergence group anchor)")
                return "; ".join(probs) or None
            specs.append((
                menu_id,
                menu,
                lambda task: None if task.status in allowed else
                f"status `{task.status}` is an illegal review transition "
                f"from `{status_before}` (allowed: {sorted(allowed)})"))
            specs.append((
                "review-entry-fields",
                contract_line("review-entry-fields"),
                _entry_check))
            specs.append((
                "fix-set-closed",
                contract_line("fix-set-closed"),
                _fix_set_closed))
        return specs

    def post_checks(self, role: str, sid: str | None, status_before: str,
                    est_before: tuple[int, int] | None = None,
                    was_remediation: bool = False) -> list[str]:
        task = parse_task(self.task_path)
        specs = self.check_specs(role, sid, status_before,
                                 est_before=est_before,
                                 was_remediation=was_remediation)
        return [p for _, _, check in specs if (p := check(task))]

    def checks_preview(self, role: str, sid: str | None, status_before: str,
                       est_before: tuple[int, int] | None = None,
                       was_remediation: bool = False) -> str:
        specs = self.check_specs(role, sid, status_before,
                                 est_before=est_before,
                                 was_remediation=was_remediation)
        return (render_prompt("entry/checks-preview-header") + "\n"
                + "\n".join(f"- {line}" for _, line, _ in specs))

    # -- prompts --

    def _sid_line(self, sid: str | None) -> str:
        if sid:
            return render_prompt("entry/sid-line", sid=sid)
        return render_prompt("entry/sid-line-from-hook")

    def _preamble(self, sid: str | None) -> list[str]:
        parts: list[str] = []
        if self.backend.injects_protocol:
            parts.append(protocol_block(sid or "orchestrated-session"))
        else:
            parts.append(render_prompt("entry/preamble-native-note"))
        parts += [render_prompt("entry/automation-wrapper",
                                automation_md=render_prompt(
                                    "entry/conduct-annex")), ""]
        return parts

    @staticmethod
    def _utc_now() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _entry_checklist(self, role: str, sid: str | None,
                         task: TaskState) -> str:
        """Item-level instantiation of the entry-bookkeeping claim steps
        with this session's concrete values (attention amplification — the
        rules themselves live in the protocol docs)."""
        sid_disp = sid or "<your session id from the session-start context>"
        lines = [render_prompt("entry/checklist-header"),
                 render_prompt("entry/checklist-claim", sid_disp=sid_disp,
                               now=self._utc_now())]
        if role == "dev":
            if task.est_tuple:
                cur, tot = task.est_tuple
                nxt, ntot = cur + 1, max(tot, cur + 1)
                undershoot = (render_prompt("entry/checklist-est-undershoot")
                              if ntot > tot else "")
                lines.append(render_prompt(
                    "entry/checklist-dev-est", cur=cur, tot=tot, nxt=nxt,
                    ntot=ntot, undershoot=undershoot))
            else:
                lines.append(render_prompt("entry/checklist-dev-est-unknown"))
            if task.status == "pending":
                lines.append(render_prompt(
                    "entry/checklist-dev-claim-status"))
            lines.append(render_prompt("entry/checklist-dev-prefetch"))
        else:
            lines.append(render_prompt("entry/checklist-review-est"))
            pending = task.unreviewed_dev_sids()
            lines.append(render_prompt(
                "entry/checklist-review-pending",
                pending=", ".join(pending) if pending else "(empty)"))
        return "\n".join(lines)

    def dev_prompt(self, sid: str | None) -> str:
        task = parse_task(self.task_path)
        latest = task.review_entries[-1] if task.review_entries else None
        remediation = bool(latest and latest.verdict == "changes-requested")
        parts = self._preamble(sid)
        if remediation:
            parts.append(render_prompt(
                "entry/dev-remediation-wrapper",
                dev_rule=DEV_REMEDIATION_RULE.read_text()))
        else:
            parts.append(render_prompt(
                "entry/dev-advancement-wrapper",
                dev_rule=DEV_ADVANCEMENT_RULE.read_text()))
        parts.append("")
        parts.append(render_prompt("entry/dev-invocation",
                                   task_id=self.task_id,
                                   sid_line=self._sid_line(sid)))
        parts.append("\n" + self._entry_checklist("dev", sid, task))
        if remediation:
            group = latest.group or latest.reviewed_sid or latest.session_id
            parts.append("\n" + render_prompt("entry/dev-remediation",
                                              group=group))
        else:
            parts.append("\n" + render_prompt("entry/dev-pre-re-est"))
        if self.pending_ruling:
            parts.append("\n" + render_prompt("entry/human-ruling",
                                              ruling=self.pending_ruling))
            self.pending_ruling = None
        parts.append("\n" + self.checks_preview(
            "dev", sid, task.status, est_before=task.est_tuple,
            was_remediation=remediation))
        return "\n".join(parts)

    def review_prompt(self, sid: str | None) -> str:
        task = parse_task(self.task_path)
        return "\n".join([
            *self._preamble(sid),
            render_prompt("entry/review-rule-wrapper",
                          review_rule=REVIEW_RULE.read_text()),
            "",
            render_prompt("entry/review-invocation", task_id=self.task_id,
                          sid_line=self._sid_line(sid)),
            render_prompt("entry/review-independence"),
            "",
            self._entry_checklist("review", sid, task),
            "",
            self.checks_preview("review", sid, task.status),
        ])

    # -- escalation paths --

    def handle_blocked(self) -> None:
        task = parse_task(self.task_path)
        blocked_sid = task.claimed_by.split("@")[0].strip()
        # The transition table lets ANY session block — dev or review (e.g. a
        # reviewer escalating its round budget). Resume with the role the
        # blocked session actually had, and post-check against the status
        # it entered with: the left side of `→ blocked` in its entry
        # heading — the same value the resume prompt tells it to restore.
        own = [e for e in task.entries if e.session_id == blocked_sid]
        role = "review" if any(e.is_review for e in own) else "dev"
        status_before = "blocked"
        if own:
            m = re.search(r"\(\s*(\w+)\s*(?:→|->)\s*blocked\s*\)",
                          own[-1].heading)
            if m:
                status_before = m.group(1)
        latest = task.entries[-1] if task.entries else None
        open_ctx = ""
        if latest:
            m = re.search(r"- Open:(.*)", latest.body, re.DOTALL)
            open_ctx = (m.group(1).strip() if m else latest.body.strip())[:2000]
        session: BackendSession | None = None

        def discuss_turn(prompt: str) -> TurnResult:
            nonlocal session
            if session is None:
                session = self.backend.resume_session(blocked_sid, role)
            return session.turn(prompt)

        previous = self._human_discuss_turn
        self._human_discuss_turn = discuss_turn
        try:
            try:
                answer = self.ask_human(
                    render_prompt("midflight/banner-blocked", role=role,
                                  sid=blocked_sid, blockers=task.blockers,
                                  open_context=open_ctx),
                    kind="blocked")
                if session is None:
                    session = self.backend.resume_session(blocked_sid, role)
            except Exception as err:
                sys.exit(
                    f"cannot resume blocked session {blocked_sid}: {err}\n"
                    "If it was created manually in another tool, answer the "
                    "blocker there (or edit the task file) and restart.")
            self.log(f"resuming blocked {role} session {blocked_sid} "
                     "with answer")
            prompt = render_prompt("midflight/blocked-resume", answer=answer)
            result = session.turn(prompt)
            if not self.task_path.exists():
                # Same cc-codex seam as run_session: a resumed blocked
                # REVIEWER may conclude the final gate (pass → completed),
                # which trips its native Stop hook → in-session close-out →
                # the task is archived before post-checks can parse it.
                if (ARCHIVE_DIR / self.task_path.name).exists():
                    self._native_closeout_text = result.text
                    if role == "review":
                        self.last_review_agent = blocked_sid
                    self.log("task file archived mid-session — the native "
                             "hook chain ran the close-out inside the "
                             "resumed session; skipping post-checks")
                    return
                sys.exit(f"task file vanished without an archive copy "
                         f"mid-session: {self.task_path} — investigate "
                         "manually")
            problems = self.post_checks(role, blocked_sid, status_before)
            if problems:
                session.turn(render_prompt("midflight/blocked-violation",
                                           problems="; ".join(problems)))
        finally:
            self._human_discuss_turn = previous
            if session is not None:
                session.close()

    def check_convergence(self) -> None:
        """After a review session: enforce the per-group budget, detect a
        reviewer-side escalation (final_review kept + changes-requested),
        and pause immediately on a surviving dispute (`Dispute-unresolved:`
        marker — a disagreement both sides hold is escalated, not looped)."""
        if not self.task_path.exists():
            return  # archived by the native in-session close-out; loop() exits
        task = parse_task(self.task_path)
        if not task.review_entries:
            return
        latest = task.review_entries[-1]
        m = re.search(r"^\s*-\s*Dispute-unresolved:\s*(.*)$", latest.body,
                      re.MULTILINE)
        if m:
            self.log("convergence: unresolved dispute — escalating "
                     "immediately (no budget rounds spent on it)")
            ruling = self.ask_resumable_human(
                render_prompt("midflight/banner-dispute",
                              reviewer_line=m.group(1).strip()[:500],
                              review_entry=latest.body.strip()[:3000]),
                kind="dispute-unresolved", sid=latest.session_id,
                role="review")
            self.pending_ruling = ruling
            return
        if latest.verdict != "changes-requested":
            return
        group = latest.group
        rounds = sum(1 for e in task.review_entries
                     if e.group == group and e.verdict == "changes-requested")
        self.log(f"convergence: group={group} changes-requested rounds={rounds}")
        if rounds > GROUP_BUDGET:
            findings = latest.body.strip()[:3000]
            ruling = self.ask_resumable_human(
                render_prompt("midflight/banner-convergence", group=group,
                              rounds=rounds, findings=findings),
                kind="convergence-budget", sid=latest.session_id,
                role="review")
            self.pending_ruling = ruling

    # -- close-out --

    def _active_task_paths(self) -> list[Path]:
        return sorted(p for p in TASKS_DIR.glob("*.md")
                      if p.name != INDEX_FILE.name)

    @staticmethod
    def _blocker_items(raw: str) -> list[str]:
        text = (raw or "").strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return [item.strip().strip("`'\"") for item in text.split(",")
                if item.strip()]

    @staticmethod
    def _remaining_task_audit_seen(text: str | None) -> bool:
        return bool(text and re.search(r"remaining[- ]task audit\s*:",
                                       text, re.IGNORECASE))

    def _closeout_problems(self, audit_text: str | None = None) -> list[str]:
        problems = []
        if self.task_path.exists():
            problems.append(f"{self.task_path.name} still in .ai-tasks/ "
                            "(not archived)")
        if not (ARCHIVE_DIR / self.task_path.name).exists():
            problems.append("archive copy missing")
        if self.task_id in INDEX_FILE.read_text():
            problems.append("task row still present in .ai-tasks/index.md")
        archived_ids = {self.task_id}
        if ARCHIVE_DIR.exists():
            archived_ids.update(p.stem for p in ARCHIVE_DIR.glob("*.md"))
        for path in self._active_task_paths():
            if path == self.task_path:
                continue
            task = parse_task(path)
            blockers = self._blocker_items(task.blockers)
            stale = sorted(set(blockers) & archived_ids)
            for blocker in stale:
                problems.append(f"stale blocker: {path.name} still lists "
                                f"archived task {blocker} in blockers")
            if task.status == "blocked" and not blockers:
                problems.append(f"blocked task {path.name} has no blockers; "
                                "restore an active status or add a real "
                                "blocker")
        if not self._remaining_task_audit_seen(audit_text):
            problems.append("remaining-task audit evidence missing (reply "
                            "with `Remaining-task audit: checked N active "
                            "task(s); updated ...; unchanged ...`)")
        if not tree_clean():
            problems.append("working tree not clean after close-out")
        return problems

    def close_out(self) -> None:
        if not self.last_review_agent:
            self.log("status=completed but no review agent from this run — "
                     "spawning a fresh close-out session (review role)")
        active_count = len([p for p in self._active_task_paths()
                            if p != self.task_path])
        prompt = render_prompt("entry/closeout", task_id=self.task_id,
                               sync_skill=SYNC_SKILL,
                               active_count=active_count)
        if self.last_review_agent:
            session = self.backend.resume_session(self.last_review_agent,
                                                  "review")
        else:
            session = self.backend.new_session("review")
        try:
            result = session.turn(prompt)
            problems = self._closeout_problems(result.text)
            for _ in range(MAX_FOLLOWUPS):
                if not problems:
                    break
                self.log("close-out violations: " + "; ".join(problems))
                result = session.turn(
                    render_prompt("midflight/closeout-incomplete",
                                  problems="\n- ".join(problems)))
                problems = self._closeout_problems(result.text)
            if problems:
                self.ask_session_human(
                    session,
                    render_prompt("midflight/banner-closeout-incomplete",
                                  problems="; ".join(problems)),
                    kind="closeout-incomplete")
        finally:
            session.close()
        self.log("close-out done")

    # -- main loop --

    def _verify_native_closeout(self) -> None:
        """The session's native hook chain (cc/codex Stop hook →
        /ai-sync-v2) archived the task mid-session. Verify the close-out is
        complete — archive copy already confirmed by the caller — and end
        the run instead of driving a second close-out."""
        problems = self._closeout_problems(self._native_closeout_text)
        if problems:
            banner = render_prompt(
                "midflight/banner-native-closeout-incomplete",
                problems="\n- ".join(problems))
            if self.last_review_agent:
                self.ask_resumable_human(
                    banner, kind="closeout-incomplete",
                    sid=self.last_review_agent, role="review")
            else:
                self.ask_human(banner, kind="closeout-incomplete")
        self.log("close-out done (performed in-session by the native hook "
                 "chain; orchestrator verified archive/index/tree/remaining "
                 "tasks)")

    def loop(self) -> None:
        if self.control_dir:
            self.log(f"control-dir enabled: {self.control_dir} "
                     f"(next question seq {self._control_seq:03d})")
            if REPO in self.control_dir.parents:
                self.log("WARNING: control dir is inside the repo working "
                         "tree — question/answer files will dirty the tree "
                         "and fail post-checks; use a run dir outside the "
                         "repo")
        if not self.task_path.exists():
            if (ARCHIVE_DIR / self.task_path.name).exists():
                sys.exit(f"task already archived: {self.task_path.name}")
            sys.exit(f"task file not found: {self.task_path}")
        sessions = 0
        while True:
            if (self.control_dir
                    and (self.control_dir / "stop.flag").exists()):
                self.log("control-dir stop request (stop.flag) — stopping "
                         "at session boundary")
                return
            if not self.task_path.exists():
                if (ARCHIVE_DIR / self.task_path.name).exists():
                    self._verify_native_closeout()
                    return
                sys.exit(f"task file vanished without an archive copy: "
                         f"{self.task_path} — investigate manually")
            task = parse_task(self.task_path)
            self.log(f"state: status={task.status} "
                     f"pending-review={task.unreviewed_dev_sids()}")
            if task.status == "completed":
                self.close_out()
                return
            if task.status == "blocked":
                self.handle_blocked()
                continue
            if sessions >= self.max_sessions:
                sys.exit(f"session budget exhausted ({self.max_sessions}); "
                         "re-run to continue")
            pending = task.unreviewed_dev_sids()
            fix_set_open = task.fix_set == "open"
            remediation_open = bool(
                task.review_entries
                and task.review_entries[-1].verdict == "changes-requested")
            if fix_set_open and not remediation_open:
                # The flag is remediation-only (dev contract). Without an
                # open remediation it is protocol-illegal — ignore it rather
                # than skipping the review.
                self.log("WARNING: frontmatter `fix-set: open` without a "
                         "changes-requested latest review verdict — "
                         "remediation-only flag ignored; landed work is "
                         "reviewed next")
                fix_set_open = False
            if fix_set_open:
                # Remediation continuation: the last remediation session
                # wrapped up (context budget) before its fix set was
                # complete. The same role continues; re-review waits until
                # the fix set completes (the fix-set line removed).
                self.log("remediation continuation: resuming dev in a fresh "
                         "session (re-review deferred until the fix set "
                         "completes)")
                self.run_session("dev", self.dev_prompt)
                sessions += 1
            elif pending:
                self.last_review_agent = self.run_session(
                    "review", self.review_prompt)
                sessions += 1
                self.check_convergence()
            elif task.status == "final_review":
                latest_rev = (task.review_entries[-1]
                              if task.review_entries else None)
                if latest_rev and latest_rev.verdict == "changes-requested":
                    # Final-gate rejection keeps final_review (transition table);
                    # the next turn is a dev remediation session.
                    self.log("final-gate rejection: dispatching dev "
                             "remediation (status stays final_review)")
                    self.run_session("dev", self.dev_prompt)
                    sessions += 1
                else:
                    # Everything reviewed, still final_review, and the last
                    # review didn't conclude with a verdict-driven handback:
                    # a dumb scheduler doesn't loop on this.
                    banner = render_prompt(
                        "midflight/banner-final-review-stall")
                    if latest_rev:
                        ruling = self.ask_resumable_human(
                            banner, kind="final-review-stall",
                            sid=latest_rev.session_id, role="review")
                    else:
                        ruling = self.ask_human(
                            banner, kind="final-review-stall")
                    self.pending_ruling = ruling
                    self.last_review_agent = self.run_session(
                        "review",
                        lambda sid: self.review_prompt(sid) + "\n"
                        + render_prompt("entry/stall-ruling", ruling=ruling))
                    sessions += 1
                    self.check_convergence()
            else:  # pending / in_progress, nothing awaiting review → dev turn
                self.run_session("dev", self.dev_prompt)
                sessions += 1
            if self.once:
                self.log("--once: stopping after one session")
                return


# --- entry ------------------------------------------------------------------

# Imported at module level so the mock tests can monkeypatch
# `orchestrator.Agent`. Kept as a soft dependency: the cc-codex backend must
# work without a usable cursor_sdk install.
try:
    from cursor_sdk import Agent, CursorAgentError  # noqa: F401
except Exception:  # pragma: no cover
    Agent = None  # type: ignore[assignment]
    CursorAgentError = None  # type: ignore[assignment]


def validate_models(api_key: str | None, ids: list[str]) -> None:
    avail: list[str] = []
    try:
        from cursor_sdk import Cursor
        avail = sorted(m.id for m in Cursor.models.list(api_key=api_key))
    except Exception:
        # The SDK catalog call needs an API key even when cursor-agent is
        # logged in; fall back to the CLI's model list.
        proc = subprocess.run(["cursor-agent", "models"], capture_output=True,
                              text=True, check=False)
        avail = sorted(ln.split(" - ")[0].strip()
                       for ln in proc.stdout.splitlines()
                       if " - " in ln)
    if not avail:
        print("WARNING: cannot list models — skipping validation "
              "(a bad model id will fail at session start)", flush=True)
        return
    missing = [i for i in ids if i not in avail]
    if missing:
        sys.exit(f"model(s) not available: {missing}\navailable: {avail}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task_id", nargs="?",
                    type=lambda s: s.removesuffix(".md"),
                    help="task id, e.g. 2026-06-23-v1-risk-control "
                         "(a trailing .md is tolerated); omitted only with "
                         "--print-config")
    ap.add_argument("--print-config", action="store_true",
                    help="print the resolved launch configuration as JSON "
                         "and exit without starting a task")
    ap.add_argument("--profile", choices=sorted(ORCH_CONFIG["profiles"]),
                    default=None,
                    help="backward-compatible run-wide named profile; "
                         "role-specific profiles and explicit role flags "
                         "override its values")
    role_profile_choices = ["default", *sorted(ORCH_CONFIG["profiles"])]
    ap.add_argument("--dev-profile", choices=role_profile_choices,
                    default=None,
                    help="optional dev-role profile; omitted inherits "
                         "--profile, while 'default' explicitly restores "
                         "environment/config inheritance for dev")
    ap.add_argument("--review-profile", choices=role_profile_choices,
                    default=None,
                    help="optional review-role profile; omitted inherits "
                         "--profile, while 'default' explicitly restores "
                         "environment/config inheritance for review")
    ap.add_argument("--once", action="store_true",
                    help="run exactly one session, then exit")
    ap.add_argument("--backend", choices=["cursor", "cc-codex"],
                    default=None,
                    help=f"cursor = Cursor SDK both roles; cc-codex = "
                         f"Claude/Codex dev + Codex CLI review "
                         f"(effective default: {DEFAULT_BACKEND})")
    ap.add_argument("--dev-agent", choices=sorted(CLI_AGENTS),
                    default=None,
                    help="cc-codex only: dev-role CLI agent. Default: "
                         f"{DEFAULT_CC_DEV_AGENT} (claude = Claude Code; "
                         "codex = Codex CLI)")
    ap.add_argument("--review-agent", choices=sorted(CLI_AGENTS),
                    default=None,
                    help="cc-codex only: review-role CLI agent. Default: "
                         f"{DEFAULT_CC_REVIEW_AGENT}. Selecting the dev "
                         "agent's model here gives up the cross-model "
                         "independence the review prompt states")
    ap.add_argument("--plan-gate", action="store_true",
                    help="each dev session first iterates a plan-report in a "
                         "read-only planning session and blocks for human "
                         "confirmation before implementing")
    ap.add_argument("--dev-model", default=None,
                    help=f"default: {DEFAULT_DEV_MODEL} (cursor) / "
                         f"{DEFAULT_CC_MODEL} (cc-codex claude dev) / "
                         f"{DEFAULT_CODEX_DEV_MODEL} (cc-codex codex dev)")
    ap.add_argument("--review-model", default=None,
                    help=f"default: {DEFAULT_REVIEW_MODEL} (cursor) / "
                         f"{DEFAULT_CC_REVIEW_MODEL} (cc-codex claude "
                         f"review) / {DEFAULT_CODEX_MODEL} (cc-codex codex "
                         "review)")
    ap.add_argument("--dev-effort", default=None,
                    help="dev-role effort. cursor: claude effort axis "
                         f"low..max (default: {DEFAULT_CURSOR_DEV_EFFORT}); "
                         "cc-codex claude: claude --effort "
                         f"(default: {DEFAULT_CC_EFFORT}); cc-codex codex: "
                         "codex reasoning effort "
                         f"(default: {DEFAULT_CODEX_DEV_EFFORT})")
    ap.add_argument("--review-effort", default=None,
                    help="review-role effort, validated on the selected "
                         "review agent's axis: codex/cursor-gpt reasoning "
                         "none/minimal/low/medium/high/xhigh (cursor calls "
                         "the top tier 'extra-high'; both spellings are "
                         "accepted and translated per backend), claude "
                         f"effort low..max. Default: cursor = "
                         f"{DEFAULT_CURSOR_REVIEW_EFFORT}; cc-codex claude "
                         f"= {DEFAULT_CC_REVIEW_EFFORT}; cc-codex codex = "
                         f"{DEFAULT_CODEX_EFFORT}")
    ap.add_argument("--max-sessions", type=int, default=None,
                    help=f"maximum sessions (default: "
                         f"{DEFAULT_MAX_SESSIONS})")
    ap.add_argument("--control-dir", type=Path, default=None, metavar="DIR",
                    help="file-based control channel for an external "
                         "supervisor (orch-hub): every human escalation "
                         "writes NNN-question.json into DIR and waits for "
                         "NNN-answer.json; a stop.flag file in DIR stops "
                         "the run at the next safe point. Unset (default): "
                         "interactive stdin, behavior unchanged")
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        resolved = resolve_launch_config(
            backend=args.backend,
            profile=args.profile,
            dev_profile=args.dev_profile,
            review_profile=args.review_profile,
            dev_agent=args.dev_agent,
            dev_model=args.dev_model,
            review_agent=args.review_agent,
            review_model=args.review_model,
            dev_effort=args.dev_effort,
            review_effort=args.review_effort,
            max_sessions=args.max_sessions,
        )
    except OrchestratorConfigError as err:
        ap.error(str(err))

    launch_config = launch_config_dict(resolved, args)
    if args.print_config:
        print(json.dumps(launch_config, indent=2, sort_keys=True))
        return
    if not args.task_id:
        ap.error("task_id is required unless --print-config is used")

    err = prompts_error()
    if err:
        sys.exit(err)

    dev_model = resolved.dev_model
    review_model = resolved.review_model
    dev_effort = resolved.dev_effort
    review_effort = resolved.review_effort
    if resolved.backend == "cursor":
        api_key = os.environ.get("CURSOR_API_KEY")
        validate_models(api_key, [dev_model, review_model])
        # Hooks must no-op inside SDK-driven sessions (the orchestrator owns
        # the lifecycle); the SDK's local executor inherits this env.
        os.environ["AI_ORCH"] = "1"
        orch = Orchestrator(args.task_id, dev_model, review_model, api_key,
                            args.once, resolved.max_sessions,
                            plan_gate=args.plan_gate,
                            control_dir=args.control_dir)
        orch.backend = CursorBackend(orch, dev_model, review_model, api_key,
                                     dev_effort=dev_effort,
                                     review_effort=review_effort)
    else:
        dev_agent, review_agent = resolved.dev_agent, resolved.review_agent
        assert dev_agent is not None and review_agent is not None
        # NO AI_ORCH here: cc/codex hooks must fire — they carry protocol
        # injection and end-discipline natively for their own sessions.
        orch = Orchestrator(args.task_id, dev_model, review_model, None,
                            args.once, resolved.max_sessions,
                            plan_gate=args.plan_gate,
                            control_dir=args.control_dir)
        orch.backend = CliBackend(orch, dev_agent, dev_model, dev_effort,
                                  review_agent, review_model, review_effort)

    orch.log("effective-config: " + json.dumps(
        launch_config, sort_keys=True, separators=(",", ":")))
    orch.log(f"orchestrator start: task={args.task_id} "
             f"backend={resolved.backend} profiles="
             f"dev:{resolved.dev_profile or 'default'},"
             f"review:{resolved.review_profile or 'default'} "
             f"dev={orch.backend.describe('dev')} "
             f"review={orch.backend.describe('review')} once={args.once} "
             f"plan_gate={args.plan_gate}"
             + (f" control_dir={orch.control_dir}" if orch.control_dir
                else ""))
    notice = same_model_notice(launch_config)
    if notice:
        orch.log(notice)
    if not tree_clean():
        sys.exit("working tree is not clean — resolve before orchestrating")
    orch.loop()
    orch.log("orchestrator done")


if __name__ == "__main__":
    main()
