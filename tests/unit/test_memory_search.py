from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assistant.memory.manager import MemoryManager


class DeterministicEmbedder:
    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "gamma" in lowered:
                vectors.append([0.8, 0.6])
            elif "alpha" in lowered or "beta" in lowered or "project" in lowered or "release" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


@pytest.mark.asyncio
async def test_temporal_decay_prefers_recent_memory(app_config) -> None:
    workspace = app_config.agent.workspace_dir
    memory_dir = workspace / "memory"
    old_path = memory_dir / "2026-01-01.md"
    recent_path = memory_dir / "2026-03-23.md"
    old_path.write_text("project alpha update", encoding="utf-8")
    recent_path.write_text("project alpha update", encoding="utf-8")

    old_time = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    recent_time = datetime.now(timezone.utc).timestamp()
    os.utime(old_path, (old_time, old_time))
    os.utime(recent_path, (recent_time, recent_time))

    manager = MemoryManager(app_config)

    old_source = str(old_path.relative_to(workspace))
    recent_source = str(recent_path.relative_to(workspace))
    manager.search_engine.indexer.query = lambda _query, limit: [
        {"id": f"{old_source}:0", "document": "project alpha update", "metadata": {"source": old_source}, "score": 1.0},
        {"id": f"{recent_source}:0", "document": "project alpha update", "metadata": {"source": recent_source}, "score": 1.0},
    ][:limit]

    results = await manager.search("project alpha", limit=2)

    assert results
    assert results[0].startswith(f"[{recent_source}]")


@pytest.mark.asyncio
async def test_mmr_promotes_more_diverse_memory_results(app_config, monkeypatch) -> None:
    workspace = app_config.agent.workspace_dir
    memory_dir = workspace / "memory"
    alpha_path = memory_dir / "2026-03-20.md"
    beta_path = memory_dir / "2026-03-21.md"
    gamma_path = memory_dir / "2026-03-22.md"
    alpha_path.write_text("python release alpha notes", encoding="utf-8")
    beta_path.write_text("python release beta notes", encoding="utf-8")
    gamma_path.write_text("python gamma async migration guide", encoding="utf-8")

    monkeypatch.setattr("assistant.memory.search.get_embedder", lambda: DeterministicEmbedder())

    manager = MemoryManager(app_config)
    alpha_source = str(alpha_path.relative_to(workspace))
    beta_source = str(beta_path.relative_to(workspace))
    gamma_source = str(gamma_path.relative_to(workspace))
    manager.search_engine.indexer.query = lambda _query, limit: [
        {"id": f"{alpha_source}:0", "document": "python release alpha notes", "metadata": {"source": alpha_source}, "score": 1.0},
        {"id": f"{beta_source}:0", "document": "python release beta notes", "metadata": {"source": beta_source}, "score": 1.0},
        {"id": f"{gamma_source}:0", "document": "python gamma async migration guide", "metadata": {"source": gamma_source}, "score": 2.0},
    ][:limit]

    results = await manager.search("python release", limit=2)

    assert len(results) == 2
    assert any(gamma_source in result for result in results)
