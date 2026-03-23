from __future__ import annotations

from assistant.skills.loader import check_eligibility, load_skill_from_markdown


def test_skill_with_missing_binary_is_ineligible() -> None:
    metadata = {"requires": {"bins": ["definitely-not-installed-sonarbot-bin"]}}
    assert check_eligibility(metadata) is False


def test_skill_with_always_true_is_eligible() -> None:
    metadata = {"always": True, "requires": {"bins": ["definitely-not-installed-sonarbot-bin"]}}
    assert check_eligibility(metadata) is True


def test_skill_loader_parses_natural_language_metadata(tmp_path) -> None:
    skill_dir = tmp_path / "daily-briefing"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: daily-briefing
description: Daily briefing skill
user_invocable: true
natural_language_enabled: true
aliases:
  - morning briefing
activation_examples:
  - what should i focus on today
keywords:
  - briefing
  - priorities
priority: 6
---

Skill body.
""",
        encoding="utf-8",
    )

    skill = load_skill_from_markdown(skill_dir)

    assert skill is not None
    assert skill.user_invocable is True
    assert skill.natural_language_enabled is True
    assert skill.aliases == ["morning briefing"]
    assert skill.activation_examples == ["what should i focus on today"]
    assert skill.keywords == ["briefing", "priorities"]
    assert skill.priority == 6
    assert skill.command_name == "daily-briefing"
