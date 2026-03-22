"""Format skill metadata for prompt injection."""

from __future__ import annotations

from xml.sax.saxutils import escape


def format_skills_for_prompt(skills) -> str:
    if not skills:
        return "<skills />"
    inner = "".join(
        f"<skill><name>{escape(skill.name)}</name><description>{escape(skill.description)}</description></skill>"
        for skill in skills
    )
    return f"<skills>{inner}</skills>"
