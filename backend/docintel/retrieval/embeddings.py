"""Dense embeddings for hybrid retrieval and semantic search.

* ``fastembed`` — a local multilingual ONNX model (English queries find Vietnamese
  clauses); downloaded once into ``data/models``; no data leaves the machine.
* ``openai`` — any OpenAI-compatible ``/embeddings`` endpoint (e.g. Ollama).
* ``none`` — BM25 only.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

import httpx
import numpy as np

from ..settings import Settings, get_settings


class Embedder:
    name = "none"

    async def embed(self, texts: list[str]) -> np.ndarray | None:
        return None


class FastEmbedder(Embedder):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.name = f"fastembed:{settings.embeddings_model}"
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            cache = self.settings.data_dir / "models"
            cache.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(self.settings.embeddings_model, cache_dir=str(cache))
        return self._model

    def _embed_sync(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._load().embed(texts, batch_size=32)), dtype=np.float32)
        return vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)

    async def embed(self, texts: list[str]) -> np.ndarray | None:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        return await asyncio.to_thread(self._embed_sync, texts)


class OpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.name = f"openai:{settings.embeddings_model}"

    async def embed(self, texts: list[str]) -> np.ndarray | None:
        s = self.settings
        if not s.embeddings_base_url:
            return None
        headers = {"Content-Type": "application/json"}
        if s.embeddings_api_key:
            headers["Authorization"] = f"Bearer {s.embeddings_api_key.get_secret_value()}"
        out = []
        async with httpx.AsyncClient(timeout=120) as http:
            for i in range(0, len(texts), 64):
                r = await http.post(s.embeddings_base_url.rstrip("/") + "/embeddings", headers=headers,
                                    json={"model": s.embeddings_model, "input": texts[i:i + 64]})
                r.raise_for_status()
                out.extend(d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"]))
        vecs = np.array(out, dtype=np.float32)
        return vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)


@lru_cache
def _cached(provider: str, model: str, data_dir: str) -> Embedder:
    s = get_settings()
    if provider == "fastembed":
        try:
            import fastembed  # noqa: F401
            return FastEmbedder(s)
        except ImportError:
            return Embedder()
    if provider == "openai":
        return OpenAIEmbedder(s)
    return Embedder()


def get_embedder(settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()
    return _cached(s.embeddings_provider, s.embeddings_model, str(s.data_dir))


def to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(blob: bytes | None) -> np.ndarray | None:
    return None if not blob else np.frombuffer(blob, dtype=np.float32)
