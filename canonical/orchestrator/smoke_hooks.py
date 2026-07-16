#!/usr/bin/env python3
"""Empirical smoke test: do .cursor/hooks.json hooks (sessionStart / stop) fire
when an agent runs under the LOCAL Cursor SDK?

Runs a trivial no-tool agent against the quantx repo twice:
  1. default setting sources (inline-only)
  2. local.setting_sources = ["project"]

Detection, two independent signals per run:
  a. New `session-start` / `stop` lines appended to ~/.cursor/ai-hooks.log
     (both hook scripts log every invocation there).
  b. The agent itself reports whether a context block labeled
     "PROJECT PROTOCOL CONTEXT" is present (sessionStart injects it via
     additional_context).

Usage:
    CURSOR_API_KEY=... .venv/bin/python smoke_hooks.py [model-id]
Default model: composer-2.5 (any cheap model works; the agent only replies).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

REPO = Path(__file__).resolve().parents[2]  # .../quantx
HOOK_LOG = Path.home() / ".cursor" / "ai-hooks.log"

PROBE_PROMPT = (
    "This is a context probe. Do NOT use any tools, do NOT read or write any "
    "files, do NOT run any commands. Answer from your context only, with "
    "exactly one word:\n"
    "- Reply INJECTED if your context contains a block labeled "
    "'PROJECT PROTOCOL CONTEXT (ai-protocol)'.\n"
    "- Reply CLEAN if it does not.\n"
)


def log_tail_len() -> int:
    try:
        return HOOK_LOG.stat().st_size
    except FileNotFoundError:
        return 0


def new_log_lines(offset: int) -> list[str]:
    if not HOOK_LOG.exists():
        return []
    with HOOK_LOG.open() as f:
        f.seek(offset)
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def probe(label: str, model: str, setting_sources: list[str] | None) -> None:
    print(f"\n=== {label} (setting_sources={setting_sources}) ===")
    offset = log_tail_len()
    t0 = time.time()
    local = LocalAgentOptions(cwd=str(REPO))
    if setting_sources is not None:
        local = LocalAgentOptions(cwd=str(REPO), setting_sources=setting_sources)
    try:
        result = Agent.prompt(
            PROBE_PROMPT,
            AgentOptions(
                api_key=os.environ.get("CURSOR_API_KEY"),
                model=model,
                local=local,
            ),
        )
    except CursorAgentError as err:
        print(f"  STARTUP FAILURE (never ran): {err}")
        return
    print(f"  run status={result.status} agent_id={result.agent_id} "
          f"({time.time() - t0:.1f}s)")
    reply = (result.result or "").strip()
    print(f"  agent reply: {reply[:200]!r}")
    lines = new_log_lines(offset)
    if lines:
        print(f"  hook log: {len(lines)} new line(s):")
        for ln in lines:
            print(f"    {ln}")
    else:
        print("  hook log: NO new lines")
    verdict_inject = "INJECTED" in reply.upper()
    verdict_hooklog = any("session-start" in ln or "stop" in ln for ln in lines)
    print(f"  VERDICT: sessionStart-injection-seen-by-agent={verdict_inject} "
          f"hook-scripts-executed={verdict_hooklog}")


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "composer-2.5"
    if "CURSOR_API_KEY" not in os.environ:
        print("CURSOR_API_KEY not set — relying on cursor-agent login state")
    print(f"repo={REPO}\nhook log={HOOK_LOG}\nmodel={model}")
    probe("run 1: default (inline-only) setting sources", model, None)
    probe("run 2: project setting sources", model, ["project"])
    print("\nInterpretation: if hook-scripts-executed=True in either mode, the "
          "orchestrator must export AI_ORCH=1 and the hook scripts need the "
          "early-exit guard (see plan step 1).")


if __name__ == "__main__":
    main()
