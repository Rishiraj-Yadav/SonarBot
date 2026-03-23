"""In-memory skill registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from assistant.skills.loader import SkillDefinition, load_skill_from_markdown


@dataclass(slots=True)
class SkillMatch:
    skill: SkillDefinition
    score: int
    exact: bool = False
    alias_hits: list[str] = field(default_factory=list)
    example_hits: list[str] = field(default_factory=list)
    keyword_hits: list[str] = field(default_factory=list)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _contains_phrase(query: str, phrase: str) -> bool:
    normalized_query = f" {_normalize_text(query)} "
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in normalized_query


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
        target = _normalize_text(command_name).replace(" ", "-")
        for skill in self.list_enabled():
            if not skill.user_invocable:
                continue
            aliases = [_normalize_text(alias).replace(" ", "-") for alias in skill.aliases]
            if skill.command_name == target or target in aliases:
                return skill
        return None

    def list_natural_language_enabled(self) -> list[SkillDefinition]:
        return [skill for skill in self.list_enabled() if skill.natural_language_enabled]

    def match_natural_language(self, query: str) -> list[SkillMatch]:
        matches: list[SkillMatch] = []
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return matches

        for skill in self.list_natural_language_enabled():
            exact_hits = [phrase for phrase in skill.match_phrases if _contains_phrase(normalized_query, phrase)]
            if exact_hits:
                matches.append(
                    SkillMatch(
                        skill=skill,
                        score=100,
                        exact=True,
                        alias_hits=exact_hits,
                    )
                )
                continue

            example_hits = [
                example for example in skill.activation_examples if _contains_phrase(normalized_query, example)
            ]
            keyword_hits = [
                keyword for keyword in skill.keywords if _contains_phrase(normalized_query, keyword)
            ]
            alias_hits = [
                alias for alias in skill.aliases if _contains_phrase(normalized_query, alias)
            ]

            score = 0
            if alias_hits:
                score += 6
            if example_hits:
                score += 4 * len(example_hits)
            if keyword_hits:
                score += min(6, 2 * len(keyword_hits))
            if score <= 0:
                continue
            matches.append(
                SkillMatch(
                    skill=skill,
                    score=score,
                    alias_hits=alias_hits,
                    example_hits=example_hits,
                    keyword_hits=keyword_hits,
                )
            )

        return sorted(matches, key=lambda item: (item.score, item.skill.priority, item.skill.name), reverse=True)

    def load_skill_prompt(self, name: str) -> str:
        skill = self.skills[name]
        return skill.load_markdown()

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
