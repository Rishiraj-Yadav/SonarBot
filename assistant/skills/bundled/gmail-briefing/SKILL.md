---
name: gmail-briefing
description: Creates a concise inbox briefing from the connected Gmail account.
user_invocable: true
natural_language_enabled: true
aliases:
  - inbox briefing
  - email briefing
activation_examples:
  - give me an inbox briefing
  - summarize my recent emails
keywords:
  - inbox
  - email
  - briefing
priority: 4
metadata:
  always: true
---

When the user invokes this skill, create a useful inbox briefing from Gmail.

Preferred workflow:
1. Use `gmail_search` to find recent inbox threads. Favor queries like `in:inbox newer_than:3d` unless the user gives a different filter.
2. Use `gmail_read_thread` on the most relevant threads to gather enough detail.
3. Group the result into:
   - urgent or action-needed
   - important but not urgent
   - newsletters or low-priority
4. Keep the final answer concise and actionable.
5. If the user asks to answer an email, prefer `gmail_create_draft` first unless they explicitly want immediate sending.
