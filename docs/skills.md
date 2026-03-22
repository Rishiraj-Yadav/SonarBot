# Skills Guide

## What A Skill Is

A skill is a directory containing a `SKILL.md` file. SonarBot scans bundled skills, user skills in `~/.assistant/skills`, and workspace skills in `workspace/skills`.

## File Format

`SKILL.md` supports YAML frontmatter followed by markdown instructions.

Example:

```md
---
name: release-helper
description: Helps summarize release notes.
user_invocable: true
metadata:
  requires:
    bins: ["git"]
    env: []
    os: ["windows", "linux", "darwin"]
---

When invoked, inspect the current changelog and summarize the latest release.
```

## Eligibility Rules

Skills are enabled only when:

- required binaries are present on `PATH`
- required environment variables are set
- the current OS matches the declared allowlist

If `always: true` is set, the skill is always considered eligible.

## Lazy Loading

Only the name and description are kept in memory for prompt injection. The full `SKILL.md` content is loaded only when the skill is explicitly invoked.

## Invocation

- prompt injection: all enabled skills are listed in compact XML form
- slash commands: if `user_invocable: true`, `/skill-name` routes through the agent with the full skill markdown appended to the active system context

## Writing A Good Skill

- keep the description short and concrete
- describe the tool assumptions clearly
- avoid embedding secrets in the file
- prefer reusable operating instructions over one-off prompt text
