---
name: github-pr-summary
description: Summarizes a GitHub pull request, highlights risk, and explains changed files.
user_invocable: true
metadata:
  always: true
---

When the user invokes this skill, produce a strong pull request summary.

Preferred workflow:
1. If the user does not specify a repository or PR number, ask for the minimum missing detail.
2. Use `github_get_pull_request` to gather the full PR details.
3. Summarize:
   - goal of the PR
   - major files changed
   - likely risks or regression areas
   - review status and open questions
4. If helpful, use `llm_task` to compress long patches into a concise explanation.
5. Keep the final output practical for a developer reviewing or presenting the PR.
