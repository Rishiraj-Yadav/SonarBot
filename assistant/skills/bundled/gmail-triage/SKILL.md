---
name: gmail-triage
description: Classifies inbox threads into urgent, important, and low-priority with suggested next steps.
user_invocable: true
natural_language_enabled: true
aliases:
  - inbox triage
  - email triage
activation_examples:
  - check my inbox
  - triage my emails
  - what important mails do i have
keywords:
  - inbox
  - email
  - triage
  - urgent
priority: 6
metadata:
  always: true
---

When the user invokes this skill, perform a quick but useful inbox triage.

Preferred workflow:
1. Use `gmail_search` to fetch recent inbox threads, defaulting to `in:inbox newer_than:3d` unless the user asks for a different window.
2. Use `gmail_read_thread` on the most relevant threads to understand why each thread matters.
3. Group results into:
   - urgent or action-needed
   - important but not urgent
   - low-priority or informational
4. For urgent threads, suggest the next concrete action.
5. If the user wants help replying, prefer `gmail_create_draft` before any direct send action.
