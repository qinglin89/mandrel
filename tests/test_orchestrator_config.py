from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = ROOT / "canonical" / "orchestrator" / "orchestrator.py"


def query_config(*args: str, env_overrides: dict[str, str] | None = None) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("ORCH_")
    }
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "--print-config", *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def test_default_config_is_visible_without_a_task_or_profile() -> None:
    config = query_config()

    assert config["profile"] == "default"
    assert config["backend"] == "cc-codex"
    assert config["dev"] == {
        "agent": "claude",
        "model": "claude-opus-4-8",
        "effort": "max",
    }
    assert config["review"] == {
        "agent": "codex",
        "model": "gpt-5.5",
        "effort": "xhigh",
    }
    assert config["codex_sandbox"] == "danger-full-access"
    assert config["max_sessions"] == 40
    assert config["available_profiles"] == ["excellent", "standard"]
    assert config["sources"]["dev.model"] == "config"


def test_named_profiles_inherit_cc_codex_without_a_backend_flag() -> None:
    for profile in ("standard", "excellent"):
        config = query_config("--profile", profile)

        assert config["profile"] == profile
        assert config["backend"] == "cc-codex"
        assert config["dev"]["agent"] == "claude"
        assert config["review"]["agent"] == "codex"
        assert config["sources"]["backend"] == "config"


def test_excellent_profile_uses_claude_cli_full_model_name() -> None:
    config = query_config("--profile", "excellent")

    assert config["backend"] == "cc-codex"
    assert config["dev"]["agent"] == "claude"
    assert config["dev"]["model"] == "claude-fable-5"


def test_custom_role_flags_inherit_cc_codex_without_a_backend_flag() -> None:
    config = query_config(
        "--dev-agent", "codex",
        "--dev-model", "custom-dev",
        "--dev-effort", "high",
        "--review-model", "custom-review",
        "--review-effort", "medium",
    )

    assert config["backend"] == "cc-codex"
    assert config["dev"] == {
        "agent": "codex",
        "model": "custom-dev",
        "effort": "high",
    }
    assert config["review"] == {
        "agent": "codex",
        "model": "custom-review",
        "effort": "medium",
    }
    assert config["sources"]["backend"] == "config"


def test_named_profiles_resolve_backend_specific_values() -> None:
    standard = query_config("--profile", "standard", "--backend", "cc-codex")
    excellent = query_config("--profile", "excellent", "--backend", "cursor")

    assert standard["dev"] == {
        "agent": "claude",
        "model": "claude-opus-4-8",
        "effort": "max",
    }
    assert standard["review"]["model"] == "gpt-5.5"
    assert standard["review"]["effort"] == "xhigh"
    assert standard["sources"]["dev.model"] == "profile:standard"

    assert excellent["dev"]["model"] == "claude-fable-5"
    assert excellent["dev"]["effort"] == "max"
    assert excellent["review"]["model"] == "gpt-5.6-sol"
    assert excellent["review"]["effort"] == "xhigh"


def test_resolution_precedence_is_cli_then_profile_then_env_then_config() -> None:
    env = {
        "ORCH_CODEX_MODEL": "env-review",
        "ORCH_CODEX_EFFORT": "low",
    }
    inherited = query_config(env_overrides=env)
    profiled = query_config("--profile", "excellent", env_overrides=env)
    explicit = query_config(
        "--profile", "excellent",
        "--review-model", "cli-review",
        "--review-effort", "high",
        env_overrides=env,
    )

    assert inherited["review"]["model"] == "env-review"
    assert inherited["review"]["effort"] == "low"
    assert inherited["sources"]["review.model"] == "env:ORCH_CODEX_MODEL"
    assert inherited["effective_revision"] != profiled["effective_revision"]

    assert profiled["review"]["model"] == "gpt-5.6-sol"
    assert profiled["review"]["effort"] == "xhigh"
    assert profiled["sources"]["review.model"] == "profile:excellent"

    assert explicit["review"]["model"] == "cli-review"
    assert explicit["review"]["effort"] == "high"
    assert explicit["sources"]["review.model"] == "cli"


def test_cc_codex_dev_agent_selects_its_default_namespace() -> None:
    config = query_config(
        "--backend", "cc-codex",
        "--dev-agent", "codex",
    )

    assert config["dev"] == {
        "agent": "codex",
        "model": "gpt-5.5",
        "effort": "xhigh",
    }
    assert config["sources"]["dev.agent"] == "cli"


def test_cli_max_sessions_supersedes_an_invalid_environment_value() -> None:
    config = query_config(
        "--max-sessions", "7",
        env_overrides={"ORCH_MAX_SESSIONS": "not-an-integer"},
    )

    assert config["max_sessions"] == 7
    assert config["sources"]["max_sessions"] == "cli"


def test_profile_dev_agent_override_requires_a_complete_custom_pair() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ORCHESTRATOR),
            "--print-config",
            "--backend", "cc-codex",
            "--profile", "standard",
            "--dev-agent", "codex",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert "also requires explicit --dev-model and --dev-effort" in proc.stderr
