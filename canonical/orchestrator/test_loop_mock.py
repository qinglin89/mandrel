#!/usr/bin/env python3
"""Offline validation of orchestrator.py loop mechanics with a mocked SDK.

Builds a throwaway git repo that mimics quantx's layout (.ai-tasks/ gitignored,
task file with one unreviewed dev entry) and drives the Orchestrator with a
FakeAgent whose scripted behaviors edit that repo the way a real session
would. No CURSOR_API_KEY needed.

Scenarios (one function each, run in order by main()): loop mechanics
(review dispatch, followups, budgets, blocked resume, close-out — plain and
native-in-session), prompt instantiation (checklists, wrap-up kinds,
remediation lock), event-stream logging, CLI argv/routing shapes, and the
escalation paths (§5.4/§5.6/§5.7 of the README).

Run: .venv/bin/python test_loop_mock.py
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orchestrator as o

TASK_ID = "2026-01-01-mock-task"
DEV_SID = "dev-aaaa-1111"

TASK_BODY = f"""---
id: {TASK_ID}
status: in_progress
session-est: 1/2
blockers: []
claimed-by: {DEV_SID}@2026-01-01T00:00:00Z
---

# Mock task

## Session log

### 2026-01-01 / {DEV_SID} / (pending → in_progress)
- Done: implemented the widget
- Next: reviewer take a look
- Open: none
"""


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_repo() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="orch-mock-"))
    (tmp / ".ai-tasks").mkdir()
    (tmp / ".ai-tasks" / f"{TASK_ID}.md").write_text(TASK_BODY)
    (tmp / ".ai-tasks" / "index.md").write_text(
        f"| [{TASK_ID}]({TASK_ID}.md) | in_progress | mock |\n")
    (tmp / ".gitignore").write_text(".ai-tasks/\n.cursor/\n")
    (tmp / "widget.go").write_text("package main\n")
    sh(tmp, "git", "init", "-q")
    sh(tmp, "git", "config", "user.email", "mock@test")
    sh(tmp, "git", "config", "user.name", "mock")
    sh(tmp, "git", "add", "-A")
    sh(tmp, "git", "commit", "-qm", "init")
    return tmp


class FakeRun:
    id = "run-fake"
    events: list = []  # scripted stream events, consumed by the next run
    conversation_chars = 0  # drives the context-budget estimate

    def __init__(self, status: str = "finished") -> None:
        self._status = status

    def messages(self):
        msgs, FakeRun.events = FakeRun.events, []
        return iter(msgs)

    def conversation_json(self) -> str:
        return "x" * FakeRun.conversation_chars

    def wait(self):
        class R:  # noqa: N801
            pass
        r = R()
        r.status = self._status
        return r

    def supports(self, _op: str) -> bool:
        return False


class FakeAgent:
    """Consumes one scripted behavior per send(). A behavior is
    fn(prompt) -> None and mutates the sandbox repo like a real session."""

    script: list = []          # shared queue, set per scenario
    prompts: list[str] = []    # every prompt seen, for assertions
    created: list[str] = []
    next_id = 0

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id

    @classmethod
    def create(cls, _options=None, **_kw) -> "FakeAgent":
        cls.next_id += 1
        aid = f"fake-agent-{cls.next_id:03d}"
        cls.created.append(aid)
        return cls(aid)

    @classmethod
    def resume(cls, agent_id: str, _options=None, **_kw) -> "FakeAgent":
        return cls(agent_id)

    def send(self, prompt: str) -> FakeRun:
        FakeAgent.prompts.append(prompt)
        if not FakeAgent.script:
            raise AssertionError("FakeAgent.send with empty script:\n"
                                 + prompt[:400])
        behavior = FakeAgent.script.pop(0)
        behavior(self, prompt)
        return FakeRun()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patch_module(repo: Path) -> None:
    o.REPO = repo
    o.TASKS_DIR = repo / ".ai-tasks"
    o.ARCHIVE_DIR = repo / ".ai-tasks" / "archive"
    o.INDEX_FILE = repo / ".ai-tasks" / "index.md"
    o.SESSION_MAP = repo / ".ai-tasks" / "sessions.json"
    o.Agent = FakeAgent


def new_orch(**kw) -> o.Orchestrator:
    orch = o.Orchestrator(TASK_ID, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True,
                          max_sessions=kw.pop("max_sessions", 10),
                          plan_gate=kw.pop("plan_gate", False))
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    return orch


def bump_est(p: Path) -> None:
    """What a protocol-conformant dev session does at claim (§10 Entry 3)."""
    t = p.read_text()
    m = o.re.search(r"^session-est:\s*(\d+)/(\d+)", t, o.re.MULTILINE)
    cur, tot = int(m.group(1)) + 1, int(m.group(2))
    tot = max(tot, cur)
    p.write_text(o.re.sub(r"^session-est:.*$", f"session-est: {cur}/{tot}",
                          t, flags=o.re.MULTILINE))


def append_review(repo: Path, sid: str, of_sid: str, verdict: str,
                  group: str, status: str) -> None:
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    text = p.read_text()
    text = o.re.sub(r"^status:.*$", f"status: {status}", text,
                    flags=o.re.MULTILINE)
    text += (f"\n### 2026-01-02 / {sid} / review of {of_sid} / "
             f"(in_progress → {status})\n"
             f"- Verdict: {verdict}\n- Group: {group}\n"
             f"- Findings: correctness: mock finding\n")
    p.write_text(text)


def scenario_1_happy_interim(repo: Path) -> None:
    def do_review(agent: FakeAgent, prompt: str) -> None:
        assert f"review {TASK_ID}" in prompt, "review verb missing from prompt"
        assert "PROJECT PROTOCOL CONTEXT" in prompt, "protocol block missing"
        assert "AUTOMATION MODE" in prompt, "automation fragment missing"
        assert "review-workflow.mdc" in prompt, "review rule missing"
        assert "ENTRY CHECKLIST" in prompt, "entry checklist missing"
        assert f"pending review set at dispatch: {DEV_SID}" in prompt, \
            "pending set must be instantiated in the review prompt"
        assert "POST-SESSION CHECKS" in prompt, "check preview missing"
        assert "findings never gate an interim review" in prompt, \
            "interim status menu must be instantiated"
        append_review(repo, agent.agent_id, DEV_SID, "changes-requested",
                      DEV_SID, "in_progress")

    FakeAgent.script = [do_review]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation: " + banner[:200]))
    orch.loop()
    task = o.parse_task(o.TASKS_DIR / f"{TASK_ID}.md")
    assert task.unreviewed_dev_sids() == [], "dev entry should now be reviewed"
    assert task.review_entries[-1].group == DEV_SID
    assert orch.pending_ruling is None, "1 round must not escalate"
    assert not FakeAgent.script, "behavior not consumed"
    print("scenario 1 (happy interim review): PASS")


def scenario_2_dirty_tree_followup(repo: Path) -> None:
    def bad_review(agent: FakeAgent, prompt: str) -> None:
        bump_est(repo / ".ai-tasks" / f"{TASK_ID}.md")
        append_review(repo, agent.agent_id, DEV_SID, "pass",
                      DEV_SID, "in_progress")
        (repo / "scratch.txt").write_text("oops")  # untracked → dirty tree

    def fix_it(agent: FakeAgent, prompt: str) -> None:
        assert "Protocol violation" in prompt, "followup should cite violation"
        assert "not clean" in prompt
        (repo / "scratch.txt").unlink()

    FakeAgent.script = [bad_review, fix_it]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch.loop()
    assert not FakeAgent.script, "followup behavior not consumed"
    assert o.tree_clean(), "tree should be clean after fix"
    print("scenario 2 (dirty tree → followup → fixed): PASS")


def scenario_3_budget_escalation(repo: Path) -> None:
    # Seed 2 prior changes-requested rounds in the same group, plus an
    # unreviewed dev entry so a review turn dispatches.
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    append_review(repo, "rev-1", DEV_SID, "changes-requested", DEV_SID,
                  "in_progress")
    append_review(repo, "rev-2", DEV_SID, "changes-requested", DEV_SID,
                  "in_progress")
    p.write_text(p.read_text() + (
        f"\n### 2026-01-03 / dev-bbbb-2222 / (in_progress → in_progress)\n"
        "- Done: attempted fix\n- Next: re-review\n- Open: none\n"))

    def third_round(agent: FakeAgent, prompt: str) -> None:
        append_review(repo, agent.agent_id, "dev-bbbb-2222",
                      "changes-requested", DEV_SID, "in_progress")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "accept the residual risk; ship it"

    FakeAgent.script = [third_round]
    orch = new_orch()
    orch.ask_human = fake_ask
    orch.loop()
    assert asked, "budget exhaustion must escalate to human"
    assert "budget exhausted" in asked[0] or "escalated" in asked[0]
    assert orch.pending_ruling == "accept the residual risk; ship it"
    print("scenario 3 (per-group budget → escalate + ruling): PASS")


def scenario_4_blocked(repo: Path) -> None:
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    text = p.read_text()
    text = o.re.sub(r"^status:.*$", "status: blocked", text,
                    flags=o.re.MULTILINE)
    text = o.re.sub(r"^blockers:.*$",
                    "blockers: [external:UTC day or rolling 24h?]", text,
                    flags=o.re.MULTILINE)
    text = o.re.sub(r"^claimed-by:.*$",
                    "claimed-by: dev-cccc-3333@2026-01-04T00:00:00Z", text,
                    flags=o.re.MULTILINE)
    text += ("\n### 2026-01-04 / dev-cccc-3333 / (in_progress → blocked)\n"
             "- Done: S4 partially wired\n- Next: resume after answer\n"
             "- Open: need reset-boundary ruling (UTC vs rolling)\n")
    p.write_text(text)

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "UTC day"

    def resume_behavior(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "dev-cccc-3333", "must resume the blocked sid"
        assert "UTC day" in prompt, "answer must be forwarded"
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: in_progress", t,
                     flags=o.re.MULTILINE)
        t = o.re.sub(r"^blockers:.*$", "blockers: []", t, flags=o.re.MULTILINE)
        p.write_text(t)

    # after unblocking, loop() continues: next turn is a review of the two
    # unreviewed dev sids; script it, then --once stops.
    def review_after(agent: FakeAgent, prompt: str) -> None:
        append_review(repo, agent.agent_id, "dev-cccc-3333", "pass",
                      "dev-cccc-3333", "in_progress")
        # also cover dev-bbbb-2222 so pending set empties
        t = p.read_text()
        t += (f"\n### 2026-01-04 / {agent.agent_id} / review of dev-bbbb-2222"
              " / (in_progress → in_progress)\n- Verdict: pass\n"
              f"- Group: dev-bbbb-2222\n- Findings: none\n")
        p.write_text(t)

    FakeAgent.script = [resume_behavior, review_after]
    orch = new_orch()
    orch.ask_human = fake_ask
    orch.loop()
    assert asked and "BLOCKED" in asked[0]
    assert o.parse_task(p).status == "in_progress"
    assert not FakeAgent.script
    print("scenario 4 (blocked → human answer → resume → unblock): PASS")


def scenario_5_event_stream_logging(repo: Path) -> None:
    """Stream events go to the log FILE only; high-rate text/thinking char
    events aggregate into windowed [gen] lines that flush BEFORE any
    immediate event (stream order preserved) and at run end; duration-
    carrying thinking events stay immediate."""
    import io
    from types import SimpleNamespace as NS

    p = repo / ".ai-tasks" / f"{TASK_ID}.md"

    def do_dev(agent: FakeAgent, prompt: str) -> None:
        bump_est(p)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-05 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: mock dev work\n- Next: review\n- Open: none\n"))

    FakeRun.events = [
        NS(type="assistant",
           message=NS(content=[NS(type="text", text="hi!")])),      # +3 chars
        NS(type="tool_call", status="running", name="shell",
           args={"command": "git show --stat"}),          # flushes the window
        NS(type="tool_call", status="completed", name="shell", result="ok"),
        NS(type="tool_call", status="error", name="read_file",
           result="no such file"),
        NS(type="thinking", thinking_duration_ms=4200, text=""),  # immediate
        NS(type="thinking", text="mulling over the diff"),        # +21 chars
        NS(type="status", status="working", message="analyzing"),   # flushes
        NS(type="assistant",
           message=NS(content=[NS(type="text", text="hello")])),  # end flush
    ]
    FakeAgent.script = [do_dev]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        orch.loop()
    term = stdout.getvalue()
    log = orch.log_file.read_text()
    assert "run started" in term, "status lines must stay on the terminal"
    for marker in ("[tool]", "[tool-error]", "[thinking]", "[status]",
                   "[gen]"):
        assert marker not in term, f"{marker} leaked to the terminal"
        assert marker in log, f"{marker} missing from the log file"
    assert log.count("[tool] shell") == 1, "completed tool event must not log"
    assert "[thinking] 4s" in log, "duration thinking stays an immediate line"
    assert "[thinking] 21 chars" not in log, "char thinking must aggregate"
    assert "[text]" not in log, "per-event [text] lines are replaced by [gen]"
    assert "[gen] text +3 chars" in log
    assert "[gen] thinking +21 chars" in log
    assert "[gen] text +5 chars" in log, "run end must flush the open window"
    assert log.index("[gen] text +3 chars") < log.index("[tool] shell"), \
        "window must flush BEFORE the immediate event that interrupts it"
    assert log.index("[gen] thinking +21 chars") < log.index(
        "[status] working"), "same ordering for the [status] interrupt"
    assert "hello" in log, "assistant text must reach the transcript"
    print("scenario 5 (event stream → [gen] aggregation, log file only): "
          "PASS")


def scenario_6_plan_gate(repo: Path) -> None:
    """--plan-gate: extra conversational turn — plan proposed as assistant
    text (no task-file writes), human ruling injected into the working turn
    of the SAME session; single session-log entry at the end."""
    from types import SimpleNamespace as NS

    p = repo / ".ai-tasks" / f"{TASK_ID}.md"

    # Clear the pending-review set left by earlier scenarios so the next
    # turn is a dev turn.
    for sid in o.parse_task(p).unreviewed_dev_sids():
        append_review(repo, "rev-cleanup", sid, "pass", sid, "in_progress")
    entries_before = len(o.parse_task(p).entries)

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "confirmed, but skip S6 for now"

    # Turn 1: planning only — replies with plan text, touches NOTHING.
    def propose_plan(agent: FakeAgent, prompt: str) -> None:
        assert "PLANNING ONLY" in prompt, "gate instruction missing"
        assert "task " + TASK_ID in prompt, "role line missing"
        FakeRun.events = [NS(type="assistant", message=NS(content=[
            NS(type="text",
               text="PLAN: fix finding 1+2, then S4; touching riskpolicy/")]))]
        assert not asked, "must not ask before the plan turn ends"

    # Turn 2: working turn carries the ruling; session ends normally.
    def execute(agent: FakeAgent, prompt: str) -> None:
        assert "PLAN RULING" in prompt and "skip S6" in prompt, \
            "human ruling must be injected into the working turn"
        bump_est(p)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-06 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: executed confirmed plan\n- Next: review\n- Open: none\n"))

    FakeAgent.script = [propose_plan, execute]
    orch = new_orch(plan_gate=True)
    orch.ask_human = fake_ask
    orch.loop()
    task = o.parse_task(p)
    assert asked and "PLAN CONFIRMATION" in asked[0], asked
    assert "PLAN: fix finding" in asked[0], "plan text must reach the banner"
    assert task.status == "in_progress", "no status churn from the gate"
    assert len(task.entries) == entries_before + 1, \
        "exactly one session-log entry (no plan entry)"
    assert not FakeAgent.script
    print("scenario 6 (--plan-gate conversational: plan → confirm → "
          "execute): PASS")


def scenario_7_cli_event_parsers(repo: Path) -> None:
    """ClaudeSession/CodexSession JSONL event digestion: [tool]/[text] lines,
    error flagging, codex session-id capture."""
    orch = new_orch()

    cs = o.ClaudeSession.__new__(o.ClaudeSession)
    cs.orch, cs.sid = orch, "cc-sid"
    chunks: list[str] = []
    assert cs._handle_event(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "go test ./..."}},
            {"type": "text", "text": "done"}]}}, chunks) is None
    # thinking_tokens bursts (claude 2.1.199: one event per ~1.5s of
    # thinking, 237 observed in one session) collapse to ONE line; a
    # non-thinking event ends the burst so the next burst logs again
    for _ in range(5):
        assert cs._handle_event(
            {"type": "system", "subtype": "thinking_tokens"}, chunks) is None
    cs._handle_event({"type": "system", "subtype": "compact"}, chunks)
    for _ in range(3):
        cs._handle_event({"type": "system", "subtype": "thinking_tokens"},
                         chunks)
    # context = per-request usage of the latest MAIN-thread assistant event;
    # subagent events (parent_tool_use_id) and the result event's cumulative
    # usage must both be ignored
    cs._handle_event(
        {"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [], "usage": {
             "input_tokens": 100, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 50, "output_tokens": 7}}},
        chunks)
    assert cs.context_tokens == 1157, cs.context_tokens
    cs._handle_event(
        {"type": "assistant", "parent_tool_use_id": "tu_01",
         "message": {"content": [], "usage": {"input_tokens": 5}}}, chunks)
    assert cs.context_tokens == 1157, "subagent usage must not overwrite"
    assert cs._handle_event(
        {"type": "result", "subtype": "error", "is_error": True,
         "usage": {"input_tokens": 1_890_000}},
        chunks) == "error"
    assert cs.context_tokens == 1157, \
        "cumulative result usage must not be used as a context signal"
    assert chunks == ["done"]

    xs = o.CodexSession.__new__(o.CodexSession)
    xs.orch, xs.sid = orch, None
    chunks = []
    xs._handle_event({"type": "thread.started", "thread_id": "codex-123"},
                     chunks)
    assert xs.sid == "codex-123", "codex sid must be captured from the stream"
    xs._handle_event({"type": "item.completed",
                      "item": {"type": "agent_message",
                               "text": "verdict below"}}, chunks)
    assert chunks == ["verdict below"]
    xs._handle_event({"type": "turn.completed",
                      "usage": {"input_tokens": 2_900_000}}, chunks)
    assert xs.context_tokens == 0, \
        "cumulative turn.completed usage must not be used as context"
    assert xs._handle_event({"type": "turn.failed", "message": "boom"},
                            chunks) == "error"
    assert o._session_map_load().get("codex-123", {}).get("tool") == "codex"

    # codex context estimate = rollout transcript chars / 4 (Stop-hook style)
    import os as _os
    home = Path(tempfile.mkdtemp(prefix="codex-home-"))
    day = home / "sessions" / "2026" / "07" / "04"
    day.mkdir(parents=True)
    (day / "rollout-2026-07-04T00-00-00-codex-123.jsonl").write_text(
        "x" * 4000)
    prev = _os.environ.get("CODEX_HOME")
    _os.environ["CODEX_HOME"] = str(home)
    try:
        xs._update_context()
    finally:
        if prev is None:
            _os.environ.pop("CODEX_HOME", None)
        else:
            _os.environ["CODEX_HOME"] = prev
    assert xs.context_tokens == 1000, xs.context_tokens

    log = orch.log_file.read_text()
    assert "[tool] Bash" in log and "[text]" in log
    assert log.count("thinking_tokens (burst") == 2, \
        "each thinking burst must log exactly once (2 bursts scripted)"
    assert "[status] compact" in log, "burst-breaking event still logs"
    print("scenario 7 (CLI JSONL event parsers): PASS")


def scenario_8_cursor_effort_selection(repo: Path) -> None:
    """CursorBackend maps --dev-effort/--review-effort onto the right
    ModelSelection param axis; unset effort keeps the bare model id."""
    orch = new_orch()
    b = o.CursorBackend(orch, "claude-fable-5", "gpt-5.5", api_key=None,
                        dev_effort="max", review_effort="xhigh")
    assert b._model_selection("dev") == {
        "id": "claude-fable-5",
        "params": [{"id": "effort", "value": "max"}]}
    assert b._model_selection("review") == {
        "id": "gpt-5.5",
        "params": [{"id": "reasoning", "value": "extra-high"}]}, \
        "canonical xhigh must translate to cursor's extra-high"
    assert b.describe("dev") == "cursor:claude-fable-5@max"
    b2 = o.CursorBackend(orch, "claude-fable-5", "gpt-5.5", api_key=None)
    assert b2._model_selection("dev") == "claude-fable-5", \
        "no effort → bare id → catalog default variant"
    assert b2.describe("review") == "cursor:gpt-5.5"
    # codex adapter accepts the cursor spelling too
    xs = o.CodexSession.__new__(o.CodexSession)
    o.CodexSession.__init__(xs, orch, "gpt-5.5", "extra-high")
    assert xs.effort == "xhigh", "extra-high must translate to codex's xhigh"
    # startup allowlist: the server accepts bogus values silently, so the
    # orchestrator must refuse them client-side
    assert o.effort_error("effort", None) is None, "unset effort is fine"
    assert o.effort_error("effort", "max") is None
    assert o.effort_error("effort", "xhigh") is None
    assert o.effort_error("reasoning", "extra-high") is None, "alias spelling"
    assert o.effort_error("reasoning", "minimal") is None
    err = o.effort_error("effort", "hgih")
    assert err and "invalid effort 'hgih'" in err and "low" in err, err
    assert o.effort_error("reasoning", "max"), \
        "max is claude-axis-only and must be refused on the gpt/codex axis"
    assert o.effort_error("effort", "extra-high"), \
        "extra-high is a gpt/codex spelling, not a claude effort"
    print("scenario 8 (effort aliases + startup allowlist): PASS")


def scenario_9_dispute_escalation(repo: Path) -> None:
    """A `Dispute-unresolved:` marker in a review entry escalates to the
    human immediately (round 1, budget untouched), and the next dev prompt
    is a remediation session carrying the ruling."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    pending = o.parse_task(p).unreviewed_dev_sids()
    assert pending, "scenario 6 must have left an unreviewed dev entry"

    def review_disputed(agent: FakeAgent, prompt: str) -> None:
        t = p.read_text()
        for sid in pending:
            t += (f"\n### 2026-01-07 / {agent.agent_id} / review of {sid} / "
                  "(in_progress → in_progress)\n"
                  "- Verdict: changes-requested\n"
                  "- Group: dev-zzzz-9999\n"
                  "- Findings: correctness: double-count still present\n"
                  "- Dispute-unresolved: dev disputes the double-count "
                  "finding; I verified against the code and still hold it "
                  "valid\n")
        p.write_text(t)

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "dev is right: rows are pre-batch, drop the finding"

    FakeAgent.script = [review_disputed]
    orch = new_orch()
    orch.ask_human = fake_ask
    orch.loop()
    assert asked and "DISPUTE UNRESOLVED" in asked[0], asked
    assert orch.pending_ruling == \
        "dev is right: rows are pre-batch, drop the finding"
    log = orch.log_file.read_text()
    assert "escalating immediately" in log, "must not wait for the budget"

    prompt = orch.dev_prompt("probe-sid")
    assert "REMEDIATION SESSION" in prompt, \
        "changes-requested must force a remediation-only dev prompt"
    assert "HUMAN RULING" in prompt and "rows are pre-batch" in prompt
    print("scenario 9 (dispute-unresolved → immediate escalation + "
          "remediation prompt): PASS")


