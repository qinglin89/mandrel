#!/usr/bin/env python3
"""Offline validation of orchestrator.py loop mechanics with a mocked SDK.

Builds a throwaway git repo that mimics quantx's layout (.ai-tasks/ gitignored,
task file with one unreviewed dev entry) and drives the Orchestrator with a
FakeAgent whose scripted behaviors edit that repo the way a real session
would. No CURSOR_API_KEY needed.

Scenarios (one function each, run in order by main()): loop mechanics
(review dispatch, followups, budgets, blocked resume, close-out — plain and
native-in-session), prompt instantiation (checklists, wrap-up kinds,
remediation lock), event-stream logging, CLI argv/routing shapes, the
escalation paths (§5.4/§5.6/§5.7 of the README), the --control-dir
file channel (question/answer files, malformed/stale handling, stop.flag,
and same-kind discussion rounds), and prompt-template startup validation
(strict render + refusal on missing/mismatched templates).

Run: .venv/bin/python test_loop_mock.py
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
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
    canonical = Path(__file__).resolve().parents[1]
    shutil.copy2(canonical / "repo-root" / "CLAUDE.md", tmp / "CLAUDE.md")
    for rel in (
        "protocols/conduct.md",
        "protocols/dev-advancement.md",
        "protocols/dev-remediation.md",
        "protocols/plan.md",
        "protocols/review.md",
        "meta/taskfile.md",
        "meta/memory.md",
    ):
        dst = tmp / ".ai-protocol" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical / rel, dst)
    hooks = tmp / ".cursor" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(canonical / "cursor" / "hooks" / "session-start.sh",
                 hooks / "session-start.sh")
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


def assistant_text(text: str) -> None:
    from types import SimpleNamespace as NS
    FakeRun.events = [NS(type="assistant", message=NS(content=[
        NS(type="text", text=text)]))]


def patch_module(repo: Path) -> None:
    class FakeOptions:
        def __init__(self, **kw) -> None:
            self.__dict__.update(kw)

    fake_sdk = types.ModuleType("cursor_sdk")
    fake_sdk.AgentOptions = FakeOptions
    fake_sdk.LocalAgentOptions = FakeOptions
    sys.modules.setdefault("cursor_sdk", fake_sdk)

    o.REPO = repo
    o.TASKS_DIR = repo / ".ai-tasks"
    o.ARCHIVE_DIR = repo / ".ai-tasks" / "archive"
    o.INDEX_FILE = repo / ".ai-tasks" / "index.md"
    o.SESSION_START_SH = repo / ".cursor" / "hooks" / "session-start.sh"
    o.REVIEW_RULE = repo / ".ai-protocol" / "protocols" / "review.md"
    o.PLAN_RULE = repo / ".ai-protocol" / "protocols" / "plan.md"
    o.DEV_ADVANCEMENT_RULE = (repo / ".ai-protocol" / "protocols"
                              / "dev-advancement.md")
    o.DEV_REMEDIATION_RULE = (repo / ".ai-protocol" / "protocols"
                              / "dev-remediation.md")
    o.SESSION_MAP = repo / ".ai-tasks" / "sessions.json"
    o.Agent = FakeAgent


def new_orch(**kw) -> o.Orchestrator:
    orch = o.Orchestrator(TASK_ID, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True,
                          max_sessions=kw.pop("max_sessions", 10),
                          plan_gate=kw.pop("plan_gate", False),
                          control_dir=kw.pop("control_dir", None))
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    return orch


def claim(p: Path, agent: "FakeAgent") -> None:
    """What a protocol-conformant session does at claim: set claimed-by to
    its own exact session id (the claim-sid post-check verifies it)."""
    t = p.read_text()
    line = f"claimed-by: {agent.agent_id}@2026-01-05T00:00:00Z"
    if o.re.search(r"^claimed-by:.*$", t, o.re.MULTILINE):
        t = o.re.sub(r"^claimed-by:.*$", line, t, count=1,
                     flags=o.re.MULTILINE)
    else:
        t = t.replace("\n---\n", f"\n{line}\n---\n", 1)
    p.write_text(t)


def set_fix_set(p: Path, value: str | None) -> None:
    """Declare (or clear) the frontmatter fix-set flag like a
    protocol-conformant remediation session."""
    t = p.read_text()
    t = o.re.sub(r"^fix-set:.*\n", "", t, flags=o.re.MULTILINE)
    if value is not None:
        t = t.replace("\nclaimed-by:", f"\nfix-set: {value}\nclaimed-by:", 1)
    p.write_text(t)


def bump_est(p: Path, agent: "FakeAgent | None" = None) -> None:
    """What a protocol-conformant dev session does at claim."""
    if agent is not None:
        claim(p, agent)
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
    text = o.re.sub(r"^claimed-by:.*$",
                    f"claimed-by: {sid}@2026-01-05T00:00:00Z", text,
                    count=1, flags=o.re.MULTILINE)
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
        assert "===== BEGIN .ai-protocol/protocols/review.md =====" in prompt, \
            "review rule missing"
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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "convergence-budget", kind
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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "blocked", kind
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
        bump_est(p, agent)
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
    """--plan-gate: the loop revolves around a plan-report artifact — a
    revision replaces it (restated from `## Goal / Acceptance` on), a
    clarifying round keeps it (`PLAN-REPORT: unchanged` sentinel, pointer
    banner), a malformed reply keeps it with a WARNING — and confirm
    delivers the CURRENT report (never the last turn's raw text) plus the
    human ruling to a fresh formal dev session."""
    from types import SimpleNamespace as NS

    p = repo / ".ai-tasks" / f"{TASK_ID}.md"

    # Clear the pending-review set left by earlier scenarios so the next
    # turn is a dev turn.
    for sid in o.parse_task(p).unreviewed_dev_sids():
        append_review(repo, "rev-cleanup", sid, "pass", sid, "in_progress")
    entries_before = len(o.parse_task(p).entries)

    answers = iter(["does S6 depend on riskpolicy/?",
                    "skip S6 and avoid riskpolicy/",
                    "one more consideration?",
                    "confirm"])
    asked: list[str] = []

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "plan-gate", kind
        return next(answers)

    def reply(text: str) -> None:
        FakeRun.events = [NS(type="assistant", message=NS(content=[
            NS(type="text", text=text)]))]

    report_v1 = (
        "## Goal / Acceptance\nFix finding 1+2, then S4.\n\n"
        "## Confirmed Facts\n- riskpolicy/ is in scope today.\n\n"
        "## Assumptions / Unknowns\nNone identified\n\n"
        "## Work Approach\nPLAN: fix finding 1+2, then S4; touching "
        "riskpolicy/\n\n"
        "## Verification Strategy\nmock suite\n\n"
        "## Risks / Likely Failure Points\nNone identified")
    report_v2 = (
        "## Goal / Acceptance\nFix finding 1+2 only.\n\n"
        "## Confirmed Facts\n- S6 depends only on widget.go.\n\n"
        "## Assumptions / Unknowns\nNone identified\n\n"
        "## Work Approach\nREVISED PLAN: skip S6; touch widget.go only\n\n"
        "## Verification Strategy\nmock suite\n\n"
        "## Risks / Likely Failure Points\nNone identified")

    # Turn 1: planning only — replies with a preamble + the fixed-headings
    # report (rev 1 = extraction from the heading on), touches NOTHING.
    def propose_plan(agent: FakeAgent, prompt: str) -> None:
        assert "PLANNING ONLY" in prompt, "gate instruction missing"
        assert "===== BEGIN .ai-protocol/protocols/plan.md =====" in prompt, \
            "plan rule missing"
        assert "read-only shadow of the next formal dev session" in prompt
        assert "upcoming dev session" in prompt
        assert "normal entry checklist" in prompt
        # bounds/shape substrings arrive via the injected plan.md text
        assert "bounded read-only discovery" in prompt
        assert "`sed`, `ls`, `git show`" in prompt
        assert "run tests/builds, start services" in prompt
        assert "`## Assumptions / Unknowns`" in prompt
        assert "`## Risks / Likely Failure Points`" in prompt
        assert "`None identified`" in prompt
        assert "plan-report rev 1" in prompt, "report-capture contract missing"
        assert "task " + TASK_ID in prompt, "role line missing"
        assert agent.agent_id == "fake-agent-006"
        reply("Preamble: read the task and skimmed the code.\n\n" + report_v1)
        assert not asked, "must not ask before the plan turn ends"

    # Turn 2: a purely clarifying answer keeps the report via the sentinel.
    def answer_unchanged(agent: FakeAgent, prompt: str) -> None:
        assert "PLAN FEEDBACK" in prompt and "does S6 depend" in prompt
        assert "PLANNING ONLY" in prompt, "clarifying turn is still planning"
        assert "PLAN-REPORT: unchanged" in prompt, "reply shapes missing"
        assert "COMPLETE updated plan-report" in prompt
        assert agent.agent_id == "fake-agent-006"
        reply("No: S6 only touches widget.go — nothing new to fold in.\n"
              "PLAN-REPORT: unchanged")

    # Turn 3: real feedback → a change note + the full restated report
    # (rev 2 replaces rev 1 wholesale).
    def revise_plan(agent: FakeAgent, prompt: str) -> None:
        assert "PLAN FEEDBACK" in prompt and "skip S6" in prompt, \
            "feedback must be sent back before execution"
        assert "PLANNING ONLY" in prompt, "revision must still be planning"
        assert "read-only bounds still apply" in prompt
        assert "REPLACES the current plan-report" in prompt
        assert agent.agent_id == "fake-agent-006"
        reply("Change note: dropped S6 and riskpolicy/ per feedback.\n\n"
              + report_v2)

    # Turn 4: a reply in neither shape — warn-and-keep (rev 2 stays).
    def malformed(agent: FakeAgent, prompt: str) -> None:
        assert "PLAN FEEDBACK" in prompt and "one more consideration" in prompt
        assert agent.agent_id == "fake-agent-006"
        reply("Considered: nothing changes.")

    # Turn 5: a fresh formal dev session carries the CURRENT report + ruling.
    def execute(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "fake-agent-007", \
            "formal dev session must be fresh after the planning session"
        assert "APPROVED PLAN GATE" in prompt
        assert "Approved plan-report:" in prompt
        assert "REVISED PLAN: skip S6; touch widget.go only" in prompt, \
            "rev 2 report must be delivered"
        assert "Change note:" not in prompt, \
            "extraction must strip text above the report heading"
        assert "Preamble:" not in prompt
        assert "Considered: nothing changes." not in prompt, \
            "a kept round's raw text must never be delivered"
        assert "PLAN-REPORT" not in prompt, "sentinel must not leak"
        assert "Human ruling:\nconfirm" in prompt, \
            "human ruling must be injected into the formal dev session"
        assert "Your session id is fake-agent-007" in prompt, \
            "entry checklist must use the formal dev sid"
        bump_est(p, agent)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-06 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: executed confirmed plan\n- Next: review\n- Open: none\n"))

    FakeAgent.script = [propose_plan, answer_unchanged, revise_plan,
                        malformed, execute]
    orch = new_orch(plan_gate=True)
    assert orch._plan_gate_confirmed("confirm")
    assert orch._plan_gate_confirmed("  APPROVE  ")
    assert orch._plan_gate_confirmed("确认")
    assert not orch._plan_gate_confirmed("confirm: execute it"), \
        "approval words with trailing text must remain plan feedback"
    assert not orch._plan_gate_confirmed("ok, one more concern"), \
        "feedback after an approval-looking prefix must not be discarded"
    assert not orch._plan_gate_confirmed("confirm!"), \
        "confirmation requires an exact standalone word"
    orch.ask_human = fake_ask
    orch.loop()
    task = o.parse_task(p)
    assert len(asked) == 4, [a[:80] for a in asked]
    assert "PLAN CONFIRMATION" in asked[0], asked
    assert "plan-report rev 1" in asked[0]
    assert "PLAN: fix finding 1+2" in asked[0], \
        "report text must reach the banner"
    assert "still rev 1 from round 1" in asked[1], \
        "unchanged round must point at the current rev"
    assert "widget.go" in asked[1], "the clarifying answer must be shown"
    assert "## Goal / Acceptance" not in asked[1], \
        "pointer banner must not re-attach the report"
    assert "plan-report rev 2" in asked[2]
    assert "Change note:" in asked[2] and "REVISED PLAN: skip S6" in asked[2]
    assert "WARNING" in asked[3] and \
        "keeping plan-report rev 2 from round 3" in asked[3], \
        "malformed reply must warn-and-keep"
    assert task.status == "in_progress", "no status churn from the gate"
    assert len(task.entries) == entries_before + 1, \
        "exactly one session-log entry (no plan entry)"
    assert task.entries[-1].session_id == "fake-agent-007", \
        "plan-gate sid must not become the dev session-log sid"
    assert not FakeAgent.script
    print("scenario 6 (--plan-gate plan-report: revise → unchanged pointer → "
          "warn-and-keep → confirm delivers report): PASS")


def scenario_7_cli_event_parsers(repo: Path) -> None:
    """ClaudeSession/CodexSession JSONL event digestion: [tool]/[text] lines,
    error flagging, codex session-id capture."""
    orch = new_orch()

    cs = o.ClaudeSession.__new__(o.ClaudeSession)
    cs.orch, cs.sid = orch, "cc-sid"
    cs.model, cs.effort = "fable-5", "max"
    chunks: list[str] = []
    assert cs._handle_event(
        {"type": "assistant", "message": {
            "model": "claude-fable-5",
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "content": [
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
    xs.model, xs.effort = "gpt-5.5", "xhigh"
    chunks = []
    xs._handle_event(
        {"type": "session_meta",
         "payload": {"session_id": "codex-123", "cli_version": "0.142.5"}},
        chunks)
    assert xs.sid == "codex-123", "codex sid must be captured from the stream"
    xs._handle_event(
        {"type": "turn_context",
         "payload": {"model": "gpt-5.5", "effort": "xhigh",
                     "sandbox_policy": {"type": "danger-full-access"}}},
        chunks)
    xs._handle_event({"type": "item.completed",
                      "item": {"type": "agent_message",
                               "text": "verdict below"}}, chunks)
    assert chunks == ["verdict below"]
    xs._handle_event(
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {
                "input_tokens": 100, "cached_input_tokens": 90,
                "output_tokens": 5, "reasoning_output_tokens": 3,
                "total_tokens": 108},
            "model_context_window": 258400}}},
        chunks)
    xs._handle_event({"type": "turn.completed",
                      "usage": {"input_tokens": 2_900_000}}, chunks)
    assert xs.context_tokens == 0, \
        "cumulative turn.completed usage must not be used as context"
    assert xs._handle_event(
        {"type": "error",
         "message": "Reconnecting... 2/5 (request timed out)"},
        chunks) is None, "transient codex reconnect must not fail the turn"
    assert xs._handle_event({"type": "error"}, chunks) is None, \
        "bare codex error after reconnect is part of the retry sequence"
    assert xs._handle_event({"type": "turn.failed", "message": "boom"},
                            chunks) == "error"
    assert o._session_map_load().get("codex-123", {}).get("tool") == "codex"

    xs_unknown = o.CodexSession.__new__(o.CodexSession)
    xs_unknown.orch, xs_unknown.sid = orch, "codex-unknown-error"
    xs_unknown.model, xs_unknown.effort = "gpt-5.5", "xhigh"
    assert xs_unknown._handle_event(
        {"type": "error", "message": "model unavailable"}, []) == "error"
    assert xs_unknown._handle_event({"type": "error"}, []) == "error"

    class FakeCodexProc:
        pid = 4242
        returncode = 0

        def __init__(self) -> None:
            self.stdout = iter([
                json.dumps({"type": "thread.started",
                            "thread_id": "codex-turn"}) + "\n",
                json.dumps({"type": "error",
                            "message": "Reconnecting... 3/5 "
                                       "(request timed out)"}) + "\n",
                json.dumps({"type": "item.completed",
                            "item": {"type": "agent_message",
                                     "text": "still finished"}}) + "\n",
                json.dumps({"type": "turn.completed"}) + "\n",
            ])
            self.stderr = types.SimpleNamespace(read=lambda: "")

        def wait(self) -> int:
            return self.returncode

    prev_popen = o.subprocess.Popen
    try:
        o.subprocess.Popen = lambda *_args, **_kw: FakeCodexProc()
        xs_turn = o.CodexSession(orch, "gpt-5.5", "xhigh")
        result = xs_turn.turn("review after transient reconnect")
    finally:
        o.subprocess.Popen = prev_popen
    assert result.status == "finished", \
        "recoverable codex error event plus exit 0 must finish"
    assert result.text == "still finished"

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

    xs2 = o.CodexSession.__new__(o.CodexSession)
    xs2.orch, xs2.sid = orch, "codex-rollout"
    xs2.model, xs2.effort = "gpt-5.5", "xhigh"
    home2 = Path(tempfile.mkdtemp(prefix="codex-home-"))
    day2 = home2 / "sessions" / "2026" / "07" / "04"
    day2.mkdir(parents=True)
    rollout = day2 / "rollout-2026-07-04T00-00-01-codex-rollout.jsonl"
    rollout.write_text("\n".join([
        json.dumps({"type": "thread.started",
                    "thread_id": "codex-rollout"}),
        json.dumps({"type": "turn_context", "payload": {
            "model": "gpt-5.5",
            "effort": "xhigh",
            "sandbox_policy": {"type": "danger-full-access"},
            "collaboration_mode": {"settings": {
                "model": "gpt-5.5",
                "reasoning_effort": "xhigh"}}}}),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 99}}),
    ]))
    prev = _os.environ.get("CODEX_HOME")
    _os.environ["CODEX_HOME"] = str(home2)
    try:
        xs2._update_context()
    finally:
        if prev is None:
            _os.environ.pop("CODEX_HOME", None)
        else:
            _os.environ["CODEX_HOME"] = prev
    assert xs2.context_tokens == rollout.stat().st_size // 4

    log = orch.log_file.read_text()
    assert "[tool] Bash" in log and "[text]" in log
    assert ("claude observed response model=claude-fable-5 "
            "requested_model=fable-5 requested_effort=max") in log
    assert "codex observed context model=gpt-5.5 effort=xhigh" in log
    assert "codex observed context model=gpt-5.5 effort=xhigh" \
        " requested_model=gpt-5.5 requested_effort=xhigh" \
        " sandbox=danger-full-access source=rollout" in log
    assert "[status] error \"Reconnecting... 2/5 (request timed out)\"" in log
    assert ("codex token usage usage=input=100,cached=90,output=5,"
            "reasoning=3,total=108 context_window=258400") in log
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
        claim(p, agent)

    asked: list[str] = []

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "dispute-unresolved", kind
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
    the CONDITIONAL continuation marker (remediation-only)."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    FakeRun.conversation_chars = 1_200_000  # ≈300k tokens > 200k budget

    def dev_violates(agent: FakeAgent, prompt: str) -> None:
        bump_est(p, agent)  # claim-time increment (part of the claim)
        (repo / "wip.txt").write_text("dirty")  # dirty tree, no log entry

    def wrap_up(agent: FakeAgent, prompt: str) -> None:
        assert "Wrap up NOW" in prompt, \
            "over-budget violation must get the wrap-up instruction"
        assert "Do NOT start any new work" in prompt
        assert "ONLY if your remediation fix set is not yet complete" \
            in prompt, "remediation wrap-up must carry the conditional flag"
        assert "fix-set: open" in prompt
        (repo / "wip.txt").unlink()
        p.write_text(p.read_text() + (
            f"\n### 2026-01-08 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: partial fix; wrapped up over context budget\n"
            "- Next: fresh session continues\n"
            "- Open: none\n"))
        set_fix_set(p, "open")

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
    re-review is deferred until the fix set completes. Even with --plan-gate,
    remediation sessions skip the preflight gate because the review findings
    are already the plan."""
    p = repo / ".ai-tasks" / f"{TASK_ID}.md"
    task = o.parse_task(p)
    assert task.fix_set == "open", \
        "scenario 10 must have left an open fix set"
    assert task.review_entries[-1].verdict == "changes-requested", \
        "the flag is honored only with an open remediation"
    assert task.unreviewed_dev_sids(), "split entries are pending review"

    def dev_completes(agent: FakeAgent, prompt: str) -> None:
        assert f"task {TASK_ID}" in prompt, "must be a dev-role prompt"
        assert "REMEDIATION SESSION" in prompt, \
            "continuation of an open fix set is a remediation session"
        assert ("===== BEGIN .ai-protocol/protocols/dev-remediation.md "
                "=====") in prompt, \
            "the caller must inject the remediation contract"
        assert ("===== BEGIN .ai-protocol/protocols/dev-advancement.md "
                "=====") not in prompt, \
            "a remediation prompt must not carry the advancement contract"
        bump_est(p, agent)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-08 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: completed the fix set; ends as one reviewable unit "
            "(narrating `fix-set` in prose must stay inert)\n"
            "- Next: re-review\n- Open: none\n"))
        set_fix_set(p, None)

    FakeAgent.script = [dev_completes]
    orch = new_orch(plan_gate=True)
    orch.ask_human = lambda banner, kind="": (_ for _ in ()).throw(
        AssertionError(f"unexpected escalation ({kind}): {banner[:200]}"))
    orch.loop()
    log = orch.log_file.read_text()
    assert "remediation continuation: resuming dev" in log
    assert "plan-gate start" not in log, \
        "remediation dev sessions should not run the plan gate"
    assert "review session start" not in log, \
        "re-review must be deferred while the fix set is open"
    assert "dev session start" in log
    # fix set now complete (flag back to complete) → next turn is review
    task = o.parse_task(p)
    assert task.fix_set != "open"
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
        assert ("===== BEGIN .ai-protocol/protocols/dev-advancement.md "
                "=====") in prompt, \
            "the caller must inject the advancement contract"
        assert ("===== BEGIN .ai-protocol/protocols/dev-remediation.md "
                "=====") not in prompt, \
            "an advancement prompt must not carry the remediation contract"
        assert "session-est: 6/6 → 7/7" in prompt, \
            "est increment must be instantiated with concrete values"
        assert "POST-SESSION CHECKS" in prompt, "check preview missing"
        assert "ONLY when the whole scope is complete" in prompt, \
            "advancement status menu must be in the preview"
        assert "frontmatter `fix-set` is not set" in prompt, \
            "fix-set-closed rule must be in the advancement preview"
        assert "when present, is exactly `open`" not in prompt, \
            "fix-set-value is remediation-only preview text"
        p.write_text(p.read_text() + (
            f"\n### 2026-01-09 / {agent.agent_id} / "
            "(in_progress → in_progress)\n"
            "- Done: work\n- Next: more\n"
            "- Open: none\n"))
        set_fix_set(p, "open")

    def fixes_all(agent: FakeAgent, prompt: str) -> None:
        assert "session-est not incremented" in prompt, \
            "followup must cite the est violation"
        assert "declared only by a remediation" in prompt, \
            "followup must cite the illegal advancement fix-set flag"
        assert "does not match this session's id" in prompt, \
            "followup must cite the claim-sid violation"
        set_fix_set(p, None)
        bump_est(p, agent)

    FakeAgent.script = [forgets_est, fixes_all]
    orch = new_orch()
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("unexpected escalation"))
    orch.loop()
    assert not FakeAgent.script, "est-fix followup must be consumed"
    print("scenario 12 (est increment + advancement no-marker enforced): "
          "PASS")


