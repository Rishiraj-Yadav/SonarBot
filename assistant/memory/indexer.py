"""Memory chunking and vector indexing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from assistant.memory.embeddings import get_embedder


def chunk_text(content: str, words_per_chunk: int = 200) -> list[str]:
    words = content.split()
    if not words:
        return []
    return [" ".join(words[index : index + words_per_chunk]) for index in range(0, len(words), words_per_chunk)]


class MemoryIndexer:
    def __init__(self, config) -> None:
        self.config = config
        self._collection = None

    def upsert_content(
        self,
        source: str,
        content: str,
        when: datetime | None = None,
        image_path: str | None = None,
    ) -> None:
        chunks = chunk_text(content)
        multimodal_chunks = self._build_multimodal_chunks(content, image_path)
        documents = chunks + multimodal_chunks
        if not documents:
            return

        collection = self._get_collection()
        if collection is None:
            return

        embedder = get_embedder()
        embeddings = embedder.encode(documents)
        if hasattr(embeddings, "tolist"):
            embeddings = embeddings.tolist()
        iso_when = (when or datetime.utcnow()).isoformat()
        ids = [f"{source}:{index}" for index in range(len(chunks))]
        metadatas: list[dict[str, Any]] = [
            {"source": source, "date": iso_when, "chunk_index": index, "kind": "text"} for index in range(len(chunks))
        ]
        for extra_index, _chunk in enumerate(multimodal_chunks):
            ids.append(f"{source}:image:{extra_index}")
            metadatas.append(
                {
                    "source": source,
                    "date": iso_when,
                    "chunk_index": len(chunks) + extra_index,
                    "kind": "image",
                    "image_path": image_path or "",
                }
            )
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    def query(self, query: str, limit: int) -> list[dict[str, Any]]:
        collection = self._get_collection()
        if collection is None:
            return []

        embedder = get_embedder()
        query_embedding = embedder.encode([query])[0]
        if hasattr(query_embedding, "tolist"):
            query_embedding = query_embedding.tolist()
        results = collection.query(query_embeddings=[query_embedding], n_results=max(limit, 1))
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        output: list[dict[str, Any]] = []
        for doc_id, document, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            output.append(
                {
                    "id": doc_id,
                    "document": document,
                    "metadata": metadata or {},
                    "score": 1.0 / (1.0 + float(distance)),
                }
            )
        return output

    def _build_multimodal_chunks(self, content: str, image_path: str | None) -> list[str]:
        if not image_path:
            return []
        image_name = Path(image_path).stem.replace("-", " ").replace("_", " ").strip()
        summary = " ".join(content.split()[:40])
        return [f"Image memory for {image_name}. Related note: {summary}".strip()]

    def _get_collection(self):
        if not getattr(self.config.memory, "vector_enabled", True):
            return None
        if self._collection is not None:
            return self._collection

        try:
            import chromadb  # type: ignore
        except Exception:
            return None

        client = chromadb.PersistentClient(path=str(self.config.chroma_dir))
        self._collection = client.get_or_create_collection("assistant_memory")
        return self._collection