def scenario_10_context_budget(repo: Path) -> None:
    """Context over budget: violations get ONE wrap-up instruction (not the
    generic followup); once discipline is met, no further turns are sent
    into the oversized conversation. Scenario 9 left a changes-requested
    verdict, so this dev session is a REMEDIATION — its wrap-up instructs
    the CONDITIONAL continuation marker (remediation-only, §10)."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    FakeRun.conversation_chars = 1_200_000  # ≈300k tokens > 200k budget

    def dev_violates(agent: FakeAgent, prompt: str) -> None:
        bump_est(p)  # claim-time increment (§10 Entry 3)
        (repo / "wip.txt").write_text("dirty")  # dirty tree, no log entry

    def wrap_up(agent: FakeAgent, prompt: str) -> None:
        assert "Wrap up NOW" in prompt, \
            "over-budget violation must get the wrap-up instruction"
        assert "Do NOT start any new work" in prompt
        assert "ONLY if your remediation fix set is not yet complete" \
            in prompt, "remediation wrap-up must carry the conditional marker"
        assert "Handoff: continuation" in prompt
        (repo / "wip.txt").unlink()
        p.write_text(p.read_text() + (
            f"\n### 2026-01-08 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: partial fix; wrapped up over context budget\n"
            "- Next: fresh session continues\n"
            "- Handoff: continuation\n- Open: none\n"))

    FakeAgent.script = [dev_violates, wrap_up]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("wrap-up within followup budget must not escalate"))
    orch.loop()
    log = orch.log_file.read_text()
    assert "context budget exceeded" in log, "wrap-up branch must log"
    assert "handing off to a fresh session" in log, \
        "clean over-budget session must end without further turns"
    assert not FakeAgent.script
    FakeRun.conversation_chars = 0
    print("scenario 10 (context budget → wrap-up → fresh-session handoff): "
          "PASS")


def scenario_11_continuation_same_role(repo: Path) -> None:
    """After a REMEDIATION continuation-marked wrap-up (scenario 10; latest
    verdict is changes-requested), the next turn is DEV remediation again;
    re-review is deferred until the fix set completes."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    task = o.parse_task(p)
    assert task.entries[-1].is_continuation, \
        "scenario 10 must have left a continuation entry"
    assert task.review_entries[-1].verdict == "changes-requested", \
        "marker is honored only with an open remediation"
    assert task.unreviewed_dev_sids(), "split entries are pending review"

    def dev_completes(agent: FakeAgent, prompt: str) -> None:
        assert f"task {TASK_ID}" in prompt, "must be a dev-role prompt"
        assert "REMEDIATION SESSION" in prompt, \
            "continuation of an open fix set is a remediation session"
        bump_est(p)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-08 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: completed the fix set\n"
            "- Next: re-review\n- Open: none\n"))

    FakeAgent.script = [dev_completes]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch.loop()
    log = orch.log_file.read_text()
    assert "remediation continuation: resuming dev" in log
    assert "review session start" not in log, \
        "re-review must be deferred while the fix set is open"
    assert "dev session start" in log
    # fix set now complete (latest entry has no marker) → next turn is review
    task = o.parse_task(p)
    assert not task.entries[-1].is_continuation
    assert len(task.unreviewed_dev_sids()) >= 2, \
        "both split remediation entries await the batched re-review"
    print("scenario 11 (remediation continuation → same-role dev resume, "
          "re-review deferred): PASS")


