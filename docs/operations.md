# Operations reference

Operational behavior for deployment, drift checks, receipts, and protocol
evolution. Use each command's `--help` output for the exhaustive CLI flags.
Start at the [README](../README.md) if you are new here.

This repository is the canonical, version-controlled home for the AI-native
coding protocol suite and deployment tooling.

Target repositories should receive copies from this repository. Deployed files
should not be hand-edited in target repos; edit canonical files here, redeploy,
and use `status` to verify drift.

For the whole chain — source of truth → deploy → target layout → the three IDE
surfaces → what actually ends up in the context window — see
[ARCHITECTURE.md](../ARCHITECTURE.md).

## Repository Layout

- `canonical/repo-root/` deploys to the target repo root.
- `canonical/protocols/` deploys to target `.ai-protocol/protocols/` (role contracts).
- `canonical/workflow/` deploys to target `.ai-protocol/workflow/` (runbook, rolemapping, boundary-skill specs).
- `canonical/meta/` deploys to target `.ai-protocol/meta/` (taskfile schema, memory protocol, init).
- `canonical/cursor/` deploys to target `.cursor/`.
- `canonical/codex/` deploys to target `.codex/`.
- `canonical/claude/` deploys to target `.claude/`, including the workflow
  skills under `canonical/claude/skills/`.
- `canonical/orchestrator/` deploys to target `.mandrel/orchestrator/`.
- `mandrel/` contains deploy, manifest, registry, status, and CLI code.
- `.registry/repos.local.json` is a gitignored local inventory of managed repos.

The orchestrator is a canonical deployment payload in this repository.
Runtime changes are made and tested under `canonical/orchestrator/`, then
deployed to target repositories; deployed copies should not be hand-edited.

## Deploy

Run from this repository:

```bash
python -m mandrel.cli deploy ../some-target-repo
```

The repo-local wrapper is shorter and does not require installation:

```bash
./bin/mandrel deploy ../some-target-repo
```

To make the deployed orchestrator runtime-ready in the target repo, bootstrap
its local virtualenv during deploy:

```bash
./bin/mandrel deploy --bootstrap-orchestrator ../some-target-repo
```

This creates or updates `.mandrel/orchestrator/.venv` with `python3.14 -m venv`,
upgrades `pip`, installs `.mandrel/orchestrator/requirements.txt`, and creates
`.mandrel/orchestrator/.env` from `.env.example` only if `.env` does not already
exist. Use `--orchestrator-python /path/to/python` if `python3.14` is not the
right executable on the machine. Credentials and CLI logins are still local:
log in to `claude` and `codex` for the default `cc-codex` backend, or set
`CURSOR_API_KEY` when explicitly using `--backend cursor`.

If installed in editable mode, the same command is exposed as:

```bash
mandrel deploy ../some-target-repo
```

Deployment writes:

- canonical files into the target paths listed above
- rendered `.codex/config.toml` from `canonical/codex/config.toml.template`
- target `.ai-deploy-manifest.json` for local status checks
- target `.ai-deploy-lock.json` as a portable deploy receipt suitable for Git
- an idempotent `.gitignore` block in the target repo that ignores deployed
  payload files, `.ai-tasks/`, and the local manifest, but leaves the lockfile
  trackable
- a local registry entry in `.registry/repos.local.json`
- with `--bootstrap-orchestrator`: target `.mandrel/orchestrator/.venv/` and a
  non-overwriting `.mandrel/orchestrator/.env` scaffold

`canonical/codex/config.toml.template` may contain `{{REPO_ROOT}}`; deploy
renders that placeholder to the absolute target repo path.

Preview a deploy without writing the target repo:

```bash
./bin/mandrel deploy --dry-run ../some-target-repo
```

Dry-run reports which managed files would be added, updated, left unchanged, or
blocked by a non-file target path. It does not write payload files, manifests,
lockfiles, `.gitignore`, registry entries, or orchestrator bootstrap files.

### If Git already tracks a deploy-owned path

