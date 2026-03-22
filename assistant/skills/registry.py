"""In-memory skill registry."""

from __future__ import annotations

import json
from pathlib import Path

from assistant.skills.loader import SkillDefinition, load_skill_from_markdown


class SkillRegistry:
    def __init__(self, config) -> None:
        self.config = config
        self.skills: dict[str, SkillDefinition] = {}
        self._disabled: set[str] = set()
        self._load_state()

    @property
    def skill_dirs(self) -> list[Path]:
        return [
            Path(__file__).resolve().parent / "bundled",
            self.config.skills_home,
            self.config.agent.workspace_dir / "skills",
        ]

    def refresh(self) -> None:
        discovered: dict[str, SkillDefinition] = {}
        for base_dir in self.skill_dirs:
            base_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(base_dir.iterdir()):
                if not child.is_dir():
                    continue
                skill = load_skill_from_markdown(child)
                if skill is None or not skill.eligible:
                    if skill is not None:
                        discovered[skill.name] = skill
                    continue
                skill.enabled = skill.name not in self._disabled
                discovered[skill.name] = skill
        self.skills = discovered

    def start(self) -> None:
        self.refresh()

    def list_enabled(self) -> list[SkillDefinition]:
        return [skill for skill in self.skills.values() if skill.eligible and skill.enabled]

    def list_all(self) -> list[SkillDefinition]:
        return list(self.skills.values())

    def toggle(self, name: str) -> SkillDefinition:
        skill = self.skills[name]
        if skill.enabled:
            self._disabled.add(name)
            skill.enabled = False
        else:
            self._disabled.discard(name)
            skill.enabled = True
        self._save_state()
        return skill

    def find_user_invocable(self, command_name: str) -> SkillDefinition | None:
        target = command_name.lower()
        for skill in self.list_enabled():
            normalized = skill.name.lower().replace(" ", "-")
            if skill.user_invocable and normalized == target:
                return skill
        return None

    def active_count(self) -> int:
        return len(self.list_enabled())

    @property
    def state_path(self) -> Path:
        return self.config.assistant_home / "skills_state.json"

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self._disabled = set(data.get("disabled", []))

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"disabled": sorted(self._disabled)}
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