def scenario_12_est_increment_enforced(repo: Path) -> None:
    """A dev session that forgets to bump session-est gets a followup."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    for sid in o.parse_task(p).unreviewed_dev_sids():
        append_review(repo, "rev-cleanup2", sid, "pass", sid, "in_progress")

    def forgets_est(agent: FakeAgent, prompt: str) -> None:
        assert "ENTRY CHECKLIST" in prompt, "dev entry checklist missing"
        assert "session-est: 6/6 → 7/7" in prompt, \
            "est increment must be instantiated with concrete values"
        assert "POST-SESSION CHECKS" in prompt, "check preview missing"
        assert "ONLY when the whole scope is complete" in prompt, \
            "advancement status menu must be in the preview"
        assert "remediation-only" in prompt, \
            "no-marker rule must be in the advancement preview"
        p.write_text(p.read_text() + (
            f"\n### 2026-01-09 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: work\n- Next: more\n- Handoff: continuation\n"
            "- Open: none\n"))

    def fixes_est(agent: FakeAgent, prompt: str) -> None:
        assert "session-est not incremented" in prompt, \
            "followup must cite the est violation"
        assert "remediation-only" in prompt, \
            "followup must cite the illegal advancement marker"
        # drop the LAST marker occurrence (this session's entry), then bump
        head, _, tail = p.read_text().rpartition("\n- Handoff: continuation")
        p.write_text(head + tail)
        bump_est(p)

    FakeAgent.script = [forgets_est, fixes_est]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch.loop()
    assert not FakeAgent.script, "est-fix followup must be consumed"
    print("scenario 12 (est increment + advancement no-marker enforced): "
          "PASS")


def scenario_13_final_gate_loop(repo: Path) -> None:
    """tasks-v2 §3 table end-to-end at the final gate: reject keeps
    final_review → dev remediation (status untouched) → re-review pass →
    completed → close-out archives."""
    tid = "2026-01-02-final-loop"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: final_review
session-est: 1/1
blockers: []
claimed-by: dev-ffff-0001@2026-01-10T00:00:00Z
---

# Final-gate loop mock

## Session log

### 2026-01-10 / dev-ffff-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final review
- Open: none
""")
    idx = repo / ".ai-tasks" / "index.md"
    idx.write_text(idx.read_text() + f"| [{tid}]({tid}.md) | final_review | mock |\n")

    def gate_rejects(agent: FakeAgent, prompt: str) -> None:
        p.write_text(p.read_text() + (
            f"\n### 2026-01-10 / {agent.agent_id} / review of dev-ffff-0001 "
            "/ (final_review → final_review)\n"
            "- Verdict: changes-requested\n- Group: dev-ffff-0001\n"
            "- Findings: correctness: off-by-one in cap check\n"))

    def remediates(agent: FakeAgent, prompt: str) -> None:
        assert "REMEDIATION SESSION" in prompt
        assert "keep `final_review` UNCHANGED" in prompt, \
            "remediation menu must be instantiated with the entry status"
        bump_est(p)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-10 / {agent.agent_id} / "
            "(final_review → final_review)\n"
            "- Done: fixed the cap check; status untouched per tasks-v2 §3\n"
            "- Next: re-review\n- Open: none\n"))

    def gate_passes(agent: FakeAgent, prompt: str) -> None:
        task = o.parse_task(p)
        rem_sid = [e.session_id for e in task.entries if not e.is_review][-1]
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: completed", t,
                     flags=o.re.MULTILINE)
        p.write_text(t + (
            f"\n### 2026-01-10 / {agent.agent_id} / review of {rem_sid} / "
            "(final_review → completed)\n"
            "- Verdict: pass\n- Group: dev-ffff-0001\n- Findings: resolved\n"))

    def closes_out(agent: FakeAgent, prompt: str) -> None:
        assert "/ai-sync-v2" in prompt
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        idx.write_text("\n".join(
            ln for ln in idx.read_text().splitlines() if tid not in ln) + "\n")

    FakeAgent.script = [gate_rejects, remediates, gate_passes, closes_out]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=False, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("no escalation expected: " + banner[:200]))
    orch.loop()
    log = orch.log_file.read_text()
    assert "final-gate rejection: dispatching dev remediation" in log
    assert "close-out done" in log
    assert (repo / ".ai-tasks" / "archive" / f"{tid}.md").exists()
    assert not FakeAgent.script
    print("scenario 13 (final gate: reject stays final_review → remediation "
          "→ pass → completed → close-out): PASS")


