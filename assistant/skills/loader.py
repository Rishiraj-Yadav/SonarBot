"""Load and validate SKILL.md based skills."""

from __future__ import annotations

import os
import platform
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
    if "always" in frontmatter:
        metadata["always"] = frontmatter["always"]
    if "user_invocable" in frontmatter:
        metadata["user_invocable"] = frontmatter["user_invocable"]
    if "user-invocable" in frontmatter:
        metadata["user-invocable"] = frontmatter["user-invocable"]

    description = str(frontmatter.get("description") or body.strip().splitlines()[0] if body.strip() else "")
    skill = SkillDefinition(
        name=str(frontmatter.get("name") or skill_dir.name),
        description=description,
        path=skill_dir,
        metadata=metadata,
        eligible=check_eligibility(metadata),
    )
    return skill