def scenario_13_final_gate_loop(repo: Path) -> None:
    """Transition table end-to-end at the final gate: reject keeps
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
        claim(p, agent)

    def remediates(agent: FakeAgent, prompt: str) -> None:
        assert "REMEDIATION SESSION" in prompt
        assert "keep `final_review` UNCHANGED" in prompt, \
            "remediation menu must be instantiated with the entry status"
        bump_est(p, agent)
        p.write_text(p.read_text() + (
            f"\n### 2026-01-10 / {agent.agent_id} / "
            "(final_review → final_review)\n"
            "- Done: fixed the cap check; status untouched per the table\n"
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
        claim(p, agent)

    def closes_out(agent: FakeAgent, prompt: str) -> None:
        assert "/ai-sync-v2" in prompt
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        idx.write_text("\n".join(
            ln for ln in idx.read_text().splitlines() if tid not in ln) + "\n")
        assistant_text("Remaining-task audit: checked active tasks; updated "
                       "none; unchanged all others.")

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
    """A blocked REVIEW session (any session may block — here a final
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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "blocked", kind
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
        bump_est(p, agent)
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
        assistant_text("Remaining-task audit: checked active tasks; updated "
                       "none; unchanged all others.")

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
    continuation marker (one dev session = one reviewable unit), and
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
        bump_est(p, agent)
        (repo / "wip2.txt").write_text("dirty")

    def wrap_up_adv(agent: FakeAgent, prompt: str) -> None:
        assert "Wrap up NOW" in prompt
        assert "your landed slice is a complete reviewable unit" in prompt, \
            "advancement wrap-up must carry the advancement note"
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
    assert task.fix_set != "open", \
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
        claim(p, agent)

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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "final-review-stall", kind
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
        claim(p, agent)

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
        assistant_text("Remaining-task audit: checked active tasks; updated "
                       "none; unchanged all others.")

    asked: list[str] = []

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "closeout-incomplete", kind
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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "blocked", kind
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

    # -- CliBackend session routing (sessions.json is the truth for resume)
    b = o.CliBackend(orch, "claude", "fable-5", "max", "gpt-5.5",
                     "xhigh")
    fresh = b.new_session("dev")
    assert isinstance(fresh, o.ClaudeSession), \
        "cc-codex default dev agent stays Claude Code"
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

    b2 = o.CliBackend(orch, "codex", "gpt-5.5", "xhigh", "gpt-5.5",
                      "xhigh")
    fresh = b2.new_session("dev")
    assert isinstance(fresh, o.CodexSession) \
        and fresh.model == "gpt-5.5" and fresh.effort == "xhigh", \
        "--dev-agent codex must dispatch dev through CodexSession"
    s = b2.resume_session("mystery-dev-sid", "dev")
    assert isinstance(s, o.CodexSession), \
        "unknown dev sid must fall back to the configured dev agent"
    log = orch.log_file.read_text()
    assert "WARNING: sid mystery-sid not in" in log, \
        "role-guess fallback must be logged"
    assert "WARNING: sid mystery-dev-sid not in" in log, \
        "codex dev fallback must be logged"
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

    def fake_ask(banner: str, kind: str = "") -> str:
        asked.append(banner)
        assert kind == "blocked", kind
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
        assistant_text("Remaining-task audit: checked active tasks; updated "
                       "none; unchanged all others.")

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


def scenario_22_closeout_reconciles_remaining_tasks(repo: Path) -> None:
    """Close-out must reconcile remaining active tasks, not only archive the
    completed task. A stale task-id blocker referencing the archived task is a
    close-out violation and is sent back to the close-out session to fix."""
    tid = "2026-01-10-closeout-reconcile"
    dep = "2026-01-10-dependent-task"
    p = repo / ".ai-tasks" / f"{tid}.md"
    dep_path = repo / ".ai-tasks" / f"{dep}.md"
    p.write_text(f"""---
id: {tid}
status: final_review
session-est: 1/1
blockers: []
claimed-by: dev-mmmm-0001@2026-01-18T00:00:00Z
---

# Close-out reconciliation mock

## Session log

### 2026-01-18 / dev-mmmm-0001 / (in_progress → final_review)
- Done: whole scope implemented
- Next: final gate
- Open: none
""")
    dep_path.write_text(f"""---
id: {dep}
status: blocked
session-est: 0/1
blockers: [{tid}]
claimed-by: dep-sid@2026-01-18T00:00:00Z
---

# Dependent task

## Session log

### 2026-01-18 / dep-sid / (pending → blocked)
- Done: blocked on {tid}
- Next: wait for dependency
- Open: dependency not complete
""")
    idx = repo / ".ai-tasks" / "index.md"
    idx.write_text(idx.read_text()
                   + f"| [{tid}]({tid}.md) | final_review | mock |\n"
                   + f"| [{dep}]({dep}.md) | blocked | mock |\n")

    def gate_passes(agent: FakeAgent, prompt: str) -> None:
        t = o.re.sub(r"^status:.*$", "status: completed", p.read_text(),
                     flags=o.re.MULTILINE)
        p.write_text(t + (
            f"\n### 2026-01-18 / {agent.agent_id} / review of "
            "dev-mmmm-0001 / (final_review → completed)\n"
            "- Verdict: pass\n- Group: dev-mmmm-0001\n- Findings: none\n"))
        claim(p, agent)

    def sloppy_closeout(agent: FakeAgent, prompt: str) -> None:
        assert "Remaining-task audit:" in prompt, \
            "close-out prompt must demand the remaining-task audit"
        (repo / ".ai-tasks" / "archive").mkdir(exist_ok=True)
        p.rename(repo / ".ai-tasks" / "archive" / p.name)
        idx.write_text("\n".join(
            ln for ln in idx.read_text().splitlines() if tid not in ln) + "\n")
        assistant_text("Remaining-task audit: checked active tasks; updated "
                       "none; unchanged all others.")

    def fix_dependent(agent: FakeAgent, prompt: str) -> None:
        assert "stale blocker" in prompt and dep_path.name in prompt
        t = dep_path.read_text()
        t = o.re.sub(r"^status:.*$", "status: pending", t,
                     flags=o.re.MULTILINE)
        t = o.re.sub(r"^blockers:.*$", "blockers: []", t,
                     flags=o.re.MULTILINE)
        dep_path.write_text(t)
        idx.write_text(o.re.sub(
            rf"\| \[{dep}\]\({dep}\.md\) \| blocked \|",
            f"| [{dep}]({dep}.md) | pending |",
            idx.read_text()))
        assistant_text(f"Remaining-task audit: checked active tasks; updated "
                       f"{dep}; unchanged all others.")

    FakeAgent.script = [gate_passes, sloppy_closeout, fix_dependent]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=False, max_sessions=10)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = lambda banner: (_ for _ in ()).throw(
        AssertionError("no escalation expected: " + banner[:200]))
    orch.loop()
    log = orch.log_file.read_text()
    assert "close-out violations: stale blocker" in log
    dep_task = o.parse_task(dep_path)
    assert dep_task.status == "pending"
    assert dep_task.blockers == "[]"
    assert not FakeAgent.script
    print("scenario 22 (close-out reconciles remaining active tasks): PASS")