def scenario_14_blocked_review_resume(repo: Path) -> None:
    """A blocked REVIEW session (§3: any session may block — here a final
    gate escalating a ruling) resumes with the REVIEW role, and post-checks
    key on the status it entered with (left side of `→ blocked` in its
    entry heading): restoring final_review must pass without a violation
    followup, and the loop then dispatches dev remediation."""
    tid = "2026-01-03-blocked-review"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: blocked
session-est: 1/2
blockers: [external:does the deferred style finding gate completion?]
claimed-by: rev-gate-0001@2026-01-11T00:00:00Z
---

# Blocked final-gate review mock

## Session log

### 2026-01-11 / dev-gggg-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none

### 2026-01-11 / rev-gate-0001 / review of dev-gggg-0001 / (final_review → blocked)
- Findings: correctness: cap check off-by-one (ruling pending)
- Open: need a ruling whether the deferred item gates completion
""")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "changes required — hand it back to dev"

    def resume_behavior(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "rev-gate-0001", "must resume the blocked sid"
        assert "The human answered your blocker" in prompt
        assert "pre-blocked value" in prompt
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: final_review", t,
                     flags=o.re.MULTILINE)
        t = o.re.sub(r"^blockers:.*$", "blockers: []", t, flags=o.re.MULTILINE)
        # amend the (still last) review entry with the concluded verdict
        t += "- Verdict: changes-requested\n- Group: dev-gggg-0001\n"
        p.write_text(t)

    def remediates(agent: FakeAgent, prompt: str) -> None:
        assert "REMEDIATION SESSION" in prompt
        bump_est(p)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-11 / {agent.agent_id} / "
            "(final_review → final_review)\n"
            "- Done: fixed the cap check\n- Next: re-review\n- Open: none\n"))

    roles: list[str] = []
    n0 = len(FakeAgent.prompts)
    FakeAgent.script = [resume_behavior, remediates]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = fake_ask
    orig_resume = orch.backend.resume_session
    orch.backend.resume_session = (
        lambda sid, role: roles.append(role) or orig_resume(sid, role))
    orch.loop()
    assert asked and "BLOCKED by review session rev-gate-0001" in asked[0], \
        asked
    assert roles == ["review"], f"resume must use the review role: {roles}"
    new_prompts = FakeAgent.prompts[n0:]
    assert len(new_prompts) == 2, new_prompts
    assert not any("Protocol violation" in pr for pr in new_prompts), \
        "restoring final_review must satisfy the review post-checks " \
        "(status keyed on the pre-blocked value, not on `blocked`)"
    log = orch.log_file.read_text()
    assert "resuming blocked review session rev-gate-0001" in log
    assert "final-gate rejection: dispatching dev remediation" in log
    assert o.parse_task(p).status == "final_review"
    assert not FakeAgent.script
    print("scenario 14 (blocked review → review-role resume, pre-blocked "
          "status post-checks): PASS")


def scenario_15_native_closeout_archival(repo: Path) -> None:
    """cc-codex seam (crashed live 2026-07-04): a final-gate pass trips the
    review session's NATIVE Stop hook, which runs /ai-sync-v2 INSIDE the
    session — the task file is already archived when post-checks run. The
    orchestrator must recognize the fait accompli (skip post-checks, verify
    close-out, exit cleanly) instead of crashing on the vanished file."""
    tid = "2026-01-04-native-closeout"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: final_review
session-est: 1/1
blockers: []
claimed-by: dev-hhhh-0001@2026-01-12T00:00:00Z
---

# Native close-out mock

## Session log

### 2026-01-12 / dev-hhhh-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none
""")
    idx = repo / ".ai-tasks" / "index.md"
    idx.write_text(idx.read_text()
                   + f"| [{tid}]({tid}.md) | final_review | mock |\n")

    def gate_passes_then_native_closeout(agent: FakeAgent, prompt: str) -> None:
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: completed", t,
                     flags=o.re.MULTILINE)
        t += (f"\n### 2026-01-12 / {agent.agent_id} / review of "
              "dev-hhhh-0001 / (final_review → completed)\n"
              "- Verdict: pass\n- Group: dev-hhhh-0001\n- Findings: none\n")
        p.write_text(t)
        # ...then the native Stop hook chain runs ai-sync-v2 in-session:
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        idx.write_text("\n".join(ln for ln in idx.read_text().splitlines()
                                 if tid not in ln) + "\n")

    FakeAgent.script = [gate_passes_then_native_closeout]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=False, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("clean native close-out must not escalate: "
                       + banner[:200]))
    orch.loop()
    log = orch.log_file.read_text()
    assert "archived mid-session" in log, "must recognize the fait accompli"
    assert "close-out done (performed in-session by the native hook" in log
    assert "status=completed+archived" in log
    assert (repo / ".ai-tasks" / "archive" / f"{tid}.md").exists()
    assert not FakeAgent.script
    print("scenario 15 (native in-session close-out → recognized, clean "
          "exit): PASS")


