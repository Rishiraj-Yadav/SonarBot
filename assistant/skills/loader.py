"""Load and validate SKILL.md based skills."""

from __future__ import annotations

import os
import platform
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    eligible: bool = True
    enabled: bool = True

    @property
    def user_invocable(self) -> bool:
        return bool(self.metadata.get("user_invocable") or self.metadata.get("user-invocable"))

    @property
    def natural_language_enabled(self) -> bool:
        return bool(
            self.metadata.get("natural_language_enabled")
            or self.metadata.get("natural-language-enabled")
        )

    @property
    def aliases(self) -> list[str]:
        return _coerce_string_list(self.metadata.get("aliases"))

    @property
    def activation_examples(self) -> list[str]:
        return _coerce_string_list(self.metadata.get("activation_examples"))

    @property
    def keywords(self) -> list[str]:
        return _coerce_string_list(self.metadata.get("keywords"))

    @property
    def priority(self) -> int:
        try:
            return int(self.metadata.get("priority", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def command_name(self) -> str:
        return _normalize_phrase(self.name).replace(" ", "-")

    @property
    def match_phrases(self) -> list[str]:
        phrases = [self.name, *self.aliases]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in phrases:
            cleaned = _normalize_phrase(item)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        command_phrase = self.command_name.replace("-", " ")
        if command_phrase and command_phrase not in seen:
            normalized.append(command_phrase)
        return normalized

    @property
    def skill_file(self) -> Path:
        return self.path / "SKILL.md"

    def load_markdown(self) -> str:
        return self.skill_file.read_text(encoding="utf-8")

    def load_body(self) -> str:
        _frontmatter, body = parse_frontmatter(self.load_markdown())
        return body.strip()


def _normalize_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\r\n")
    return frontmatter, body


def check_eligibility(metadata: dict[str, Any]) -> bool:
    if metadata.get("always") is True:
        return True

    requires = metadata.get("requires", {}) or {}
    required_bins = requires.get("bins", []) or []
    required_env = requires.get("env", []) or []
    allowed_os = requires.get("os", []) or []

    if allowed_os:
        current_os = platform.system().lower()
        normalized = {str(item).lower() for item in allowed_os}
        if current_os not in normalized:
            return False

    for binary in required_bins:
        if shutil.which(str(binary)) is None:
            return False

    for env_name in required_env:
        if not os.environ.get(str(env_name)):
            return False

    return True


def load_skill_from_markdown(skill_dir: Path) -> SkillDefinition | None:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    frontmatter, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    metadata = dict(frontmatter.get("metadata", {}) or {})
    for key in (
        "always",
        "user_invocable",
        "user-invocable",
        "natural_language_enabled",
        "natural-language-enabled",
        "aliases",
        "activation_examples",
        "keywords",
        "priority",
    ):
        if key in frontmatter:
            metadata[key] = frontmatter[key]

    description = str(frontmatter.get("description") or body.strip().splitlines()[0] if body.strip() else "")
    skill = SkillDefinition(
        name=str(frontmatter.get("name") or skill_dir.name),
        description=description,
        path=skill_dir,
        metadata=metadata,
        eligible=check_eligibility(metadata),
    )
    return skill