# -- --control-dir file channel (scenarios 23-27) ----------------------------

def control_answer(ctl: Path, seq: int, payload: dict) -> threading.Thread:
    """Background hub stand-in: wait for NNN-question.json to appear, then
    atomically drop NNN-answer.json."""
    def run() -> None:
        q = ctl / f"{seq:03d}-question.json"
        for _ in range(2000):
            if q.exists():
                break
            time.sleep(0.005)
        else:
            raise AssertionError(f"question {seq:03d} never appeared")
        tmp = ctl / f"{seq:03d}-answer.json.tmp"
        tmp.write_text(json.dumps(payload))
        o.os.replace(tmp, ctl / f"{seq:03d}-answer.json")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def scenario_23_control_dir_channel(repo: Path) -> None:
    """--control-dir: the REAL ask_human writes NNN-question.json and
    returns the NNN-answer.json payload instead of touching stdin; the dir
    is created on construction and seq numbering continues across asks."""
    o.CONTROL_POLL_SECONDS = 0.01
    base = Path(tempfile.mkdtemp(prefix="orch-ctl-"))
    ctl = base / "nested" / "run"  # does not exist yet → __init__ must mkdir
    orch = new_orch(control_dir=ctl)
    assert ctl.is_dir(), "control dir must be created at construction"
    assert orch._control_seq == 1

    t = control_answer(ctl, 1, {"seq": 1, "ts": "2026-07-06T00:00:00Z",
                                "responder": "mock-hub",
                                "answer": "  proceed  "})
    got = orch.ask_human("Need a ruling on X", kind="blocked")
    t.join(timeout=10)
    assert not t.is_alive(), "answerer thread must have finished"
    assert got == "proceed", got  # answer is stripped like the stdin path
    q = json.loads((ctl / "001-question.json").read_text())
    assert q["seq"] == 1 and q["kind"] == "blocked", q
    assert q["banner"] == "Need a ruling on X"
    assert q["message"] == q["banner"], "message aliases banner in v1"
    o.dt.datetime.fromisoformat(q["ts"])  # ts must be ISO-8601

    # minimal answer shape ({"answer": ...} only) + seq continuation
    t = control_answer(ctl, 2, {"answer": "second"})
    assert orch.ask_human("Another?", kind="request") == "second"
    t.join(timeout=10)
    q2 = json.loads((ctl / "002-question.json").read_text())
    assert q2["seq"] == 2 and q2["kind"] == "request", q2

    log = orch.log_file.read_text()
    assert "--- HUMAN INPUT NEEDED ---" in log, "banner must stay audited"
    assert "control-dir question 001 written (kind=blocked)" in log
    assert "human answered: 'proceed'" in log
    shutil.rmtree(base)
    print("scenario 23 (control-dir question/answer channel): PASS")


