from __future__ import annotations

from assistant.skills.registry import SkillRegistry


def _write_skill(skill_dir, content: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_skill_registry_matches_workspace_skill_naturally(app_config) -> None:
    skill_dir = app_config.agent.workspace_dir / "skills" / "gmail-triage"
    _write_skill(
        skill_dir,
        """---
name: gmail-triage
description: Inbox triage
user_invocable: true
natural_language_enabled: true
aliases:
  - inbox triage
activation_examples:
  - check my inbox
keywords:
  - inbox
  - email
priority: 5
---

Skill body.
""",
    )

    registry = SkillRegistry(app_config)
    registry.refresh()

    matches = registry.match_natural_language("check my inbox")

    assert matches
    assert matches[0].skill.name == "gmail-triage"
    assert registry.find_user_invocable("inbox-triage") is not None


def test_disabled_skill_is_not_auto_matched(app_config) -> None:
    skill_dir = app_config.agent.workspace_dir / "skills" / "memory-curator"
    _write_skill(
        skill_dir,
        """---
name: memory-curator
description: Memory helper
user_invocable: true
natural_language_enabled: true
aliases:
  - remember this
activation_examples:
  - remember this preference
keywords:
  - remember
  - memory
priority: 5
---

Skill body.
""",
    )

    registry = SkillRegistry(app_config)
    registry.refresh()
    registry.toggle("memory-curator")

    assert registry.match_natural_language("remember this preference") == []