Deploy overwrites the paths it owns — `CLAUDE.md`, `.claude/`, `.cursor/`,
`.codex/`, `.ai-protocol/`, `.mandrel/` — and the managed ignore rules do not
untrack anything: `.gitignore` has no effect on a file already in the index. So
if your repository was tracking its own `CLAUDE.md`, or a
`.claude/settings.json`, deploy overwrote it and Git shows it as modified. The
ordinary two-file receipt commit
([getting-started.md](getting-started.md#step-4-commit-the-receipt)) would leave that
modification sitting in the tree, and the first session would refuse to end.
Handle it explicitly.

**Before deploying**, find out whether you have any collisions at all:

```bash
cd ~/src/your-repo
git ls-files -- CLAUDE.md .claude .cursor .codex .ai-protocol .mandrel
```

No output means there is nothing to reconcile — the plain receipt commit
applies. Otherwise, copy anything of your own out of the way first, because
deploy will replace it:

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
follow go in the target-owned conventions memory (`.ai/conventions.md`, or its
directory-form entrypoint), which loads into every initialized session.

## Status

Check one repo:

```bash
python -m mandrel.cli status ../some-target-repo
./bin/mandrel status ../some-target-repo
```

Check every locally registered repo:

```bash
python -m mandrel.cli status --all
```

Status asks two questions, of the target's two receipts. *Integrity* is the
target against its own `.ai-deploy-manifest.json`: the bytes and modes the
deploy left behind, still there. *Currency* is `.ai-deploy-lock.json` against
the lines this repository is releasing — the commit at the configured source
line, plus the latest sealed round of an open experiment when there is one. A
target standing on either line is current.

It reads no canonical files. Editing `canonical/` does not move any target's
readiness, and neither does switching this checkout to another branch. "What
would change if I deployed right now" is a different question, and
`deploy --dry-run` is what answers it.

- `in sync`
- `missing manifest`
- `target modified`
- `unplaceable deployment`
- `off the source line`
- `unresolved source line`
- `stale eager import`
- `ambiguous memory entrypoint`
- `shadowed skill`
- `missing target file`
- `invalid manifest entry`

Drift returns a nonzero exit code.

### Placement against the source line

A lock states a source commit only where the deployment's own files, bytes and
modes matched that commit's canonical tree exactly. The receipt therefore
already says which commit the payload came from, proved once with both sides in
hand, and nothing re-reads the target's files to check it.

What that commit is compared *as* is the protocol it carries: the deployable
canonical tree, read from the commit itself. A commit is a whole-repository
identity and moves for work under `mandrel/`, `tests/`, `docs/` and `.ai/` that
no target ever receives, so comparing commit strings would take every deployed
target off the line the first time anyone committed a test.

- `off the source line` — the payload came from a commit carrying neither the
  current release's protocol nor the sealed candidate's. The detail names the
  revision the target holds and the ones being released, because a commit is
  what you redeploy from; redeploy. A release commit that changed nothing a
  target receives moves no target.
- `unplaceable deployment` — the target carries no lock, one this build cannot
  read, one tying its payload to no commit, or one naming a commit whose
  canonical tree this checkout does not hold. Nothing places what that target
  holds, so no evaluation of it can be attributed to a protocol revision. The
  last case is a fetch away from an answer; the rest want a redeploy from a
  committed canonical tree.
- `unresolved source line` — this repository could not name a line to place
  anything against: the configured ref does not resolve in this checkout, or the
  evolution policy does not read. Nothing is wrong with the target; the source
  checkout is what needs attention.

A target deployed at a sealed candidate is current, and is meant to be: it is
running the change under measurement, and redeploying it would destroy the
measurement. An experiment whose latest round is still taking work contributes
no candidate, because a moving ref tip is a revision no report can be placed
against afterwards.

`status` needs no evolution workspace. A checkout with none releases on
`refs/heads/main` and has no candidate.

### Memory entrypoints in `CLAUDE.md`

`CLAUDE.md` is deploy-owned, but which file each eager memory topic
(`overview`, `architecture`, `design`, `conventions`) lives in is target state:
the memory protocol upgrades a doc from `.ai/design.md` to
`.ai/design/index.md` once it outgrows the size limit. Deploy therefore
resolves each import against the target's own `.ai/` — `.ai/index.md` routing
first, then file shape — using the same order as the cursor and codex
session-start hooks, so all three surfaces load the same file. A directory-form
upgrade survives the next deploy instead of being reverted, and a loader left
on the pre-upgrade path is repaired by it.

For `CLAUDE.md` only, status treats the two entrypoint forms as equivalent for
hashing. Because that makes the hash blind to which form is deployed, the two
entrypoint checks are reported separately:

- `stale eager import` — the loader imports a path that is no longer the
  current entrypoint (Claude Code ignores missing imports silently, so the
  document just goes absent), or `.ai/index.md` routes a topic somewhere that
  is neither entrypoint form.
- `ambiguous memory entrypoint` — both `x.md` and `x/index.md` exist. The
  upgrade renames; it does not duplicate.

Other files remain exact content-and-mode checks.

### Shadowed skills

The same blind spot in the other direction: every deployed skill file can hash
correctly and still not be the one that runs. Claude Code resolves a
[same-named personal skill ahead of a project skill](https://code.claude.com/docs/en/skills#where-skills-live),
so `~/.claude/skills/<name>/SKILL.md` wins over the deployed copy — silently,
and no content comparison can see it.

- `shadowed skill` — a deployed skill name also exists as a personal-level
  skill. The detail names the file that takes precedence; removing it restores
  the deployed contract.

The check reads the personal skills root and writes nothing. It looks under
`~/.claude/skills`; set `MANDREL_CLAUDE_SKILLS_ROOT` if the agent
home is relocated.

The manifest is target-local state: it includes rendered file hashes, modes,
and local absolute paths, so it should remain ignored. The lockfile is portable:
it records canonical file hashes, modes, and source commit information without
target machine paths, so target repos may commit it for auditability.

### The machine shape

`status` takes `--json` for the same findings a script can read, on both forms:

```bash
python -m mandrel.cli status --json ../some-target-repo
python -m mandrel.cli status --all --json
```

One envelope either way. A single target is a sweep of one, so a consumer parses
the same document whichever flag was typed; `name` is the machine-local registry
name a sweep walked the target under, and null for a target named by path.

```json
{
  "schema_version": 2,
  "source_lines": {
    "ref": "refs/heads/main",
    "base": "<full commit>",
    "candidate": null,
    "unresolved": []
  },
  "targets": [
    {
      "name": "some-target-repo",
      "target_root": "/abs/path",
      "in_sync": false,
      "total_files": 42,
      "placed_line": null,
      "drifts": [
        {
          "kind": "off the source line",
          "origin": "placement",
          "subject": ".ai-deploy-lock.json",
          "detail": "holds <full commit>; the lines being released are base <full commit>",
          "placement": {
            "reason": null,
            "holds": "<full commit>",
            "base": "<full commit>",
            "candidate": null
          }
        }
      ]
    }
  ]
}
```

The example is ordered for reading; the command prints keys sorted, so the same
findings produce byte-identical output.

`source_lines` is stated once above the targets because that is how it is read:
this repository's lines are resolved once per run and handed to every target, so
a sweep cannot report two targets against two different readings.

Findings come out in the order the prose prints them and carry everything it
prints. `subject` is a target-relative path for every origin but `source`, where
it names the line this checkout could not resolve. `placement` is null on every
kind but the two placement ones. Exit codes are unchanged: 0 in sync, 1 drift,
2 error — the flag changes what is printed and nothing about what was found.
Failures still print prose on stderr.

`placed_line` sits beside `drifts` rather than inside one, because a target
standing on a line is a reading and not a finding.

#### Origin — what diverged

Each kind says what was found; `origin` says whose fault it is, which is the
part a consumer acts on.

| Origin | Meaning | Kinds |
|---|---|---|
| `target` | the target's files against what its deployment established | `target modified`, `missing target file`, `stale eager import`, `ambiguous memory entrypoint` |
| `receipt` | the deploy receipt itself, before it can be believed about anything | `missing manifest`, `invalid manifest entry` |
| `placement` | that receipt's revision against the lines being released | `unplaceable deployment`, `off the source line` |
| `source` | this repository's own reading; the subject is not the target | `unresolved source line` |
| `precedence` | a file outside the deployment winning over it | `shadowed skill` |

Two of these are counter-intuitive and are the reason the label exists.

`off the source line` is not "the target is broken". A target at a sealed
candidate is at neither base nor drift and produces no finding at all; a target
at the release before last is a plain redeploy. Which one it is depends on what
the receipt holds against what is being released, so `placement` carries both
rather than leaving the sentence to be parsed back apart. A release commit that
changed nothing a target receives produces this finding on nobody.

`unresolved source line` is this repository, not the target. Filed under the
target's faults it sends somebody to redeploy a repository nothing was found
wrong with.

#### `placement.reason` — why a deployment places nothing

`unplaceable deployment` is one kind and four next steps. A consumer folding
them sends an operator to overwrite a target nothing was established about.

| Reason | The receipt | Next step |
|---|---|---|
| `no-receipt` | absent | redeploy from committed canonical work |
| `unreadable` | present, and this build cannot read it | look at the file |
| `unstated` | present, and ties its payload to no commit | redeploy from committed canonical work |
| `unreadable-protocol` | names a revision whose canonical tree this checkout does not hold | fetch, or repair a receipt naming an object that is no commit |

`holds` is null for the first three and carries the stated revision for
`unreadable-protocol` — the commit to go and fetch. Two ways reach that reason:
the objects are absent here, or the id names no commit at all, since a tree or a
blob passes the receipt's lexical shape check and is refused only when Git is
asked. Neither is a redeploy.

#### `placed_line` — which line a target is standing on

A target passes on one of two lines, and passing looks identical either way:
`in_sync: true` with no findings. Which line it is is the fact an evaluation
report has to be attributed to, so it is stated per target rather than left to
be inferred from an absence of drift.

| Value | The target's receipt | What it means |
|---|---|---|
| `base` | carries the protocol at the configured source line | running the current release |
| `candidate` | carries the protocol of the latest sealed round | running the change under measurement |

Null is every way there is no line, and it never arrives alone: each of them
also emits its own finding — the four `placement.reason` cases above,
`off the source line`, `unresolved source line`, or the `missing manifest`
reading that stops before the placement is asked. So `in_sync: true` implies a
named line, and null with no finding is a bug in this surface rather than a
state a target can be in.

It is the same comparison `drifts[].placement` reports the negative side of,
read once. A target naming a line carries no placement finding, and a target
carrying one names no line; the two cannot disagree.

Sealed is the only candidate that appears. An open round's ref tip moves, so a
target deployed from it produces reports naming a tree nothing can afterwards
identify.

#### Versioning

`schema_version` versions this payload and only this payload. It is deliberately
not the `mandrel evolution status` schema version and is not tied to it in
either direction: those version persisted lifecycle records under
`evolution/schemas/`, this versions one CLI reading of two receipts and persists
nothing. A shared counter would make every lifecycle change announce a breaking
change to a deployment surface that did not move.

The number is the promise of the payload's key set: every key added, removed, or
repurposed moves it, an addition included. There is no minor half to file an
addition in, and leaving one out would put payloads with and without a key into
circulation under one number — which stops the version describing a shape at all
and leaves a consumer probing for keys to learn what it has. Read the version;
the fields it names are always present. Bumping costs nothing here because
nothing persists a status payload, so no stored document is ever read back at an
older version.

| Version | Change |
|---|---|
| 2 | added `targets[].placed_line` |
| 1 | the initial payload |

## Registry

The registry is local machine inventory, not GitHub truth:

```bash
python -m mandrel.cli registry list --json
./bin/mandrel registry list --json
python -m mandrel.cli registry add ../some-target-repo
python -m mandrel.cli registry remove some-target-repo
```

`registry add` requires the target repo to already have a readable
`.ai-deploy-manifest.json`; otherwise run `deploy` first. `registry remove`
only removes local tracking. It does not delete deployed files, manifests,
hooks, or repo contents.

The registry is inventory only; `mandrel` does not launch registered targets.
A launcher can consume it to discover managed repos and start each target's
deployed `.mandrel/orchestrator/orchestrator.py`. The orchestrator exposes its
effective defaults and named model/effort profiles as machine-readable JSON:

```bash
.mandrel/orchestrator/.venv/bin/python \
  .mandrel/orchestrator/orchestrator.py --print-config
```

See `.mandrel/orchestrator/README.md` for the resolution precedence and profile
contract.

## Skills

Workflow skills are ordinary canonical payload. They live in
`canonical/claude/skills/<name>/` and deploy to:

```text
<target>/.claude/skills/<name>/SKILL.md
```

Like every other deployed file they are hashed into `.ai-deploy-manifest.json`
and `.ai-deploy-lock.json`, so a target's protocol revision covers its skills
too. Claude Code discovers them natively; the Cursor rule and the Codex
SessionStart injection point at the same repo-relative path.

Deployed set: `ai-housekeeping`, `ai-init`, `ai-load`, `ai-sync-v2`,
`ctd-tasks`, `intake-task`, `invoke`.

## Hook prerequisites

Context delivery and session-end enforcement run through per-tool hooks, and
every one of them shells out to `jq`. A missing `jq` is not a failure you will
notice in time, because the three tools degrade differently:

| Tool | Without `jq` |
|---|---|
| **Claude Code** | The stop hook reads its input through an unguarded `jq` under `set -e`, so it exits with an error on every session end and the clean-tree / session-log discipline stops being enforced. Loud, at least. Eager context is unaffected — it arrives through `CLAUDE.md` imports, and the session-start hook falls back to `python3`. |
| **Cursor** | Same unguarded stop-hook read, same erroring session end. Session-start injection *is* guarded, so it silently emits no context. |
| **Codex CLI** | Silent all the way through, which makes it the worst case: both hooks guard every `jq` call with `\|\| true`, so session start injects no context at all and the stop hook — seeing an empty session id — fails open and allows every session end. Nothing on screen tells you enforcement is gone. |

Codex has a second prerequisite of its own. It will not run non-managed command
hooks until they are trusted: run `codex` in the target and use `/hooks` to
trust the two entries, or pass `codex exec --dangerously-bypass-hook-trust` for
automation ([official hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)).
Trust is recorded against the hook definition's current hash, so re-trust after
a redeploy that changes it. Mandrel renders `.codex/config.toml` with absolute
paths to the two hook scripts; move or re-clone the repository and redeploy
before relying on those hooks.

## Evolution

`evolution/README.md` is the normative contract for changing the canonical
protocol suite from evidence. `mandrel evolution` is the mechanical half of it:
it discovers already-complete evaluation reports, stages them, offers a batch
out of the pool, and — once a human fixes that offer — freezes an immutable
cohort and prepares its pending analysis task. It runs against this repository,
not a target.

Nothing here starts or schedules an evaluation, and nothing here decides
policy: batch formation, change admission, and canary promotion stay human
gates.

```bash
./bin/mandrel evolution status                 # lifecycle phase; writes nothing
./bin/mandrel evolution list  --feed-dir ...   # inspect candidates; writes nothing
./bin/mandrel evolution sync  --feed-dir ...   # import eligible reports into the pool
./bin/mandrel evolution start --feed-dir ...   # repair/close, sync, then propose a batch
./bin/mandrel evolution batch-include ...      # argue one task into that proposal
./bin/mandrel evolution batch-exclude ...      # take one out of it
./bin/mandrel evolution freeze                 # fix that proposal as an immutable cohort
```

Forming a batch is two commands on purpose. `start` writes a proposal to ignored
runtime state — the base it read off the configured source line, and the pooled
reports selected against it — and writes nothing durable at all: no manifest, no
report claim, no analysis task, no ledger line. `freeze` is the human's own
command and the first durable write of the formation. The window in between is
where membership can still be argued with, which is precisely what an immutable
manifest cannot offer.

A proposal is a reading, not evidence. The next `start` rebuilds it, and the
freeze refuses one whose source line has since moved or whose members the pool no
longer holds — naming the rebuild, because a stale reading is retaken rather than
mended. `--force` (with `--justification`) belongs to `freeze`: waiving the
configured target is a decision about forming a batch, and `start` forms none.

**The window is two verbs.** Both name one pooled task as two arguments —
`<repo-id> <task-id>`, each exactly as the offer states it — and both require
`--reason` and `--actor`, because a move nobody is named for is not a recorded
judgement, and it survives into the frozen manifest. The two halves stay apart
on the command line because a report's repository id may itself hold a slash
(`owner/repo` is the ordinary form of one); readings still print the pair
joined, and that is a display rather than a spelling to type back.

| Verb | What it does |
|---|---|
| `batch-include <repo-id> <task-id> --reason ... --actor ...` | argue one pooled task into the offer past the membership test; counts toward the thresholds like any other member |
| `batch-exclude <repo-id> <task-id> --reason ... --actor ...` | take one task out of the offer, on a stated judgement, recorded as `human-excluded` |

Each is refused where the move would change no answer: a task the identity test
already selects cannot be included, and one it already refuses cannot be
excluded. Both are refused outright once the batch is frozen — a manifest is
immutable, so the next window is the offer that follows this batch's outcome.

Moves survive the ordinary poll. The next `start` re-applies every one the
standing offer records, and drops only those the automatic answer has come to
agree with — where the test no longer needs overriding there is no judgement
left to record. A move is lost only three ways — the task leaves the pool, the
base moves to one its evidence matches, or the offer becomes unreadable and the
poll rebuilds it — each costing what losing the offer entirely costs.

Those seven manage report intake and cohort formation. What a frozen cohort then
becomes — experiments, rounds, replays, a promotion, its reversal, and the next
cohort's reading of the release — is
[the lifecycle console](#the-lifecycle-console) below.

Every subcommand takes `--repo <path>` to work on another checkout, and
`status` takes `--json` for the same shape a script can read. Exit status is 0
for any completed run — including a `start` that offered no batch, which is the
contract's normal outcome when evidence is still thin — and 2 for a refusal,
corrupt local state, lock contention, or an unusable feed.

`status` names a lifecycle phase — `idle`, `pool N/<target>`,
`batch-proposed N/<target>`, `batch-frozen`, `dispositions-ready`,
`proposals-pending`, `implementing`, `candidate-ready`, `supersede-pending`,
`conclusion-pending` — and then the facts behind it:

```text
$ ./bin/mandrel evolution status
evolution: batch-proposed 4/20
  pool         6 unique completed task(s); target 20, minimum 10
               oldest member imported 5 day(s) ago, max wait 30
  proposal     4 of 6 considered task(s) at base 3ed93a7a0c1f (refs/heads/dev)
               2 not selected: identity-differs
  admission    no batch — pool-below-minimum (4 selected of 6 pooled, target 20, minimum 10)
  batches      0 frozen, none current
  revisions    none in play — no batch is current
  actions      start
               batch-include
               batch-exclude
               22 other verb(s) refuse here; `--json` states each reason
  state        1-ac1087623f3fa990 — what this reading is of
```

One label is chosen by what blocks the next action: an open batch first (it is
what stops another cohort forming), then an admitted change task in flight,
then drafts waiting at the admission gate, then the pool. Every fact is printed
regardless of which label won, so nothing is hidden by the choice.

Nothing is stored to produce that: the phase is re-derived on every call from
the manifests, the closure records and drafts beside them, the runtime pool,
`.ai-tasks/`, and git. `--json` emits the same facts, with a `schema_version`.
Two of them are machine-local — the admitted change tasks and an open batch's
analysis lifecycle both come from gitignored `.ai-tasks/` — so another clone
reads the batch's committed closure record instead.

### The lifecycle console

Every operation the contract defines has exactly one `mandrel evolution` verb, so
this CLI is sufficient on its own: a Web surface is an optional visualization and
interaction adapter over the same JSON and the same verbs, never a second
implementation of the lifecycle. `status` names each verb it allows and gives
every refused one a reason, so the ordinary loop is `status`, then the verb it
named.

None of them decides anything. Which drafts belong together, whether an attempt
is worth continuing, whether the evidence justifies the source line, whether a
release is kept — all of it is a human judgement stated here and recorded
(invariant 9). Every verb below takes `--repo` and the optional
`--expect <state_revision>` described under
[the JSON and action contract](#the-json-and-action-contract).

**The change lineage of a batch**

| Verb | What it does |
|---|---|
| `create <draft>... [--base <rev>] [--reason ...]` | admit a group of drafts as a new experiment on the base the batch was formed at; `--base` asserts which commit that is expected to be |
| `add-tasks <draft>...` | admit further drafts into the open experiment's open round |
| `reject <draft>... --reason ...` | decline drafts at the admission gate; terminal for those proposals |
| `seal-round` | observe every admitted task complete and pin the round's candidate revision |
| `revise --reason ...` | open the next round, from the candidate already pinned |
| `abandon --reason ... [--experiment <id>]` | end the open experiment without replacing it |
| `supersede --reason ... [--experiment <id>]` | replace it with a fresh attempt; the operation creates the successor |
| `conclude-no-change --reason ...` | end the batch having changed nothing (invariant 7) |

**The evidence, and the source line**

| Verb | What it does |
|---|---|
| `replay-start --expectation ... [--source-ref <ref>]` | request the measurement of the sealed candidate integrated onto the configured source line, and record the run once one is stated; `--source-ref` asserts which line that was expected to be |
| `replay-conclude --outcome ... --detail ...` | record what the harness reported for the active run |
| `replay-abandon --reason ...` | record why a run ended when its harness cannot say |
| `replay-withdraw` | give up a request the harness never answered for |
| `promote --reason ... [--target <name>]... [--source-ref <ref>]` | carry the replayed candidate onto the line its replay integrated onto, and end the batch with it |
| `rollback --reason ... [--source-ref <ref>]` | add and record the inverse commit for the latest promotion, on the line that promotion reached, and end the batch standing on that release |

**The release the next cohort owes a reading of**

| Verb | What it does |
|---|---|
| `assess --verdict ... --confidence ... --rationale ... --recommend ... [--metric NAME UNIT BEFORE AFTER BETTER]...` | record this cohort's reading of the release before it, and which settlement it advises |
| `assess-measure --expectation ...` | request the pinned counterfactual, and record its run once one is stated |
| `assess-conclude --outcome ... --detail ...` | record what the harness reported for it |
| `assess-abandon --reason ...` | record why that run ended when its harness cannot say |
| `assess-withdraw` | give up a counterfactual request the harness never answered for |
| `assess-resolve --verdict ... --confidence ... --rationale ... --recommend ...` | revise the reading on the strength of the completed run |
| `settle --settlement retain\|rolled-back --reason ...` | answer the gate the next batch's first experiment waits on; `rolled-back` runs the reversal itself and ends the batch with it |

The reading states which settlement it advises as well as what it found,
because the analysis of a post-release batch ends in two answers and a human
takes one: the change-task drafts, or a rollback of the release those drafts
would be built on. `--recommend` is that advice and `settle --settlement` is
the answer; the two may disagree, and `status` says so when they do.
`rolled-back` is advisable only over a `regressed` verdict, which rests on a
completed counterfactual.

`settle` is one verb and not a sequence: a `rolled-back` settlement performs the
rollback, adopts an inverse already on the line, and finishes one left prepared.
`rollback` is for a reversal that answers no gate.

Either way the reversal ends the batch standing on the release it took back. That
batch was formed at the line while the release was on it, so its base carries a
promotion nobody kept and there is nothing left to admit on it — the outcome
record says `rolled-back`, and the reports it claimed stay claimed. Which batch
ends is read off the base rather than off who answered the gate, so a cohort that
concluded without reading the release does not shield the one after it.

Where the source line comes from differs by verb, and `--source-ref` never
changes it. A batch freeze and a replay *resolve* the line, from
`evolution/config.toml` `[source_line].ref` — one name for the project, so every
clone measures against the line it releases from rather than the branch someone
was on. The freeze resolves it for the manifest's `runner_protocol_revision`,
which is this batch's base: where that line stands when the batch is formed is
both the protocol revision the cohort's tasks are governed by and the commit
every experiment of that batch starts from, so a checkout that does not hold the
line refuses to freeze rather than record the absence. Admission resolves
nothing — it reads the base out of the manifest. A promotion and a rollback do
not resolve one either: they take it from their own evidence, the replay's
integration and the promoted outcome's merge unit, so no edit to the policy file
can put an unmeasured tree or an inverse commit on a line nothing was measured
against. On replay, promotion, and rollback, `--source-ref` states which line the
operator read; on `create`, `--base` states which commit they expect the batch to
have been formed at. Either mismatch refuses before anything is written.

**`refs/heads/dev` is evolution's receiving line, and `refs/heads/main` is
human-maintained.** Evolution moves `dev` and its own `refs/evolution/...`
namespace, and nothing else — no operation here writes `main`. So do not work on
`dev`: canonical work happens on a feature branch, and a promotion lands it on
`dev` for you. Pushing `dev` afterwards needs no checkout of it
(`git push origin dev` from wherever you are).

That is a working rule, not a slack the code takes. A promotion and a rollback
both refuse while any working tree of this repository sits on the line they are
about to move, linked worktrees included, because moving a branch some tree is on
leaves that tree describing the commit before — and the obvious repair takes
whatever else was uncommitted there with it. The refusal is not relaxed for
`dev`: the cost is identical for any branch, and the reason has nothing to do
with the name. It is also the one refusal an operator answers without deciding
anything — run it from a checkout that is not on the source line. Trading it for
a class of silently lost uncommitted work would be the worse bargain, so the
habit `dev` invites is the thing to correct, not the guard.

#### One cycle, end to end

```bash
./bin/mandrel evolution start                      # propose the next cohort
# review what it offers, then fix it:
./bin/mandrel evolution freeze                     # writes the manifest and the analysis task
# complete the generated analysis task and commit its findings and drafts
# create publishes the analysis closure before admitting the named drafts
./bin/mandrel evolution create loader-fallback hook-side-loader --reason "one change"
# work the admitted change tasks; commit the work on the experiment's ref
./bin/mandrel evolution seal-round                 # pins the round's candidate revision
./bin/mandrel evolution replay-start \
    --expectation "fewer remediation rounds, quality unchanged"
# ...run that evaluation yourself, then state what is running:
# export CASE_SET_SHA256 and HARNESS_CONFIG_SHA256 with the hashes from your harness
./bin/mandrel evolution replay-start \
    --expectation "fewer remediation rounds, quality unchanged" \
    --case-set loader-regressions "$CASE_SET_SHA256" 12 \
    --evaluator claude claude-opus-5 --harness local-replay 0.1.0 "$HARNESS_CONFIG_SHA256" \
    --handle run-7
./bin/mandrel evolution replay-conclude --outcome completed --detail "12 of 12 judged" \
    --metric "remediation rounds" rounds 2.0 1.0 lower --handle run-7
./bin/mandrel evolution promote --reason "the replay justifies it" --target orch-hub
./bin/mandrel deploy ../orch-hub                   # promotion is not deployment
```

The cohort frozen after that promotion owes a reading of it, and no later first
experiment is admitted until the reading is settled (invariant 17):

```bash
./bin/mandrel evolution start && ./bin/mandrel evolution freeze
# what the cohorts came to, and inconclusive: no manifest states the shape of
# the work, so they carry no direction of their own to record here
./bin/mandrel evolution assess --verdict inconclusive --confidence low \
    --rationale "..." --recommend retain \
    --metric "remediation rounds" rounds 2.1 1.4 lower
./bin/mandrel evolution assess-measure --expectation "..."   # the pinned counterfactual
# ...run it, state it, and `assess-conclude` it — the same two-step as a replay
./bin/mandrel evolution assess-resolve --verdict improved --confidence medium \
    --rationale "..." --recommend retain
./bin/mandrel evolution settle --settlement retain --reason "the reading holds"
```

The direction arrives on `assess-resolve` and not before it. A directional
verdict rests on evidence that can carry one, and the cohorts cannot: the shape
of the work is a facet no manifest version states, so the comparison is
incoherent on every frame this build can form and `assess` refuses `improved`
outright. What the first reading records is the cohorts' numbers and
`inconclusive`; the pinned counterfactual is what settles a direction, and
`assess-resolve` is where the reading is revised on it.

Either path can end differently and both endings are verbs: `conclude-no-change`
for a batch whose evidence justified nothing, `abandon` or `supersede` for an
attempt that did not work out, and `settle --settlement rolled-back` for a
release the next cohort read as a regression — which reverses the release and
ends that cohort's own batch with it.

#### The harness is you

Four verbs need something outside this checkout, and all four cross the same
boundary: `replay-start`, `replay-conclude`, `assess-measure`,
`assess-conclude`. Nothing here schedules or triggers an evaluation, so the
harness those verbs speak to is the operator.

- **A start is two commands over one request.** The first writes the request,
  prints the integration to exercise, and stops — the position is held and
  nothing is running. Run the evaluation however you run it, then run the same
  verb again stating what is running: `--case-set`, `--evaluator`, `--harness`,
  `--handle`, plus `--rubric` and `--exclude` where they apply. That statement is
  all or none; a partial one is refused before anything is allocated.
- **`--handle` is the run's name and it matters later.** It is opaque here and
  stored unread, and it is what the matching `*-conclude` polls — a run recorded
  without one could only ever be abandoned. Giving it to `*-conclude` is a
  precondition: numbers named for another run are refused rather than recorded.
- **A rerun of a completed attempt has to state that attempt back.** A second run
  of the same round replaces the first as that round's evidence, so its cohort,
  exclusions, evaluator and harness configuration must be restated exactly;
  anything else is refused as the substitution it would be. The gate allows
  `replay-start` there — it is the harness that refuses — so a surface driving
  `allowed_actions` should expect it. `status` prints what to restate.

#### What the targets hold

`promote --target <name>` records a **plan**, and deploys nothing. `status` reads
the other half beside it: for each planned name it resolves this machine's
registry entry, reads that target's own `.ai-deploy-lock.json`, and asks Git
where the revision that receipt states sits relative to the promotion. A receipt
is held to the shape this deploy contract writes before any of that: a schema
this build reads, and a full commit id rather than a name. `HEAD`, a branch, or
an abbreviation would be resolved against *this* checkout instead of against what
that target holds, so it reads as `unreadable` rather than as a placement that
moves whenever this repository does. A receipt that omits `source_git_commit`
reads as `unreadable` too, and is not the `unstated` below: every receipt this
contract writes states the field, so its absence is a document that answers
nothing, where its null is the deploy's own answer that nothing places what that
target holds.

```text
  promoted     6f1c0a5b2e33 from evolution-batch-0001-exp-01 round 1 (evolution-batch-0001)
               41ab99c0f7de onto refs/heads/dev at 9d2e8c17b40a, tree 5b1f0e93aa72
               planned targets: api-service, web-app — the plan this promotion recorded, not what they hold
  deployed     what each planned target holds now, from its own .ai-deploy-lock.json:
               api-service at 6f1c0a5b2e33 — carries this promotion
               web-app at 9d2e8c17b40a — does not carry this promotion; `mandrel deploy` is what carries it there
```

| State | What it means, and what it asks for |
|---|---|
| `carries` | that receipt's revision has the promotion in its history |
| `reversed` | it has the inverse commit too, so the change is off that target as well |
| `behind` | it does not have the promotion — the redeploy that is actually owed |
| `unplaceable` | this checkout cannot place that revision — it does not hold the commit, or Git could not answer |
| `unstated` | the receipt ties its payload to no source commit, so nothing places what it holds |
| `no-receipt` | the repository is registered and nothing has been deployed to it |
| `unreadable` | the receipt, or the registry naming it, could not be read — including a receipt whose schema this build does not read, or that states no `source_git_commit` or something other than a commit id |
| `unregistered` | no repository of that name is registered on this machine |
| `ambiguous` | two registered repositories share that name, so neither can answer for the plan |

The block is null until something has been promoted: the plan is where the
targets in play come from, so with no promotion there is nothing to answer for.

All of it is machine-local: the registry is this machine's inventory and a
receipt is a file in a repository this tool does not own. A clone that manages
nothing therefore reads every planned target as `unregistered`, which is an
answer rather than a finding — and a broken registry or receipt is reported as
that one target's state rather than failing the whole reading. It is in no
`state_revision`, and it gates no verb: a promotion is not a deployment, and a
deployment is not lifecycle state.

### The JSON and action contract

`status --json` is the machine shape of everything above, at
`schema_version: 11`. Alongside the pool, proposal, admission, batches, gate,
experiments, revisions, replay, release, `last_promotion` and `deployment` blocks
it carries
the two fields a surface acts on:

```json
{
  "state_revision": "1-ac1087623f3fa990",
  "allowed_actions": [
    {
      "action": "seal-round",
      "allowed": false,
      "object": {"type": "experiment", "id": "evolution-batch-0002-exp-01"},
      "reason": "round 1 of evolution-batch-0002-exp-01 is not ready to seal: ['2026-08-11-loader-fallback']; ...",
      "recovers": null
    }
  ]
}
```

- **Every verb is emitted every time**, refused ones included. A menu listing
  only what is legal leaves "why not" to be discovered by running it.
- **`reason` is the operation's own sentence.** The gate asks the owning module's
  read-only predicate rather than restating its policy, so an operator meets one
  wording whether they read `status` or ran the command.
- **`object` is the id the verb takes** — the experiment a decision names, the
  batch a conclusion ends, the promotion a rollback reverses — and is null where
  the verb is about something that does not exist here. That is the difference
  between a refusal an operator can act on and one that is simply not what this
  lifecycle is at.
- **`recovers` names a redo.** Every operation is redoable by being run again
  with the same arguments: it finishes what an interrupted run left and reports
  what is already on record. A non-null `recovers` says that is what running the
  verb here would do — an already-sealed round, a batch that concluded, a
  rollback already on the line — so a surface can offer the repair without
  presenting it as new work. The human form marks those `(redo)`.
- **A verb the gate allows may still refuse**, on four conditions that belong to
  the moment of the write rather than to a reading: whether a working tree sits
  on the source line, whether later work stands on the promotion a rollback would
  reverse, whether an admitted task's file is the copy admission published, and
  whatever Git or the harness answers under the lock. Arguments are the same —
  this says whether the verb may run at all, not whether your drafts exist. A
  verb it refuses is refused.

`state_revision` is what makes acting on the reading safe. It is a digest —
`1-<16 hex>` — of the durable state the lifecycle is derived from: the versioned
records under `evolution/batches` and `evolution/experiments`,
`evolution/config.toml`, `.ai-evolution/state.json`,
`.ai-evolution/batch-proposal.json`, the tips of every ref those
records name, and the configured source line (which no record names, and which a
batch's formation resolves before any record could). The offer is in it because
the window's verbs are writes like any other: a token taken before somebody
argued a report in or out describes a batch that is no longer the one on offer.
The staged bundles and the writer's lock are the two runtime files left out, and
for opposite reasons — hashing every bundle would re-read the whole pool on every
reading, and the lock is held for the duration of every mutation, so hashing it
would move the token during the operation it guards. `HEAD` is deliberately not
in it: nothing this tool writes resolves the checkout any more, so a
`git checkout` of an unrelated branch, or a commit touching no evolution record
and no watched ref, leaves an outstanding token usable. Pass it back on any
mutation:

```bash
./bin/mandrel evolution seal-round --expect 1-ac1087623f3fa990
```

The operation re-derives it first thing under the single-writer lock and refuses
if the lifecycle moved, before it writes anything. `--expect` is optional: an
operator reading `status` and typing the next verb is their own single writer,
while a surface that is not — a Web adapter, orch-hub, a script resuming — passes
the token. Two refusals are deliberately different sentences: a token from
another scheme does not describe this repository, and a token that no longer
matches means this repository moved. A caller treating them as one retries the
wrong thing forever.

The clock, `.ai-tasks/`, and the ledger are outside the digest on purpose — a
token that expired overnight would refuse operations over a repository nobody
wrote to, a task finishing is this machine's own work, and the ledger is an audit
rather than flow state.

Mutations print for humans and take no `--json`; `status` is the machine shape,
and it is where a surface reads what a verb did.

### Report source

Reports come from orch-hub's protected global feed. Point the client at it with
two environment variables, named by `[source]` in `evolution/config.toml` and
never committed:

```bash
export ORCH_HUB_URL=https://orch-hub.example
export ORCH_HUB_TOKEN=...
```

Without those variables, `list`, `sync`, and `start` fail with one message
naming both of them and the offline path — they do not hang, and they do not
silently import nothing. The offline path is a local report bundle:

```text
<feed-dir>/reports/*.json                          one import record per file
<feed-dir>/artifacts/<report_key>/<artifact-name>  the four L1+L2 bodies
```

`--feed-dir` stays the way to run a deterministic cohort offline and to replay
a fixed one afterwards.

The wire contract implemented by the client is detailed in
`mandrel/evolution/hub.py`:

```text
GET <ORCH_HUB_URL><report_feed_path>?limit=<n>[&after=<watermark>]
    -> {"enabled": true, "reports": [<catalog entry>...],
        "cursor": <int>, "next_cursor": <int>, "has_more": <bool>}
GET <ORCH_HUB_URL><report_feed_path>/<report_key>/artifacts/<wire name>
    -> the artifact bytes exactly as published
       404 unknown report key or artifact name   410 published, bytes pruned
       409 incoherent stored identity            500 unreadable artifact
```

Both requests carry `Authorization: Bearer $ORCH_HUB_TOKEN`. `has_more` is
required: its negation is what tells a later batch freeze that the pool is the
whole eligible set rather than a prefix, so it is read from the feed and never
inferred from a short page. The cursor is an integer watermark on the wire and
opaque above the client, which converts at that boundary.

A catalog entry is orch-hub's own shape, not this repository's import record:
the client translates one into the other so the offline feed stays
interchangeable. Two fields are derived rather than copied — `completed` from
the entry's `archived`, and each artifact's `media_type` from its published
filename. When `provenance.protocol` marks a verified identity available, the
client copies its `effective_revision` and `deploy_lock_hash` as an atomic pair;
otherwise both remain null. Other provenance fields that orch-hub does not
publish also remain null rather than being inferred.

Artifact bytes are served byte for byte and verified here against the digests
the feed itself published. The route reports a failed integrity check in a
header instead of hiding the body, and the client deliberately ignores that
header for decisions: the check belongs to the side that did not publish the
bytes.

Two transport safeguards are enforced by the client rather than inferred from
the feed:

- **No redirect is followed.** `urllib` would copy the `Authorization` header
  onto whatever host a `Location` named, so a checked URL would say nothing
  about where the token ends up. Point `ORCH_HUB_URL` at the final URL instead.
- **Every response body is read under a fixed 32 MiB bound**, not the
  `size_bytes` the record declares. A body that reaches the bound is rejected by
  the usual size/hash check rather than truncated into the pool.

`scripts/probe-orch-hub.py` checks all of that against the running service —
envelope, reported exhaustion, one entry translated into a record the real
import schema admits, and one report's artifact bytes hashed against the digests
the feed published. It is read-only and credentialed, so it is not in
`scripts/check.sh` and no CI runner can reach the feed; run it by hand with both
variables exported after either side changes.

### Storage and privacy

| Path | Contents | Committed |
|---|---|---|
| `.ai-evolution/state.json` | controller runtime | no — machine-local |
| `.ai-evolution/imported-artifacts/` | raw fetched bundles | no — raw evidence |
| `.ai-evolution/lock` | single-writer guard | no |
| `evolution/batches/<id>/` | frozen membership, analysis, proposals, and batch decisions | yes; the manifest is immutable |
| `evolution/experiments/<id>/` | change-attempt lineage and replay records | yes |
| `evolution/ledger.jsonl` | sanitized audit | yes, append-only |
| `.ai-tasks/` | task lifecycle state, including generated analysis and admitted change-task working copies | no — machine-local |

Privacy follows that split. Raw report content and any diagnostic quoting a
feed value stay under ignored `.ai-evolution/`; the ledger carries identities,
hashes, and a bounded vocabulary of reason codes only. Credentials live in the
environment and appear in no file, URL, or error message this tool writes.

Everything marked committed above is versioned lifecycle state, but the tool
does not commit it for you. Until the relevant commit lands, another clone
cannot observe that transition.

### Recovery

- **Interrupted run.** Re-run the same command. A freeze writes manifest →
  state → task → ledger and each step is redoable, so the next `start` finishes
  whatever remains and reports what it repaired. Repair runs before the feed is
  contacted, so an outage never blocks it.
- **Lock held.** `evolution lock held: ... (pid N on HOST since TIME)` means
  another run holds `.ai-evolution/lock`. It is never broken automatically — a
  crashed holder and a slow one look identical. Remove the file once you have
  confirmed no run is active.
- **Corrupt runtime state.** `state.json` failures are reported, never
  repaired: a silent reset would rewind the discovery cursor and drop pending
  evidence. Restore the file, or delete `.ai-evolution/` and re-run `start` to
  rebuild the pool from the feed. The committed batches are what protect you
  there: discovery skips every report a frozen manifest already names, so a
  wipe re-imports only what no cohort has analyzed, and an open batch's claims
  and analysis task are recreated from its manifest. Raw bundles that were
  staged before the wipe are gone; `status` reports the batch's evidence as
  absent from this machine rather than as damage.
- **`pool-incomplete`.** No discovery pass has reported the feed drained, so
  the pool may be a prefix and no batch may form from it. Run `sync` until it
  reports the feed drained.
- **`no-proposal`.** Nothing has been offered out of this pool, so there is no
  membership for admission to measure. Run `start`; `freeze` refuses with the
  same sentence.
- **`pool-off-base`.** The pool holds evidence and none of it was produced at
  the base the batch would be formed at. Not the same as an empty pool: the
  evaluations exist, they are just about another protocol. It waits for reports
  produced at the configured source line.
- **A generated analysis task that no longer looks like one.** The controller
  identifies it by its id line, its `# Batch analysis — <batch-id>` heading, and
  the manifest path it cites, then reads its lifecycle. Keep those three intact
  while working the task; if the file is damaged, restore them or remove the
  file so the next run can write it again.

## Excluded from deployment

Deploy intentionally skips local or sensitive files:

- `.venv/`
- `logs/` and `.logs/`
- `sessions.json`
- `.env`
- `.env.*` except `.env.example`
- `.claude/settings.local.json`
- `.claude/projects/`
- Python cache files
- Git metadata

Do not place credentials or machine-local state in `canonical/`.

## Development

### Bootstrap

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

The `dev` extra is the complete tool set the verification gate needs — pytest,
ruff, shellcheck (via `shellcheck-py`), and the build backend. Nothing else has
to be installed by hand; the gate refuses to run rather than skip a check whose
tool is missing, and its error names the command above.

### One command

```bash
scripts/check.sh
```

This is the repository's only verification entrypoint. It runs from any working
directory, runs every check even after one fails, exits nonzero if any failed,
and leaves `git status --porcelain` unchanged. It runs:

| Check | What it covers |
|---|---|
| `whitespace` | trailing whitespace, CR line endings, final newline in tracked text files |
| `ruff` | Python lint (pyflakes plus the pycodestyle error rules) |
| `shellcheck` | every tracked shell script, found by extension or shebang |
| `boundary-lint` | `scripts/boundary-lint.sh` — canonical protocol boundaries |
| `pytest` | the `tests/` suite |
| `orchestrator-mock-loop` | `canonical/orchestrator/test_loop_mock.py` scenarios |
| `package-build` | wheel build, offline install into a throwaway venv, CLI starts |
| `tree-unchanged` | the run leaves `git status --porcelain` unchanged |

The interpreter is `.venv/bin/python` when present, otherwise `python3`; set
`AII_PYTHON` to override.

`canonical/orchestrator/test_loop_mock.py` is deliberately outside pytest's
`testpaths` — it is a standalone script with its own `main()`, and the gate is
the only thing that runs it. The same is true of the two shell checks. That is
what `tests/test_verification_gate.py` guards: it asserts the gate still
carries every required check, that every mock-loop scenario is wired into
`main()`, and that CI has not grown a copy of any check.

The suites use temporary source and target repos, so they never touch a real
managed repository.

### Optional Git hook

```bash
scripts/install-git-hooks.sh
```

Installs a `pre-push` hook that calls `scripts/check.sh` and nothing else.
`git push --no-verify` skips it; deleting `.git/hooks/pre-push` uninstalls it.

Rerunning the installer over the hook it wrote changes nothing. Any other
`pre-push` hook is left untouched and reported, including one of your own that
calls the gate alongside its own commands — ownership is decided by the file's
full contents, so a hook is replaced only when it is exactly what the installer
writes. To adopt the managed hook, move the existing one aside first.

### CI

`.github/workflows/ci.yml` runs on pull requests, pushes to `main` and `dev`,
and manual dispatch. It installs the `dev` extra and calls `scripts/check.sh` on
Python 3.11 (the `requires-python` floor) and 3.14 (the orchestrator runtime).
It needs no repository secrets and requests only `contents: read`.

Both branches are on the push trigger because both receive commits no pull
request ever gated: `main` is human-maintained, and `dev` is the line evolution
promotes onto.

**No CI check can run before a promotion moves the ref.** A promotion is a local
`git commit-tree` followed by `git update-ref` (`mandrel/evolution/revisions.py`)
— there is no `push` anywhere under `mandrel/`, so it is neither a push nor a
pull request and GitHub does not see it at all until a human pushes `dev`
afterwards. The push trigger runs the gate at that point, on a line the
promotion is already on. That is worth having and it is not a gate on promotion.
If a hard gate on promotion is wanted, the place for it is a preflight inside
`promote` itself, where the merge is computed and the ref is moved. What already
stands in for one is `experiments._require_measured_tree`: promotion recomputes
the merge and refuses unless the result equals the replay's `integration.tree`,
so what lands is the tree a replay actually exercised.

A promotion also moves the state every outstanding `--expect <state_revision>`
token was minted against — twice over, since the token hashes both the
configured source line's tip and `evolution/config.toml` itself. Changing
`[source_line].ref` therefore invalidates every outstanding token at once. That
is the token working: it refuses a mutation planned against a state that has
since moved, and the line the whole lifecycle stands on moving is exactly the
case it exists for. Re-read `mandrel evolution status` and use the new token.

**Branch protection is a repository setting, not a file in this repo.** Until
the `gate (Python 3.11)` and `gate (Python 3.14)` checks are marked required
for `main` and `dev` under Settings → Branches, a red run can still be merged.
Adding the workflow does not enable the gate; marking the checks required does.
Required checks on `dev` bind a human pushing it, not the promotion that moved
it locally — see above.

`.github/workflows/smoke.yml` is the non-default counterpart: a manual
`workflow_dispatch` run of `canonical/orchestrator/smoke_hooks.py`, which drives
a live Cursor SDK agent to observe whether the deployed hooks fire. It needs
`CURSOR_API_KEY`, costs money, and reports observations rather than a verdict,
so it is classified out of the merge gate on purpose.

### Adding a check

Add a `check_*` function and one `run_check` line to the CHECKS section of
`scripts/check.sh`. CI and the Git hook call that script and never re-list its
steps, so a check added there is enforced everywhere at once. Do not add a
second entrypoint; if a new check needs a tool, declare it in the `dev` extra
so the preflight can fail closed on it.