def scenario_24_control_dir_malformed_answers(repo: Path) -> None:
    """Malformed answers are logged (once per distinct error, never
    silently swallowed) and re-read each tick until a valid answer
    appears; extras (seq mismatch, responder) are logged file-only."""
    o.CONTROL_POLL_SECONDS = 0.01
    ctl = Path(tempfile.mkdtemp(prefix="orch-ctl-"))
    orch = new_orch(control_dir=ctl)
    a_tmp, a = ctl / "001-answer.json.tmp", ctl / "001-answer.json"

    def drop(content: str) -> None:  # atomic, like a well-behaved hub
        a_tmp.write_text(content)
        o.os.replace(a_tmp, a)

    def wait_for_log(needle: str) -> None:
        for _ in range(2000):
            if needle in orch.log_file.read_text():
                return
            time.sleep(0.005)
        raise AssertionError(f"log line never appeared: {needle!r}")

    def stage() -> None:
        q = ctl / "001-question.json"
        for _ in range(2000):
            if q.exists():
                break
            time.sleep(0.005)
        else:
            raise AssertionError("question 001 never appeared")
        drop("{ not json")
        wait_for_log("malformed: unreadable JSON")
        drop(json.dumps({"answer": "   "}))
        wait_for_log("missing/empty `answer` string")
        drop(json.dumps({"seq": 7, "answer": "fixed", "responder": "hub"}))

    t = threading.Thread(target=stage, daemon=True)
    t.start()
    got = orch.ask_human("ruling?", kind="dispute-unresolved")
    t.join(timeout=10)
    assert not t.is_alive(), "stage thread must have finished"
    assert got == "fixed", got
    log = orch.log_file.read_text()
    assert log.count("malformed: unreadable JSON") == 1, \
        "same malformed content must be logged once, not every tick"
    assert log.count("missing/empty `answer` string") == 1
    assert "seq field 7 != 1 (filename wins)" in log
    assert "responder: hub" in log
    shutil.rmtree(ctl)
    print("scenario 24 (control-dir malformed answers logged, then healed): "
          "PASS")


