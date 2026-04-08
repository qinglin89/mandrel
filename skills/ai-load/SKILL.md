---
name: ai-load
description: Load project context from .ai/. Call at the start of any task
requiring codebase knowledge not already present in this conversation(including when you want to explore/read current repo directly, like glob/grep/find). e.g. project overview, architecture, design, apis, conventions, modules, features, history tasks/decisions.
Do NOT call if context/conversation-info is already sufficient for the task.
---
Follow the .ai/ retrieval protocol(section 8,9,10) in CLAUDE.md(no need to reload, it's on top of the conversation).

BEFORE any action, assess: is current context/conversation-info sufficient for the task?

- YES → output "Context sufficient" and stop. Do NOT proceed.
- NO  → continue below.

REQUIRED steps — execute in order, do NOT skip:

1. Read `.ai/index.md` — skip if already in this conversation
2. From index: follow the .ai/ retrieval protocol (CLAUDE.md) to explore .ai/
3. After retrieval protocol exits, re-assess sufficiency:
   - Sufficient → output "Context loaded: [list]" and stop
   - Still insufficient → output "Fallback: direct repo exploration" and stop
