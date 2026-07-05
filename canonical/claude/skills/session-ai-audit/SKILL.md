---
name: session-ai-audit
description: Audit whether a past Claude Code work session (or every session of a task) actually READ the `.ai/` memory before acting — read-first vs scratch-first — and report whether `.ai/` is helping or being ignored. Accepts a session id OR a task slug; a multi-session task resolves to its whole session arc via the task's session log. Use when asked to evaluate/audit a session's `.ai/` usage, or whether the `.ai/` memory snapshot is actually working.
---

# Session `.ai/` usage audit

Answer one question for the user: **does the `.ai/` snapshot get READ when it should?**
A session is *read-first* if it consulted the relevant memory doc before a decision, and
*scratch-first* if it re-derived from code while a relevant doc sat unread. Writing into
`.ai/` already works; this skill checks the read side.

## How it works (so you judge correctly)

Two stages. `scripts/session_ai_audit.py` does **stage 1** (deterministic): it compresses a
raw session `.jsonl` into a short, line-referenced TRACE and a deterministic summary, tagging
every `.ai/` / `.ai-tasks/` access `★MEM`. **You are stage 2** (semantic judgment). You are a
*fresh* session — independent of the one being audited — and you already hold the `.ai/`
routing catalog in your eager context, so you are the right judge. Do **not** call the script's
own `--judge`/`--model` (that spawns a redundant nested model); you judge directly.

Run everything from the repo root. Confirm `scripts/session_ai_audit.py` exists first.

## Step 1 — resolve the target to session id(s)

The user gives you either a **session id** (UUID) or a **task slug**.

- **Looks like a UUID** (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) → that one session. Skip to Step 2.
- **Otherwise treat it as a task slug** → run:

  ```bash
  python3 scripts/session_ai_audit.py --resolve-task <slug>
  ```

  This reads the task file (`.ai-tasks/archive/*<slug>*.md`, or active `.ai-tasks/*<slug>*.md`)
  and prints **every** session of the task in chronological order — parsed from the
  `## Session log` headers (`### <date> / <uuid> / (a → b)`), which record the real session
  UUIDs, plus `claimed-by`. A multi-session task therefore resolves to its **whole arc** — you
  do not pick one; auditing all of them and showing the trend is the point.

  Handle the output:
  - 0 task files → tell the user, show `ls .ai-tasks/archive/` is the place to find the slug,
    and ask for an exact slug or session id.
  - \>1 task file matched → list them and ask which (or audit all if clearly the same work).
  - Any session marked `MISSING` → its `.jsonl` is gone; skip it and note the gap.

## Step 2 — get the trace + judging brief for each session

For each resolved session id:

```bash
python3 scripts/session_ai_audit.py <session-id> --print-prompt
```

Then `Read` `.captures/session-audit/<session-id>/prompt.txt` — this path is **deterministic**
(`<session-id>` is the one you ran), so construct it directly; do not parse the script's stdout.
It is a self-contained judging brief: the read-first/scratch-first rubric, the **session-time**
`.ai/` catalog (the snapshot AS OF session start, with a doc inventory), and the compressed trace.
Judge that session against it and produce its verdict.

Three markers in the trace/brief are load-bearing — read them right or you will mis-score:
- The `PHASE ════ close-out … ════` divider: everything after it is the **write path**
  (`/ai-sync-v2` absorbing into `.ai/`), **not** consultation. Judge read-side only from
  *before* the divider.
- `↳subN` lines are **subagent** actions spliced inline. Subagents mostly read code; but a
  `↳sub … ★MEM` read means the session consulted that doc *by delegation* — credit it.
- The catalog is **session-time**, not today's. A doc absent from its inventory did not exist to
  read — never charge a session for "skipping" a doc it (or a later task) wrote. Many tasks
  DISCOVER knowledge in code and ABSORB it at close-out: that is the loop working, not a miss.
  Reserve a critical SCRATCH verdict for re-deriving a doc that already existed, or for skipping
  the task's own prefetch list.

(The brief's own line "Do NOT use tools, output only the report" governs the *judging output*
for that one session — you still orchestrate resolution and synthesis around it.)

For a very large multi-session task where ingesting every trace is impractical, you may instead
offload per-session judging to the script: `python3 scripts/session_ai_audit.py <id> --model
sonnet`, then read each `.captures/session-audit/<id>/verdict.md` and synthesize. Prefer judging
directly when the volume is manageable.

## Step 3 — report back

**Single session** — give the standard verdict (Verdict / Decisions table with T-ref evidence /
Missed consultations / Summary), exactly as the brief specifies.

**Multi-session task** — after each session's verdict, synthesize a lifecycle report:

```
# .ai/ usage audit — <task slug>  (<N> sessions)

**Headline:** <e.g. "MIXED — .ai/ consulted read-first in 2/5 sessions, scratch-first in 3/5">

## Per session
| # | session (short) | date | verdict | what .ai/ did — or was skipped (with T-refs) |
| 1 | a9b7b17f | 06-07 | SCRATCH-FIRST | edited net-mode flip; design/tradeoffs FLIP entry never opened (T..) |
| … |

For any MIXED or SCRATCH-FIRST session, the "what .ai/ did — or was skipped" cell MUST name the
specific lazy doc that was relevant but left unread, plus the routing that points to it (e.g.
"map.md → Position Tracking → features/position-tracking.md, never opened"). Don't just say
"scratch-first" — name the doc that would have helped.

## Is .ai/ working for this task?
<2–4 sentences: the trend across the arc — did read-side discipline improve as the task
matured, or stay scratch-first? Where did memory demonstrably help? Where was a relevant lazy
doc skipped despite existing (name the doc + the routing that points to it)?>

## Suggestion
<one concrete, specific change to improve read-side usage — or "none — exemplary">
```

## Guardrails

- Read-only. Do **not** edit `.ai/`, archive tasks, or touch `.ai-tasks/`. This is an audit.
- Credit only what the trace shows. The eager set (`index`, `map`, `overview`, `architecture`,
  current `design` entrypoint, current `conventions` entrypoint, `.ai-tasks/index`) is always in context, so *not*
  reading it is **not** scratch-first — only unread **lazy** docs count against a session.
- Be honest about blind spots: subagent reads are spliced but their depth>1 grandchildren are
  not; a crashed session may have done work without a session-log entry.
- Keep the final report skimmable. The user wants a verdict on whether `.ai/` earns its keep,
  not a transcript replay.