def scenario_16_advancement_wrapup_review_next(repo: Path) -> None:
    """An ADVANCEMENT session over the context budget wraps up WITHOUT the
    continuation marker (one dev session = one reviewable unit, §10), and
    the next dispatched turn is a REVIEW of its landed work — never another
    dev session."""
    tid = "2026-01-05-adv-wrapup"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: in_progress
session-est: 1/3
blockers: []
claimed-by: dev-iiii-0001@2026-01-13T00:00:00Z
---

# Advancement wrap-up mock

## Session log

### 2026-01-13 / dev-iiii-0001 / (pending → in_progress)
- Done: chunk 1 landed
- Next: chunk 2
- Open: none

### 2026-01-13 / rev-0-adv / review of dev-iiii-0001 / (in_progress → in_progress)
- Verdict: pass
- Group: dev-iiii-0001
- Findings: none
""")

    def dev_violates_adv(agent: FakeAgent, prompt: str) -> None:
        bump_est(p)
        (repo / "wip2.txt").write_text("dirty")

    def wrap_up_adv(agent: FakeAgent, prompt: str) -> None:
        assert "Wrap up NOW" in prompt
        assert "do NOT write a `Handoff: continuation` line" in prompt, \
            "advancement wrap-up must forbid the marker"
        assert "ONLY if your remediation fix set" not in prompt, \
            "remediation clause must not leak into an advancement wrap-up"
        (repo / "wip2.txt").unlink()
        p.write_text(p.read_text() + (
            f"\n### 2026-01-13 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: chunk 2 partially landed; wrapped up over budget\n"
            "- Next: chunk 3\n- Open: none\n"))

    FakeRun.conversation_chars = 1_200_000
    FakeAgent.script = [dev_violates_adv, wrap_up_adv]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch.loop()
    FakeRun.conversation_chars = 0
    log1 = orch.log_file.read_text()
    assert "context budget exceeded" in log1
    assert "dev session start" in log1
    assert "review session start" not in log1
    task = o.parse_task(p)
    assert not task.entries[-1].is_continuation, \
        "advancement wrap-up entry must not carry the marker"
    assert task.unreviewed_dev_sids(), "landed work must be pending review"

    # fresh orchestrator: the next turn MUST be a review of the wrapped work
    def reviews_it(agent: FakeAgent, prompt: str) -> None:
        t = p.read_text()
        for sid_ in o.parse_task(p).unreviewed_dev_sids():
            t += (f"\n### 2026-01-13 / {agent.agent_id} / review of {sid_} "
                  "/ (in_progress → in_progress)\n- Verdict: pass\n"
                  f"- Group: {sid_}\n- Findings: none\n")
        p.write_text(t)

    FakeAgent.script = [reviews_it]
    orch2 = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                           api_key=None, once=True, max_sessions=10)
    orch2.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch2.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch2.loop()
    log2 = orch2.log_file.read_text()
    assert "review session start" in log2, \
        "after an advancement wrap-up the next turn is REVIEW, not dev"
    assert not FakeAgent.script
    print("scenario 16 (advancement wrap-up: no marker + review next): PASS")


def scenario_17_final_review_stall(repo: Path) -> None:
    """§5.6 stall: everything reviewed, status stuck at final_review, and the
    latest review verdict is NOT changes-requested (no verdict-driven
    handback). A dumb scheduler doesn't loop: it asks for a ruling and
    dispatches a FRESH review carrying it."""
    tid = "2026-01-06-final-stall"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: final_review
session-est: 1/1
blockers: []
claimed-by: rev-stall-0001@2026-01-14T00:00:00Z
---

# final_review stall mock

## Session log

### 2026-01-14 / dev-jjjj-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none

### 2026-01-14 / rev-stall-0001 / review of dev-jjjj-0001 / (final_review → final_review)
- Verdict: pass
- Group: dev-jjjj-0001
- Findings: none (session died before concluding the gate)
""")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "re-run the gate; the deferred style item does not block"

    def fresh_review_concludes(agent: FakeAgent, prompt: str) -> None:
        assert f"review {tid}" in prompt, "must be a review-role prompt"
        assert ("HUMAN RULING (binding): re-run the gate" in prompt), \
            "the stall ruling must ride the fresh review prompt"
        assert "pending review set at dispatch: (empty)" in prompt, \
            "stall means nothing is pending"
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: completed", t,
                     flags=o.re.MULTILINE)
        t += (f"\n### 2026-01-14 / {agent.agent_id} / review of "
              "dev-jjjj-0001 / (final_review → completed)\n"
              "- Verdict: pass\n- Group: dev-jjjj-0001\n"
              "- Findings: ledger clean; ruling applied\n")
        p.write_text(t)

    FakeAgent.script = [fresh_review_concludes]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = fake_ask
    orch.loop()
    assert asked and "final_review with no unreviewed dev sessions" \
        in asked[0], asked
    assert o.parse_task(p).status == "completed"
    log = orch.log_file.read_text()
    assert "review session start" in log, "stall must dispatch a review"
    assert "dev session start" not in log, "stall never dispatches dev"
    assert not FakeAgent.script
    print("scenario 17 (final_review stall → ruling → fresh review): PASS")


