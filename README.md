# mandrel

A protocol for running AI coding agents on the same codebase for months, not
sessions.

The failure mode of long-horizon AI-assisted development is not model
capability — it's **drift**: context accumulating without curation until
neither you nor the agent can face it. This repository is the canonical source
for a protocol built around one invariant:

> **The working set stays bounded over unbounded project time.**

Day 1 and day 300, a fresh session faces the same *shape* of context: a small
constant set of project invariants, one task file, and a routed handful of
relevant documents. The corpus behind it grows. The slice is bounded by the
task rather than being allowed to grow merely because the project is older.

In use for five months across four repositories — **286 completed tasks**, the
largest project a ~122k-line Go service whose curated snapshot sits at ~34k
words. Background and evidence: **[Context isn't the bottleneck. Drift is.][post]**

[post]: https://qinglin89.github.io/blog/2026/context-isnt-the-bottleneck-drift-is/

---

## What it actually is

Three mechanisms: task-scoped work, selective memory, and deterministic control
outside the model. Markdown contracts plus a small amount of Python and Bash —
no runtime service, no model lock-in.

| | Mechanism | Where |
|---|---|---|
| **Work** | Development sessions are bounded to one effective context window (~200k tokens), with a specified handoff shape when work needs more than one. The task file is ground truth; its lifecycle includes development, review, remediation, handoff, and completion. Review converges through frozen finding groups, delta-only re-review, severity gates, and one-shot dispute escalation. | `canonical/meta/taskfile.md`, `canonical/protocols/review.md` |
| **Memory** | A curated, timeless project snapshot. Initialization creates it; thereafter durable task findings enter at completion closeout, while explicit housekeeping is the separate maintenance path. A fact must pass three admission tests — derivation cost, stability, leverage — to get in. | `canonical/meta/memory.md` |
| **Control** | A deterministic caller re-parses the task file, selects the next legal role, builds the role prompt with its contract and entry checklist, verifies declared outputs, counts convergence budgets, and escalates decisions it may not make. Backend hooks or imports provide eager context; task frontmatter tells the session which additional documents to preload. | `canonical/workflow/runbook.md`, `canonical/orchestrator/README.md` |

Role contracts remain caller-agnostic and self-contained: a session receives
its own contract, not the caller's choreography or the next dispatch. That
boundary enables **two interchangeable executors**: a human running the loop by
hand, and a headless scheduler running it unattended. Same runbook, same
contracts, byte-equivalent delivery. You can take the wheel at any session
boundary and the scheduler picks up where you left off.

What the snapshot holds is two kinds of thing, and both fail "just re-derive
it" for different reasons. **Decisions** are absent from the code — what was
rejected and why, what is deliberately not there. **Conclusions** are present
but unaffordable — the code implies them, but reaching them from zero costs more
than a session has. The second kind compounds: each one becomes a starting
altitude, so later reasoning begins a layer deeper without the working set
getting any larger.

### What lands where after deployment

```text
your-repo/
  .ai/                  project memory — YOURS. Version-controlled. We never own it.
  .ai-tasks/            active work, one file per task. Local, gitignored.
  .ai-protocol/         the deployed contracts — deploy-owned, do not hand-edit
  .claude/ .cursor/ .codex/   per-tool hooks, rules, and workflow skills — deploy-owned
  .mandrel/orchestrator/  the unattended scheduler — deploy-owned
  CLAUDE.md             the loader — deploy-owned, rendered for your repo
  .ai-deploy-lock.json  portable receipt; commit it if you want version audit
```

The split is the point: **the protocol is deployed, your knowledge is yours.**
Upgrading the protocol never touches `.ai/` or `.ai-tasks/`.

---

## Quickstart

Requires Python 3.11+, and Claude Code, Cursor, or Codex CLI as the agent.

```bash
git clone https://github.com/qinglin89/mandrel && cd mandrel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# See exactly what would be written, without writing it
./bin/mandrel deploy --dry-run /path/to/your-repo

# Deploy
./bin/mandrel deploy /path/to/your-repo
```

Then, in your repo, with your agent:

```
/ai-init                  # derive the initial .ai/ snapshot from your codebase
/intake-task              # describe what you want done; creates .ai-tasks/<id>.md
/invoke dev <task-id>     # work the task under the dev contract
/invoke review <task-id>  # review the landed work under the review contract
```

Session-end bookkeeping (clean tree, session log, status) is carried by a stop
hook — you don't invoke it. When a task reaches `completed`, the hook triggers
closeout: absorption into `.ai/`, archive, index reconcile.

Check for drift at any time:

```bash
./bin/mandrel status /path/to/your-repo     # one repo
./bin/mandrel status --all                  # every locally registered repo
```

`status` reports `in sync`, `target modified`, `canonical changed`,
`stale eager import`, `ambiguous memory entrypoint`, `shadowed skill`,
`missing target file`, or `extra deployed file`, and exits nonzero on drift.

### Running the loop unattended

Optional. The scheduler runs `dev → review → dev → …` over one task, pausing for
you on every decision it isn't allowed to make itself. Each role selects its own
agent, model, and effort independently. They may differ, but do not have to:
review independence comes first from a separate fresh-context conversation.
Different models add diversity; using the same agent, model, and subscription
for both roles is fully supported.

```bash
./bin/mandrel deploy --bootstrap-orchestrator /path/to/your-repo

cd /path/to/your-repo
.mandrel/orchestrator/.venv/bin/python .mandrel/orchestrator/orchestrator.py <task-id>
```

Read `.mandrel/orchestrator/README.md` before you do. It runs agents with
permission prompts disabled — see [Safety](#safety).

---

## Evolution: changing the protocol from evidence

The protocol improves itself from batched evidence rather than from opinion.
Completed tasks are evaluated; reports accumulate into an immutable batch; a
batch analysis produces dispositions; admitted changes become candidate
revisions that must survive a replay before a human promotes them.

```bash
./bin/mandrel evolution status                        # lifecycle phase; writes nothing
./bin/mandrel evolution list --feed-dir ./reports     # inspect candidates; writes nothing
./bin/mandrel evolution sync --feed-dir ./reports     # import eligible reports
./bin/mandrel evolution start                         # freeze a batch when policy allows
```

**`--feed-dir` is the public entry point.** The evidence source is pluggable: a
local bundle directory works with no service, no token, and no network. Ours
happens to be a private control plane that publishes evaluation reports; yours
can be a directory of JSON records and their artifact bodies. The lifecycle,
the batching policy, the experiment records, and the promotion gates are all
here and all usable. What is *not* here is report production — the evaluation
that writes those artifacts runs in the private control plane, so a standalone
user brings their own evaluator or writes the artifacts by hand. The layout, a
record you can copy, and what the importer does and does not read are in
`evolution/README.md` § The report source.

The normative contract is `evolution/README.md` — the invariants are the
interesting part: batch evidence rather than anecdotes, import the denominator,
freeze before analysis, pin the runner so a candidate never governs the run
that creates it, no-change is a valid conclusion.

---

## Safety

**The scheduler runs agents with filesystem permission prompts disabled**
(`--dangerously-skip-permissions` for Claude Code, `danger-full-access` for
Codex). This is deliberate and you should understand why before using it.

The safety model is not per-command approval — it's the protocol layer plus
verification after the fact:

- authority tiers in the conduct contract (what may be changed freely, what
  needs confirmation, what is forbidden)
- a headless conduct annex that forbids inline questions — a session that needs
  a human declares `status: blocked` and stops
- post-session checks that verify every declared output, with a bounded number
  of fix round-trips before escalating to you
- review as a separate fresh-context session; it may share the dev model
- a required clean working tree, so every session's changes are a reviewable
  git delta

That is a real safety model for *supervised autonomy on a repository you can
`git reset`*. It is **not** a sandbox. Run it on a repo whose worktree you're
willing to lose, on a machine you control. Interactive use (`/invoke`) has no
such requirement — your agent's normal permission prompts apply.

---

## What this costs

The underlying consumption is tokens. Session count is still useful because
the workflow draws an operating boundary around each one.

- Tasks averaged **3.8 sessions**, development and review combined, most at a
  top-tier model on high reasoning effort.
- A session is expected to wrap at roughly 200k tokens of context. That is a
  handoff policy, not a hard stop — the heaviest single session in the archive
  peaked near 390k. Each session reloads assembled context, occupies a
  rate-limit window, and — after the first — introduces a handoff. The 3.8
  average says how many such working envelopes a task used; it does not measure
  token consumption.
- These agents ran through subscriptions rather than metered APIs, so the
  archive does not support a credible per-task dollar estimate. Another session
  has no itemized price, but it still consumes subscription capacity and
  wall-clock time.
- Every session also writes a log, and every task pays for review and
  absorption. That fixed overhead only amortizes when the project lives long
  enough for preserved decisions to be reused. **If you're building something
  you'll throw away in three weeks, don't use this.**
- For me, the tradeoff has been more work per task and less re-derivation over
  the project's lifetime — including fewer cases where an agent fails to
  recover a deliberate decision and confidently undoes it.

---

## Status

| | |
|---|---|
| Protocol contracts, memory schema, task lifecycle | stable, in daily use |
| Deploy / status / registry / lock / skills | stable |
| Orchestrator (headless loop) | stable; model profiles need periodic updating |
| Evolution lifecycle | implemented and usable; the web console and some polish are in flight |

**Tested with:** Claude Code (Opus 4.8 / Opus 5), Codex CLI (GPT-5.5 / GPT-5.6),
Cursor SDK. Model identifiers live in `canonical/orchestrator/orchestrator.toml`, not
in source. They will go stale; update the profile rather than the code.

**Not in scope:** this repository never owns your `.ai/`, `.ai-tasks/`, product
code, or secrets.

---

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | source of truth → deploy → target layout → three IDE surfaces → what reaches the context window |
| [CHARTER.md](CHARTER.md) | the boundary rules: what protocols / workflow / meta each own, and why |
| [docs/operations.md](docs/operations.md) | full operational reference — every command, flag, drift state, receipt, and lifecycle verb |
| `canonical/workflow/runbook.md` | how sessions chain; the spec both executors read |
| `canonical/orchestrator/README.md` | scheduler implementation notes and configuration |
| `evolution/README.md` | the normative protocol-evolution contract |

## Development

```bash
scripts/check.sh    # full gate: tests, scheduler scenarios, lints, and package build
```

That one entrypoint is what developers, agents, the optional Git hook, and CI
all run; nothing re-lists its steps. Deployable protocol and runtime changes
are authored **only** under `canonical/`. Deployed copies in target repos are
never hand-edited: edit here, redeploy, and use `status` to verify.

## Contributing

Issues and discussion are open, and questions about why something is shaped the
way it is are the most useful thing you can send. **I'm not taking code
contributions yet** — the protocol contracts are still moving, and merging
changes into them before that settles would cost more than it adds. That will
change; when it does, it will say so here.

If you run this and it goes badly, that's a report I want.

## License

[Apache-2.0](LICENSE).
