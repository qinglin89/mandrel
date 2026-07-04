#!/usr/bin/env python3
"""Dev↔review orchestrator for the ai-coding-v2 workflow (local,
single-machine, status-driven state machine, pluggable execution backend).

Runs the `dev → review → dev → …` loop over one `.ai-tasks/` task, with a
different model per role. The orchestrator is a dumb scheduler: it reads task
status + session log, advances or pauses, and never decides. Any
human-decision event — Confirm-tier, load-bearing uncertainty, disputed
finding, over-budget convergence group — pauses the loop and pulls the human
in on stdin.

Backends (--backend):
  cursor   (default) fresh Cursor SDK agent per session (dev=Fable-5,
           review=GPT-5.5 by default). Hooks do NOT run under the SDK, so the
           orchestrator injects protocol context itself and exports AI_ORCH=1
           to keep the .cursor hooks quiet. Needs CURSOR_API_KEY.
  cc-codex Claude Code headless (`claude -p`, dev role, fable-5 @ max effort)
           + Codex CLI (`codex exec`, review role, gpt-5.5 @ xhigh effort).
           No Cursor dependency; each tool's own hook/import chain loads the
           protocol natively, so the orchestrator does NOT inject it.
           Post-checks stay on as an end-discipline backstop.

Lifecycle the orchestrator owns (both backends):
  - post-session checks: clean tree, session-log entry, legal status per role
  - convergence-group budget counting (Group: field in review entries)
  - blocked → surface question → resume with answer
  - completed → ai-sync-v2 close-out via the review agent → verify archive
  - optional --plan-gate: every dev session first states goal+plan and blocks
    for human confirmation before touching code

Usage:
    .venv/bin/python orchestrator.py <task-id> [--once] [--backend B]
        [--plan-gate] [--dev-model ID] [--review-model ID]
        [--dev-effort E] [--review-effort E] [--max-sessions N]
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# --- paths -----------------------------------------------------------------

ORCH_DIR = Path(__file__).resolve().parent
REPO = ORCH_DIR.parents[1]  # .../quantx
TASKS_DIR = REPO / ".ai-tasks"
ARCHIVE_DIR = TASKS_DIR / "archive"
INDEX_FILE = TASKS_DIR / "index.md"
SESSION_START_SH = REPO / ".cursor" / "hooks" / "session-start.sh"
# Canonical review workflow (single source; the .cursor/.codex files are
# pointers to it — inject the real text, not a pointer).
REVIEW_RULE = REPO / "ai-coding-review-v2.md"
AUTOMATION_MD = ORCH_DIR / "automation-mode.md"
SYNC_SKILL = Path.home() / ".claude" / "skills" / "ai-sync-v2" / "SKILL.md"
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

# cursor backend — NOTE: the SDK model namespace differs from `cursor-agent
# models` (CLI): the SDK wants base ids (claude-fable-5, gpt-5.5), the CLI
# lists variant ids (claude-opus-4-8-thinking-high, gpt-5.5-high, ...).
DEFAULT_DEV_MODEL = os.environ.get("ORCH_DEV_MODEL", "claude-fable-5")
DEFAULT_REVIEW_MODEL = os.environ.get("ORCH_REVIEW_MODEL", "gpt-5.5")

# cursor backend efforts — None = the catalog's default variant (claude
# family: high, gpt-5.5: medium). Verified live 2026-07-03: claude effort=max
# and gpt-5.5 reasoning=extra-high are accepted by the SDK even though the
# Cursor app UI doesn't offer them.
DEFAULT_CURSOR_DEV_EFFORT = os.environ.get("ORCH_CURSOR_DEV_EFFORT") or None
DEFAULT_CURSOR_REVIEW_EFFORT = os.environ.get("ORCH_CURSOR_REVIEW_EFFORT") or None
# The parameter axis differs per model family (and so does the value
# vocabulary: claude family low..max, gpt none..extra-high).
EFFORT_AXIS = {"claude": "effort", "fable": "effort", "gpt": "reasoning"}
# Canonical top-tier spelling at the flag level is codex's `xhigh`; Cursor's
# gpt `reasoning` axis calls the same tier `extra-high`. Translate per
# consumer so either spelling works everywhere.
CURSOR_REASONING_ALIASES = {"xhigh": "extra-high"}
CODEX_EFFORT_ALIASES = {"extra-high": "xhigh"}

# cc-codex backend — each tool's own model namespace and effort scale.
# Codex accepts: none|minimal|low|medium|high|xhigh (xhigh = "extra high" in
# the TUI; verified against the API enum 2026-07-03).
DEFAULT_CC_MODEL = os.environ.get("ORCH_CC_MODEL", "fable-5")
DEFAULT_CC_EFFORT = os.environ.get("ORCH_CC_EFFORT", "max")
DEFAULT_CODEX_MODEL = os.environ.get("ORCH_CODEX_MODEL", "gpt-5.5")
DEFAULT_CODEX_EFFORT = os.environ.get("ORCH_CODEX_EFFORT", "xhigh")
# danger-full-access by default (ruled 2026-07-04): codex's workspace-write
# sandbox leaves `.git` READ-ONLY, so any git commit fails — which breaks the
# review-side ai-sync (Stop hook forces it at status=completed) and the
# close-out absorption (verified live: `git add` → index.lock Operation not
# permitted → blocked). Safety here is the protocol layer + post-checks,
# same philosophy as claude's --dangerously-skip-permissions.
CODEX_SANDBOX = os.environ.get("ORCH_CODEX_SANDBOX", "danger-full-access")

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

MAX_FOLLOWUPS = 3     # post-check violation round-trips per session
GROUP_BUDGET = 2      # changes-requested RE-reviews per group (so 3 entries total)
DEFAULT_MAX_SESSIONS = 40
HEARTBEAT_SILENCE = 30  # seconds without stream events before a log-file beat
GEN_WINDOW = 30       # seconds — [gen] aggregation window for text/thinking
# Session context budget (tokens) — port of stop-context-check.sh: a session
# whose conversation outgrows this must wrap up (clean tree + session-log
# entry + keep in_progress) and hand off to a fresh session.
CONTEXT_BUDGET = int(os.environ.get("ORCH_CONTEXT_BUDGET", "200000"))

DEV_LEGAL_STATUSES = {"in_progress", "final_review", "blocked"}
# reviewer transitions, keyed by status found at entry (status-transition
# table: ai-coding-tasks-v2.md §3; in_progress from final_review is legal
# only for the "final_review set in error" revert):
REVIEW_LEGAL = {
    "in_progress": {"in_progress", "blocked"},
    "final_review": {"completed", "in_progress", "final_review", "blocked"},
}

CLEAN_HOWTO = (
    "make the working tree clean (`git status --porcelain` empty) — each "
    "modified file committed, and each untracked file handled by its nature: "
    "real work committed; an unwanted scratch file removed; a run-time "
    "artifact covered by a gitignore rule for its category."
)


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
        m = re.search(r"Verdict:\s*([a-z-]+)", self.body)
        return m.group(1) if m else None

    @property
    def group(self) -> str | None:
        m = re.search(r"Group:\s*(\S+)", self.body)
        return m.group(1) if m else None

    @property
    def is_continuation(self) -> bool:
        """Remediation-only wrap-up marker (§10): the remediation session
        wrapped before its fix set was complete; remediation resumes in a
        fresh session and re-review waits until the fix set completes. An
        advancement session never writes it — one dev session is one
        reviewable unit."""
        return bool(re.search(r"Handoff:\s*continuation", self.body))


@dataclasses.dataclass
class TaskState:
    path: Path
    status: str
    blockers: str
    claimed_by: str
    est: str
    entries: list[LogEntry]

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
                     entries=entries)


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


# --- execution backends ------------------------------------------------------

@dataclasses.dataclass
class TurnResult:
    status: str               # "finished" | "error"
    saw_request: bool = False
    text: str = ""            # assistant text of this turn


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
        for prefix, axis in EFFORT_AXIS.items():
            if model.startswith(prefix):
                return axis
        return "effort"

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


# -- cc-codex backend (Claude Code dev + Codex review, subprocess CLIs) --

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
            content = (ev.get("message") or {}).get("content") or []
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
            u = (ev.get("message") or {}).get("usage") or {}
            if u and not ev.get("parent_tool_use_id"):
                self.context_tokens = (
                    (u.get("input_tokens") or 0)
                    + (u.get("cache_read_input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0)
                    + (u.get("output_tokens") or 0))
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

    def _handle_event(self, ev: dict, chunks: list[str]) -> str | None:
        o = self.orch
        etype = ev.get("type", "")
        # session id discovery (schema varies across codex versions)
        if not self.sid:
            val = (ev.get("thread_id") or ev.get("session_id")
                   or ev.get("conversation_id"))
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
        elif etype == "turn.completed":
            # usage here is CUMULATIVE across every request of the turn
            # (measured 2.9M for an 8-min review) — useless as a context
            # signal; the estimate comes from _update_context() instead.
            pass
        elif etype in ("turn.failed", "error"):
            self._emit(f"[status] {etype} {summarize(ev.get('message') or ev)}")
            return "error"
        elif etype and not etype.startswith(("item.", "turn.", "thread.")):
            self._emit(f"[status] {etype}")
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
                self.context_tokens = rollouts[-1].stat().st_size // 4
            else:
                self.orch.flog(f"[status] no rollout file found for "
                               f"{self.sid} — context estimate unavailable")


class CliBackend:
    name = "cc-codex"
    injects_protocol = False  # native hook/import chains load the protocol

    def __init__(self, orch: "Orchestrator", cc_model: str, cc_effort: str,
                 codex_model: str, codex_effort: str) -> None:
        self.orch = orch
        self.cc_model, self.cc_effort = cc_model, cc_effort
        self.codex_model, self.codex_effort = codex_model, codex_effort

    def describe(self, role: str) -> str:
        if role == "dev":
            return f"claude:{self.cc_model}@{self.cc_effort}"
        return f"codex:{self.codex_model}@{self.codex_effort}"

    def new_session(self, role: str) -> CliSession:
        if role == "dev":
            return ClaudeSession(self.orch, self.cc_model, self.cc_effort)
        return CodexSession(self.orch, self.codex_model, self.codex_effort)

    def resume_session(self, sid: str, role: str) -> CliSession:
        info = _session_map_load().get(sid)
        tool = (info or {}).get("tool") or ("claude" if role == "dev"
                                            else "codex")
        if info is None:
            self.orch.log(f"WARNING: sid {sid} not in {SESSION_MAP} — "
                          f"assuming tool={tool} by role. If this session "
                          "was created manually in another tool, resume may "
                          "fail; unblock manually in that tool instead.")
        if tool == "claude":
            return ClaudeSession(self.orch, self.cc_model, self.cc_effort,
                                 sid=sid, resume=True)
        s = CodexSession(self.orch, self.codex_model, self.codex_effort,
                         sid=sid)
        s.first_turn = False  # force the resume argv shape
        return s


# --- orchestrator -----------------------------------------------------------

class Orchestrator:
    def __init__(self, task_id: str, dev_model: str, review_model: str,
                 api_key: str | None, once: bool, max_sessions: int,
                 backend: object | None = None,
                 plan_gate: bool = False) -> None:
        self.task_id = task_id
        self.task_path = TASKS_DIR / f"{task_id}.md"
        self.once = once
        self.max_sessions = max_sessions
        self.plan_gate = plan_gate
        self.last_review_agent: str | None = None
        self.pending_ruling: str | None = None
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

    def transcript(self, agent_id: str, text: str) -> None:
        with self._log_lock, self.log_file.open("a") as f:
            f.write(f"--- {agent_id} ---\n{text}\n")

    def ask_human(self, banner: str) -> str:
        # The banner must be auditable after the fact — it only ever printed
        # to the terminal before, which made blockers untraceable.
        with self.log_file.open("a") as f:
            f.write(f"--- HUMAN INPUT NEEDED ---\n{banner}\n--- end banner ---\n")
        # Drain stray input buffered during the (possibly hour-long) run —
        # a queued Enter must not silently answer the question.
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
        self.log(f"human answered: {answer!r}")
        if answer.lower() == "stop":
            sys.exit("stopped by human")
        return answer

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
        try:
            session = self.backend.new_session(role)
        except SessionStartError as err:
            sys.exit(str(err))
        try:
            sid = session.sid
            self.log(f"--- {role} session start: sid={sid or '(pending)'} "
                     f"backend={self.backend.describe(role)} "
                     f"status_before={status_before}")
            prompt = first_prompt_fn(sid)
            if self.plan_gate and role == "dev":
                ruling = self._plan_gate_turn(session, prompt)
                prompt = (f"[orchestrator] PLAN RULING from the human: "
                          f"{ruling}\nProceed with the session per the "
                          "original instructions, honoring this ruling. "
                          "From here on the normal end-of-session rules "
                          "apply (clean tree, session-log entry, role-legal "
                          "status).")
            followups = 0
            while True:
                try:
                    result = session.turn(prompt)
                except SessionStartError as err:
                    sys.exit(str(err))
                if session.sid != sid:
                    sid = session.sid  # codex names its id on first turn
                if result.saw_request:
                    answer = self.ask_human(
                        f"The {role} agent ({sid}) paused awaiting input/"
                        f"approval (see log tail in {self.log_file}).\n"
                        "Your reply is sent into the same conversation.")
                    prompt = (f"[orchestrator] The human answered: {answer}\n"
                              "Continue per AUTOMATION MODE rules; do not "
                              "await interactive input again.")
                    continue
                if result.status == "error":
                    answer = self.ask_human(
                        f"The {role} run errored mid-flight (run ran, then "
                        f"failed). sid={sid}. Inspect the repo/log, then "
                        "give an instruction to retry with, or 'stop'.")
                    prompt = (f"[orchestrator] The previous run errored. "
                              f"Human instruction: {answer}\nResume the "
                              f"{role} session for task {self.task_id} and "
                              "finish per protocol.")
                    continue
                if not self.task_path.exists():
                    # cc-codex seam (seen live 2026-07-04): a final-gate pass
                    # trips the session's NATIVE Stop hook, which runs the
                    # /ai-sync-v2 close-out INSIDE the review session — the
                    # task is already archived when we get control back.
                    # Recognize the fait accompli instead of fighting it.
                    if (ARCHIVE_DIR / self.task_path.name).exists():
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
                    # The handoff note depends on the session kind (§10):
                    # only an incomplete REMEDIATION fix set may defer its
                    # re-review via the continuation marker.
                    if role == "review":
                        handoff_note = (
                            "any pending dev session you did not finish "
                            "reviewing simply stays pending — the next "
                            "review session continues the set; ")
                    elif was_remediation:
                        handoff_note = (
                            "ONLY if your remediation fix set is not yet "
                            "complete, include the line `- Handoff: "
                            "continuation` so remediation continues in a "
                            "fresh session before re-review; ")
                    else:
                        handoff_note = (
                            "do NOT write a `Handoff: continuation` line — "
                            "an advancement session's landed work is "
                            "reviewed next (one dev session = one "
                            "reviewable unit, §10); ")
                    self.log(f"context budget exceeded "
                             f"(≈{session.context_tokens} tokens) with "
                             "violations — sending wrap-up instruction "
                             f"(followup {followups})")
                    prompt = (
                        "[orchestrator] Context has grown to approximately "
                        f"{session.context_tokens} tokens (over the "
                        f"{CONTEXT_BUDGET} budget for reliable work). Wrap "
                        f"up NOW, in this order: (1) {CLEAN_HOWTO} (2) "
                        f"append a `## Session log` entry for session id "
                        f"{sid} (Done / Next / Open) describing what is done "
                        f"and what the next session picks up; {handoff_note}"
                        "re-estimate session-est. (3) set a "
                        "status legal for your role (keep in_progress if "
                        "work remains). Do NOT start any new work. Then end.")
                    if followups > MAX_FOLLOWUPS:
                        self.ask_human(
                            "Session is over the context budget and still "
                            "failing post-checks after wrap-up attempts:\n- "
                            + "\n- ".join(problems) +
                            "\nFix the task file / tree manually, then type "
                            "'done' to continue with a fresh session.")
                        break
                    continue
                if followups > MAX_FOLLOWUPS:
                    answer = self.ask_human(
                        "Post-session checks still failing after "
                        f"{MAX_FOLLOWUPS} followups:\n- " +
                        "\n- ".join(problems) +
                        "\nGive an instruction for the agent, or 'stop'.")
                    prompt = f"[orchestrator] Human instruction: {answer}"
                    followups = 0
                    continue
                self.log(f"post-check violations (followup {followups}): "
                         + "; ".join(problems))
                prompt = (
                    "[orchestrator] Protocol violation(s) detected after your "
                    "session ended:\n- " + "\n- ".join(problems) +
                    f"\nFix now, in order: (1) {CLEAN_HOWTO} (2) ensure a "
                    f"`## Session log` entry for session id {sid} exists "
                    "(Done / Next / Open — review entries also need Verdict/"
                    "Group/Findings). (3) set a status legal for your role. "
                    "Then end.")
            end_status = ("completed+archived"
                          if not self.task_path.exists()
                          else parse_task(self.task_path).status)
            self.log(f"--- {role} session end: sid={sid} "
                     f"status={end_status}")
            return sid
        finally:
            session.close()

    def _plan_gate_turn(self, session: BackendSession, base_prompt: str) -> str:
        """--plan-gate: an extra conversational turn BEFORE any work. The
        plan lives only in the conversation and the orchestrator log — no
        task-file writes, no session-log entry, no status change (the
        session-log stays an end-of-session record). Returns the human's
        ruling to inject into the working turn."""
        plan_prompt = (
            base_prompt +
            "\n\nPLAN GATE — THIS TURN IS PLANNING ONLY. Read the task file, "
            "its session log, and the relevant context, then reply with your "
            "plan for this session: the goal as you understand it, the steps "
            "you will take, the files you expect to touch, and "
            "risks/assumptions. HARD CONSTRAINTS for this turn: do NOT "
            "modify any file, do NOT run write/state-changing commands, do "
            "NOT append a session-log entry, do NOT change the task status. "
            "Reply with the plan and stop; the human will confirm or amend "
            "it before you execute.")
        result = session.turn(plan_prompt)
        plan = (result.text or "").strip()
        if not tree_clean():
            self.log("plan-gate violation: the planning turn modified the "
                     "tree — surfacing to human")
        banner = ("PLAN CONFIRMATION (--plan-gate): the dev session "
                  f"({session.sid}) proposes:\n\n"
                  + (plan[:4000] if plan else "(no plan text captured — see "
                     f"log {self.log_file})"))
        if not tree_clean():
            banner += ("\n\nWARNING: the planning turn left the tree dirty "
                       "(it was told not to touch anything). Your answer is "
                       "forwarded either way; consider 'stop'.")
        return self.ask_human(banner + "\n\nConfirm the plan or state "
                              "amendments; your answer is binding.")

    # -- post-session checks (Stop-hook replica / backstop) --

    def check_specs(self, role: str, sid: str | None, status_before: str,
                    est_before: tuple[int, int] | None = None,
                    was_remediation: bool = False):
        """Single source for the end-of-session discipline. Each spec is
        (requirement line, check(task) -> problem | None): post_checks RUNS
        the checks, and the same requirement lines render into the session
        prompt as its POST-SESSION CHECKS preview — so what the agent is
        told and what the orchestrator verifies cannot drift.

        est_before: session-est at session start for a FRESH dev session
        (None skips — review sessions don't consume the estimate; a resumed
        blocked session already counted). was_remediation: latest review
        verdict was changes-requested at dev entry → status must not change
        (tasks-v2 §3)."""
        sid_disp = sid or "<this session's id>"
        specs: list[tuple[str, object]] = []
        specs.append((
            "working tree clean (`git status --porcelain` empty)",
            lambda task: None if tree_clean() else
            "working tree is not clean (git status --porcelain is "
            "non-empty)"))
        specs.append((
            f"a `## Session log` entry for session id {sid_disp} "
            "(Done / Next / Open)",
            lambda task: None
            if sid and any(e.session_id == sid for e in task.entries) else
            f"no `## Session log` entry for session id {sid}"))
        if role == "dev":
            if was_remediation:
                specs.append((
                    f"status: keep `{status_before}` UNCHANGED — a "
                    "remediation session never touches status (tasks-v2 §3; "
                    "re-review is triggered by your session-log entry; "
                    "`blocked` only for a genuine human question)",
                    lambda task: None
                    if task.status in {status_before, "blocked"} else
                    f"remediation session changed status `{status_before}` "
                    f"→ `{task.status}` — a remediation session never "
                    "touches status (tasks-v2 §3); restore it (re-review "
                    "is triggered by your session-log entry)"))
            else:
                specs.append((
                    "status per tasks-v2 §3 (dev advancement): `in_progress`"
                    " (work remains) | `final_review` (ONLY when the whole "
                    "scope is complete) | `blocked` (genuine human question)"
                    " — never `completed`",
                    lambda task: None
                    if task.status in DEV_LEGAL_STATUSES else
                    f"status `{task.status}` is illegal for a dev session "
                    f"(allowed: {sorted(DEV_LEGAL_STATUSES)}; a dev session "
                    "never sets completed)"))

                def _no_marker(task):
                    own = [e for e in task.entries
                           if e.session_id == sid and not e.is_review]
                    if any(e.is_continuation for e in own):
                        return ("`- Handoff: continuation` on an advancement"
                                " entry — the marker is remediation-only "
                                "(§10); remove it: an advancement session's "
                                "landed work is reviewed after every session")
                    return None
                specs.append((
                    "no `- Handoff: continuation` line in your entry (the "
                    "marker is remediation-only — §10; advancement work is "
                    "reviewed after every session)",
                    _no_marker))
            if est_before:
                cur, tot = est_before
                nxt, ntot = cur + 1, max(tot, cur + 1)
                undershoot = (" (this raises <total> — the estimate "
                              "undershot)") if ntot > tot else ""

                def _est_check(task, cur=cur, tot=tot):
                    after = task.est_tuple
                    if after and after[0] > cur:
                        return None
                    return (f"session-est not incremented: still {task.est} "
                            f"(was {cur}/{tot}) — a dev session increments "
                            "<current> as part of the claim (§10 Entry step "
                            "3); raise <total> too if the estimate "
                            "undershot")
                specs.append((
                    f"session-est incremented at claim: {cur}/{tot} → "
                    f"{nxt}/{ntot}{undershoot}",
                    _est_check))
        else:
            allowed = REVIEW_LEGAL.get(status_before,
                                       {"in_progress", "blocked"})
            if status_before == "final_review":
                menu = ("status per tasks-v2 §3 (FINAL GATE — entered at "
                        "`final_review`): `completed` (pass — the sole "
                        "ai-sync trigger) | `final_review` (changes "
                        "required — your entry itself hands back to dev "
                        "remediation) | `in_progress` (ONLY if final_review "
                        "was set in error — verify apparent dev-completeness"
                        " at entry, record why) | `blocked`")
            else:
                menu = ("status per tasks-v2 §3 (interim review — entered "
                        f"at `{status_before}`): keep `in_progress` "
                        "(findings never gate an interim review) | "
                        "`blocked`")

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
                menu,
                lambda task: None if task.status in allowed else
                f"status `{task.status}` is an illegal review transition "
                f"from `{status_before}` (allowed: {sorted(allowed)})"))
            specs.append((
                "the review entry carries `Verdict:` (pass | "
                "changes-requested) and `Group:` (convergence anchor, "
                "review-v2)",
                _entry_check))
        return specs

    def post_checks(self, role: str, sid: str | None, status_before: str,
                    est_before: tuple[int, int] | None = None,
                    was_remediation: bool = False) -> list[str]:
        task = parse_task(self.task_path)
        specs = self.check_specs(role, sid, status_before,
                                 est_before=est_before,
                                 was_remediation=was_remediation)
        return [p for _, check in specs if (p := check(task))]

    def checks_preview(self, role: str, sid: str | None, status_before: str,
                       est_before: tuple[int, int] | None = None,
                       was_remediation: bool = False) -> str:
        specs = self.check_specs(role, sid, status_before,
                                 est_before=est_before,
                                 was_remediation=was_remediation)
        return ("POST-SESSION CHECKS — the orchestrator verifies exactly "
                "these after you end (each failed round-trip wastes a "
                "turn; get them right the first time):\n"
                + "\n".join(f"- {line}" for line, _ in specs))

    # -- prompts --

    def _sid_line(self, sid: str | None) -> str:
        if sid:
            return f"Your session id is {sid}."
        return ("Your session id is provided by your session-start context "
                "(hook injection); use that id in the session log and "
                "claimed-by.")

    def _preamble(self, sid: str | None) -> list[str]:
        parts: list[str] = []
        if self.backend.injects_protocol:
            parts.append(protocol_block(sid or "orchestrated-session"))
        else:
            parts.append(
                "NOTE: the full project protocol (PROJECT PROTOCOL CONTEXT) "
                "arrives via your tool's native session-start mechanism "
                "(hooks / CLAUDE.md imports); follow it.")
        parts += ["===== BEGIN AUTOMATION MODE =====",
                  AUTOMATION_MD.read_text(),
                  "===== END AUTOMATION MODE =====", ""]
        return parts

    @staticmethod
    def _utc_now() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _entry_checklist(self, role: str, sid: str | None,
                         task: TaskState) -> str:
        """Item-level instantiation of the §10 Entry / review-v2 claim steps
        with this session's concrete values (attention amplification — the
        rules themselves live in the protocol docs)."""
        sid_disp = sid or "<your session id from the session-start context>"
        lines = ["ENTRY CHECKLIST (do this first, with these exact values):",
                 f"- claim: set `claimed-by: {sid_disp}@{self._utc_now()}` "
                 "(refresh the timestamp with `date -u` at claim time)"]
        if role == "dev":
            if task.est_tuple:
                cur, tot = task.est_tuple
                nxt, ntot = cur + 1, max(tot, cur + 1)
                undershoot = (" — this raises <total>: the estimate "
                              "undershot") if ntot > tot else ""
                lines.append(f"- session-est: {cur}/{tot} → {nxt}/{ntot}"
                             f"{undershoot} (part of the claim, §10 Entry 3)")
            else:
                lines.append("- session-est: increment <current> by 1 "
                             "(part of the claim, §10 Entry 3)")
            if task.status == "pending":
                lines.append("- status: pending → in_progress "
                             "(part of the claim)")
            lines.append("- pre-load the `prefetch:` docs listed in the "
                         "task frontmatter")
        else:
            lines.append("- do NOT touch session-est (review sessions "
                         "don't consume the estimate)")
            pending = task.unreviewed_dev_sids()
            lines.append("- pending review set at dispatch: "
                         + (", ".join(pending) if pending else "(empty)"))
        return "\n".join(lines)

    def dev_prompt(self, sid: str | None) -> str:
        task = parse_task(self.task_path)
        latest = task.review_entries[-1] if task.review_entries else None
        remediation = bool(latest and latest.verdict == "changes-requested")
        parts = self._preamble(sid)
        parts.append(
            f"You are invoked as: `task {self.task_id}` — dev role per the "
            "Cross-model review section of the protocol. Develop or continue "
            f"the task per §10. {self._sid_line(sid)} If the session log "
            "contains review entries with unresolved findings, verify each "
            "finding against the actual code first, fix the valid ones "
            "(correctness first), and record any finding you verify as "
            "invalid in your session-log entry as a dispute — do not "
            "silently fix or silently skip it.")
        parts.append("\n" + self._entry_checklist("dev", sid, task))
        if remediation:
            parts.append(
                "\nREMEDIATION SESSION (roles §11, remediation before "
                "advancement): the latest review verdict is "
                "changes-requested, so this session ONLY remediates that "
                "review — fix the valid findings, record disputes for "
                "invalid ones, then hand back to review. Do NOT advance new "
                "scope (no new plan steps or features) this session; new "
                "scope resumes after a pass verdict.")
        if self.pending_ruling:
            parts.append(f"\nHUMAN RULING (binding for this session): "
                         f"{self.pending_ruling}")
            self.pending_ruling = None
        parts.append("\n" + self.checks_preview(
            "dev", sid, task.status, est_before=task.est_tuple,
            was_remediation=remediation))
        return "\n".join(parts)

    def review_prompt(self, sid: str | None) -> str:
        task = parse_task(self.task_path)
        return "\n".join([
            *self._preamble(sid),
            "===== BEGIN ai-coding-review-v2.md =====",
            REVIEW_RULE.read_text(),
            "===== END ai-coding-review-v2.md =====",
            "",
            f"You are invoked as: `review {self.task_id}` — review role. "
            f"{self._sid_line(sid)}",
            "Cross-model independence: your evidence is ONLY the task file "
            "with its full session log, the relevant `.ai/` docs, and the "
            "actual commits/diffs of the pending dev sessions (`git log` / "
            "`git show`). No dev conversation transcript exists; do not ask "
            "for one. Follow the review-workflow procedure exactly, including "
            "the `Group:` field and the per-group round budget.",
            "",
            self._entry_checklist("review", sid, task),
            "",
            self.checks_preview("review", sid, task.status),
        ])

    # -- escalation paths --

    def handle_blocked(self) -> None:
        task = parse_task(self.task_path)
        blocked_sid = task.claimed_by.split("@")[0].strip()
        # The §3 table lets ANY session block — dev or review (e.g. a
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
        answer = self.ask_human(
            f"Task is BLOCKED by {role} session {blocked_sid}.\n"
            f"blockers: {task.blockers}\n\nOpen context:\n{open_ctx}")
        try:
            session = self.backend.resume_session(blocked_sid, role)
        except Exception as err:
            sys.exit(f"cannot resume blocked session {blocked_sid}: {err}\n"
                     "If it was created manually in another tool, answer the "
                     "blocker there (or edit the task file) and restart.")
        try:
            self.log(f"resuming blocked {role} session {blocked_sid} "
                     "with answer")
            prompt = (f"[orchestrator] The human answered your blocker: "
                      f"{answer}\nRestore `status` to its pre-blocked value "
                      "(the status on the left of the `→ blocked` in your "
                      "last session-log entry heading), clear `blockers`, "
                      "and continue the session per protocol (AUTOMATION "
                      "MODE rules still apply; end with a clean tree + "
                      "session-log entry).")
            session.turn(prompt)
            if not self.task_path.exists():
                # Same cc-codex seam as run_session: a resumed blocked
                # REVIEWER may conclude the final gate (pass → completed),
                # which trips its native Stop hook → in-session close-out →
                # the task is archived before post-checks can parse it.
                if (ARCHIVE_DIR / self.task_path.name).exists():
                    self.log("task file archived mid-session — the native "
                             "hook chain ran the close-out inside the "
                             "resumed session; skipping post-checks")
                    return
                sys.exit(f"task file vanished without an archive copy "
                         f"mid-session: {self.task_path} — investigate "
                         "manually")
            problems = self.post_checks(role, blocked_sid, status_before)
            if problems:
                session.turn("[orchestrator] Protocol violation(s): "
                             + "; ".join(problems) + " — fix and end.")
        finally:
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
        m = re.search(r"Dispute-unresolved:\s*(.*)", latest.body)
        if m:
            self.log("convergence: unresolved dispute — escalating "
                     "immediately (no budget rounds spent on it)")
            ruling = self.ask_human(
                "DISPUTE UNRESOLVED: the dev session disputed a finding and "
                "the reviewer still holds it valid.\n"
                f"Reviewer's line: {m.group(1).strip()[:500]}\n\nLatest "
                f"review entry:\n{latest.body.strip()[:3000]}\n\nGive a "
                "binding ruling for the next dev session, or 'stop'.")
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
            ruling = self.ask_human(
                f"Convergence budget exhausted (group {group}, {rounds} "
                "changes-requested rounds).\n\nLatest "
                f"review entry:\n{findings}\n\nGive a binding ruling for the "
                "next dev session, or 'stop'.")
            self.pending_ruling = ruling

    # -- close-out --

    def close_out(self) -> None:
        if not self.last_review_agent:
            self.log("status=completed but no review agent from this run — "
                     "spawning a fresh close-out session (review role)")
        prompt = (
            f"Task '.ai-tasks/{self.task_id}.md' has status: completed. "
            f"Invoke /ai-sync-v2 now — read '{SYNC_SKILL}' and follow it — to "
            "apply absorption and archive the task before ending the "
            "session. End with a clean working tree.")
        if self.last_review_agent:
            session = self.backend.resume_session(self.last_review_agent,
                                                  "review")
        else:
            session = self.backend.new_session("review")
        try:
            session.turn(prompt)
            problems = []
            if self.task_path.exists():
                problems.append(f"{self.task_path.name} still in .ai-tasks/ "
                                "(not archived)")
            if not (ARCHIVE_DIR / self.task_path.name).exists():
                problems.append("archive copy missing")
            if self.task_id in INDEX_FILE.read_text():
                problems.append("task row still present in .ai-tasks/index.md")
            if not tree_clean():
                problems.append("working tree not clean after close-out")
            for _ in range(MAX_FOLLOWUPS):
                if not problems:
                    break
                self.log("close-out violations: " + "; ".join(problems))
                session.turn("[orchestrator] Close-out incomplete:\n- "
                             + "\n- ".join(problems)
                             + "\nFinish the ai-sync-v2 close-out per the "
                             "skill, then end with a clean tree.")
                problems = []
                if self.task_path.exists():
                    problems.append("task not archived")
                if not tree_clean():
                    problems.append("tree not clean")
            if problems:
                self.ask_human("Close-out still incomplete: "
                               + "; ".join(problems)
                               + "\nFinish manually, then type 'done'.")
        finally:
            session.close()
        self.log("close-out done")

    # -- main loop --

    def _verify_native_closeout(self) -> None:
        """The session's native hook chain (cc/codex Stop hook →
        /ai-sync-v2) archived the task mid-session. Verify the close-out is
        complete — archive copy already confirmed by the caller — and end
        the run instead of driving a second close-out."""
        problems = []
        if self.task_id in INDEX_FILE.read_text():
            problems.append("task row still present in .ai-tasks/index.md")
        if not tree_clean():
            problems.append("working tree not clean after close-out")
        if problems:
            self.ask_human("Close-out (performed in-session by the native "
                           "hook chain) is incomplete:\n- "
                           + "\n- ".join(problems)
                           + "\nFinish manually, then type 'done'.")
        self.log("close-out done (performed in-session by the native hook "
                 "chain; orchestrator verified archive/index/tree)")

    def loop(self) -> None:
        if not self.task_path.exists():
            if (ARCHIVE_DIR / self.task_path.name).exists():
                sys.exit(f"task already archived: {self.task_path.name}")
            sys.exit(f"task file not found: {self.task_path}")
        sessions = 0
        while True:
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
            latest = task.entries[-1] if task.entries else None
            marker = bool(latest and not latest.is_review
                          and latest.is_continuation)
            remediation_open = bool(
                task.review_entries
                and task.review_entries[-1].verdict == "changes-requested")
            if marker and not remediation_open:
                # The marker is remediation-only (§10). On an advancement
                # entry it is protocol-illegal — ignore it rather than
                # skipping the review.
                self.log("WARNING: `Handoff: continuation` on a "
                         "non-remediation entry (latest review verdict is "
                         "not changes-requested) — marker ignored; an "
                         "advancement session's work is reviewed next")
                marker = False
            if marker:
                # Remediation continuation: the last remediation session
                # wrapped up (context budget) before its fix set was
                # complete. The same role continues; re-review waits until
                # the fix set completes (an entry without the marker).
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
                    # Final-gate rejection keeps final_review (tasks-v2 §3);
                    # the next turn is a dev remediation session.
                    self.log("final-gate rejection: dispatching dev "
                             "remediation (status stays final_review)")
                    self.run_session("dev", self.dev_prompt)
                    sessions += 1
                else:
                    # Everything reviewed, still final_review, and the last
                    # review didn't conclude with a verdict-driven handback:
                    # a dumb scheduler doesn't loop on this.
                    ruling = self.ask_human(
                        "Task sits at final_review with no unreviewed dev "
                        "sessions (last review didn't conclude). Give a "
                        "ruling for a fresh review session, or 'stop'.")
                    self.pending_ruling = ruling
                    self.last_review_agent = self.run_session(
                        "review",
                        lambda sid: self.review_prompt(sid)
                        + f"\nHUMAN RULING (binding): {ruling}")
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task_id", type=lambda s: s.removesuffix(".md"),
                    help="task id, e.g. 2026-06-23-v1-risk-control "
                         "(a trailing .md is tolerated)")
    ap.add_argument("--once", action="store_true",
                    help="run exactly one session, then exit")
    ap.add_argument("--backend", choices=["cursor", "cc-codex"],
                    default="cursor",
                    help="cursor = Cursor SDK both roles (default); "
                         "cc-codex = Claude Code dev + Codex CLI review")
    ap.add_argument("--plan-gate", action="store_true",
                    help="each dev session first proposes goal+plan and "
                         "blocks for human confirmation before implementing")
    ap.add_argument("--dev-model", default=None,
                    help=f"default: {DEFAULT_DEV_MODEL} (cursor) / "
                         f"{DEFAULT_CC_MODEL} (cc-codex)")
    ap.add_argument("--review-model", default=None,
                    help=f"default: {DEFAULT_REVIEW_MODEL} (cursor) / "
                         f"{DEFAULT_CODEX_MODEL} (cc-codex)")
    ap.add_argument("--dev-effort", default=None,
                    help="dev-role effort. cursor: claude effort axis "
                         "low..max (default: catalog default = high); "
                         "cc-codex: claude --effort (default: max)")
    ap.add_argument("--review-effort", default=None,
                    help="review-role effort: none/low/medium/high/xhigh "
                         "(canonical = codex spelling; the cursor gpt axis "
                         "calls the top tier 'extra-high' — both spellings "
                         "are accepted and translated per backend). "
                         "Default: cursor = catalog default (medium); "
                         "cc-codex = xhigh")
    ap.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    args = ap.parse_args()

    if args.backend == "cursor":
        dev_model = args.dev_model or DEFAULT_DEV_MODEL
        review_model = args.review_model or DEFAULT_REVIEW_MODEL
        dev_effort = args.dev_effort or DEFAULT_CURSOR_DEV_EFFORT
        review_effort = args.review_effort or DEFAULT_CURSOR_REVIEW_EFFORT
        for label, model, eff in (
                ("--dev-effort", dev_model, dev_effort),
                ("--review-effort", review_model, review_effort)):
            err = effort_error(CursorBackend._effort_axis(model), eff)
            if err:
                sys.exit(f"{label}: {err}")
        api_key = os.environ.get("CURSOR_API_KEY")
        validate_models(api_key, [dev_model, review_model])
        # Hooks must no-op inside SDK-driven sessions (the orchestrator owns
        # the lifecycle); the SDK's local executor inherits this env.
        os.environ["AI_ORCH"] = "1"
        orch = Orchestrator(args.task_id, dev_model, review_model, api_key,
                            args.once, args.max_sessions,
                            plan_gate=args.plan_gate)
        orch.backend = CursorBackend(orch, dev_model, review_model, api_key,
                                     dev_effort=dev_effort,
                                     review_effort=review_effort)
    else:
        dev_model = args.dev_model or DEFAULT_CC_MODEL
        review_model = args.review_model or DEFAULT_CODEX_MODEL
        cc_effort = args.dev_effort or DEFAULT_CC_EFFORT
        codex_effort = args.review_effort or DEFAULT_CODEX_EFFORT
        for label, axis, eff in (("--dev-effort", "effort", cc_effort),
                                 ("--review-effort", "reasoning",
                                  codex_effort)):
            err = effort_error(axis, eff)
            if err:
                sys.exit(f"{label}: {err}")
        # NO AI_ORCH here: cc/codex hooks must fire — they carry protocol
        # injection and end-discipline natively for their own sessions.
        orch = Orchestrator(args.task_id, dev_model, review_model, None,
                            args.once, args.max_sessions,
                            plan_gate=args.plan_gate)
        orch.backend = CliBackend(orch, dev_model, cc_effort,
                                  review_model, codex_effort)

    orch.log(f"orchestrator start: task={args.task_id} "
             f"backend={args.backend} dev={orch.backend.describe('dev')} "
             f"review={orch.backend.describe('review')} once={args.once} "
             f"plan_gate={args.plan_gate}")
    if not tree_clean():
        sys.exit("working tree is not clean — resolve before orchestrating")
    orch.loop()
    orch.log("orchestrator done")


if __name__ == "__main__":
    main()
