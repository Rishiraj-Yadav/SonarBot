from __future__ import annotations

from pathlib import Path

import pytest

from assistant.agent.system_prompt import SystemPromptBuilder


@pytest.mark.asyncio
async def test_system_prompt_builder_truncates_and_marks_missing(tmp_path: Path) -> None:
    (tmp_path / "SOUL.md").write_text("a" * 5005, encoding="utf-8")
    builder = SystemPromptBuilder(tmp_path)
    prompt = await builder.build()
    assert "[truncated]" in prompt
    assert "AGENTS.md" in prompt