def scenario_25_control_dir_stale_files(repo: Path) -> None:
    """A reused control dir: numbering continues after existing files;
    stale question/answer files are neither overwritten nor consumed."""
    o.CONTROL_POLL_SECONDS = 0.01
    ctl = Path(tempfile.mkdtemp(prefix="orch-ctl-"))
    (ctl / "001-question.json").write_text('{"seq": 1}')
    (ctl / "001-answer.json").write_text('{"answer": "stale"}')
    (ctl / "002-question.json").write_text('{"seq": 2}')
    before = {p.name: p.read_text() for p in ctl.iterdir()}

    orch = new_orch(control_dir=ctl)
    assert orch._control_seq == 3, orch._control_seq
    t = control_answer(ctl, 3, {"answer": "fresh"})
    assert orch.ask_human("new run, new question", kind="request") == "fresh"
    t.join(timeout=10)
    for name, text in before.items():
        assert (ctl / name).read_text() == text, f"{name} was modified"
    assert (ctl / "003-question.json").exists()
    shutil.rmtree(ctl)
    print("scenario 25 (control-dir stale files skipped, never touched): "
          "PASS")


def scenario_26_control_dir_stop_answer_and_flag(repo: Path) -> None:
    """'stop' through the file channel exits exactly like stdin 'stop';
    a stop.flag dropped while a question is pending aborts the wait."""
    o.CONTROL_POLL_SECONDS = 0.01
    ctl = Path(tempfile.mkdtemp(prefix="orch-ctl-"))
    orch = new_orch(control_dir=ctl)
    t = control_answer(ctl, 1, {"answer": "stop", "responder": "mock-hub"})
    try:
        orch.ask_human("continue?", kind="run-error")
        raise AssertionError("'stop' answer must sys.exit")
    except SystemExit as e:
        assert str(e.code) == "stopped by human", e.code
    t.join(timeout=10)
    assert "human answered: 'stop'" in orch.log_file.read_text()

    # fresh orchestrator, same dir (001 files present → next seq is 2)
    orch2 = new_orch(control_dir=ctl)

    def drop_flag() -> None:
        q = ctl / "002-question.json"
        for _ in range(2000):
            if q.exists():
                break
            time.sleep(0.005)
        else:
            raise AssertionError("question 002 never appeared")
        (ctl / "stop.flag").write_text("")

    t2 = threading.Thread(target=drop_flag, daemon=True)
    t2.start()
    try:
        orch2.ask_human("second?", kind="blocked")
        raise AssertionError("stop.flag while awaiting an answer must exit")
    except SystemExit as e:
        assert "stopped by control-dir stop.flag while awaiting answer " \
               "002" in str(e.code), e.code
    t2.join(timeout=10)
    assert not t2.is_alive()
    assert ("stop request (stop.flag) while awaiting answer 002"
            in orch2.log_file.read_text())
    shutil.rmtree(ctl)
    print("scenario 26 (control-dir stop: answer 'stop' + stop.flag while "
          "waiting): PASS")


