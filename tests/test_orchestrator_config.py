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

    assert config["schema_version"] == 3
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
    # The catalog states what a caller may offer, not what a run may launch.
    published = config["options"]["agents"]["cc-codex"]["review"]["claude"]
    assert "some-unreleased-model" not in [
        entry["id"] for entry in published["models"]
    ]


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


def test_option_catalog_publishes_every_selectable_value() -> None:
    options = query_config()["options"]

    assert options["backends"] == ["cc-codex", "cursor"]
    assert options["run_profiles"] == ["excellent", "standard"]
    assert options["role_profiles"] == ["default", "excellent", "standard"]
    assert options["codex_sandbox"] == [
        "danger-full-access",
        "read-only",
        "workspace-write",
    ]
    assert sorted(options["agents"]) == ["cc-codex", "cursor"]
    for backend, agents in (("cc-codex", ["claude", "codex"]),
                            ("cursor", ["cursor"])):
        for role in ("dev", "review"):
            assert sorted(options["agents"][backend][role]) == agents


def test_option_catalog_does_not_narrow_with_the_current_selection() -> None:
    # One query has to answer every control, so what a caller MAY select is
    # independent of what this invocation DID select.
    inherited = query_config()["options"]

    assert query_config("--review-agent", "claude")["options"] == inherited
    assert query_config("--backend", "cursor")["options"] == inherited
    assert query_config("--profile", "excellent")["options"] == inherited


def test_cc_codex_catalog_efforts_follow_the_agents_own_axis() -> None:
    agents = query_config()["options"]["agents"]["cc-codex"]

    for role in ("dev", "review"):
        claude, codex = agents[role]["claude"], agents[role]["codex"]
        assert claude["effort_axis"] == "effort"
        assert claude["efforts"] == ["low", "medium", "high", "xhigh", "max"]
        assert codex["effort_axis"] == "reasoning"
        assert codex["efforts"] == [
            "none", "minimal", "low", "medium", "high", "xhigh",
        ]
        # The agent fixes the axis, so every catalogued model repeats it —
        # including the model a caller has not selected yet.
        for agent_options in (claude, codex):
            for entry in agent_options["models"]:
                assert entry["effort_axis"] == agent_options["effort_axis"]
                assert entry["efforts"] == agent_options["efforts"]


def test_cursor_catalog_states_the_axis_per_model_family() -> None:
    cursor = query_config()["options"]["agents"]["cursor"]["review"]["cursor"]

    # The one SDK takes either model family, so no single axis applies to
    # the agent and only the per-model statement is meaningful.
    assert cursor["effort_axis"] is None
    assert cursor["efforts"] is None
    by_id = {entry["id"]: entry for entry in cursor["models"]}
    assert by_id["claude-opus-5"]["effort_axis"] == "effort"
    assert by_id["claude-opus-5"]["efforts"][-1] == "max"
    assert by_id["gpt-5.5"]["effort_axis"] == "reasoning"
    assert "max" not in by_id["gpt-5.5"]["efforts"]


def test_every_published_cc_codex_effort_is_accepted_by_its_role_flag() -> None:
    agents = query_config()["options"]["agents"]["cc-codex"]

    for role in ("dev", "review"):
        for agent in ("claude", "codex"):
            offered = agents[role][agent]
            other = agents[role]["codex" if agent == "claude" else "claude"]
            for effort in offered["efforts"]:
                config = query_config(f"--{role}-agent", agent,
                                      f"--{role}-effort", effort)
                assert config[role]["effort"] == effort
                assert config[role]["agent"] == agent
            for foreign in sorted(set(other["efforts"])
                                  - set(offered["efforts"])):
                stderr = refuse_config(f"--{role}-agent", agent,
                                       f"--{role}-effort", foreign)
                assert f"for the {offered['effort_axis']} axis" in stderr


def test_published_effort_aliases_are_accepted_but_never_offered() -> None:
    options = query_config()["options"]

    assert options["effort_aliases"] == {
        "effort": {},
        "reasoning": {"extra-high": "xhigh"},
    }
    assert "extra-high" not in options["efforts"]["reasoning"]
    assert query_config(
        "--review-agent", "codex",
        "--review-effort", "extra-high",
    )["review"]["effort"] == "extra-high"
    assert "for the effort axis" in refuse_config(
        "--review-agent", "claude", "--review-effort", "extra-high")


def test_the_deployed_configuration_only_selects_catalogued_models() -> None:
    # A deployment that shipped a model outside its own catalog would offer
    # a caller less than it launches itself.
    for backend in ("cc-codex", "cursor"):
        for profile in ((), ("--profile", "standard"),
                        ("--profile", "excellent")):
            config = query_config("--backend", backend, *profile)
            published = config["options"]["agents"][backend]
            for role in ("dev", "review"):
                # A null agent means the backend itself is the agent.
                agent = config[role]["agent"] or backend
                catalogued = [entry["id"]
                              for entry in published[role][agent]["models"]]
                assert config[role]["model"] in catalogued, (
                    backend, profile, role)
