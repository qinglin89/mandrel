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


def refuse_config(*args: str, env_overrides: dict[str, str] | None = None) -> str:
    """Run a rejected selection and return its stderr."""
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
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stdout
    return proc.stderr


def test_default_config_is_visible_without_a_task_or_profile() -> None:
    config = query_config()

    assert config["schema_version"] == 2
    assert config["profile"] == "default"
    assert config["profiles"] == {
        "dev": "default",
        "review": "default",
    }
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
        assert config["profiles"] == {
            "dev": profile,
            "review": profile,
        }
        assert config["backend"] == "cc-codex"
        assert config["dev"]["agent"] == "claude"
        assert config["review"]["agent"] == "codex"
        assert config["sources"]["backend"] == "config"


def test_excellent_profile_uses_claude_cli_full_model_name() -> None:
    config = query_config("--profile", "excellent")

    assert config["backend"] == "cc-codex"
    assert config["dev"]["agent"] == "claude"
    assert config["dev"]["model"] == "claude-opus-5"


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

    assert excellent["dev"]["model"] == "claude-opus-5"
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


def test_role_profiles_can_be_selected_independently() -> None:
    config = query_config(
        "--dev-profile", "standard",
        "--review-profile", "excellent",
    )

    assert config["profile"] == "default"
    assert config["profiles"] == {
        "dev": "standard",
        "review": "excellent",
    }
    assert config["dev"] == {
        "agent": "claude",
        "model": "claude-opus-4-8",
        "effort": "max",
    }
    assert config["review"] == {
        "agent": "codex",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
    }
    assert config["sources"]["dev.model"] == "profile:standard"
    assert config["sources"]["review.model"] == "profile:excellent"


def test_role_profile_overrides_legacy_profile_for_only_that_role() -> None:
    config = query_config(
        "--profile", "excellent",
        "--dev-profile", "standard",
    )

    assert config["profile"] == "excellent"
    assert config["profiles"] == {
        "dev": "standard",
        "review": "excellent",
    }
    assert config["dev"]["model"] == "claude-opus-4-8"
    assert config["review"]["model"] == "gpt-5.6-sol"


def test_role_default_explicitly_clears_legacy_profile() -> None:
    config = query_config(
        "--profile", "excellent",
        "--review-profile", "default",
        env_overrides={
            "ORCH_CODEX_MODEL": "env-review",
            "ORCH_CODEX_EFFORT": "high",
        },
    )

    assert config["profiles"] == {
        "dev": "excellent",
        "review": "default",
    }
    assert config["dev"]["model"] == "claude-opus-5"
    assert config["review"]["model"] == "env-review"
    assert config["review"]["effort"] == "high"
    assert config["sources"]["review.model"] == "env:ORCH_CODEX_MODEL"


def test_explicit_role_flags_override_only_the_selected_role_profile() -> None:
    config = query_config(
        "--dev-profile", "excellent",
        "--review-profile", "standard",
        "--review-model", "custom-review",
        "--review-effort", "high",
    )

    assert config["dev"]["model"] == "claude-opus-5"
    assert config["sources"]["dev.model"] == "profile:excellent"
    assert config["review"]["model"] == "custom-review"
    assert config["review"]["effort"] == "high"
    assert config["sources"]["review.model"] == "cli"


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


def test_cc_codex_review_agent_selects_its_default_namespace() -> None:
    config = query_config(
        "--backend", "cc-codex",
        "--review-agent", "claude",
    )

    assert config["review"] == {
        "agent": "claude",
        "model": "claude-opus-4-8",
        "effort": "max",
    }
    assert config["sources"]["review.agent"] == "cli"
    assert config["dev"]["agent"] == "claude"


def test_review_agent_resolves_by_cli_then_profile_then_env_then_config() -> None:
    env = {"ORCH_CC_REVIEW_AGENT": "claude"}

    inherited = query_config()
    from_env = query_config(env_overrides=env)
    from_profile = query_config("--profile", "standard", env_overrides=env)
    from_cli = query_config("--review-agent", "claude", env_overrides=env)

    assert inherited["review"]["agent"] == "codex"
    assert inherited["sources"]["review.agent"] == "config"

    assert from_env["review"]["agent"] == "claude"
    assert from_env["sources"]["review.agent"] == "env:ORCH_CC_REVIEW_AGENT"

    assert from_profile["review"]["agent"] == "codex"
    assert from_profile["sources"]["review.agent"] == "profile:standard"

    assert from_cli["review"]["agent"] == "claude"
    assert from_cli["sources"]["review.agent"] == "cli"


def test_review_model_and_effort_follow_the_selected_review_agent() -> None:
    env = {
        "ORCH_CODEX_MODEL": "env-codex-review",
        "ORCH_CODEX_EFFORT": "high",
        "ORCH_CC_REVIEW_MODEL": "env-claude-review",
        "ORCH_CC_REVIEW_EFFORT": "low",
    }
    codex_review = query_config(env_overrides=env)
    claude_review = query_config("--review-agent", "claude",
                                 env_overrides=env)

    assert codex_review["review"]["model"] == "env-codex-review"
    assert codex_review["review"]["effort"] == "high"
    assert codex_review["sources"]["review.model"] == "env:ORCH_CODEX_MODEL"

    assert claude_review["review"]["model"] == "env-claude-review"
    assert claude_review["review"]["effort"] == "low"
    assert (claude_review["sources"]["review.model"]
            == "env:ORCH_CC_REVIEW_MODEL")


def test_review_effort_is_validated_on_the_selected_agents_axis() -> None:
    # `max` is legal on the claude effort axis and illegal on the reasoning
    # axis; `minimal` is the mirror case.
    assert query_config(
        "--review-agent", "claude",
        "--review-effort", "max",
    )["review"]["effort"] == "max"
    assert query_config(
        "--review-agent", "codex",
        "--review-effort", "minimal",
    )["review"]["effort"] == "minimal"

    on_reasoning = refuse_config("--review-agent", "codex",
                                 "--review-effort", "max")
    on_effort = refuse_config("--review-agent", "claude",
                              "--review-effort", "minimal")

    assert "review_effort" in on_reasoning
    assert "for the reasoning axis" in on_reasoning
    assert "review_effort" in on_effort
    assert "for the effort axis" in on_effort


def test_review_agent_is_rejected_on_the_cursor_backend() -> None:
    stderr = refuse_config("--backend", "cursor", "--review-agent", "claude")

    assert "--review-agent is only supported with --backend cc-codex" in stderr


def test_profile_review_agent_override_requires_a_complete_custom_pair() -> None:
    stderr = refuse_config(
        "--backend", "cc-codex",
        "--profile", "standard",
        "--review-agent", "claude",
    )

    assert ("overriding a profile's --review-agent also requires explicit "
            "--review-model and --review-effort") in stderr

    config = query_config(
        "--backend", "cc-codex",
        "--profile", "standard",
        "--review-agent", "claude",
        "--review-model", "claude-opus-5",
        "--review-effort", "max",
    )

    assert config["review"] == {
        "agent": "claude",
        "model": "claude-opus-5",
        "effort": "max",
    }
    assert config["dev"]["model"] == "claude-opus-4-8"
    assert config["sources"]["dev.model"] == "profile:standard"


def test_an_uncatalogued_review_model_still_resolves() -> None:
    config = query_config(
        "--review-agent", "claude",
        "--review-model", "some-unreleased-model",
        "--review-effort", "high",
    )

    assert config["review"]["model"] == "some-unreleased-model"
    assert config["sources"]["review.model"] == "cli"


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
