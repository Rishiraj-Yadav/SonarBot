from __future__ import annotations

import pytest

from assistant.memory.manager import MemoryManager


@pytest.mark.asyncio
async def test_memory_manager_writes_and_reads(app_config) -> None:
    manager = MemoryManager(app_config)

    await manager.write_daily("remember this from today")
    await manager.write_long_term("Favorite Food", "Masala dosa")

    recent = await manager.read_today_and_yesterday()
    long_term = await manager.read_long_term()
    search_results = await manager.search("dosa", limit=3)

    assert "remember this from today" in recent
    assert "Masala dosa" in long_term
    assert any("dosa" in item.lower() for item in search_results)
