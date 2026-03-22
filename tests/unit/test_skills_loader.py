from __future__ import annotations

from assistant.skills.loader import check_eligibility


def test_skill_with_missing_binary_is_ineligible() -> None:
    metadata = {"requires": {"bins": ["definitely-not-installed-sonarbot-bin"]}}
    assert check_eligibility(metadata) is False


def test_skill_with_always_true_is_eligible() -> None:
    metadata = {"always": True, "requires": {"bins": ["definitely-not-installed-sonarbot-bin"]}}
    assert check_eligibility(metadata) is True
