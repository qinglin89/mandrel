# Walkthrough: greenfield

One complete task in a project that does not exist yet — `linkaudit`, a new
Python CLI that crawls a static site and reports broken links. At the start
there is nothing but a directory you are about to create.

**What this walkthrough shows.** Interview-driven initialization, a generated
task pool, and the full convergence loop: advancement, a `changes-requested`
review, remediation, re-review, a second advancement, the final gate, and
closeout with absorption.

**Starting state.** An empty directory. No Git repository, no mandrel
deployment, no code. Adopting mandrel in a repository that already has content
is [the brownfield walkthrough](brownfield.md) instead.

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

## Step 1: Create the repository

> **Your action — in a terminal, anywhere you keep projects**

```bash
mkdir -p ~/src/linkaudit
cd ~/src/linkaudit
git init
```

**Result:** an empty Git repository with no commits, no `HEAD`, and no files.

## Step 2: Install the mandrel CLI

> **Your action — in a separate directory, once per machine**

```bash
git clone https://github.com/qinglin89/mandrel ~/src/mandrel
cd ~/src/mandrel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

**Result:** `./bin/mandrel` runs from `~/src/mandrel`. Every deploy command
below is issued from that checkout, against the target path.

**What Mandrel did:** nothing to your project yet. Installing the CLI touches
only the mandrel checkout.

## Step 3: Preview the deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy --dry-run ~/src/linkaudit
```

**Expected terminal output**

```text
/Users/you/src/linkaudit: dry-run deploy preview (117 files)
  add: 117, update: 0, unchanged: 0, blocked: 0
  gitignore: add
  manifest: would write .ai-deploy-manifest.json
  lockfile: would write .ai-deploy-lock.json
  registry: would add/update local registry entry
  add:
    - CLAUDE.md
    - .ai-protocol/protocols/conduct.md
    ...
```

**Result:** nothing changed on disk. No payload, no manifest, no lockfile, no
`.gitignore` edit, no registry entry.

**What Mandrel did:** resolved the payload for this target and reported exactly
what a real deploy would write. `update: 0` here means the deploy collides with
nothing — expected in an empty directory.

## Step 4: Deploy

> **Your action — from `~/src/mandrel`**

```bash
./bin/mandrel deploy ~/src/linkaudit
```

**Expected terminal output**

```text
deployed 117 files to /Users/you/src/linkaudit
manifest: /Users/you/src/linkaudit/.ai-deploy-manifest.json
source revision: 8039354bc29e9beddd271703844cc75c0cda585b
```

**Result:** the payload is on disk and the tree is dirty. There is still no
`.ai/` and no `.ai-tasks/` — deploy does not create them.

**What Mandrel did:** copied the payload, rendered `CLAUDE.md` and the Codex
config for this target, wrote both receipts, appended a managed `.gitignore`
block, and recorded the repository in your machine-local registry. The file
count moves with the payload; the shape of the report is what matters.
`source revision: none` would mean the payload is not exactly a committed
canonical revision — ordinary task work still proceeds, but release assessment
cannot place reports from that target.

## Step 5: Commit the receipt

> **Your action — in `~/src/linkaudit`**

```bash
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"
```

**Result:** one commit, and the repository has a `HEAD` for the first time.
Everything else the deploy wrote sits inside the managed ignore block, so in a
repository that never tracked a deploy-owned path this commit is exactly two
files.

> **Your action — confirm the tree is clean**

```bash
git status --porcelain
```

**Result:** no output.

**What Mandrel did:** nothing — this commit is yours. It matters because
initialization stamps `git rev-parse HEAD` onto every memory document, and a
repository created five minutes ago has no `HEAD` until you make one.

## Step 6: Trust the hooks (Codex CLI only)

Claude Code and Cursor need nothing here: imports and hooks are live on the
next session. Codex CLI is the exception.

> **Your action — start Codex in `~/src/linkaudit`**

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

