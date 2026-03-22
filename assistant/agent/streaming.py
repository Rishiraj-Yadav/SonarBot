"""Streaming helpers."""


def merge_text_chunks(chunks: list[str]) -> str:
    return "".join(chunks).strip()
