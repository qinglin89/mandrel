# Walkthrough: brownfield

One complete task in a repository that already exists — `invoicing-api`, a
FastAPI service with two years of history, ~60 source files, a test suite, and a
team. Nobody wrote a `.ai/` for it, because it did not exist.

**What this walkthrough shows.** Derivation-driven initialization, an ordinary
English request turning into a task through `/intake-task`, the shortest legal
path through the lifecycle, a non-blocking review finding becoming its own task,
and a closeout whose admission tests reject as much as they accept.

**Starting state.** A working repository with real code, real history, and a
clean tree on a branch you are willing to commit to. Starting a project that
does not exist yet is [the greenfield walkthrough](greenfield.md) instead.

**What you need first.** Python 3.11+, `jq` on `PATH`, Git, and one of Claude
Code, Cursor, or Codex CLI — the full list, and what breaks without each, is in
[Before you start](../getting-started.md#before-you-start).

## How to read this document

Every step is one action followed by what that action produces. Four labels
carry the whole structure:

| Label | Means |
|---|---|
| `Your action` | something you type or do, in a blockquote |
| `Expected terminal output` / `Expected agent output` | what comes back, literally, in its own fence |
| `Result` | the state you can go and check yourself |
| `What Mandrel did` | the mechanism, explained after you have seen the result |

The rules behind the loop — turn selection, severity, the fresh-conversation
requirement — are in [the annotated lifecycle](../lifecycle-annotated.md).
[getting-started.md](../getting-started.md) is the same path in short form.

## Step 1: Confirm a clean tree

> **Your action — in `~/src/invoicing-api`**

```bash
git status --porcelain
```

**Result:** no output. A deploy into a dirty tree mixes the payload with your
own uncommitted work and makes the receipt commit in
[Step 5](#step-5-commit-the-receipt) impossible to isolate.

## Step 2: Install the mandrel CLI

> **Your action — in a separate directory, once per machine**

```bash
git clone https://github.com/qinglin89/mandrel ~/src/mandrel
cd ~/src/mandrel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

**Result:** `./bin/mandrel` runs from `~/src/mandrel`. Every deploy command
below is issued from that checkout, against the target path.

## Step 3: Preview the deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy --dry-run ~/src/invoicing-api
```

**Result:** a preview, and nothing written. **Read the `update:` section this
time** — unlike an empty directory, a repository with history may already own
some of these paths.

⚠ **Warning — deploy overwrites the paths it owns.** Deploy owns `CLAUDE.md`,
`.claude/`, `.cursor/`, `.codex/`, `.ai-protocol/`, and `.mandrel/`. A repo with
its own `CLAUDE.md` sees it listed under `update:`, and the deploy will replace
it. Copy out what you want to keep. Project rules that agents must follow belong
in `.ai/conventions.md`, which you are about to create and which loads into
every session anyway.

> **Your action — check whether Git already tracks any deploy-owned path**

```bash
cd ~/src/invoicing-api
git ls-files -- CLAUDE.md 'ai-coding-*.md' .claude .cursor .codex .ai-protocol .mandrel
```

**Result:** in this repository, no output — nothing collides, and the two-file
receipt commit below applies unchanged. If yours prints anything, follow
[the collision sequence](../operations.md#if-git-already-tracks-a-deploy-owned-path)
instead, or the tracked overwrite stays in the tree and the first session cannot
end.

## Step 4: Deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy ~/src/invoicing-api
```

**Expected terminal output**

```text
deployed 117 files to /Users/you/src/invoicing-api
manifest: /Users/you/src/invoicing-api/.ai-deploy-manifest.json
source revision: 8039354bc29e9beddd271703844cc75c0cda585b
```

**Result:** the payload is on disk. As in the greenfield walkthrough, `.ai/` and
`.ai-tasks/` do not exist yet — deploy did not create them.

**What Mandrel did:** copied the payload, rendered `CLAUDE.md` and the Codex
config for this target, wrote both receipts, appended a managed `.gitignore`
block, and recorded the repository in your machine-local registry.

## Step 5: Commit the receipt

> **Your action — in `~/src/invoicing-api`**

```bash
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"
```

**Result:** one commit on top of the existing history. `HEAD` was already there,
so unlike greenfield this commit is not what makes initialization possible — it
is what gives the repository a portable record of which protocol revision it
runs.

> **Your action — confirm the tree is clean again**

```bash
git status --porcelain
```

**Result:** no output.

## Step 6: Trust the hooks (Codex CLI only)

Claude Code and Cursor need nothing here: imports and hooks are live on the
next session. Codex CLI is the exception.

> **Your action — start Codex in `~/src/invoicing-api`**

```bash
codex
```

**Result:** a Codex session in the repository. The hooks are on disk but not yet
trusted, so no context is injected and no session end is checked.

> **Your action — trust the two hook entries**

```text
/hooks
```

**Result:** the session-start and session-end hooks run from this point on.

⚠ **Warning — untrusted Codex hooks never run, and say nothing about it.** No
context is injected, every session end is allowed, and nothing appears on
screen. Trust is recorded against the script hash, so re-trust after any
redeploy that changes them. `.codex/config.toml` also carries **absolute**
paths, so redeploy if you move or re-clone the repository.

## Step 7: Initialize memory

> **Your action — open your agent in `~/src/invoicing-api` and send exactly
> this**

```text
/ai-init
```

**Expected agent output**

```text
Passes 1-4 complete. Derived .ai/: index, map, overview, architecture, design,
modules, apis, features, conventions. Nothing is stamped or committed yet.

Three calls I would especially like checked:
- overview.md scopes the service as "invoice issuance and delivery", but
  app/dunning/ looks like a second product area rather than part of that.
- architecture.md treats app/workers/ as one layer; it has two distinct queues.
- conventions.md derives the error style from app/api/errors.py. Confirm that is
  the pattern new code should follow, not just the one this code happens to use.
```

**Result:** the documents exist on disk, but nothing is stamped and nothing is
committed. The agent is waiting for your sign-off.

**What Mandrel did:** classified the repository over the
[target-project surface](../lifecycle-annotated.md#the-target-project-surface).
What remains after the exclusions still holds `app/`, `tests/`,
`pyproject.toml`, and a real README — **brownfield**. There is no interview.
Instead the skill ran a five-pass derivation, and every pass read only that
surface, so nothing in `.ai-protocol/` or `.mandrel/orchestrator/` can end up
described in your `.ai/` as though it were your service.

| Pass | Reads | Writes |
|---|---|---|
| 1. Inventory | README, top-level dirs, `pyproject.toml` | `overview.md`, skeleton `architecture.md` |
| 2. Module survey | every module directory, in depth (fanned out in parallel) | `modules.md` |
| 3. Cross-reference | passes 1–2 | `map.md`, `features.md` |
| 4. Conventions sniff | 5–10 representative files — a test, an error path, a typical handler | `conventions.md` |
| 5. Review | — | your sign-off, then the frontmatter stamps |

**Pass 5 is a gate, not a formality.** Every later session starts from these
documents, so a wrong conclusion here is one you keep paying for.

## Step 8: Correct the derivation and sign off

> **Your action — read what it wrote, then correct it in ordinary conversation**

```text
Dunning is in scope — same product, same team. Fix overview.md. Split the
workers layer in architecture.md: invoice-render and delivery-retry have
different failure semantics. The error style is right.
```

**Result:** the drafted documents are revised in place. Nothing is stamped and
nothing is committed yet — corrections are still free at this point.

> **Your action — sign off, explicitly**

```text
Looks right now. Sign off — stamp and commit.
```

**Result:** the snapshot is stamped and committed.

```text
.ai/
  index.md  map.md  overview.md  architecture.md  design.md
  modules.md  apis.md  features.md  conventions.md
.ai-tasks/
  index.md
```

```markdown
---
last-updated: 2026-03-09
---

# Active tasks

(none)
```

**What Mandrel did:** only on that sign-off did it stamp `last-updated:` and
`verified-against: <40-char HEAD SHA>` onto every document and commit
`chore(.ai): initial setup via /ai-init`, containing `.ai/`.

**Brownfield initialization derives no tasks.** `.ai-tasks/index.md` stays
`(none)` by contract; work enters one request at a time through `/intake-task`.

## Step 9: Describe the work to intake

This is where an ordinary English request meets the lifecycle. You do not
hand-write a task file, and intake needs no fresh conversation — the
initialization one is fine.

> **Your action — describe the work in your own words**

```text
/intake-task the public webhook endpoint has no rate limiting — one noisy
integrator can saturate the worker pool. Cap it per API key.
```

**Result:** nothing on disk. **Intake stops here and waits**, showing you the
draft task file it proposes to write:

```markdown
---
id: 2026-03-09-webhook-rate-limit
status: pending
session-est: 0/1
blockers: []
prefetch: [.ai/apis.md, .ai/modules.md]
claimed-by:
---

# Rate-limit the public webhook endpoint

## Goal

Cap request rate per API key on `POST /webhooks/{provider}` so a single
integrator cannot saturate the worker pool, and make the limit observable.

## Scope
- Per-API-key fixed-window counter in the existing Redis client.
- Return 429 with `Retry-After` when the cap is exceeded.
- Configurable cap and window; documented defaults.
- Emit the existing `rate_limited` counter metric.

## Acceptance
- Requests over the cap get 429 with `Retry-After`; requests under it are
  unaffected.
- The limiter is per key, not global; two keys do not interfere.
- Cap and window read from settings; the defaults are documented where the
  other service settings are.
- Tests cover under-limit, over-limit, window rollover, and key isolation.

## Session log
```

**What Mandrel did:** read your request, checked `.ai-tasks/index.md` for an
overlapping active task (if one existed it would propose extending that task
instead of creating a new one), picked 2–5 **lazy** memory docs as `prefetch` —
never the eager ones, which load anyway — and estimated the size in sessions.

## Step 10: Confirm the task draft

Refine the draft in the same conversation first — narrow the scope, add an
acceptance bullet, change the estimate — and confirm only when it is right.

> **Your action — confirm**

```text
Looks right, create it.
```

**Result:** two writes land — `.ai-tasks/2026-03-09-webhook-rate-limit.md`, and
a row in `.ai-tasks/index.md`.

```markdown
# Active tasks

| id | title | status | session-est | blockers |
|---|---|---|---|---|
| 2026-03-09-webhook-rate-limit | Rate-limit the public webhook endpoint | pending | 0/1 | [] |
```

**What Mandrel did:** wrote exactly what you confirmed and nothing else. The
index is target-owned and its exact columns are not fixed by the schema; this is
the shape `/intake-task` writes.

Note the estimate: `0/1`. A single-session task has no `## Session plan` — plans
exist only when the total is greater than one.

## Step 11: Dev session

> **Your action — open a new conversation and send exactly this**

```text
/invoke dev 2026-03-09-webhook-rate-limit
```

**Result:** the claim, two commits, and a status that reaches the gate in one
session.

```diff
-status: pending
+status: in_progress
-session-est: 0/1
+session-est: 1/1
-claimed-by:
+claimed-by: 2b9e7f10-4c85-4a63-9d02-7e1f5a8c3046@2026-03-09T10:02:41Z
```

```text
feat(webhooks): per-key fixed-window rate limit with 429 + Retry-After
test(webhooks): limit, isolation, and window rollover cases
```

```diff
-status: in_progress
+status: final_review
```

```markdown
### 2026-03-09 / 2b9e7f10-4c85-4a63-9d02-7e1f5a8c3046 / (pending → final_review)
- Done: Added `app/limits/window.py` — fixed-window counter keyed
  `rl:{api_key}:{window_start}` on the existing Redis client, applied as a
  FastAPI dependency on the webhook route only. 429 carries `Retry-After` set to
  the seconds left in the window. Cap/window are `WEBHOOK_RATE_CAP` and
  `WEBHOOK_RATE_WINDOW_S`, defaults 120/60. Rejected a token bucket: it needs a
  second Redis round trip for the refill timestamp, and the acceptance criteria
  only ask for a cap. Truth learned: `.ai/apis.md` did not mention that the
  webhook route bypasses the global auth dependency — it authenticates from the
  provider signature instead, which is why the key had to come from the signed
  payload. 11 tests green.
- Next: none — task scope complete.
- Open: none.
```

**What Mandrel did:** no review entries exist, so the predicate certified
**advancement**. The session pre-loaded the `prefetch:` docs — `.ai/apis.md` and
`.ai/modules.md` — on top of the eager set that is already there, implemented,
and committed. Because the whole scope was done in this one session, it set
`final_review`.

That `.ai/` gap recorded in `Done` is exactly right: sessions never edit `.ai/`
mid-task. Noticing a gap is a fact for the log, and closeout decides whether it
belongs in the snapshot.

## Step 12: Final gate

> **Your action — a new conversation**

```text
/invoke review 2026-03-09-webhook-rate-limit
```

**Expected agent output**

```text
The fixed window admits up to 2x the cap across a boundary — 120 requests at
0:59 and 120 more at 1:00. That is a behaviour change, not a defect in this
task's delivered scope, so I would carry it rather than fix it here. Draft:
```

```markdown
---
id: 2026-03-09-webhook-burst-smoothing
status: pending
session-est: 0/1
blockers: []
prefetch: [.ai/apis.md, .ai/design.md]
claimed-by:
---

# Smooth webhook rate across window boundaries

## Goal

Replace the fixed-window webhook limiter with a scheme that does not admit up
to twice the configured cap across a window boundary.

## Scope
- Sliding window or token bucket over the existing Redis client.
- Preserve the current settings names and the 429 + `Retry-After` contract.

## Acceptance
- 120 requests at 0:59 followed by 120 more at 1:00 do not both pass under a cap
  of 120/60s.
- The existing limiter tests still pass unchanged.

## Session log
```

```text
Index row to append:

| 2026-03-09-webhook-burst-smoothing | Smooth webhook rate across window boundaries | pending | 0/1 | [] |

Create it?
```

**Result:** nothing is written, and the review has not recorded its verdict yet.

**What Mandrel did:** entering at `final_review` with one unreviewed dev entry,
this is the final gate — verify dev-completeness, then the whole ledger. Before
it can record a finding as *carried out*, it has to actually create the task,
and creating a task means the intake contract. So it drafted and stopped,
exactly as in [Step 9](#step-9-describe-the-work-to-intake). A review session
gets no exemption from that gate.

## Step 13: Confirm the carried task

> **Your action — confirm**

```text
Yes, create it.
```

**Result:** two writes land —
`.ai-tasks/2026-03-09-webhook-burst-smoothing.md` and its index row. Only now,
with the carried task genuinely on disk, does the review record what it did and
pass.

```markdown
### 2026-03-09 / 9c4d15ab-6e72-4f80-b3a1-08d259e7c4b6 / review of 2b9e7f10-4c85-4a63-9d02-7e1f5a8c3046 / (final_review → completed)
- Verdict: pass
- Group: 2b9e7f10-4c85-4a63-9d02-7e1f5a8c3046
- Findings:
  - design: a fixed window admits up to 2× the cap across a boundary — 120
    requests at 0:59 and 120 more at 1:00. Acceptable against this task's
    acceptance criteria, which specify a cap per window, not a smoothed rate.
    Carried out as pending task 2026-03-09-webhook-burst-smoothing rather than
    fixed here; it is a behaviour change, not a defect in the delivered scope.
  - All four Acceptance bullets verified against the diff and the test run;
    key isolation and rollover are covered. No correctness finding.
```

```diff
-status: final_review
+status: completed
```

**What Mandrel did: this is the severity gate working.** A `design` finding does
not block completion: it gets fixed in place when cheap, or carried out as a new
pending task through the intake contract while the review passes. Only a
`correctness` finding could have held `final_review` back. Had it, the verdict
would be `changes-requested`, the status would stay `final_review`, and the next
turn would be remediation — the loop from
[the greenfield walkthrough](greenfield.md#step-12-remediation), with the status
unchanged throughout.

## Step 14: Closeout

> **Your action — none. Do not close the conversation yet.**

**Result:** two `.ai/` edits, then the archive.

**Expected agent output**

```text
Remaining-task audit: checked 1 active task(s); updated none; unchanged 2026-03-09-webhook-burst-smoothing
```

**What Mandrel did:** the hook fired `/ai-sync-v2` in the same conversation. It
walked the session log and applied the admission tests to each candidate:

| Candidate | Verdict |
|---|---|
| The webhook route authenticates from the provider signature, not the global auth dependency | **admitted** — costs a multi-file read to re-derive, stable, and changes what the next session does on that route |
| Fixed window chosen over token bucket, and why | **admitted** — a decision absent from the code; the code shows the *what*, not the rejected alternative |
| Default cap 120/60s | rejected — a one-line grep in settings |
| The 11 test names | rejected — greppable, and volatile |

Both admitted facts route to `.ai/apis.md` (the auth path) and `.ai/design.md`
(the tradeoff), and the edits are committed. Then, unconditionally: the task file
moves to `.ai-tasks/archive/`, its index row disappears, and the other active
tasks are re-checked.

Had nothing passed admission — a typo fix, a dependency bump, a rename — the
archive and the audit would still have happened and `.ai/` would be untouched.
**Archiving is unconditional; absorption is not.**

## Where the repository ended up

> **Your action — in `~/src/invoicing-api`**

```bash
git log --oneline -5
```

**Expected terminal output**

```text
a1c47f8 chore(.ai): absorb webhook rate-limit findings
5e93b2d test(webhooks): limit, isolation, and window rollover cases
c08fa14 feat(webhooks): per-key fixed-window rate limit with 429 + Retry-After
3d61e97 chore(.ai): initial setup via /ai-init
f27b405 chore: deploy mandrel protocol payload
```

**Result:**

- **2 sessions** for one task: 1 dev, 1 review. That is the floor.
- One task archived, one new pending task created by the reviewer.
- `.ai/` is two facts richer than the code alone can say.

## Where to go next

| | |
|---|---|
| [getting-started.md](../getting-started.md) | the shared path in short form: setup once, then the loop |
| [lifecycle-annotated.md](../lifecycle-annotated.md) | every rule the loop runs on, and when a fresh conversation is required |
| [greenfield.md](greenfield.md) | the same lifecycle from an empty directory, including remediation and re-review |
| [operations.md](../operations.md) | every command, flag, drift state, receipt, and lifecycle verb |