def scenario_27_control_dir_graceful_loop_stop(repo: Path) -> None:
    """stop.flag at the loop top: a pre-existing flag stops before any
    session is dispatched; a flag dropped during session 1 stops at the
    next session boundary (once=False) instead of dispatching session 2."""
    tid = "2026-01-20-ctl-stop"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: in_progress
session-est: 1/2
blockers: []
claimed-by: dev-ctl-0001@2026-01-20T00:00:00Z
---

# Control-dir stop mock

## Session log

### 2026-01-20 / dev-ctl-0001 / (pending → in_progress)
- Done: initial work
- Next: review
- Open: none
""")

    # part 1: flag already present → not even one session
    ctl1 = Path(tempfile.mkdtemp(prefix="orch-ctl-"))
    (ctl1 / "stop.flag").write_text("")
    FakeAgent.script = []
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10,
                          control_dir=ctl1)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch.ask_human = lambda banner, kind="": (_ for _ in ()).throw(
        AssertionError("unexpected escalation: " + banner[:200]))
    orch.loop()
    log = orch.log_file.read_text()
    assert "control-dir enabled:" in log
    assert ("control-dir stop request (stop.flag) — stopping at session "
            "boundary") in log
    assert "session start" not in log, "no session may be dispatched"

    # part 2: flag dropped DURING session 1 → stop before session 2
    ctl2 = Path(tempfile.mkdtemp(prefix="orch-ctl-"))

    def review_and_drop_flag(agent: FakeAgent, prompt: str) -> None:
        t = p.read_text()
        t += (f"\n### 2026-01-20 / {agent.agent_id} / review of "
              "dev-ctl-0001 / (in_progress → in_progress)\n"
              "- Verdict: changes-requested\n- Group: dev-ctl-0001\n"
              "- Findings: correctness: mock finding\n")
        p.write_text(t)
        claim(p, agent)
        (ctl2 / "stop.flag").write_text("")

    FakeAgent.script = [review_and_drop_flag]
    orch2 = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                           api_key=None, once=False, max_sessions=10,
                           control_dir=ctl2)
    orch2.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    orch2.ask_human = lambda banner, kind="": (_ for _ in ()).throw(
        AssertionError("unexpected escalation: " + banner[:200]))
    orch2.loop()
    assert not FakeAgent.script, "session 1 must have run"
    log2 = orch2.log_file.read_text()
    assert "review session end" in log2
    assert ("control-dir stop request (stop.flag) — stopping at session "
            "boundary") in log2
    assert o.parse_task(p).status == "in_progress", \
        "no second session may have advanced the task"
    shutil.rmtree(ctl1)
    shutil.rmtree(ctl2)
    print("scenario 27 (control-dir stop.flag → graceful stop at session "
          "boundary): PASS")


def scenario_28_session_start_error_logged(repo: Path) -> None:
    """A CLI/tool startup failure after the orchestrator log exists must be
    visible in that log, not only in the outer run manager's spawn.out."""
    tid = "2026-01-28-session-start-error"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: pending
