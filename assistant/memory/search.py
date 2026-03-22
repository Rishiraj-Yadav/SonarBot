"""Hybrid memory search with temporal decay and MMR reranking."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from rank_bm25 import BM25Okapi

from assistant.agent.session import estimate_tokens
from assistant.memory.embeddings import get_embedder
from assistant.memory.indexer import chunk_text


@dataclass(slots=True)
class SearchChunk:
    chunk_id: str
    source: str
    text: str
    metadata: dict[str, Any]
    score: float = 0.0
    embedding: list[float] = field(default_factory=list)


class MemorySearchEngine:
    def __init__(self, memory_manager, indexer) -> None:
        self.memory_manager = memory_manager
        self.indexer = indexer

    def hybrid_search(self, query: str, limit: int = 5) -> list[str]:
        chunks = self._load_chunks()
        if not chunks:
            return []

        bm25_results = self._bm25_search(chunks, query, limit=max(limit * 4, 20))
        semantic_results = self.indexer.query(query, limit=max(limit * 4, 20))
        merged = self._merge_results(chunks, bm25_results, semantic_results)
        if not merged:
            return self._fallback_search(chunks, query, limit)

        candidates = self._apply_temporal_decay(merged)
        candidates = candidates[:20]
        reranked = self._apply_mmr(query, candidates, limit)
        return [self._format_chunk(chunk) for chunk in reranked if chunk.text.strip()]

    def stats(self) -> dict[str, str | int]:
        documents = self.memory_manager.iter_memory_documents()
        chunks = self._load_chunks()
        oldest = min((document.written_at for document in documents), default=None)
        combined_messages = [{"content": document.content} for document in documents]
        return {
            "total_entries_indexed": len(chunks),
            "oldest_entry_date": oldest.date().isoformat() if oldest is not None else "",
            "approximate_token_coverage": estimate_tokens(combined_messages),
        }

    def _load_chunks(self) -> list[SearchChunk]:
        items: list[SearchChunk] = []
        for document in self.memory_manager.iter_memory_documents():
            text_chunks = chunk_text(document.content)
            for chunk_index, chunk in enumerate(text_chunks):
                items.append(
                    SearchChunk(
                        chunk_id=f"{document.source}:{chunk_index}",
                        source=document.source,
                        text=chunk,
                        metadata={
                            "source": document.source,
                            "chunk_index": chunk_index,
                            "date": document.written_at.isoformat(),
                            "kind": "text",
                            "image_path": document.image_paths[0] if document.image_paths else "",
                        },
                    )
                )
            for image_index, image_path in enumerate(document.image_paths):
                items.append(
                    SearchChunk(
                        chunk_id=f"{document.source}:image:{image_index}",
                        source=document.source,
                        text=f"Image memory for {image_path}",
                        metadata={
                            "source": document.source,
                            "chunk_index": len(text_chunks) + image_index,
                            "date": document.written_at.isoformat(),
                            "kind": "image",
                            "image_path": image_path,
                        },
                    )
                )
        return items

    def _bm25_search(self, chunks: list[SearchChunk], query: str, limit: int) -> list[SearchChunk]:
        tokenized_documents = [chunk.text.lower().split() for chunk in chunks]
        if not tokenized_documents:
            return []
        bm25 = BM25Okapi(tokenized_documents)
        scores = bm25.get_scores(query.lower().split())
        results: list[SearchChunk] = []
        for chunk, score in zip(chunks, scores, strict=False):
            if score <= 0:
                continue
            results.append(
                SearchChunk(
                    chunk_id=chunk.chunk_id,
                    source=chunk.source,
                    text=chunk.text,
                    metadata=dict(chunk.metadata),
                    score=float(score),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    def _merge_results(
        self,
        chunks: list[SearchChunk],
        bm25_results: list[SearchChunk],
        semantic_results: list[dict[str, Any]],
    ) -> list[SearchChunk]:
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        merged: dict[str, SearchChunk] = {}

        for item in semantic_results:
            key = str(item["id"])
            base = chunk_map.get(key)
            metadata = {**(dict(base.metadata) if base else {}), **dict(item.get("metadata", {}) or {})}
            merged[key] = merged.get(
                key,
                SearchChunk(
                    chunk_id=key,
                    source=metadata.get("source", base.source if base else "memory"),
                    text=str(item.get("document", base.text if base else "")),
                    metadata=metadata,
                ),
            )
            merged[key].score += 0.6 * float(item.get("score", 0.0))

        for item in bm25_results:
            merged[item.chunk_id] = merged.get(
                item.chunk_id,
                SearchChunk(
                    chunk_id=item.chunk_id,
                    source=item.source,
                    text=item.text,
                    metadata=dict(item.metadata),
                ),
            )
            merged[item.chunk_id].score += 0.4 * float(item.score)

        return sorted(merged.values(), key=lambda entry: entry.score, reverse=True)

    def _apply_temporal_decay(self, candidates: list[SearchChunk]) -> list[SearchChunk]:
        now = datetime.now(timezone.utc)
        decay_lambda = self.memory_manager.config.memory.temporal_decay_lambda
        adjusted: list[SearchChunk] = []
        for candidate in candidates:
            written_at = self._parse_datetime(candidate.metadata.get("date"))
            if written_at is None:
                adjusted.append(candidate)
                continue
            days_since_written = max((now - written_at).total_seconds() / 86400.0, 0.0)
            candidate.score *= math.exp(-decay_lambda * days_since_written)
            adjusted.append(candidate)
        adjusted.sort(key=lambda entry: entry.score, reverse=True)
        return adjusted

    def _apply_mmr(self, query: str, candidates: list[SearchChunk], limit: int) -> list[SearchChunk]:
        if not candidates:
            return []
        embedder = get_embedder()
        query_vector = self._normalize_vector(self._as_vector(embedder.encode([query])[0]))
        encoded_candidates = embedder.encode([candidate.text for candidate in candidates])
        vectors = [self._normalize_vector(self._as_vector(vector)) for vector in encoded_candidates]
        for candidate, vector in zip(candidates, vectors, strict=False):
            candidate.embedding = vector

        lambda_param = self.memory_manager.config.memory.mmr_lambda
        remaining = list(candidates)
        selected: list[SearchChunk] = []

        while remaining and len(selected) < limit:
            if not selected:
                selected.append(remaining.pop(0))
                continue

            best_index = 0
            best_score = float("-inf")
            for index, candidate in enumerate(remaining):
                similarity_to_query = self._cosine_similarity(candidate.embedding, query_vector)
                diversity_penalty = max(
                    (self._cosine_similarity(candidate.embedding, chosen.embedding) for chosen in selected),
                    default=0.0,
                )
                mmr_score = (lambda_param * candidate.score * similarity_to_query) - (
                    (1.0 - lambda_param) * diversity_penalty
                )
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_index = index
            selected.append(remaining.pop(best_index))

        return selected

    def _fallback_search(self, chunks: list[SearchChunk], query: str, limit: int) -> list[str]:
        query_lower = query.lower()
        fallback = [self._format_chunk(chunk) for chunk in chunks if query_lower in chunk.text.lower()]
        return fallback[:limit]

    def _format_chunk(self, chunk: SearchChunk) -> str:
        image_path = chunk.metadata.get("image_path")
        suffix = f" [image: {image_path}]" if image_path else ""
        return f"[{chunk.source}] {chunk.text}{suffix}"

    def _as_vector(self, value: Any) -> list[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        return [float(item) for item in value]

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(item * item for item in vector))
        if norm == 0:
            return vector
        return [item / norm for item in vector]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=False))

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
