"""Hybrid memory search over markdown memory files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from assistant.memory.indexer import chunk_text


@dataclass(slots=True)
class SearchChunk:
    chunk_id: str
    source: str
    text: str
    metadata: dict[str, Any]


class MemorySearchEngine:
    def __init__(self, memory_manager, indexer) -> None:
        self.memory_manager = memory_manager
        self.indexer = indexer

    def hybrid_search(self, query: str, limit: int = 5) -> list[str]:
        chunks = self._load_chunks()
        bm25_results = self._bm25_search(chunks, query, limit=max(limit * 2, 5))
        semantic_results = self.indexer.query(query, limit=max(limit * 2, 5))

        merged: dict[str, dict[str, Any]] = {}
        for item in semantic_results:
            key = item["id"]
            merged.setdefault(
                key,
                {
                    "source": item.get("metadata", {}).get("source", "memory"),
                    "text": item.get("document", ""),
                    "score": 0.0,
                },
            )
            merged[key]["score"] += 0.6 * float(item.get("score", 0.0))

        for item in bm25_results:
            key = item["chunk_id"]
            merged.setdefault(key, {"source": item["source"], "text": item["text"], "score": 0.0})
            merged[key]["score"] += 0.4 * float(item.get("score", 0.0))

        ordered = sorted(merged.items(), key=lambda pair: pair[1]["score"], reverse=True)
        results = [
            f"[{entry['source']}] {entry['text']}"
            for _, entry in ordered[:limit]
            if entry["text"].strip()
        ]
        if results:
            return results

        query_lower = query.lower()
        fallback = [
            f"[{chunk.source}] {chunk.text}"
            for chunk in chunks
            if query_lower in chunk.text.lower()
        ]
        return fallback[:limit]

    def _load_chunks(self) -> list[SearchChunk]:
        items: list[SearchChunk] = []
        for source, content in self.memory_manager.iter_memory_documents():
            for chunk_index, chunk in enumerate(chunk_text(content)):
                items.append(
                    SearchChunk(
                        chunk_id=f"{source}:{chunk_index}",
                        source=source,
                        text=chunk,
                        metadata={"source": source, "chunk_index": chunk_index},
                    )
                )
        return items

    def _bm25_search(self, chunks: list[SearchChunk], query: str, limit: int) -> list[dict[str, Any]]:
        if not chunks:
            return []
        tokenized_documents = [chunk.text.lower().split() for chunk in chunks]
        bm25 = BM25Okapi(tokenized_documents)
        scores = bm25.get_scores(query.lower().split())
        results: list[dict[str, Any]] = []
        for index, score in enumerate(scores):
            if score <= 0:
                continue
            chunk = chunks[index]
            results.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "source": chunk.source,
                    "text": chunk.text,
                    "score": float(score),
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]