def scenario_18_native_closeout_incomplete(repo: Path) -> None:
    """§5.7 / _verify_native_closeout ask_human path: the native hook chain
    archived the task mid-session but left the close-out INCOMPLETE (index
    row still present, tree dirty). The orchestrator must list exactly those
    problems and pause for manual completion instead of exiting clean."""
    tid = "2026-01-07-native-incomplete"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: final_review
session-est: 1/1
blockers: []
claimed-by: dev-kkkk-0001@2026-01-15T00:00:00Z
---

# Incomplete native close-out mock

## Session log

### 2026-01-15 / dev-kkkk-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none
""")
    idx = repo / ".ai-tasks" / "index.md"
    idx.write_text(idx.read_text()
                   + f"| [{tid}]({tid}.md) | final_review | mock |\n")
    leftover = repo / "leftover.txt"

    def gate_passes_sloppy_closeout(agent: FakeAgent, prompt: str) -> None:
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: completed", t,
                     flags=o.re.MULTILINE)
        t += (f"\n### 2026-01-15 / {agent.agent_id} / review of "
              "dev-kkkk-0001 / (final_review → completed)\n"
              "- Verdict: pass\n- Group: dev-kkkk-0001\n- Findings: none\n")
        p.write_text(t)
        # native ai-sync archives the file but forgets the index row AND
        # leaves an uncommitted artifact:
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        leftover.write_text("absorption scratch")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        # the human finishes manually, then types 'done':
        idx.write_text("\n".join(ln for ln in idx.read_text().splitlines()
                                 if tid not in ln) + "\n")
        leftover.unlink()
        return "done"

    FakeAgent.script = [gate_passes_sloppy_closeout]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=False, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = fake_ask
    orch.loop()
    assert len(asked) == 1, asked
    assert "Close-out (performed in-session by the native hook chain) " \
           "is incomplete" in asked[0], asked[0]
    assert "task row still present" in asked[0], asked[0]
    assert "working tree not clean" in asked[0], asked[0]
    log = orch.log_file.read_text()
    assert "archived mid-session" in log
    assert "close-out done (performed in-session by the native hook" in log
    assert not FakeAgent.script
    print("scenario 18 (native close-out incomplete → listed problems → "
          "manual finish): PASS")


def scenario_19_blocked_foreign_sid_exit(repo: Path) -> None:
    """§5.4 caveat: a task left blocked by a MANUAL session (claimed-by
    holds a sid the backend cannot resume) must exit with guidance — after
    collecting the human's answer — instead of crashing uncaught or
    resuming the wrong conversation."""
    tid = "2026-01-08-blocked-foreign"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: blocked
session-est: 1/2
blockers: [external:keep the legacy shim or drop it?]
claimed-by: manual-cc-abc123@2026-01-16T00:00:00Z
---

# Blocked-by-manual-session mock

## Session log

### 2026-01-16 / manual-cc-abc123 / (in_progress → blocked)
- Done: shim isolated behind an interface
- Next: resume after the ruling
- Open: keep the legacy shim or drop it? (manual interactive session)
""")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "drop the shim"

    def raiser(sid: str, role: str):
        raise RuntimeError(f"conversation not found: {sid}")

    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = fake_ask
    orch.backend.resume_session = raiser
    try:
        orch.loop()
        raise AssertionError("loop must sys.exit on a foreign-sid resume "
                             "failure")
    except SystemExit as e:
        msg = str(e.code)
    assert asked and "BLOCKED by dev session manual-cc-abc123" in asked[0], \
        asked
    assert "cannot resume blocked session manual-cc-abc123" in msg, msg
    assert "conversation not found" in msg, msg
    assert "answer the blocker there (or edit the task file)" in msg, \
        f"exit message must carry the manual-unblock guidance: {msg}"
    print("scenario 19 (blocked by foreign sid → guidance sys.exit): PASS")


