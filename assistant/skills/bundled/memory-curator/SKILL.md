---
name: memory-curator
description: Decides what user facts and durable work context should be stored or recalled from memory.
user_invocable: true
natural_language_enabled: true
aliases:
  - memory helper
  - remember this
activation_examples:
  - remember this
  - save this for later
  - what do you already know about me
keywords:
  - remember
  - memory
  - save
  - later
priority: 5
metadata:
  always: true
---

When the user invokes this skill, manage durable memory deliberately.

Preferred workflow:
1. Use `memory_search` or `memory_get` first to avoid asking the user to repeat known context.
2. If the user shares a stable preference, durable fact, recurring workflow, or important long-lived project detail, use `memory_write`.
3. Prefer daily memory for short-horizon work items and long-term memory for stable identity or preference data.
4. Explain briefly what was remembered or what existing memory was found.
5. Do not write noisy, one-off chatter into durable memory.