session-est: 0/1
blockers: []
claimed-by:
---

# Session start error mock

## Session log
""")

    class FailingSession(o.BackendSession):
        sid = "fake-missing-cli"

        def turn(self, prompt: str) -> o.TurnResult:
            assert "task " + tid in prompt
            raise o.SessionStartError(
                "claude spawn failed: [Errno 2] No such file or directory: "
                "'claude'")

    class FailingBackend:
        injects_protocol = True

        def describe(self, role: str) -> str:
            return "claude:missing@max" if role == "dev" else "codex:mock@max"

        def new_session(self, role: str) -> o.BackendSession:
            assert role == "dev"
            return FailingSession()

        def resume_session(self, sid: str, role: str) -> o.BackendSession:
            raise AssertionError("unexpected resume")

    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10,
                          backend=FailingBackend())
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    try:
        orch.loop()
        raise AssertionError("startup failure must exit")
    except SystemExit as e:
        assert e.code == 1, e.code
    log = orch.log_file.read_text()
    assert "--- dev session start:" in log
    assert "ERROR: claude spawn failed:" in log
    assert "No such file or directory: 'claude'" in log
    print("scenario 28 (session startup error is written to run log): PASS")


def scenario_29_escalation_discussion(repo: Path) -> None:
    """Marked answers iterate through the escalation's own session using
    ordinary same-kind control-dir pairs. A blocked discussion is read-only;
    the later plain answer follows the existing unblock prompt. No-session
    markers are rejected, while plan-gate keeps its own feedback semantics."""
    tid = "2026-01-29-escalation-discussion"
    p = repo / ".ai-tasks" / f"{tid}.md"
    p.write_text(f"""---
id: {tid}
status: blocked
session-est: 1/1
blockers: [external:UTC day or rolling 24h?]
claimed-by: dev-discuss-0001@2026-01-29T00:00:00Z
---

# Discussion-capable blocked escalation

## Session log

