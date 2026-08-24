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

## What it actually is

Three mechanisms: task-scoped work, selective memory, and deterministic control
outside the model. Markdown contracts plus a small amount of Python and Bash —
no runtime service, no model lock-in.

| | Mechanism | Where |
|---|---|---|
| **Work** | One task file is ground truth across development, review, remediation, handoff, and completion. Sessions stay bounded; review converges through explicit findings and gates. | `canonical/meta/taskfile.md`, `canonical/protocols/review.md` |
| **Memory** | A curated, timeless project snapshot. Initialization creates it; durable findings enter only at completion when they pass derivation-cost, stability, and leverage tests. | `canonical/meta/memory.md` |
| **Control** | A deterministic caller reads task state, selects the next legal role, assembles its context, verifies outputs, and escalates decisions it may not make. | `canonical/workflow/runbook.md`, `canonical/orchestrator/README.md` |

Role contracts remain caller-agnostic and self-contained: a session receives
its own contract, not the caller's choreography or the next dispatch. That
boundary enables **two interchangeable executors**: the scheduler can carry a
task through the loop unattended, or you can invoke a role manually when you
want to inspect or control a session boundary. They deliver the same contracts
and share the same task state, so you can switch between them after a finished,
non-blocked session.

The snapshot preserves both **decisions** that are absent from the code — what
was rejected and why — and **conclusions** that the code implies but that are
too expensive to re-derive every session. Later reasoning starts from those
results without making the working set grow with project age.

### What a managed repository looks like

```text
your-repo/
  .ai-protocol/         the deployed contracts — deploy-owned, do not hand-edit
  .claude/ .cursor/ .codex/   per-tool hooks, rules, and workflow skills — deploy-owned
  .mandrel/orchestrator/  the unattended scheduler — deploy-owned
  CLAUDE.md             the loader — deploy-owned, rendered for your repo
  .ai-deploy-lock.json  portable receipt; commit it if you want version audit

  .ai/                  project memory — YOURS. Version-controlled. We never own it.
  .ai-tasks/            active work, one file per task. Yours. Local, gitignored.
```

**Deploy owns the top block.** It also maintains a gitignored status manifest,
a managed `.gitignore` block, and a local registry entry. `.ai/` and
`.ai-tasks/` do not exist until `/ai-init` creates them, and upgrading the
protocol later never touches either one. The split is the point: **the protocol
is deployed, your knowledge is yours.**

## Quickstart