def scenario_20_cli_argv_and_resume_routing(repo: Path) -> None:
    """cc-codex argv shapes (schema-checked here, live-fired separately) and
    CliBackend resume routing through logs/sessions.json: claude first turn
    uses --session-id, followups/blocked-resume use --resume; codex resume
    uses the `codex exec resume <sid> <prompt>` subcommand; sid→tool mapping
    wins over role guessing, missing sids warn."""
    orch = new_orch()

    # -- claude argv: first turn names the orchestrator-chosen sid
    cs = o.ClaudeSession(orch, "fable-5", "max", sid="cc-sid-1")
    argv = cs._argv("DO THE TASK")
    assert argv[:2] == ["claude", "-p"], argv
    assert "--session-id" in argv and "cc-sid-1" in argv, argv
    assert "--resume" not in argv, argv
    assert argv[argv.index("--model") + 1] == "fable-5", argv
    assert argv[argv.index("--effort") + 1] == "max", argv
    assert "--dangerously-skip-permissions" in argv, argv
    assert argv[-1] == "DO THE TASK", "prompt must be the last claude arg"
    # followup turns inside the same session switch to --resume
    cs.first_turn = False
    argv = cs._argv("FIX THE TREE")
    assert "--resume" in argv and "--session-id" not in argv, argv
    assert argv[argv.index("--resume") + 1] == "cc-sid-1", argv
    # blocked-resume constructs with resume=True from the first turn
    cs2 = o.ClaudeSession(orch, "fable-5", "max", sid="cc-sid-1",
                          resume=True)
    argv = cs2._argv("HUMAN ANSWERED: use UTC")
    assert "--resume" in argv and "--session-id" not in argv, argv
    assert o._session_map_load().get("cc-sid-1", {}).get("tool") == "claude"

    # -- codex argv: fresh exec vs `exec resume <sid> <prompt>`
    xs = o.CodexSession(orch, "gpt-5.5", "xhigh")
    argv = xs._argv("REVIEW THE DIFF")
    assert argv[:3] == ["codex", "exec", "--json"], argv
    assert argv[argv.index("-m") + 1] == "gpt-5.5", argv
    assert "model_reasoning_effort=xhigh" in argv, argv
    assert argv[argv.index("-s") + 1] == o.CODEX_SANDBOX, argv
    assert "--skip-git-repo-check" in argv, argv
    assert argv[-1] == "REVIEW THE DIFF", argv
    xs.sid = "codex-777"
    xs.first_turn = False
    argv = xs._argv("CONTINUE THE REVIEW")
    assert argv[:5] == ["codex", "exec", "resume", "codex-777",
                        "CONTINUE THE REVIEW"], argv
    assert "--json" in argv and "--skip-git-repo-check" in argv, argv
    assert "model_reasoning_effort=xhigh" in argv, argv
    assert "-s" not in argv, \
        "codex 0.142.5 REJECTS -s on `exec resume` (unexpected argument, " \
        "probed live 2026-07-04) — sandbox must ride the -c override"
    assert f"sandbox_mode={o.CODEX_SANDBOX}" in argv, \
        "resume must carry the sandbox as a -c config override"
    assert "-m" not in argv, \
        "resume inherits the thread's model — argv carries no -m (verify " \
        "live that the resumed thread keeps gpt-5.5)"

    # -- CliBackend.resume_session routing (sessions.json is the truth)
    b = o.CliBackend(orch, "fable-5", "max", "gpt-5.5", "xhigh")
    o._session_map_register("known-cc", "claude", "known-cc")
    o._session_map_register("known-cx", "codex", "known-cx")
    s = b.resume_session("known-cc", "dev")
    assert isinstance(s, o.ClaudeSession) and s.resume \
        and s.sid == "known-cc"
    s = b.resume_session("known-cx", "review")
    assert isinstance(s, o.CodexSession) and not s.first_turn \
        and s.sid == "known-cx", "resume argv shape needs first_turn=False"
    s = b.resume_session("known-cx", "dev")
    assert isinstance(s, o.CodexSession), \
        "sessions.json mapping must win over the role guess"
    s = b.resume_session("mystery-sid", "review")
    assert isinstance(s, o.CodexSession), "unknown sid falls back by role"
    log = orch.log_file.read_text()
    assert "WARNING: sid mystery-sid not in" in log, \
        "role-guess fallback must be logged"
    print("scenario 20 (CLI argv shapes + sessions.json resume routing): "
          "PASS")


