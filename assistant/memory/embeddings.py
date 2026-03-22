"""Embedding helpers with lazy optional dependencies."""

from __future__ import annotations

import hashlib
from functools import lru_cache


class HashingEmbedder:
    def encode(self, texts: list[str] | str) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        return [self._encode_single(text) for text in texts]

    def _encode_single(self, text: str, dims: int = 32) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = list(digest[:dims])
        return [value / 255.0 for value in values]


@lru_cache(maxsize=1)
def get_embedder():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        return HashingEmbedder()