Requires Python 3.11+ for the CLI, Git, `jq`, and Claude Code, Cursor, or Codex
CLI as the agent. The unattended bootstrap uses `python3.14` by default; another
executable can be selected with `--orchestrator-python`. Hooks shell out to
`jq`; without it, Codex loses hook enforcement silently. See the
[per-tool prerequisites](docs/operations.md#hook-prerequisites). Start from a
clean working tree.

### 1. Install and deploy

```bash
git clone https://github.com/qinglin89/mandrel && cd mandrel       # Clone Mandrel and enter its checkout.
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'         # Install Mandrel and its verification tools in a local virtualenv.
./bin/mandrel deploy --dry-run /path/to/your-repo                  # Preview target changes without writing anything.
./bin/mandrel deploy /path/to/your-repo                            # Deploy the protocol payload and receipts to the target.
```

The shell commands below use `./bin/mandrel` from this checkout.

Read the dry-run `update:` list first: deploy overwrites the paths it owns. If
Git already tracks one, follow the
[collision sequence](docs/operations.md#if-git-already-tracks-a-deploy-owned-path).
Then commit the two portable files; this also gives a new repository the
`git rev-parse HEAD` baseline that initialization and review require:

```bash
git -C /path/to/your-repo add .gitignore .ai-deploy-lock.json
git -C /path/to/your-repo commit -m "chore: deploy mandrel protocol payload"
```

Codex CLI only: start `codex` in the repository and use `/hooks` to trust both
entries. Re-trust after a redeploy changes their script hashes. Claude Code and
Cursor require no extra step.

From this checkout, `./bin/mandrel status /path/to/your-repo` checks the
deployed copy for drift. The full deploy, upgrade, and status reference is
[docs/operations.md](docs/operations.md).

### 2. Initialize

In the target repository, ask the agent to create the project memory:

```text
/ai-init
```

Initialization adapts to what the repository already contains:

- **New project:** it interviews you, creates `.ai/`, and proposes an initial
  task pool.
- **Existing codebase:** it derives `.ai/` from the repository, then asks you to
  correct and approve it.

Mandrel never owns `.ai/` or `.ai-tasks/`; deploy and upgrade do not overwrite
them.

### 3. Intake a task

A new project can choose from its generated pool with `/ctd-tasks`. Otherwise,
describe the work in the conversation you already have:

```text
/intake-task <what you want, in plain English>
```

Intake drafts `.ai-tasks/<id>.md` and its index row, then **waits for your
confirmation** before writing either one.

### 4. Run the task

The same task state determines the next legal turn in both paths; the difference
is whether the scheduler dispatches it or you invoke it. Both paths run the
same lifecycle. You can switch after a finished, non-blocked session.

**Unattended.** Bootstrap the repo-local scheduler once, then give it the task
ID:

```bash
./bin/mandrel deploy --bootstrap-orchestrator /path/to/your-repo
cd /path/to/your-repo
.mandrel/orchestrator/.venv/bin/python .mandrel/orchestrator/orchestrator.py <task-id>
```

It runs `dev → review → remediation → …` in fresh sessions and pauses only
when it reaches a decision it is not allowed to make. Each role may use a
different agent or model, but fresh context provides the review boundary even
when they are the same.

⚠ **Read `.mandrel/orchestrator/README.md` and [Safety](#safety) first.** The
scheduler runs agents with permission prompts disabled.

For a visual control plane over the same scheduler, see
[orch-hub](https://qinglin89.github.io/projects/orch-hub/). Mandrel remains
complete without it.

**Manual.** When you want to inspect or control each session boundary, invoke
the selected role in a fresh conversation:

```text
/invoke dev <task-id>
```

```text
/invoke review <task-id>
```

These are manual entry points into the same state machine, not extra stages the
unattended path omits. Task state determines whether the next turn is another
development session, review, remediation, or closeout.

### 5. Closeout

Both executors use the same automatic closeout. A stop hook carries session
bookkeeping; when review completes a task, qualifying findings are absorbed
into `.ai/`, the task is archived, and its active index row disappears. You do
not invoke closeout separately.

**Complete end-to-end examples:** follow the
[greenfield](docs/walkthroughs/greenfield.md) or
[brownfield](docs/walkthroughs/brownfield.md) walkthrough.

For detailed setup and a boundary-by-boundary manual run, see
[docs/getting-started.md](docs/getting-started.md).

For exact turn selection and fresh-conversation rules, see
[docs/lifecycle-annotated.md](docs/lifecycle-annotated.md).

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

## Evolution: changing the protocol from evidence

Mandrel can evolve its own protocol from batches of completed-task evidence
rather than individual anecdotes. Reports accumulate into an immutable batch;
analysis produces dispositions; admitted changes become candidate revisions
that must survive replay before a human promotes them. No change is a valid
outcome.

The evidence source is pluggable: `--feed-dir` accepts a local bundle with no
service, token, or network. This repository contains the lifecycle, schemas,
batching policy, experiment records, and promotion gates; report production is
external. The normative contract and a copyable report layout are in
[evolution/README.md](evolution/README.md).

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

## Documentation

| | |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | detailed setup plus a boundary-by-boundary first task using the manual executor |
| [docs/walkthroughs/greenfield.md](docs/walkthroughs/greenfield.md) | one task carried end to end in a brand-new project |
| [docs/walkthroughs/brownfield.md](docs/walkthroughs/brownfield.md) | one task carried end to end in a codebase that already exists |
| [docs/lifecycle-annotated.md](docs/lifecycle-annotated.md) | the reference layer — every lifecycle rule, turn selection, and the classification list |
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
