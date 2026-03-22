from __future__ import annotations

from pathlib import Path

import pytest

from assistant.tools.exec_tool import build_exec_tool
from assistant.tools.file_tool import build_file_tools


@pytest.mark.asyncio
async def test_file_tool_blocks_path_traversal(tmp_path: Path) -> None:
    tool = build_file_tools(tmp_path)[0]
    with pytest.raises(ValueError):
        await tool.handler({"path": "../outside.txt"})


@pytest.mark.asyncio
async def test_exec_tool_returns_stdout(tmp_path: Path) -> None:
    tool = build_exec_tool(tmp_path)
    result = await tool.handler({"command": "python -c \"print('hello')\"", "timeout": 5})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]
