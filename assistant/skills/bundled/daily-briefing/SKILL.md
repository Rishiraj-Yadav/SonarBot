---
name: daily-briefing
description: Creates a compact daily briefing using Gmail, GitHub, memory, and standing orders.
user_invocable: true
natural_language_enabled: true
aliases:
  - morning briefing
  - daily summary
  - focus briefing
activation_examples:
  - what should i focus on today
  - give me my morning briefing
  - summarize my morning priorities
keywords:
  - briefing
  - priorities
  - focus
  - today
priority: 6
metadata:
  always: true
---

When the user invokes this skill, create a high-signal briefing for the current day.

Preferred workflow:
1. Use `memory_get` and `memory_search` to gather durable context, standing orders, and recent notes.
2. Use `gmail_search` and `gmail_read_thread` to find urgent or important inbox items from the last few days.
3. Use GitHub repo and pull request tools to identify recent engineering activity, open PRs, and likely blockers.
4. Use `llm_task` if needed to compress multiple signals into a concise final summary.
5. Present the result in sections:
   - today's likely priorities
   - urgent follow-ups
   - important but non-urgent work
   - suggested next actions
6. Prefer clarity and prioritization over exhaustiveness.