def scenario_21_blocked_resume_native_closeout(repo: Path) -> None:
    """Crash path found by inspection 2026-07-04 (same class as the
    run_session seam): a blocked final-gate REVIEWER, resumed with the
    human's answer, concludes pass → completed — on cc-codex its native
    Stop hook then runs the close-out IN-SESSION and archives the task
    before handle_blocked's post-checks parse it. handle_blocked must
    recognize the archive instead of crashing on the vanished file, and
    the loop head must then verify the close-out and exit cleanly."""
    tid = "2026-01-09-blocked-native-close"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: blocked
session-est: 1/1
blockers: [external:does the deferred test item gate completion?]
claimed-by: rev-nc-0001@2026-01-17T00:00:00Z
---

# Blocked gate → native close-out on resume

## Session log

### 2026-01-17 / dev-llll-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none

### 2026-01-17 / rev-nc-0001 / review of dev-llll-0001 / (final_review → blocked)
- Findings: ledger clean except one deferred test item
- Open: does the deferred test item gate completion?
""")
    idx = repo / ".ai-tasks" / "index.md"
    idx.write_text(idx.read_text()
                   + f"| [{tid}]({tid}.md) | blocked | mock |\n")

    asked: list[str] = []

    def fake_ask(banner: str) -> str:
        asked.append(banner)
        return "no — test item is carried by the spawned task; pass it"

    def resume_concludes_and_closes(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "rev-nc-0001"
        t = p.read_text()
        t = o.re.sub(r"^status:.*$", "status: completed", t,
                     flags=o.re.MULTILINE)
        t = o.re.sub(r"^blockers:.*$", "blockers: []", t, flags=o.re.MULTILINE)
        t += "- Verdict: pass\n- Group: dev-llll-0001\n"
        p.write_text(t)
        # native Stop hook chain: in-session ai-sync close-out
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        idx.write_text("\n".join(ln for ln in idx.read_text().splitlines()
                                 if tid not in ln) + "\n")

    FakeAgent.script = [resume_concludes_and_closes]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=False, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = fake_ask
    orch.loop()
    assert asked and "BLOCKED by review session rev-nc-0001" in asked[0]
    log = orch.log_file.read_text()
    assert "the native hook chain ran the close-out inside the resumed " \
           "session" in log, "handle_blocked must recognize the archive"
    assert "close-out done (performed in-session by the native hook" in log
    assert (repo / ".ai-tasks" / "archive" / f"{tid}.md").exists()
    assert not FakeAgent.script
    print("scenario 21 (blocked resume → pass → native close-out "
          "recognized): PASS")


def main() -> None:
    repo = make_repo()
    try:
        patch_module(repo)
        scenario_1_happy_interim(repo)
        scenario_2_dirty_tree_followup(repo)
        scenario_3_budget_escalation(repo)
        scenario_4_blocked(repo)
        scenario_5_event_stream_logging(repo)
        scenario_6_plan_gate(repo)
        scenario_7_cli_event_parsers(repo)
        scenario_8_cursor_effort_selection(repo)
        scenario_9_dispute_escalation(repo)
        scenario_10_context_budget(repo)
        scenario_11_continuation_same_role(repo)
        scenario_12_est_increment_enforced(repo)
        scenario_13_final_gate_loop(repo)
        scenario_14_blocked_review_resume(repo)
        scenario_15_native_closeout_archival(repo)
        scenario_16_advancement_wrapup_review_next(repo)
        scenario_17_final_review_stall(repo)
        scenario_18_native_closeout_incomplete(repo)
        scenario_19_blocked_foreign_sid_exit(repo)
        scenario_20_cli_argv_and_resume_routing(repo)
        scenario_21_blocked_resume_native_closeout(repo)
        print("\nALL MOCK-LOOP SCENARIOS PASSED")
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(repo)


if __name__ == "__main__":
    main()
