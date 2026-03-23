---
name: github-repo-brief
description: Summarizes repository health with open pull requests, issues, recent activity, and likely blockers.
user_invocable: true
natural_language_enabled: true
aliases:
  - repo brief
  - repository status
  - repo status
activation_examples:
  - what's going on in this repo
  - repo status
  - summarize this repository
keywords:
  - repo
  - repository
  - status
  - blockers
priority: 6
metadata:
  always: true
---

When the user invokes this skill, give a practical repository status summary.

Preferred workflow:
1. Determine the target repository from the user message or the recent chat context.
2. Use GitHub repo, issue, and pull request tools to gather open work, recent changes, and active review threads.
3. Summarize:
   - open pull requests and review status
   - notable open issues
   - recent activity or inactivity
   - likely blockers, risks, or missing follow-ups
4. Keep the output actionable for someone deciding what to do next in that repo.
