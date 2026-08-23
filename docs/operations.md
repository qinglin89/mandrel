# Operations reference

Every command, flag, drift state, receipt format, and lifecycle verb. Start at
the [README](../README.md) if you are new here.

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
- `canonical/orchestrator/` deploys to target `.mandrel/orchestrator/`. It
  used to deploy to `.cursor/orchestrator/`; targets deployed before the move
  need a [one-time migration](#upgrading-a-target-deployed-before-the-orchestrator-moved).
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

### Upgrading a target deployed before the orchestrator moved

The orchestrator used to deploy to `.cursor/orchestrator/`; it now deploys to
`.mandrel/orchestrator/`, a sibling of `.ai-protocol/` rather than a tenant of
another tool's directory. A target deployed before the move needs a one-time
migration. Until it gets one, `status` reports every orchestrator file twice —
once as `extra deployed file` at the old path, once as `canonical changed ...
(new canonical file not deployed)` at the new one.

Redeploy with the bootstrap flag. The venv has to be **rebuilt**, not moved: a
virtualenv hardcodes absolute paths in `bin/*` and `pyvenv.cfg`, so a copied
`.venv` is a broken interpreter.

```bash
./bin/mandrel deploy --bootstrap-orchestrator <target>
```

Then settle the operator-owned state by hand. `.env`, `.venv/`, and `logs/`
(with its `sessions.json` session map) are excluded from the payload, so deploy
has never written them and does not move them either:

- **`.env`** — bootstrap writes `.env` from `.env.example` only when none
  exists. None exists at the new path, so it writes a fresh scaffold and the
  configured one stays behind. Copy it over before deleting anything.
- **`logs/`** — leave it where it is. The orchestrator resolves its log dir
  from its own directory, so a migrated repo starts a fresh one under
  `.mandrel/orchestrator/logs/` while past run logs and the session map stay at
  the old path. orch-hub locates a past run's log through the `log_dir` stamped
  into that run's record, so the old directory *is* the historical access path:
  moving or deleting those files makes every pre-migration run unreadable in
  the hub, and copying them forward would make the same run resolvable twice.

Update anything that launches the orchestrator by path in the same pass.
orch-hub resolves the script and the venv interpreter as fixed paths, so a hub
still pointing at `.cursor/orchestrator/` reports every migrated repository
unready.

Then remove the abandoned payload from the old directory, keeping `logs/`:

```bash
find <target>/.cursor/orchestrator -maxdepth 1 -mindepth 1 \
  ! -name logs -exec rm -rf {} +
```

**Do that in the same pass as the redeploy**, while `status` can still see it.
Deploy does not prune a path it no longer writes, and the fresh manifest simply
stops mentioning the old records — so the `extra deployed file` drift that
flags the stale tree today disappears at the redeploy, and the target reports
`in sync` again while still carrying a full copy of the old payload and a venv
whose interpreter no longer resolves.

What is left behind afterwards is `.cursor/orchestrator/logs/` and nothing
else. That is the intended end state, not an unfinished cleanup.

`.cursor/` itself stays: it still receives `hooks/`, `hooks.json`, and `rules/`,
which are genuinely per-tool. Only the orchestrator tenant moved out.

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

Status reads the target `.ai-deploy-manifest.json` and reports:

- `in sync`
- `missing manifest`
- `target modified`
- `canonical changed`
- `stale eager import`
- `ambiguous memory entrypoint`
- `shadowed skill`
- `missing target file`
- `extra deployed file`
- `invalid manifest entry`

Drift returns a nonzero exit code.

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

Other files and other edits remain exact hash checks.

### Shadowed skills

The same blind spot in the other direction: every deployed skill file can hash
correctly and still not be the one that runs. Agent tools resolve same-named
skills personal-level over project-level, so a leftover
`~/.claude/skills/<name>/SKILL.md` wins over the deployed copy — silently, and
no content comparison can see it.

- `shadowed skill` — a deployed skill name also exists as a personal-level
  skill. The detail names the file that takes precedence; removing it restores
  the deployed contract.

The check reads the personal skills root and writes nothing. It looks under
`~/.claude/skills`; set `MANDREL_CLAUDE_SKILLS_ROOT` if the agent
home is relocated. See [One-time operator cleanup](#one-time-operator-cleanup).

The manifest is target-local state: it includes rendered file hashes and local
absolute paths, so it should remain ignored. The lockfile is portable: it
records canonical file hashes and source commit information without target
machine paths, so target repos may commit it for auditability.

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

Future `orch-hub` tooling can consume `.registry/repos.local.json` to discover
managed repos on this machine, then start each target repo's deployed
`.mandrel/orchestrator/orchestrator.py` as a subprocess. This repository does
not implement orch-hub. The deployed orchestrator exposes its effective
defaults and named model/effort profiles as machine-readable JSON:

```bash
.mandrel/orchestrator/.venv/bin/python \
  .mandrel/orchestrator/orchestrator.py --print-config
```

See `.mandrel/orchestrator/README.md` for the resolution precedence and profile
contract. A focused orch-hub implementation handoff is deployed as
`.mandrel/orchestrator/HANDOFF-orch-hub-split-role-profiles.md`.

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

`ai-sync` and `session-ai-audit` were retired on 2026-08-09. `ai-sync`
duplicated `ai-sync-v2`'s job against a task layout this protocol no longer
uses, and its description advertised an auto-trigger for `.ai/` writes that
the memory contract's closeout-only invariant forbids; `session-ai-audit`
drove a `scripts/session_ai_audit.py` that this repository has never carried,
so it could not run in any target. Deploy does not prune, so targets deployed
before that date keep both copies — see the cleanup below.

### One-time operator cleanup

These skills used to be installed machine-globally under `~/.claude/skills/` by
an `mandrel skills sync-claude-global` command that no longer exists. **Remove the
leftover global copies**, because personal-level skills override project-level
ones ([skill precedence](https://code.claude.com/docs/en/skills)): a stale
global copy silently wins over the deployed one, and no content hash can see
it — deploy succeeds, the manifest and lock record the skills as deployed, and
`status` reports `in sync`.

**Redeploy every managed target first.** A target still on a pre-migration
payload has no project-level copy, so deleting the global one leaves it with
neither — and the stop hooks on all three backends point at `ai-sync-v2` for
task closeout. Order matters:

```bash
./bin/mandrel status --all                      # find targets reporting canonical changed
./bin/mandrel deploy <each-target>              # project-level copies land first

ls ~/.claude/skills                       # review before deleting
rm -rf ~/.claude/skills/{ai-housekeeping,ai-init,ai-load,ai-sync,ai-sync-v2}
rm -rf ~/.claude/skills/{ctd-tasks,intake-task,invoke,session-ai-audit}

# the retired pair also has project-level copies to remove
rm -rf <each-target>/.claude/skills/{ai-sync,session-ai-audit}
```

The retired `ai-sync` and `session-ai-audit` stay in the personal-level
deletion above and get a project-level deletion of their own: they must go
from **both** roots. Redeploying replaces the other seven project-level
copies, but writes nothing for a name that has left the payload, and the
fresh manifest simply stops mentioning it — so `status` reports `in sync`
over a target that still has the orphan. Claude Code finds skills by scanning
`.claude/skills/`, not by reading the manifest, so an orphaned copy keeps
running.

Skills you added yourself that are not in the deployed or retired sets are
unaffected; delete only the names above.

`status` reports any name still shadowing a deployed skill as `shadowed skill`,
so a target that has not had this cleanup done says so instead of reporting
`in sync`.

## Evolution

`evolution/README.md` is the normative contract for changing the canonical
protocol suite from evidence. `mandrel evolution` is the mechanical half of it:
it discovers already-complete evaluation reports, stages them, freezes an
immutable cohort when the admission policy allows, and prepares that cohort's
pending analysis task. It runs against this repository, not a target.

Nothing here starts or schedules an evaluation, and nothing here decides
policy: batch formation, change admission, and canary promotion stay human
gates.

```bash
./bin/mandrel evolution status                 # lifecycle phase; writes nothing
./bin/mandrel evolution list  --feed-dir ...   # inspect candidates; writes nothing
./bin/mandrel evolution sync  --feed-dir ...   # import eligible reports into the pool
./bin/mandrel evolution start --feed-dir ...   # sync, then freeze a batch if policy allows
```

Those four are the import half. What a frozen cohort then becomes — experiments,
rounds, replays, a promotion, its reversal, and the next cohort's reading of the
release — is [the lifecycle console](#the-lifecycle-console) below.

Every subcommand takes `--repo <path>` to work on another checkout, and
`status` takes `--json` for the same shape a script can read. Exit status is 0
for any completed run — including a `start` that formed no batch, which is the
contract's normal outcome when evidence is still thin — and 2 for a refusal,
corrupt local state, lock contention, or an unusable feed.

`status` names a lifecycle phase — `idle`, `pool N/<target>`, `batch-frozen`,
`dispositions-ready`, `proposals-pending`, `implementing`, `candidate-ready`,
`supersede-pending`, `conclusion-pending` — and then the facts behind it:

```text
$ ./bin/mandrel evolution status
evolution: pool 4/20
  pool         4 unique completed task(s); target 20, minimum 10
               oldest pending report imported 5 day(s) ago, max wait 30
  admission    no batch — pool-below-minimum
  batches      0 frozen, none current
  revisions    none in play — no experiment has frozen a base
  actions      start
               21 other verb(s) refuse here; `--json` states each reason
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
| `create <draft>... [--base <rev>] [--reason ...]` | admit a group of drafts as a new experiment; a batch's first experiment freezes the base (`HEAD` unless named) |
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
| `replay-start --source-ref <ref> --expectation ...` | request the measurement of the sealed candidate integrated onto that line, and record the run once one is stated |
| `replay-conclude --outcome ... --detail ...` | record what the harness reported for the run that is going |
| `replay-abandon --reason ...` | record why a run ended when its harness cannot say |
| `replay-withdraw` | give up a request the harness never answered for |
| `promote --reason ... [--target <name>]...` | carry the replayed candidate onto the source line and end the batch with it |
| `rollback --reason ...` | add and record the inverse commit for the latest promotion |

**The release the next cohort owes a reading of**

| Verb | What it does |
|---|---|
| `assess --verdict ... --confidence ... --rationale ... [--metric NAME UNIT BEFORE AFTER BETTER]...` | record this cohort's reading of the release before it |
| `assess-measure --expectation ...` | request the pinned counterfactual, and record its run once one is stated |
| `assess-conclude --outcome ... --detail ...` | record what the harness reported for it |
| `assess-abandon --reason ...` | record why that run ended when its harness cannot say |
| `assess-withdraw` | give up a counterfactual request the harness never answered for |
| `assess-resolve --verdict ... --confidence ... --rationale ...` | revise the reading on the strength of the completed run |
| `settle --settlement retain\|rolled-back --reason ...` | answer the gate the next base freeze waits on; `rolled-back` runs the reversal itself |

`settle` is one verb and not a sequence: a `rolled-back` settlement performs the
rollback, adopts an inverse already on the line, and finishes one left prepared.
`rollback` is for a reversal that answers no gate.

#### One cycle, end to end

```bash
./bin/mandrel evolution start                      # freeze a cohort when policy allows
# work the generated analysis task; commit its findings and closure record
./bin/mandrel evolution create loader-fallback hook-side-loader --reason "one change"
# work the admitted change tasks; commit the work on the experiment's ref
./bin/mandrel evolution seal-round                 # pins the round's candidate revision
./bin/mandrel evolution replay-start --source-ref refs/heads/main \
    --expectation "fewer remediation rounds, quality unchanged"
# ...run that evaluation yourself, then state what is running:
./bin/mandrel evolution replay-start --source-ref refs/heads/main \
    --expectation "fewer remediation rounds, quality unchanged" \
    --case-set loader-regressions <sha256> 12 \
    --evaluator claude claude-opus-5 --harness local-replay 0.1.0 <sha256> \
    --handle run-7
./bin/mandrel evolution replay-conclude --outcome completed --detail "12 of 12 judged" \
    --metric "remediation rounds" rounds 2.0 1.0 lower --handle run-7
./bin/mandrel evolution promote --reason "the replay justifies it" --target orch-hub
./bin/mandrel deploy ../orch-hub                   # promotion is not deployment
```

The cohort frozen after that promotion owes a reading of it, and no later base is
frozen until the reading is settled (invariant 17):

```bash
./bin/mandrel evolution start
./bin/mandrel evolution assess --verdict improved --confidence medium \
    --rationale "..." --metric "remediation rounds" rounds 2.1 1.4 lower
./bin/mandrel evolution assess-measure --expectation "..."   # the pinned counterfactual
# ...run it, state it, `assess-conclude` it, then `assess-resolve` the reading
./bin/mandrel evolution settle --settlement retain --reason "the reading holds"
```

Either path can end differently and both endings are verbs: `conclude-no-change`
for a batch whose evidence justified nothing, `abandon` or `supersede` for an
attempt that did not work out, and `settle --settlement rolled-back` for a
release the next cohort read as a regression.

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
               41ab99c0f7de onto refs/heads/main at 9d2e8c17b40a, tree 5b1f0e93aa72
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
`schema_version: 7`. Alongside the pool, admission, batches, gate, experiments,
revisions, replay, release, `last_promotion` and `deployment` blocks it carries
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
`evolution/config.toml`, `.ai-evolution/state.json`, the tips of every ref those
records name, and `HEAD` (which is the base a first admission would freeze). Pass
it back on any mutation:

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

The wire contract, as orch-hub publishes it and the client was reconciled
against on 2026-08-12 (details in `mandrel/evolution/hub.py`):

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
required: its negation is what tells a later `freeze` that the pool is the whole
eligible set rather than a prefix, so it is read from the feed and never
inferred from a short page. The cursor is an integer watermark on the wire and
opaque above the client, which converts at that boundary.

A catalog entry is orch-hub's own shape, not this repository's import record:
the client translates one into the other so the offline feed stays
interchangeable. Two fields are derived rather than copied — `completed` from
the entry's `archived` (orch-hub catalogs only reports whose task was archived
at publication, and this protocol archives only at completion close-out), and
each artifact's `media_type` from its published filename. orch-hub publishes
none of this repository's provenance fields, so translated records carry them
null, and a release assessment reads that as provenance it never got rather
than inventing a revision.

Artifact bytes are served byte for byte and verified here against the digests
the feed itself published. The route reports a failed integrity check in a
header instead of hiding the body, and the client deliberately ignores that
header for decisions: the check belongs to the side that did not publish the
bytes.

Two bounds are the client's own rather than the feed's, and both refuse with a
message naming what to change. Both were confirmed against the published feed
rather than assumed — nothing it serves redirects, and its largest artifact is
three orders of magnitude under the bound:

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

### What lands where

| Path | Owner | Committed |
|---|---|---|
| `.ai-evolution/state.json` | controller runtime | no — machine-local |
| `.ai-evolution/imported-artifacts/` | raw fetched bundles | no — raw evidence |
| `.ai-evolution/lock` | single-writer guard | no |
| `evolution/batches/<id>/manifest.json` | frozen membership | yes, immutable |
| `evolution/batches/<id>/findings.md` | analysis dispositions | yes |
| `evolution/batches/<id>/analysis-complete.json` | closure record | yes |
| `evolution/batches/<id>/proposed-tasks/` | change-task drafts | yes, inert |
| `evolution/ledger.jsonl` | sanitized audit | yes, append-only |
| `.ai-tasks/<id>-analysis.md` | generated analysis task | no — machine-local |

Privacy follows that split. Raw report content and any diagnostic quoting a
feed value stay under ignored `.ai-evolution/`; the ledger carries identities,
hashes, and a bounded vocabulary of reason codes only. Credentials live in the
environment and appear in no file, URL, or error message this tool writes.

The closure record and the manifests are written by the controller but
committed by you, like any other versioned artifact. Until that commit lands, a
finished analysis reads as finished only on this machine.

### Recovery

- **Interrupted run.** Re-run the same command. A freeze commits manifest →
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
- **A generated analysis task that no longer looks like one.** The controller
  identifies it by its id line, its `# Batch analysis — <batch-id>` heading, and
  the manifest path it cites, then reads its lifecycle. Keep those three intact
  while working the task; if the file is damaged, restore them or remove the
  file so the next run can write it again.

## Not Copied

Import and deploy intentionally skip local or sensitive files:

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
and leaves the working tree byte-identical (its last check asserts exactly
that). It runs:

| Check | What it covers |
|---|---|
| `whitespace` | trailing whitespace, CR line endings, final newline in tracked text files |
| `ruff` | Python lint (pyflakes plus the pycodestyle error rules) |
| `shellcheck` | every tracked shell script, found by extension or shebang |
| `boundary-lint` | `scripts/boundary-lint.sh` — canonical protocol boundaries |
| `pytest` | the `tests/` suite |
| `orchestrator-mock-loop` | `canonical/orchestrator/test_loop_mock.py` scenarios |
| `package-build` | wheel build, offline install into a throwaway venv, CLI starts |
| `tree-unchanged` | the run mutated no tracked file |

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

`.github/workflows/ci.yml` runs on pull requests, pushes to `main`, and manual
dispatch. It installs the `dev` extra and calls `scripts/check.sh` on Python
3.11 (the `requires-python` floor) and 3.14 (the orchestrator runtime). It
needs no repository secrets and requests only `contents: read`.

**Branch protection is a repository setting, not a file in this repo.** Until
the `gate (Python 3.11)` and `gate (Python 3.14)` checks are marked required
for `main` under Settings → Branches, a red run can still be merged. Adding the
workflow does not enable the gate; marking the checks required does.

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
