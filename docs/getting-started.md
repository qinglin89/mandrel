# Getting started

Two complete walkthroughs, each from a repository that has never seen mandrel
through to a finished task sitting in the archive. Every command, every message
you send the agent, and every file that changes is spelled out.

Both walkthroughs run the **manual loop**: you are the scheduler and your agent
does the work. The unattended scheduler runs the identical loop and is covered
at the end.

Pick the one that matches your repository:

- **[Walkthrough A: greenfield](#walkthrough-a-greenfield)** — a new, essentially
  empty project. Initialization interviews you and generates a starting pool of
  pending tasks.
- **[Walkthrough B: brownfield](#walkthrough-b-brownfield)** — an existing
  codebase. Initialization reads your code and leaves the task list empty;
  tasks arrive one at a time through intake.

The two differ only in how project memory and the first task come into
existence. From the first `/invoke dev` onwards they are the same loop.

Contents:

- [Before you start](#before-you-start)
- [Step 0: deploy the protocol into your repo](#step-0-deploy-the-protocol-into-your-repo)
- [The lifecycle in one page](#the-lifecycle-in-one-page)
- [Walkthrough A: greenfield](#walkthrough-a-greenfield)
- [Walkthrough B: brownfield](#walkthrough-b-brownfield)
- [Two different things called "status"](#two-different-things-called-status)
- [Running the loop unattended](#running-the-loop-unattended)
- [Where to go next](#where-to-go-next)

This guide links the normative documents rather than restating them. When the
guide and a contract disagree, the contract wins.

---

## Before you start

**On your machine:**

- **Python 3.11+** for the `mandrel` CLI. Your project can be in any language —
  Python is only the deployment tool's requirement.
- **`jq`** on `PATH`. Every hook shells out to it, and the three tools degrade
  differently without it:

  | Tool | Without `jq` |
  |---|---|
  | **Claude Code** | The stop hook reads its input through an unguarded `jq` under `set -e`, so it exits with an error on every session end and the clean-tree / session-log discipline stops being enforced. Loud, at least. Eager context is unaffected — it arrives through `CLAUDE.md` imports, and the session-start hook falls back to `python3`. |
  | **Cursor** | Same unguarded stop-hook read, same erroring session end. Session-start injection *is* guarded, so it silently emits no context. |
  | **Codex CLI** | Silent all the way through, which makes it the worst case: both hooks guard every `jq` call with `\|\| true`, so session start injects no context at all and the stop hook — seeing an empty session id — fails open and allows every session end. Nothing on screen tells you enforcement is gone. |

  Install it first. A missing `jq` is not a failure you will notice in time.
- **Git**, and one of **Claude Code**, **Cursor**, or **Codex CLI** as the agent.

**In the repository you want managed:**

- **`mandrel deploy` needs only that the directory exists.** Not a Git
  repository, not a commit — walkthrough A deploys into a repo that was
  `git init`-ed seconds earlier and has no `HEAD` at all, then makes its first
  commit afterwards.
- **By the time you run `/ai-init`, the repository needs a `HEAD`.** Three
  separate parts of the protocol depend on one: initialization stamps every
  memory document with `git rev-parse HEAD`, a review session's *only* evidence
  is `git log` and `git diff`, and every session must end with
  `git status --porcelain` empty. So the deploy comes first and the first commit
  comes right after it — that ordering is deliberate, not incidental.
- Start every session from a **clean working tree**. Session-end bookkeeping is
  ordered clean-tree-first, and the hook enforces it: a session that wrote its
  session-log entry, or set `completed`, while the tree is still dirty is
  blocked from ending until the tree is resolved.

The walkthroughs use Claude Code's slash commands (`/ai-init`, `/intake-task`,
`/invoke`). Cursor and Codex reach the same skills by path — Codex's
session-start injection and Cursor's always-applied rule each map `/<name>` to
"read `.claude/skills/<name>/SKILL.md` and follow it" — so you type the same
thing under all three.

---

## Step 0: deploy the protocol into your repo

Both walkthroughs begin here. Clone this repository once; it is the source you
deploy *from*, and it is not where your project lives.

```bash
git clone https://github.com/qinglin89/mandrel ~/src/mandrel
cd ~/src/mandrel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

### Preview first

`--dry-run` writes nothing at all — no payload, no manifest, no lockfile, no
`.gitignore` edit, no registry entry:

```bash
./bin/mandrel deploy --dry-run ~/src/your-repo
```

```text
/Users/you/src/your-repo: dry-run deploy preview (117 files)
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

The file count tracks the canonical payload and moves as the payload grows;
the shape of the report is what matters.

**Read the `update:` list before deploying into an existing repository.** Deploy
owns `CLAUDE.md`, `.claude/`, `.cursor/`, `.codex/`, `.ai-protocol/`, and
`.mandrel/`, and it overwrites what it owns. If you already have a `CLAUDE.md`
or a `.claude/settings.json`, the dry-run lists them as `update` and the real
deploy replaces them. [Handling collisions](#if-git-already-tracks-a-deploy-owned-path)
below is the exact sequence. Project rules you want every session to follow
belong in `.ai/conventions.md`, which is yours and is loaded into every session
anyway.

### Deploy

```bash
./bin/mandrel deploy ~/src/your-repo
```

```text
deployed 117 files to /Users/you/src/your-repo
manifest: /Users/you/src/your-repo/.ai-deploy-manifest.json
source revision: 8039354bc29e9beddd271703844cc75c0cda585b
```

`source revision` is the canonical commit this payload came from. If it says
`none`, your mandrel clone had uncommitted changes under `canonical/` — harmless
for ordinary use, and only relevant if you later run the evolution lifecycle.

### What just landed — and what did not

```text
your-repo/
  CLAUDE.md                  deploy-owned  the loader
  .ai-protocol/              deploy-owned  contracts, schemas, runbook
  .claude/ .cursor/ .codex/  deploy-owned  hooks, rules, and the workflow skills
  .mandrel/orchestrator/     deploy-owned  the unattended scheduler
  .ai-deploy-manifest.json   deploy-owned  local receipt, gitignored
  .ai-deploy-lock.json       deploy-owned  portable receipt, committable
  .gitignore                 a managed block appended to whatever was there
```

**Deploy does not create `.ai/` or `.ai-tasks/`.** They are yours, not the
protocol's, and nothing exists in them until you run `/ai-init` in the next
step. Upgrading the protocol later never touches either one.

The managed `.gitignore` block ignores the whole deployed payload plus
`.ai-tasks/`, and explicitly un-ignores `.ai-deploy-lock.json`. Two consequences
worth internalizing now:

- **Your task files are local.** `.ai-tasks/` is gitignored, so task files never
  reach a remote and are never shared by a `git push`.
- **Your memory is version-controlled.** `.ai/` is *not* ignored: you commit it
  like source, and it travels with the repository.

### Commit the receipt

`/ai-init` needs a `HEAD` to stamp, so this is where a brand-new repository gets
its first commit — and where an existing one records which protocol revision it
is on:

```bash
cd ~/src/your-repo
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"

git status --porcelain      # expect no output
```

Everything else the deploy wrote is inside the ignored block, so in a repository
that never tracked a deploy-owned path this commit is exactly two files and the
tree is clean afterwards.

#### If Git already tracks a deploy-owned path

The managed ignore rules do not untrack anything: `.gitignore` has no effect on
a file already in the index. So if your repository was tracking its own
`CLAUDE.md`, or a `.claude/settings.json`, deploy overwrote it and Git shows it
as modified — the two-file commit above would leave that modification sitting in
the tree, and the first session would refuse to end. Handle it explicitly.

**Before deploying**, find out whether you have any collisions at all:

```bash
cd ~/src/your-repo
git ls-files -- CLAUDE.md 'ai-coding-*.md' .claude .cursor .codex .ai-protocol .mandrel
```

No output means there is nothing to reconcile — use the plain sequence above.
Otherwise, copy anything of your own out of the way first, because deploy will
replace it:

```bash
mkdir -p ../your-repo-preserved
cp CLAUDE.md ../your-repo-preserved/        # …and every other file that listing named
```

**After deploying**, untrack those paths so the managed ignore rules can take
effect. `--cached` removes them from the index and leaves the freshly deployed
files on disk:

```bash
git rm -r --cached --quiet -- CLAUDE.md .claude    # exactly what the listing named
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"

git status --porcelain      # now empty
```

That commit records two things at once: the deletions from the index, and the
receipt. From here on the payload is ignored, and `.ai-deploy-lock.json` is what
states which protocol revision the repository is on.

The alternative is to keep those paths tracked — `git add` them with the rest
instead of running `git rm --cached`. It works, and some teams want protocol
upgrades to show up as a reviewable diff. The cost is that every redeploy lands
a large mechanical diff in your history, which is the job the lockfile already
does in one file.

Whichever you choose, salvage the *content*: rules you want every session to
follow go in `.ai/conventions.md`, which you own and which loads into every
session anyway.

### Per-tool setup

| Tool | What you have to do |
|---|---|
| **Claude Code** | Nothing. `CLAUDE.md` imports and `.claude/settings.json` hooks are live on the next session. |
| **Cursor** | Nothing. `.cursor/hooks.json` is deployed and fires on session start. |
| **Codex CLI** | **Trust the hooks once.** Codex will not run non-managed command hooks until you review them: run `codex` in the repo and use `/hooks` to trust the two entries. For automation, `codex exec --dangerously-bypass-hook-trust`. Trust is recorded against the script hash, so re-trust after any redeploy that changes them. |

One Codex-specific trap: `.codex/config.toml` is rendered at deploy time with
**absolute** paths to the two hook scripts, because the Codex CLI does not
shell-evaluate that field. If you move or re-clone the repository to a different
path, redeploy — otherwise the hooks silently never run.
`.codex/README.md` in your target covers this and the user-scope fallback.

### Confirm

```bash
./bin/mandrel status ~/src/your-repo
```

```text
/Users/you/src/your-repo: in sync (117 files)
```

`status` exits nonzero on drift. Full drift vocabulary: [operations.md](operations.md#status).

---

## The lifecycle in one page

Everything below the deploy is one loop over one task file. Nine facts carry
almost all of it:

1. **The task file is ground truth.** `.ai-tasks/<id>.md` holds the goal, the
   scope, the acceptance criteria, and an append-only `## Session log`. Nothing
   is remembered between sessions except what is written there.
2. **One session = one conversation.** A session claims the task, does one
   coherent slice of work, ends with a clean tree and a session-log entry, and
   stops. The next turn is a new conversation.
3. **Roles are delivered, not ambient.** `/invoke dev <id>` and
   `/invoke review <id>` read the matching contract out of `.ai-protocol/` and
   bind the session to it. Without an invocation, a session has no role.
4. **Review is a separate fresh conversation, always.** Its only evidence is the
   task file, the `.ai/` memory, and the actual `git diff`. There is no
   transcript to inherit, which is exactly the point — do not paste the dev
   conversation into it.
5. **Dev never writes `status: completed`.** A dev session may set
   `in_progress`, or `final_review` when the *whole* task scope is done, or
   `blocked`. That is the entire set.
6. **Only a final-gate review session may set `completed`.** A review at
   `final_review` that cannot pass records `changes-requested` and leaves the
   status where it is.
7. **A `changes-requested` verdict routes the next dev turn to remediation.**
   Remediation fixes the recorded findings and changes no status at all. The
   re-review then checks only those findings and any regressions the fixes
   introduced.
8. **Severity decides what blocks.** Only `correctness` findings can hold up
   `final_review → completed`. A `design` or `test` finding is either fixed
   cheaply in place or carried out as a new pending task while the review
   passes. `style` findings never block.
9. **Closeout is automatic, and it always archives.** When a task reaches
   `completed` with a clean tree, the session-end hook blocks the stop and
   directs the same conversation into `/ai-sync-v2`: absorb what qualifies into
   `.ai/`, move the task file to `.ai-tasks/archive/`, drop its index row,
   re-check the other active tasks, commit. **Archiving always happens.
   Absorption is conditional** — findings must pass three admission tests
   (derivation cost, stability, leverage), and a task that teaches nothing
   durable is archived with `.ai/` untouched.

The turn you should run next is always derivable from the file alone. Read top
to bottom; the first row that matches is the turn:

| Task file says | Next turn |
|---|---|
| `status: completed` | **closeout** — `/ai-sync-v2`. Usually the stop hook already ran it in the final-review conversation and the file is in `.ai-tasks/archive/` by now. A task still sitting in `.ai-tasks/` at `completed` means the hook was interrupted, disabled, or unavailable, so closeout has *not* run: it is the turn, in a fresh conversation. `/ctd-tasks` flags this state as `⚠ Completed (unarchived)`. |
| `status: blocked` | answer the question in `blockers:`, then resume that conversation |
| frontmatter `fix-set: open` | `/invoke dev <id>` — the fix set is still open, so the re-review waits |
| a dev entry that no `review of <sid>` entry names | `/invoke review <id>` (the final gate, if the status is `final_review`) |
| `status: pending` | `/invoke dev <id>` |
| all reviewed, latest verdict `changes-requested` | `/invoke dev <id>` — auto-selects remediation |
| all reviewed, latest verdict `pass`, scope remains | `/invoke dev <id>` — advancement |
| `status: final_review`, all reviewed, no verdict-driven handback | the last review didn't conclude: decide it yourself and dispatch a fresh review with your ruling |

### When you need a fresh conversation

| Situation | Conversation |
|---|---|
| Every dev turn, every review turn | **new** |
| Review immediately after dev | **new** — a shared conversation destroys review independence |
| Remediation after a `changes-requested` review | **new** |
| Continuing a remediation that ran out of context (`fix-set: open`) | **new** |
| Answering a `blocked` task's question | **the same** conversation that blocked, so it keeps its role and its claim |
| Closeout after a task completes | **the same** conversation as the final review — the hook drives it |
| Closeout for a task left at `completed` in `.ai-tasks/` | **new** — the hook did not run; invoke `/ai-sync-v2` yourself |

The full scheduling spec is `.ai-protocol/workflow/runbook.md` in your target
(source: [`canonical/workflow/runbook.md`](../canonical/workflow/runbook.md)).
The lifecycle data shapes are `.ai-protocol/meta/taskfile.md`.

---

## Walkthrough A: greenfield

**The project.** `linkaudit`, a new Python CLI that crawls a static site and
reports broken links. Nothing exists yet but the directory.

**What this walkthrough shows.** Interview-driven initialization, a generated
task pool, and the full convergence loop: advancement → review
(`changes-requested`) → remediation → re-review (`pass`) → advancement →
final gate → `completed` → closeout with absorption.

### A1. Create the repo and deploy

```bash
mkdir -p ~/src/linkaudit && cd ~/src/linkaudit
git init

cd ~/src/mandrel
./bin/mandrel deploy --dry-run ~/src/linkaudit
./bin/mandrel deploy ~/src/linkaudit

cd ~/src/linkaudit
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"
```

The repository now has a `HEAD`, a clean tree, the deployed payload — and no
`.ai/` and no `.ai-tasks/`.

### A2. Initialize memory (conversation 1)

Open your agent in `~/src/linkaudit` and send exactly:

```text
/ai-init
```

The skill checks that the infrastructure is present, sees that `.ai/index.md`
does not exist, and classifies the repository. Classification runs over the **target-project surface**, which is your
repository *minus* everything the deploy owns: `.ai-protocol/`, `CLAUDE.md`,
`ai-coding-*.md` (the legacy loader), `.claude/`, `.codex/`, `.cursor/`,
`.mandrel/`, `.ai/`, `.ai-tasks/`, and the deploy receipts `.ai-deploy-*.json`.
That list covers the whole payload — the orchestrator's Python source and
requirements under `.mandrel/orchestrator/` included — so a repository holding
nothing but a mandrel deployment has an empty surface: greenfield, not
brownfield. The same exclusions are what stop a brownfield scan from reading
the protocol's own code as if it were yours.

**Greenfield initialization is interactive and blocking.** With nothing to read,
the agent asks and then stops. It creates no files yet:

```text
Greenfield detected — the target-project surface is empty after excluding
deployed AI infrastructure. I need project context before writing anything:
purpose, users, scope, non-goals, tech stack, major capabilities, external
systems, deployment/runtime expectations, known constraints.
```

Answer in the same conversation. You can also front-load this in the first
message and skip the round trip:

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

The agent then generates the snapshot, generates a task pool, stamps
frontmatter, and commits:

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

- Every `.ai/` document carries `last-updated:` and
  `verified-against: <40-char HEAD SHA>`. Sections you did not cover are marked
  `<!-- TODO -->` rather than invented.
- The task pool is **10–25 tasks** covering the system at feature scope. It is a
  work pool, not a schedule — nothing implies an order except real `blockers:`.
  At least one task is unblocked and specific enough to start.
- One commit lands: `chore(.ai): initial setup via /ai-init`. It contains `.ai/`
  only — `.ai-tasks/` is inside the managed gitignore block.

Look at the pool:

```text
/ctd-tasks
```

```text
📋 Pending  (14)
  pending  | 2026-03-02-cli-skeleton.md          | 0/1 | CLI skeleton and config loading
  pending  | 2026-03-02-crawler-core.md          | 0/2 | Crawl a site and extract internal links
  pending  | 2026-03-02-external-link-checks.md  | 0/2 | Check external links concurrently
  ...
active total: 14  (0 archived; use --all to include them)
```

**You do not run `/intake-task` here.** The pool already covers the system; use
intake later, only for work no existing task covers.

Here is the task this walkthrough works, as `/ai-init` wrote it:

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

### A3. First dev session (conversation 2 — new)

Start a **new conversation** and send exactly:

```text
/invoke dev 2026-03-02-crawler-core
```

`/invoke` checks that the role is legal for the task's current state, reads
`.ai-protocol/protocols/dev-advancement.md`, and binds the session to it. Then,
before touching code, the session **claims** the task:

```diff
-status: pending
+status: in_progress
-session-est: 0/2
+session-est: 1/2
-claimed-by:
+claimed-by: 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77@2026-03-02T09:14:03Z
```

Three things happened in that claim: the status advanced, the dev session
consumed one of the two estimated sessions, and the session stamped its own id.
That id is how the session-end hook finds the task later — under Claude Code the
session reads `$CLAUDE_CODE_SESSION_ID`; under Cursor and Codex the
session-start hook injects it.

The session then runs `preReEst` — comparing the remaining scope against the
plan and re-slicing if `session-1` is too big for one context window — and works
that one slice. It commits its own work:

```text
feat(crawl): fetch pages and extract links
test(crawl): fixture-based extraction and resolution tests
```

At the end, the hook enforces the order **clean tree first, then the log**, and
the session appends:

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

Status stays `in_progress`: scope remains. The tree is clean and the work is two
commits — that pair is what the reviewer will read.

### A4. Review (conversation 3 — new)

A review session must not inherit the dev conversation. Start a **new
conversation**:

```text
/invoke review 2026-03-02-crawler-core
```

The session claims the task (`claimed-by` moves to the review session's id;
`session-est` does **not** move — review sessions do not consume the estimate),
finds one work entry with no matching review, and reads the actual diff of that
session's commits — located from `git log`, or from the entry itself. It finds a
genuine correctness problem:

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

Note what the entry carries. `Verdict:` routes the next turn. `Group:` names the
work session that anchors this finding chain and freezes its scope: everything
the re-review may raise is either one of these findings or a regression
introduced by fixing them. Status does not move — an interim review's findings
never gate.

### A5. Remediation (conversation 4 — new)

```text
/invoke dev 2026-03-02-crawler-core
```

Same verb as before, different contract. `/invoke dev` applies the mode
predicate: the latest verdict is `changes-requested`, so it certifies
**remediation** and states so in its first reply. Remediation treats findings as
claims to verify against the code, fixes the valid ones correctness-first, and
never advances planned scope.

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

**A remediation session changes no status.** Its session-log entry is its entire
output. If it had run out of context with fixes still outstanding, it would set
frontmatter `fix-set: open`, and the next turn would be another remediation
session rather than a review.

Had the session judged a finding wrong, it would say so in `Done` as a dispute
rather than silently skipping it — and the reviewer would rule on it next turn.

### A6. Re-review (conversation 5 — new)

```text
/invoke review 2026-03-02-crawler-core
```

This review is **delta-only**: it checks whether group
`3f1b9c22-…`'s findings are resolved and whether the fixes broke anything. It
does not re-open design questions earlier reviews left alone.

```markdown
### 2026-03-02 / d0e57b93-2c18-4a6f-b5e9-3f8241a7c602 / review of c94a03e1-5b6d-4f28-8ad3-71e0c25f9a46 / (in_progress → in_progress)
- Verdict: pass
- Group: 3f1b9c22-7a04-4d51-9e2b-8c5a1d0e6f77
- Findings: redirect resolution fixed at the source and covered by
  `test_relative_links_after_redirect`. Style suggestion applied. No
  regressions in the fix diff.
```

`pass` releases the next dev turn to advance scope again.

### A7. Second dev session (conversation 6 — new)

```text
/invoke dev 2026-03-02-crawler-core
```

The predicate now certifies **advancement**. The claim moves `session-est` to
`2/2` and refreshes `claimed-by`. The session implements `session-2`, commits,
and — because the whole task scope is now done — sets `final_review`:

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

Two rules meet here. A dev session sets `final_review` **only** when the whole
scope is complete, and at `final_review` there must be **no open items** — every
loose end is either resolved or has become its own pending task. And a dev
session never writes `completed`, not even now.

### A8. Final gate (conversation 7 — new)

```text
/invoke review 2026-03-02-crawler-core
```

Entering at `final_review` makes this the **final gate**. It first checks the
task really is dev-complete — no scope item unexecuted, no deferral left in the
last entry's Next/Open — and then verifies the *whole* accumulated findings
ledger from every earlier review, not just the last session's diff.

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

### A9. Closeout — automatic, same conversation

You type nothing. The session-end hook sees `status: completed` with a clean
tree, blocks the stop, and directs the same conversation into `/ai-sync-v2`.

It walks the task's entire session log, applies the three admission tests, and
absorbs only what passes. Here, one fact does:

```diff
--- a/.ai/design.md
+++ b/.ai/design.md
@@ ## Core Principles
+- URL resolution uses the *final* response URL after redirects, never the
+  requested URL; the fetch layer returns both for that reason.
```

Then, always:

- `.ai-tasks/2026-03-02-crawler-core.md` moves to
  `.ai-tasks/archive/2026-03-02-crawler-core.md`.
- Its row leaves `.ai-tasks/index.md`.
- Every other active task is re-checked: blockers naming this task id removed,
  scope or estimates adjusted where this work changed them, a `blocked` task
  left with no blockers restored to an active status.
- The `.ai/` edit is committed, so the tree ends clean.

The final response ends with one line whose exact shape the caller checks — in
the manual loop, that caller is you:

```text
Remaining-task audit: checked 13 active task(s); updated 2026-03-02-external-link-checks; unchanged 2026-03-02-cli-skeleton, 2026-03-02-report-formats, 2026-03-02-config-file, …
```

Absorption is the conditional half: a task that produced no durable fact is
archived with `.ai/` untouched. [Walkthrough B's closeout](#b6-closeout--absorb-then-archive)
shows the admission tests turning candidates down as well as letting them in.

### A10. Where the repository ended up

```bash
git log --oneline
```

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

- **6 sessions** for one task: 3 dev, 3 review.
- `.ai-tasks/` holds 13 pending tasks and `.ai-tasks/archive/` holds 1 — both
  local and gitignored.
- `.ai/` gained one line, committed, and travels with the repo.
- The next task starts at A3 with a fresh conversation and no re-derivation.

---

## Walkthrough B: brownfield

**The project.** `invoicing-api`, an existing FastAPI service with two years of
history, ~60 source files, a test suite, and a team. Nobody wrote a `.ai/` for
it because it did not exist.

**What this walkthrough shows.** Derivation-driven initialization, an ordinary
English request turning into a task through `/intake-task`, the shortest legal
path through the lifecycle, a non-blocking review finding becoming its own task,
and a closeout whose admission tests reject as much as they accept.

### B1. Deploy into a repository that already has content

Start from a clean tree on a branch you are willing to commit to:

```bash
cd ~/src/invoicing-api
git status --porcelain      # must print nothing

cd ~/src/mandrel
./bin/mandrel deploy --dry-run ~/src/invoicing-api
```

Read the preview's `update:` section this time. A repo with its own `CLAUDE.md`
will see it listed there — deploy owns that path and will replace it. Save what
you want to keep; project rules that agents must follow belong in
`.ai/conventions.md`, which you are about to create and which loads into every
session.

This repository has never tracked a deploy-owned path, so nothing collides and
the plain sequence applies. If yours does — a hand-written `CLAUDE.md`, a
committed `.claude/settings.json` — run
[the collision sequence](#if-git-already-tracks-a-deploy-owned-path) instead, or
the tracked overwrite stays in the tree and the first session cannot end.

```bash
./bin/mandrel deploy ~/src/invoicing-api

cd ~/src/invoicing-api
git ls-files -- CLAUDE.md 'ai-coding-*.md' .claude .cursor .codex .ai-protocol .mandrel
git add .gitignore .ai-deploy-lock.json
git commit -m "chore: deploy mandrel protocol payload"

git status --porcelain      # expect no output
```

Existing history means `HEAD` is already there. As in walkthrough A, `.ai/` and
`.ai-tasks/` do not exist yet — deploy did not create them.

### B2. Initialize memory (conversation 1)

```text
/ai-init
```

The same exclusions apply, and the surface that remains still holds `app/`,
`tests/`, `pyproject.toml`, and a real README — **brownfield**. There is no
interview. Instead the skill runs a five-pass derivation, and every pass reads
only that surface: the deployed payload is not an input, so nothing in
`.ai-protocol/` or `.mandrel/orchestrator/` can end up described in your `.ai/`
as though it were your service.

| Pass | Reads | Writes |
|---|---|---|
| 1. Inventory | README, top-level dirs, `pyproject.toml` | `overview.md`, skeleton `architecture.md` |
| 2. Module survey | every module directory, in depth (fanned out in parallel) | `modules.md` |
| 3. Cross-reference | passes 1–2 | `map.md`, `features.md` |
| 4. Conventions sniff | 5–10 representative files — a test, an error path, a typical handler | `conventions.md` |
| 5. Review | — | your sign-off, then the frontmatter stamps |

**Pass 5 is a gate, not a formality.** The agent stops there and waits. The
documents exist on disk, but nothing is stamped and nothing is committed yet:

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

Read what it wrote and correct it in the same conversation. Every later session
starts from these documents, so a wrong conclusion here is one you keep paying
for. Corrections are ordinary conversation:

```text
Dunning is in scope — same product, same team. Fix overview.md. Split the
workers layer in architecture.md: invoice-render and delivery-retry have
different failure semantics. The error style is right.
```

Then sign off, explicitly:

```text
Looks right now. Sign off — stamp and commit.
```

Only on that sign-off does the agent stamp `last-updated:` and
`verified-against: <40-char HEAD SHA>` onto every document and commit. The
result:

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

**Brownfield initialization derives no tasks.** `.ai-tasks/index.md` stays
`(none)` by contract; work enters one request at a time through `/intake-task`.

One commit lands: `chore(.ai): initial setup via /ai-init`, containing `.ai/`.

### B3. Turn a request into a task (same conversation is fine)

This is where an ordinary English request meets the lifecycle. You do not
hand-write a task file:

```text
/intake-task the public webhook endpoint has no rate limiting — one noisy
integrator can saturate the worker pool. Cap it per API key.
```

Intake reads your request, checks `.ai-tasks/index.md` for an overlapping active
task (if one existed it would propose extending that task instead of creating a
new one), picks 2–5 **lazy** memory docs as `prefetch` — never the eager ones,
which load anyway — estimates the size in sessions, and drafts:

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

Plus the index row it proposes to append.

**Intake stops here and waits.** Nothing is written until you confirm. Refine
the draft in the same conversation — narrow the scope, add an acceptance bullet,
change the estimate — and confirm when it is right:

```text
Looks right, create it.
```

Two writes happen: `.ai-tasks/2026-03-09-webhook-rate-limit.md`, and a row in
`.ai-tasks/index.md`.

```markdown
# Active tasks

| id | title | status | session-est | blockers |
|---|---|---|---|---|
| 2026-03-09-webhook-rate-limit | Rate-limit the public webhook endpoint | pending | 0/1 | [] |
```

(The index is target-owned and its exact columns are not fixed by the schema;
this is the shape `/intake-task` writes.)

Note the estimate: `0/1`. A single-session task has no `## Session plan` —
plans exist only when the total is greater than one.

### B4. Dev session (conversation 2 — new)

```text
/invoke dev 2026-03-09-webhook-rate-limit
```

No review entries exist, so the predicate certifies **advancement**. The claim:

```diff
-status: pending
+status: in_progress
-session-est: 0/1
+session-est: 1/1
-claimed-by:
+claimed-by: 2b9e7f10-4c85-4a63-9d02-7e1f5a8c3046@2026-03-09T10:02:41Z
```

The session pre-loads the `prefetch:` docs — `.ai/apis.md` and `.ai/modules.md`
— on top of the eager set that is already there, implements, and commits:

```text
feat(webhooks): per-key fixed-window rate limit with 429 + Retry-After
test(webhooks): limit, isolation, and window rollover cases
```

Because the whole scope is done in this one session, it sets `final_review`:

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

That `.ai/` gap in `Done` is exactly right: sessions never edit `.ai/`
mid-task. Noticing a gap is a fact for the log, and closeout decides whether it
belongs in the snapshot.

### B5. Final gate (conversation 3 — new)

```text
/invoke review 2026-03-09-webhook-rate-limit
```

Entering at `final_review` with one unreviewed dev entry, this is the final
gate: verify dev-completeness, then the whole ledger. It finds one real issue —
but not a blocking one.

Before it can record that finding as *carried out*, it has to actually create
the task, and creating a task means the intake contract. So it drafts and
stops, exactly as in [B3](#b3-turn-a-request-into-a-task-same-conversation-is-fine):

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

**Nothing is written until you answer.** Same gate as B3 — a review session gets
no exemption from it.

```text
Yes, create it.
```

Two writes land: `.ai-tasks/2026-03-09-webhook-burst-smoothing.md` and its index
row. Only now, with the carried task genuinely on disk, can the review record
what it did and pass:

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

**This is the severity gate working.** A `design` finding does not block
completion: it gets fixed in place when cheap, or carried out as a new pending
task through the intake contract while the review passes. Only a `correctness`
finding could have held `final_review` back. Had it, the verdict would be
`changes-requested`, the status would stay `final_review`, and the next turn
would be remediation — the loop from [A5](#a5-remediation-conversation-4--new),
with the status unchanged throughout.

```diff
-status: final_review
+status: completed
```

### B6. Closeout — absorb, then archive

The hook fires `/ai-sync-v2` in the same conversation. It walks the session log
and applies the admission tests to each candidate:

| Candidate | Verdict |
|---|---|
| The webhook route authenticates from the provider signature, not the global auth dependency | **admitted** — costs a multi-file read to re-derive, stable, and changes what the next session does on that route |
| Fixed window chosen over token bucket, and why | **admitted** — a decision absent from the code; the code shows the *what*, not the rejected alternative |
| Default cap 120/60s | rejected — a one-line grep in settings |
| The 11 test names | rejected — greppable, and volatile |

Both admitted facts route to `.ai/apis.md` (the auth path) and `.ai/design.md`
(the tradeoff), and the edits are committed.

Then, unconditionally: the task file moves to `.ai-tasks/archive/`, its index row
disappears, the other active tasks are re-checked, and the response ends with:

```text
Remaining-task audit: checked 1 active task(s); updated none; unchanged 2026-03-09-webhook-burst-smoothing
```

Had nothing passed admission — a typo fix, a dependency bump, a rename — the
archive and the audit would still have happened and `.ai/` would be untouched.
**Archiving is unconditional; absorption is not.**

### B7. Where the repository ended up

```bash
git log --oneline -5
```

```text
a1c47f8 chore(.ai): absorb webhook rate-limit findings
5e93b2d test(webhooks): limit, isolation, and window rollover cases
c08fa14 feat(webhooks): per-key fixed-window rate limit with 429 + Retry-After
3d61e97 chore(.ai): initial setup via /ai-init
f27b405 chore: deploy mandrel protocol payload
```

- **2 sessions** for one task: 1 dev, 1 review. That is the floor.
- One task archived, one new pending task created by the reviewer.
- `.ai/` is two facts richer than the code alone can say.

---

## Two different things called "status"

They are unrelated, and confusing them is the most common early mistake.

| | `mandrel status <target>` | the task file's `status:` |
|---|---|---|
| Question it answers | is the deployed payload current? | where is this piece of work? |
| Values | `in sync`, `missing manifest`, `target modified`, `canonical changed`, `stale eager import`, `ambiguous memory entrypoint`, `shadowed skill`, `missing target file`, `extra deployed file`, `invalid manifest entry` | `pending`, `in_progress`, `final_review`, `completed`, `blocked` |
| Who changes it | you, by redeploying | the session, per the transition table |
| Where it lives | `.ai-deploy-manifest.json` vs. the canonical source | `.ai-tasks/<id>.md` frontmatter |

`canonical changed` after you pull a newer mandrel is the intended signal, not a
problem: redeploy that target when you want the new protocol revision. Nothing
about it touches your tasks or your memory. Full drift vocabulary and the two
receipts: [operations.md](operations.md#status).

---

## Running the loop unattended

Optional, and worth doing only after you have run the manual loop a few times —
the failure modes are much easier to recognize once you have seen the turns by
hand.

The scheduler runs `dev → review → dev → …` over one task, re-deriving the next
turn from the task file before every turn and pausing for you on every decision
it is not allowed to make. It is the same runbook and the same contracts, so you
can take a turn manually at any session boundary and let the scheduler pick up
from the file afterwards.

```bash
cd ~/src/mandrel
./bin/mandrel deploy --bootstrap-orchestrator ~/src/your-repo

cd ~/src/your-repo
.mandrel/orchestrator/.venv/bin/python .mandrel/orchestrator/orchestrator.py <task-id>
```

Bootstrapping builds `.mandrel/orchestrator/.venv` with `python3.14`
(`--orchestrator-python /path/to/python` if that is not the right executable),
installs the requirements, and writes a `.env` scaffold **only if none exists**.
Credentials stay local: log in to `claude` and `codex` for the default
`cc-codex` backend, or set `CURSOR_API_KEY` when explicitly using
`--backend cursor`.

**Read `.mandrel/orchestrator/README.md` in your target before running it, and
read the [Safety section](../README.md#safety) of the README.** The scheduler
runs agents with filesystem permission prompts disabled. That is deliberate and
the reasoning is written down, but it means: a repository you can `git reset`,
on a machine you control. The manual loop in this guide has no such requirement
— your agent's normal permission prompts apply throughout.

---

## Where to go next

| | |
|---|---|
| [README](../README.md) | what the protocol is and why it is shaped this way |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | how context reaches a session across the three agent tools |
| [operations.md](operations.md) | every command, flag, drift state, receipt, and lifecycle verb |
| `.ai-protocol/workflow/runbook.md` (in your target) | the scheduling spec both executors read |
| `.ai-protocol/meta/taskfile.md` (in your target) | task frontmatter, status transitions, session-log shapes |
| `.ai-protocol/meta/memory.md` (in your target) | what earns a place in `.ai/`, and how it is maintained |
| `.ai-protocol/protocols/` (in your target) | the contracts themselves: conduct, dev, review, plan, intake |

Two habits that make the difference early on:

- **Read the session-log entry your agent writes.** It is the whole handoff. If
  it is vague, the next session starts from a worse position — say so and have
  it rewritten before you move on.
- **Let the review find things.** A `changes-requested` verdict on session one
  is the system working, not a setback. The loop exists because the second pair
  of eyes has no memory of writing the code.

If you run this and it goes badly, that is a report worth sending.