### 2026-01-29 / dev-discuss-0001 / (in_progress → blocked)
- Done: boundary logic prepared
- Next: apply the binding reset ruling
- Open: UTC day or rolling 24h?
""")
    before = p.read_text()
    tree_before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, text=True,
        capture_output=True, check=True).stdout
    ctl = Path(tempfile.mkdtemp(prefix="orch-discuss-"))
    o.CONTROL_POLL_SECONDS = 0.01

    def wait_question(seq: int) -> dict:
        q = ctl / f"{seq:03d}-question.json"
        for _ in range(2000):
            if q.exists():
                return json.loads(q.read_text())
            time.sleep(0.005)
        raise AssertionError(f"question {seq:03d} never appeared")

    def answer(seq: int, value: str) -> None:
        tmp = ctl / f"{seq:03d}-answer.json.tmp"
        tmp.write_text(json.dumps({"answer": value}))
        o.os.replace(tmp, ctl / f"{seq:03d}-answer.json")

    def blocked_answers() -> None:
        q1 = wait_question(1)
        assert q1["kind"] == "blocked"
        assert o.DISCUSSION_HINT in q1["banner"]
        answer(1, "？ Does UTC day include leap seconds?")
        q2 = wait_question(2)
        assert q2["kind"] == "blocked", "discussion reuses the same kind"
        assert "The agent replied:" in q2["banner"]
        assert "civil UTC calendar boundary" in q2["banner"]
        assert o.DISCUSSION_HINT in q2["banner"]
        assert p.read_text() == before, \
            "discussion must not modify task/status/session log"
        assert subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, text=True,
            capture_output=True, check=True).stdout == tree_before, \
            "discussion must not modify the working tree"
        answer(2, "UTC day")

    def clarify(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "dev-discuss-0001"
        assert "DISCUSSION TURN ONLY" in prompt
        assert "Human question:\nDoes UTC day include leap seconds?" in prompt
        assert "？" not in prompt, "discussion marker must be stripped"
        assert p.read_text() == before
        assistant_text("Use the civil UTC calendar boundary; leap seconds "
                       "do not create a separate reset rule.")

    def bind(agent: FakeAgent, prompt: str) -> None:
        assert agent.agent_id == "dev-discuss-0001"
        assert "The human answered your blocker: UTC day" in prompt
        assert "DISCUSSION TURN ONLY" not in prompt
        t = o.re.sub(r"^status:.*$", "status: in_progress", p.read_text(),
                     flags=o.re.MULTILINE)
        t = o.re.sub(r"^blockers:.*$", "blockers: []", t,
                     flags=o.re.MULTILINE)
        p.write_text(t)
        assistant_text("Binding ruling applied.")

    FakeAgent.script = [clarify, bind]
    orch = o.Orchestrator(tid, "mock-dev-model", "mock-review-model",
                          api_key=None, once=True, max_sessions=10,
                          control_dir=ctl)
    orch.log_file = Path(tempfile.mkstemp(suffix=".log")[1])
    t = threading.Thread(target=blocked_answers, daemon=True)
    t.start()
    orch.handle_blocked()
    t.join(timeout=10)
    assert not t.is_alive()
    assert o.parse_task(p).status == "in_progress"
    assert not FakeAgent.script

    # The parser accepts all decided marker forms and strips only the marker.
    assert orch._discussion_text("? why") == "why"
    assert orch._discussion_text("？为什么") == "为什么"
    assert orch._discussion_text("DiScUsS: why") == "why"
    assert orch._discussion_text("plain") is None

    def no_session_answers() -> None:
        q3 = wait_question(3)
        assert q3["kind"] == "closeout-incomplete"
        assert o.DISCUSSION_HINT not in q3["banner"]
        answer(3, "discuss: can the agent explain?")
        q4 = wait_question(4)
        assert q4["kind"] == q3["kind"]
        assert "Discussion not available on this escalation" in q4["banner"]
        answer(4, "done")

    t2 = threading.Thread(target=no_session_answers, daemon=True)
    t2.start()
    assert orch.ask_human("Manual-only close-out",
                          kind="closeout-incomplete") == "done"
    t2.join(timeout=10)
    assert not t2.is_alive()

    # Plan-gate's existing rule remains: every non-confirm answer, including
    # marker-looking text, is returned as feedback to its planning loop.
    t3 = control_answer(ctl, 5, {"answer": "? revise the verification plan"})
    assert orch.ask_human("Plan report", kind="plan-gate") == \
        "? revise the verification plan"
    t3.join(timeout=10)
    assert not (ctl / "006-question.json").exists()
    shutil.rmtree(ctl)
    print("scenario 29 (marked escalation discussion + no-session rejection "
          "+ plan-gate unchanged): PASS")


def scenario_30_prompt_template_validation(repo: Path) -> None:
    """P2 startup gate: every prompt/banner text loads from a single-source
    template file (prompts/ + postcheck-contract.md). A missing template, an
    undeclared placeholder, an orphan template file, or a postcheck contract
    whose check-ids don't map 1:1 onto the code-side checks refuses startup
    (prompts_error, wired into main() like the effort allowlist) — never a
    silent fallback. Rendering is equally strict about placeholder values."""
    assert o.prompts_error() is None, "canonical prompts/ must validate"

    # strict render: a missing or extra placeholder value is a hard error
    try:
        o.render_prompt("entry/dev-invocation", task_id="t")
        raise AssertionError("missing placeholder value must raise")
    except RuntimeError as err:
        assert "sid_line" in str(err)
    try:
        o.render_prompt("midflight/clean-howto", bogus="x")
        raise AssertionError("undeclared placeholder value must raise")
    except RuntimeError as err:
        assert "bogus" in str(err)
    try:
        o.render_prompt("no/such-template")
        raise AssertionError("unknown template name must raise")
    except KeyError:
        pass

    # break a COPY of the canonical prompts dir and point the module at it
    broken = Path(tempfile.mkdtemp(prefix="orch-prompts-")) / "prompts"
    shutil.copytree(o.PROMPTS_DIR, broken)
    (broken / "midflight" / "wrapup.md").unlink()
    (broken / "entry" / "sid-line.md").write_text(
        "Your session id is {{sid}} ({{typo}}).\n")
    (broken / "entry" / "not-in-manifest.md").write_text("orphan\n")
    contract = broken / "postcheck-contract.md"
    contract.write_text(contract.read_text().replace(
        "## tree-clean", "## tree-cleen"))  # one unknown id + one missing id
    prev_dir, prev_contract = o.PROMPTS_DIR, o.POSTCHECK_CONTRACT
    o.PROMPTS_DIR = broken
    o.POSTCHECK_CONTRACT = broken / "postcheck-contract.md"
    try:
        err = o.prompts_error()
        assert err and "refusing to start" in err, err
        assert "missing template: midflight/wrapup" in err, err
        assert "entry/sid-line: unknown placeholder {{typo}}" in err, err
        assert ("template file not in the code manifest: "
                "entry/not-in-manifest") in err, err
        assert "id `tree-cleen` has no code-side check" in err, err
        assert ("no requirement line for code-side check `tree-clean`"
                in err), err
        try:
            o.contract_line("tree-clean")
            raise AssertionError("missing contract line must raise at use")
        except RuntimeError as err2:
            assert "tree-clean" in str(err2)
    finally:
        o.PROMPTS_DIR, o.POSTCHECK_CONTRACT = prev_dir, prev_contract
        shutil.rmtree(broken.parent)
    assert o.prompts_error() is None, "restored canonical dir must validate"
    print("scenario 30 (prompt templates: strict render + startup refusal "
          "on missing/mismatched templates): PASS")


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
        scenario_22_closeout_reconciles_remaining_tasks(repo)
        scenario_23_control_dir_channel(repo)
        scenario_24_control_dir_malformed_answers(repo)
        scenario_25_control_dir_stale_files(repo)
        scenario_26_control_dir_stop_answer_and_flag(repo)
        scenario_27_control_dir_graceful_loop_stop(repo)
        scenario_28_session_start_error_logged(repo)
        scenario_29_escalation_discussion(repo)
        scenario_30_prompt_template_validation(repo)
        print("\nALL MOCK-LOOP SCENARIOS PASSED")
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(repo)


if __name__ == "__main__":
    main()
