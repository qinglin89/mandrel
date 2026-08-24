# Getting started

From a repository that has never seen mandrel to a finished task in the archive.
Setup happens once; the task loop repeats.

This guide uses the manual executor so every session boundary is visible. After
intake, the [unattended scheduler](#running-the-loop-unattended) can carry the
same task state instead.

Complete examples: [greenfield](walkthroughs/greenfield.md) for a new project;
[brownfield](walkthroughs/brownfield.md) for an existing codebase. The exact
rules are in [the annotated lifecycle](lifecycle-annotated.md).

## The 30-second model

**Once per repository**

```text
mandrel deploy  →  commit the receipt  →  /ai-init
```

**Then, for every task (manual executor shown)**

```text
pick a task, or describe one to /intake-task
        │
        ▼
  /invoke dev <task-id>       ← a fresh conversation
        │
        ▼
  /invoke review <task-id>    ← a fresh conversation
        │
        ├─ pass, scope complete  →  done — closeout runs by itself
        ├─ pass, scope remains   →  round again: dev, then review
        └─ changes-requested     →  round again: dev, then review
```

You repeat that pair only while scope remains or a review asks for changes. A
one-session task is one pair — dev, review, finished, with closeout running by
itself in that same review conversation.

Talking to your agent does not change — you still describe what you want in
plain English, push back, and read the diff. What changes is that **you route
the boundaries** and mandrel keeps the bookkeeping between them
([the mechanism behind that boundary](../README.md#what-it-actually-is)). Review
gets its own conversation because its only evidence is the task file, the
project memory, and the actual `git diff`: pasting the dev conversation into it
throws the mechanism away.

## How the steps below are written

| Label | Means |
|---|---|
| `Your action` | something you type or do, in a blockquote |
| `Expected terminal output` / `Expected agent output` | what comes back, literally, in its own fence |
| `Result` | the state you can go and check yourself |
| `What Mandrel did` | the mechanism, explained after you have seen the result |

Examples use Claude Code's slash commands (`/ai-init`, `/intake-task`,
`/invoke`). Cursor and Codex map `/<name>` to "read
`.claude/skills/<name>/SKILL.md` and follow it", so you type the same thing
under all three.

## Before you start

- **Python 3.11+** for the `mandrel` CLI. Your project can be in any language;
  Python is the deployment tool's requirement, not yours.
- **`jq` on `PATH`.** Every hook shells out to it. Without it, session-end
  enforcement degrades — and under **Codex CLI it degrades silently**: no
  context injected, every session end allowed, nothing on screen. Install it
  first; [what each tool does without it](operations.md#hook-prerequisites).
- **Git**, and one of **Claude Code**, **Cursor**, or **Codex CLI**.
- **A `HEAD` by the time you run `/ai-init`.** Deploy needs only that the
  target directory exists, but initialization stamps `git rev-parse HEAD` onto
  every memory document, and review reads `git diff` as its only evidence. So a
  brand-new repository deploys first and makes its first commit immediately
  after — that ordering is deliberate, not incidental.
- **A clean working tree at the start of every session.** Session-end
  bookkeeping is ordered clean-tree-first, and the hook enforces it.

## Step 1: Install the CLI

> **Your action — in a directory separate from your project, once per machine**

```bash
git clone https://github.com/qinglin89/mandrel ~/src/mandrel
cd ~/src/mandrel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

**Result:** `./bin/mandrel` runs from `~/src/mandrel`. Every deploy command
below is issued from that checkout, against your project's path.

## Step 2: Preview the deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy --dry-run ~/src/your-repo
```

**Result:** a preview, and nothing written — no payload, no manifest, no
lockfile, no `.gitignore` edit, no registry entry. Read the `update:` list
before you go further.

⚠ **Warning — deploy overwrites the paths it owns.** Deploy owns `CLAUDE.md`,
`.claude/`, `.cursor/`, `.codex/`, `.ai-protocol/`, and `.mandrel/`. The dry-run
lists yours under `update:`; copy them out of the way first, and if Git already
*tracks* one, follow
[the collision sequence](operations.md#if-git-already-tracks-a-deploy-owned-path)
instead. Project rules you want every session to follow belong in
`.ai/conventions.md`, which is yours and loads into every session anyway.

## Step 3: Deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy ~/src/your-repo
```

**Expected terminal output**

```text
deployed 117 files to /Users/you/src/your-repo
manifest: /Users/you/src/your-repo/.ai-deploy-manifest.json
source revision: 8039354bc29e9beddd271703844cc75c0cda585b
```

**Result:** the payload is on disk and the tree is dirty. There is still no
`.ai/` and no `.ai-tasks/`.

**What Mandrel did:** copied the payload, rendered `CLAUDE.md` and the Codex
config for this target, wrote both receipts, appended a managed `.gitignore`
block, and recorded the repository in your local registry. The file count moves
with the payload.

## Step 4: Commit the receipt

> **Your action — in your repo**

```bash
cd ~/src/your-repo
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"
```

**Result:** one commit — exactly two files in a repository that never tracked a
deploy-owned path. A brand-new repository now has the `HEAD` that `/ai-init`
needs.

> **Your action — confirm the tree is clean**

```bash
git status --porcelain
```

**Result:** no output — the tree is clean, which is where every session starts.

**Deploy creates neither `.ai/` nor `.ai-tasks/`.** They are yours, and
upgrading the protocol never touches them
([the whole layout](../README.md#what-a-managed-repository-looks-like)). The
managed ignore block keeps task files local; `.ai/` is committed like source and
travels with the repository.

## Step 5: Set up your tool, then confirm

| Tool | What you have to do |
|---|---|
| **Claude Code** | Nothing. Imports and hooks are live on the next session. |
| **Cursor** | Nothing. `.cursor/hooks.json` fires on session start. |
| **Codex CLI** | Trust the hooks once — the two actions below. |

⚠ **Warning — untrusted Codex hooks never run, and say nothing about it.** No
context is injected, every session end is allowed, and nothing appears on
screen. Trust is recorded against the script hash, so re-trust after any
redeploy that changes them. `.codex/config.toml` also carries **absolute**
paths, so redeploy if you move or re-clone the repository.

> **Your action — Codex CLI only: start it in your repo**

```bash
codex
```

**Result:** a Codex session in your repository, with the hooks still untrusted.

> **Your action — Codex CLI only: trust the two hook entries**

```text
/hooks
```

**Result:** the session-start and session-end hooks run from here on.

> **Your action — confirm the deployment, from `~/src/mandrel`**

```bash
./bin/mandrel status ~/src/your-repo
```

**Expected terminal output**

```text
/Users/you/src/your-repo: in sync (117 files)
```

## Step 6: Initialize memory

> **Your action — in your repo, with your agent**

```text
/ai-init
```

**Result:** one of two things, depending on what the repository already holds.

| Your repository | What `/ai-init` does | Walkthrough |
|---|---|---|
| **Greenfield** — essentially empty | Interviews you about purpose, users, scope, non-goals, stack, and constraints. **It writes nothing until you answer.** Then `.ai/`, a pool of 10–25 pending tasks, and one commit. | [greenfield.md](walkthroughs/greenfield.md) |
| **Brownfield** — existing code | Derives memory from your code in five passes, then **stops for your sign-off** and names the calls it is least sure about. Nothing is stamped or committed until you sign off. `.ai-tasks/` stays empty by contract. | [brownfield.md](walkthroughs/brownfield.md) |

**What Mandrel did:** classified the repository by what is left after excluding
everything the deploy owns — the
[target-project surface](lifecycle-annotated.md#the-target-project-surface).

Correct a brownfield derivation now, in the same conversation. Every later
session starts from these documents, so a wrong conclusion here is one you keep
paying for.

## Step 7: Pick your first task

**Greenfield:** the pool already exists.

> **Your action — same conversation is fine**

```text
/ctd-tasks
```

**Expected agent output**

```text
pending  | 2026-03-02-cli-skeleton.md  | 0/1 | CLI skeleton and config loading
```

**Result:** nothing changed. Pick a task whose estimate reads `0/1` — a
single-session task is the cleanest first run.

**Brownfield, or when the pool doesn't cover the work:** describe it instead.
Intake needs no fresh conversation.

> **Your action — describe the work in your own words**

```text
/intake-task the public webhook endpoint has no rate limiting — one noisy
integrator can saturate the worker pool. Cap it per API key.
```

**Result:** a draft task file and a proposed index row, in the conversation.
**Nothing is written yet.**

**What Mandrel did:** checked for an overlapping active task, picked the memory
documents the task should preload, estimated the size in sessions, and drafted.

> **Your action — refine the draft, then confirm**

```text
Looks right, create it.
```

**Result:** one new `.ai-tasks/<id>.md` at `session-est: 0/1`, and one new index
row.

## Step 8: The dev turn

> **Your action — open a new conversation and send exactly this, with the id
> from step 7**

```text
/invoke dev <task-id>
```

**Result:** a short summary of what was built, a clean tree, and a task file
whose `## Session log` now has one entry — what was done, what decisions were
made, what remains. That entry is the entire handoff, so read it.

**What Mandrel did:** bound the session to the dev contract; claimed the task
(`pending → in_progress`, `session-est: 0/1 → 1/1`, and stamped the session's
own id); preloaded the memory documents the task named; implemented and
committed the work; appended the session-log entry; and — because the whole
scope is done — set `status: final_review`.

A dev session never writes `completed`. Its whole vocabulary is `in_progress`,
`final_review`, and `blocked`.

## Step 9: The review turn

> **Your action — a new conversation again**

```text
/invoke review <task-id>
```

**Result:** a verdict of `pass` with its reasoning, the task moved from
`.ai-tasks/` to `.ai-tasks/archive/`, and possibly a `chore(.ai): …` commit.

**Expected agent output**

```text
Remaining-task audit: checked 1 active task(s); updated none; unchanged …
```

**What Mandrel did:** reviewed from the task file, the project memory, and the
diff — no transcript — checking each Acceptance bullet against the code.
Entering at `final_review`, a `pass` sets `completed`, and the session-end hook
then turned the same conversation into closeout: absorb what qualifies into
`.ai/`, archive the task file, drop its index row, re-check the other active
tasks, commit.

**Archiving is unconditional; absorption is not.** A fact enters `.ai/` only by
passing three admission tests (derivation cost, stability, leverage), so a task
that taught nothing durable is archived with `.ai/` untouched. That is the
system working.

That is a complete task. The next one starts at step 7, in a fresh conversation,
with no re-derivation.

## When the review asks for changes

Nothing new happens — you run the same pair again. A `changes-requested` verdict
routes the next dev turn to **remediation**.

> **Your action — a new conversation, same command as before**

```text
/invoke dev <task-id>
```

**Result:** the recorded findings are fixed. No scope advances, and the status
does not change — a remediation session's log entry is its entire output.

> **Your action — a new conversation again**

```text
/invoke review <task-id>
```

**Result:** a delta-only re-review — only those findings, plus any regression
the fixes introduced.

Repeat until the verdict is `pass`. Only a `correctness` finding can hold a
finished task back; a `design` or `test` finding is fixed cheaply in place or
carried out as its own new pending task while the review passes.
[Severity, finding groups, and delta-only re-review](lifecycle-annotated.md#every-rule-the-loop-runs-on);
[a worked example](walkthroughs/greenfield.md#step-12-remediation).

## When a task needs more than one session

A task estimated `0/2` or larger works the same way, one slice at a time. Each
dev session ends at a coherent point, hands off through its session-log entry,
and leaves `status: in_progress`; the review after it records findings without
gating anything. Dev, review, dev, review — until a dev session finds the whole
scope done and sets `final_review`, the gate you already know.

What to run next is always derivable from the task file, never from memory.
`/ctd-tasks` shows where everything stands;
[turn selection](lifecycle-annotated.md#every-rule-the-loop-runs-on) is the
complete rule.

## Two different things called "status"

Confusing them is the most common early mistake.

| | `mandrel status <target>` | the task file's `status:` |
|---|---|---|
| Answers | is the deployed payload current? | where is this piece of work? |
| Values | `in sync` plus nine drift states | `pending`, `in_progress`, `final_review`, `completed`, `blocked` |
| Changed by | you, by redeploying | the session, per the transition table |

`canonical changed` after you pull a newer mandrel is the intended signal, not a
problem: redeploy when you want the new protocol revision — it touches neither
your tasks nor your memory. Full drift vocabulary and both receipts:
[operations.md](operations.md#status).

## Running the loop unattended

The scheduler is the other executor for the same lifecycle. It re-derives every
turn from the task file and carries the task until completion or a decision that
requires you. Same runbook, same contracts: after a finished, non-blocked
session, either executor can take the next turn from the file.

> **Your action — bootstrap the scheduler, from `~/src/mandrel`**

```bash
./bin/mandrel deploy --bootstrap-orchestrator ~/src/your-repo
```

**Result:** `.mandrel/orchestrator/` gains a virtualenv and an `.env` that a
later bootstrap never overwrites.

> **Your action — run one task, from your repo**

```bash
cd ~/src/your-repo
.mandrel/orchestrator/.venv/bin/python .mandrel/orchestrator/orchestrator.py <task-id>
```

**Result:** dev and review sessions run in turn until the task reaches
`completed`, or until a decision the scheduler may not make stops it.

⚠ **Warning — the scheduler runs agents with filesystem permission prompts
disabled.** Read `.mandrel/orchestrator/README.md` and the
[Safety section](../README.md#safety) first: use a repository you can
`git reset`, on a machine you control. The manual loop above has no such
requirement — your agent's normal permission prompts apply throughout.

## Where to go next

| | |
|---|---|
| [walkthroughs/greenfield.md](walkthroughs/greenfield.md) | one task from an empty directory to the archive, annotated turn by turn |
| [walkthroughs/brownfield.md](walkthroughs/brownfield.md) | the same, in a repository that already has code |
| [lifecycle-annotated.md](lifecycle-annotated.md) | every lifecycle rule, turn selection, and when a fresh conversation is required |
| [operations.md](operations.md) | every command, flag, drift state, receipt, and lifecycle verb; deploy collisions and hook troubleshooting |
| [README](../README.md) | what the protocol is and why it is shaped this way |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | how context reaches a session across the three agent tools |
| `.ai-protocol/` (in your target) | the contracts themselves: conduct, dev, review, plan, intake, plus the runbook and the task/memory schemas |

Two habits that make the difference early on:

- **Read the session-log entry your agent writes.** It is the whole handoff. If
  it is vague, the next session starts from a worse position — say so and have
  it rewritten before you move on.
- **Let the review find things.** A `changes-requested` verdict on session one
  is the system working, not a setback. The loop exists because the second pair
  of eyes has no memory of writing the code.

If you run this and it goes badly, that is a report worth sending.