> **Your action — open your agent in `~/src/linkaudit` and send exactly this**

```text
/ai-init
```

**Expected agent output**

```text
Greenfield detected — the target-project surface is empty after excluding
deployed AI infrastructure. I need project context before writing anything:
purpose, users, scope, non-goals, tech stack, major capabilities, external
systems, deployment/runtime expectations, known constraints.
```

**Result:** no files were written. The agent is waiting on you.

**What Mandrel did:** checked that the infrastructure is present, saw that
`.ai/index.md` does not exist, and classified the repository over the
[target-project surface](../lifecycle-annotated.md#the-target-project-surface)
— your repository minus everything the deploy owns. A directory holding nothing
but a mandrel deployment has an empty surface, so this is greenfield, and
greenfield initialization asks before it writes.

## Step 8: Answer the interview

> **Your action — in the same conversation, answer what it asked**

```text
Purpose: a CLI that crawls a built static site (a local directory or a live
base URL) and reports broken internal and external links.
Users: people publishing docs sites in CI.
Scope: crawl, extract links, check them, report; exit nonzero on breakage.
Non-goals: rendering JavaScript, fixing links, hosting anything.
Stack: Python 3.12, httpx, click, pytest.
Capabilities: recursive crawl with a page budget, internal link resolution,
external link checking with concurrency and retries, text and JSON reports,
a config file, CI-friendly exit codes.
External systems: arbitrary HTTP servers only.
Constraints: no browser engine, no persistent state between runs.
```

**Result:** the snapshot and a task pool exist, and one commit has landed.

```text
.ai/
  index.md  map.md  overview.md  architecture.md  design.md
  modules.md  apis.md  features.md  conventions.md
.ai-tasks/
  index.md
  2026-03-02-cli-skeleton.md
  2026-03-02-crawler-core.md
  2026-03-02-external-link-checks.md
  ... 11 more
```

**What Mandrel did:** generated the snapshot from your answers, generated the
task pool, stamped the frontmatter, and committed.

- Every `.ai/` document carries `last-updated:` and
  `verified-against: <40-char HEAD SHA>`. Sections you did not cover are marked
  `<!-- TODO -->` rather than invented.
- The pool is **10–25 tasks** at feature scope. It is a work pool, not a
  schedule — nothing implies an order except real `blockers:`. At least one task
  is unblocked and specific enough to start.
- The commit is `chore(.ai): initial setup via /ai-init` and contains `.ai/`
  only; `.ai-tasks/` is inside the managed gitignore block.

Front-loading all of that context in the first message skips the round trip
entirely — the interview exists for when you do not.

## Step 9: Read the generated pool

> **Your action — same conversation is fine**

```text
/ctd-tasks
```

**Expected agent output**

```text
📋 Pending  (14)
  pending  | 2026-03-02-cli-skeleton.md          | 0/1 | CLI skeleton and config loading
  pending  | 2026-03-02-crawler-core.md          | 0/2 | Crawl a site and extract internal links
  pending  | 2026-03-02-external-link-checks.md  | 0/2 | Check external links concurrently
  ...
active total: 14  (0 archived; use --all to include them)
```

**Result:** nothing changed. `/ctd-tasks` reads the local task files and groups
them by lifecycle status.

**What Mandrel did:** no writes at all. **You do not run `/intake-task` here** —
the pool already covers the system. Intake is for later, when you want work no
existing task covers.

This walkthrough works `2026-03-02-crawler-core`, a two-session task, because it
shows more of the loop than a one-session task does. Here it is exactly as
`/ai-init` wrote it:

```markdown
---
id: 2026-03-02-crawler-core
status: pending
session-est: 0/2
blockers: []
prefetch: [.ai/modules.md, .ai/features.md]
claimed-by:
---

# Crawl a site and extract internal links

## Goal

Walk a site from a base URL, fetch each reachable page within a page budget,
and produce the set of internal links found, so later tasks can check them.

## Scope
- Fetch a page over HTTP with a timeout and a user agent.
- Extract `href`/`src` targets from HTML.
- Resolve relative targets to absolute URLs.
- Breadth-first crawl bounded by a page budget, visiting each URL once.

## Acceptance
- `linkaudit crawl <base-url>` prints every discovered internal URL.
- Off-site URLs are collected but not crawled.
- The page budget is honoured exactly; the crawl is deterministic.
- Unit tests cover extraction, resolution, and the visited set.

## Session plan

### session-1
Scope:
- Fetching, HTML extraction, URL resolution.
Acceptance:
- Extraction and resolution unit-tested against fixture HTML.

### session-2
Scope:
- Breadth-first traversal, visited set, page budget, CLI wiring.
Acceptance:
- End-to-end crawl over a fixture site tree.

## Session log
```

## Step 10: First dev session

> **Your action — open a new conversation and send exactly this**

```text
/invoke dev 2026-03-02-crawler-core
```

**Result:** the task file is claimed before any code is touched.

```diff
-status: pending
+status: in_progress
-session-est: 0/2
+session-est: 1/2
-claimed-by:
+claimed-by: 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77@2026-03-02T09:14:03Z
```

Then two commits land:

```text
feat(crawl): fetch pages and extract links
test(crawl): fixture-based extraction and resolution tests
```

And the session log gains its first entry:

```markdown
### 2026-03-02 / 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77 / (pending → in_progress)
- Done: Added `linkaudit/fetch.py` (httpx client, 10s timeout, custom UA) and
  `linkaudit/extract.py` (href/src extraction, relative→absolute resolution
  against the requested URL). Chose stdlib `html.parser` over an added
  dependency: the fixture set is well-formed and the parser is replaceable
  behind `extract_links()`. 14 unit tests, all green.
- Plan-slice: session-1
- Next: session-2 — breadth-first traversal, visited set, page budget, CLI
  wiring for `linkaudit crawl`.
- Open: none.
```

**What Mandrel did:** `/invoke` checked that the role is legal for the task's
current state, read `.ai-protocol/protocols/dev-advancement.md`, and bound the
session to it. Three things happened in the claim — the status advanced, the dev
session consumed one of the two estimated sessions, and the session stamped its
own id. That id is how the session-end hook finds the task later: under Claude
Code the session reads `$CLAUDE_CODE_SESSION_ID`; under Cursor and Codex the
session-start hook injects it.

The session then ran `preReEst` — comparing the remaining scope against the plan
and re-slicing if `session-1` were too big for one context window — and worked
that one slice. At the end the hook enforced the order **clean tree first, then
the log**. Status stayed `in_progress` because scope remains.

## Step 11: Review returns changes-requested

> **Your action — a new conversation again; do not continue the dev one**

```text
/invoke review 2026-03-02-crawler-core
```

**Result:** a review entry appended to the same task file.

```markdown
### 2026-03-02 / b7d2e845-16fa-4c39-a0d7-2e9143c8b510 / review of 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77 / (in_progress → in_progress)
- Verdict: changes-requested
- Group: 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77
- Findings:
  - correctness: `extract.resolve()` resolves relative hrefs against the
    *requested* URL, not the final URL after redirects. A page fetched at
    `/docs` that redirects to `/docs/v2/` yields `/api` for `href="api"`
    instead of `/docs/v2/api` — every relative link on a redirected page is
    reported broken. `fetch.get()` already has `response.url`; it is discarded.
  - test: no fixture covers a redirect, so the suite passes on the bug.
  - style: `extract_links()` returns a list with duplicates; a set would say
    more about the intent. Optional.
```

**What Mandrel did:** claimed the task (`claimed-by` moves to the review
session's id; `session-est` does **not** move — review sessions do not consume
the estimate), found one work entry with no matching review, and read the actual
diff of that session's commits, located from `git log` or from the entry itself.

Note what the entry carries. `Verdict:` routes the next turn. `Group:` names the
work session that anchors this finding chain and freezes its scope: everything
the re-review may raise is either one of these findings or a regression
introduced by fixing them. Status does not move — an interim review's findings
never gate.

## Step 12: Remediation

> **Your action — a new conversation, same verb as before**

```text
/invoke dev 2026-03-02-crawler-core
```

**Result:** two commits and one more session-log entry.

```text
fix(crawl): resolve relative links against the final response URL
test(crawl): cover a redirected page's relative links
```

```markdown
### 2026-03-02 / c94a03e1-5b6d-4f28-8ad3-71e0c25f9a46 / (in_progress → in_progress)
- Done: `fetch.get()` now returns `(final_url, body)` and `resolve()` takes the
  final URL as its base; confirmed the reported failure with a new redirect
  fixture before fixing. Applied the style suggestion too — `extract_links()`
  returns an ordered dict-backed set, since dedup was already implicit
  downstream.
- Plan-slice: remediation for review group 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77
- Next: session-2 — breadth-first traversal, visited set, page budget, CLI
  wiring.
- Open: none.
```

**What Mandrel did:** applied the mode predicate. The latest verdict is
`changes-requested`, so `/invoke dev` certified **remediation** and said so in
its first reply. Remediation treats findings as claims to verify against the
code, fixes the valid ones correctness-first, and never advances planned scope.

**A remediation session changes no status.** Its session-log entry is its entire
output. Had it run out of context with fixes still outstanding, it would set
frontmatter `fix-set: open`, and the next turn would be another remediation
session rather than a review. Had it judged a finding wrong, it would say so in
`Done` as a dispute rather than silently skipping it, and the reviewer would
rule on it next turn.

## Step 13: Re-review

> **Your action — a new conversation**

```text
/invoke review 2026-03-02-crawler-core
```

**Result:** a `pass` on the frozen finding group.

```markdown
### 2026-03-02 / d0e57b93-2c18-4a6f-b5e9-3f8241a7c602 / review of c94a03e1-5b6d-4f28-8ad3-71e0c25f9a46 / (in_progress → in_progress)
- Verdict: pass
- Group: 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77
- Findings: redirect resolution fixed at the source and covered by
  `test_relative_links_after_redirect`. Style suggestion applied. No
  regressions in the fix diff.
```

**What Mandrel did:** ran a **delta-only** review — whether group
`3f1b9c22-…`'s findings are resolved, and whether the fixes broke anything. It
did not re-open design questions earlier reviews left alone. `pass` releases the
next dev turn to advance scope again.

## Step 14: Second dev session

> **Your action — a new conversation**

```text
/invoke dev 2026-03-02-crawler-core
```

**Result:** the second planned slice lands, and the status reaches the gate.

```diff
-status: in_progress
+status: final_review
-session-est: 1/2
+session-est: 2/2
```

```markdown
### 2026-03-02 / e5c1a768-9d34-4b02-8f17-6a90d3e4b851 / (in_progress → final_review)
- Done: Breadth-first traversal in `linkaudit/crawl.py` with a visited set keyed
  on the normalized URL (scheme+host lowercased, fragment dropped, trailing
  slash preserved — `/a` and `/a/` are different resources). Page budget stops
  the crawl exactly at N fetches, counted at dequeue. Off-site URLs are
  collected and not queued. `linkaudit crawl <base-url>` wired through click.
  32 tests green.
- Plan-slice: session-2
- Next: none — task scope complete.
- Open: none.
```

**What Mandrel did:** the predicate certified **advancement** this time. Two
rules meet here. A dev session sets `final_review` **only** when the whole scope
is complete, and at `final_review` there must be **no open items** — every loose
end is either resolved or has become its own pending task. And a dev session
never writes `completed`, not even now.

## Step 15: Final gate

> **Your action — a new conversation**

```text
/invoke review 2026-03-02-crawler-core
```

**Result:** the verdict that ends the task.

```markdown
### 2026-03-02 / f81460da-3e57-4c9b-a2d6-5b7c08e19f34 / review of e5c1a768-9d34-4b02-8f17-6a90d3e4b851 / (final_review → completed)
- Verdict: pass
- Group: e5c1a768-9d34-4b02-8f17-6a90d3e4b851
- Findings: all four Acceptance bullets verified against the diff and the test
  run. Ledger from groups 3f1b9c22-… and this one carries no unresolved
  correctness finding. URL normalization is deliberate and tested. Budget
  accounting verified at the dequeue boundary.
```

```diff
-status: final_review
+status: completed
```

**What Mandrel did:** entering at `final_review` makes this the **final gate**.
It first checked that the task really is dev-complete — no scope item
unexecuted, no deferral left in the last entry's Next/Open — and then verified
the *whole* accumulated findings ledger from every earlier review, not just the
last session's diff.

## Step 16: Closeout

> **Your action — none. Do not close the conversation yet.**

**Result:** one `.ai/` edit, then the archive.

```diff
--- a/.ai/design.md
+++ b/.ai/design.md
@@ ## Core Principles
+- URL resolution uses the *final* response URL after redirects, never the
+  requested URL; the fetch layer returns both for that reason.
```

**Expected agent output**

```text
Remaining-task audit: checked 13 active task(s); updated 2026-03-02-external-link-checks; unchanged 2026-03-02-cli-skeleton, 2026-03-02-report-formats, 2026-03-02-config-file, …
```

**What Mandrel did:** the session-end hook saw `status: completed` with a clean
tree, blocked the stop, and directed the same conversation into `/ai-sync-v2`.
It walked the task's entire session log, applied the three admission tests, and
absorbed only what passed — here, one fact. Then, always:

- `.ai-tasks/2026-03-02-crawler-core.md` moved to
  `.ai-tasks/archive/2026-03-02-crawler-core.md`.
- Its row left `.ai-tasks/index.md`.
- Every other active task was re-checked: blockers naming this task id removed,
  scope or estimates adjusted where this work changed them, a `blocked` task
  left with no blockers restored to an active status.
- The `.ai/` edit was committed, so the tree ends clean.

That closing line has an exact shape the caller checks. In the manual loop, that
caller is you.

Absorption is the conditional half: a task that produced no durable fact is
archived with `.ai/` untouched.
[The brownfield closeout](brownfield.md#step-14-closeout) shows the admission
tests turning candidates down as well as letting them in.

## Where the repository ended up

> **Your action — in `~/src/linkaudit`**

```bash
git log --oneline
```

**Expected terminal output**

```text
7c2a91f chore(.ai): absorb crawler-core findings
e40b8d3 feat(crawl): breadth-first traversal with page budget
9ab1f57 test(crawl): cover a redirected page's relative links
1d83c04 fix(crawl): resolve relative links against the final response URL
b62e0aa test(crawl): fixture-based extraction and resolution tests
4f19d7e feat(crawl): fetch pages and extract links
2e5c8b1 chore(.ai): initial setup via /ai-init
0a7d3f9 chore: deploy mandrel protocol payload
```

**Result:**

- **6 sessions** for one task: 3 dev, 3 review.
- `.ai-tasks/` holds 13 pending tasks and `.ai-tasks/archive/` holds 1 — both
  local and gitignored.
- `.ai/` gained one line, committed, and it travels with the repository.
- The next task starts at [Step 10](#step-10-first-dev-session) in a fresh
  conversation, with no re-derivation.

## Where to go next

| | |
|---|---|
| [getting-started.md](../getting-started.md) | the shared path in short form: setup once, then the loop |
| [lifecycle-annotated.md](../lifecycle-annotated.md) | every rule the loop runs on, and when a fresh conversation is required |
| [brownfield.md](brownfield.md) | the same lifecycle in a repository that already has code |
| [operations.md](../operations.md) | every command, flag, drift state, receipt, and lifecycle verb |
