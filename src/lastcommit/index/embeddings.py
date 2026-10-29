"""Local embeddings.  Default: fastembed (ONNX, CPU, no torch, no server).
Optional: Ollama's /api/embed.  Both are swappable via config; failure to
build an embedder degrades gracefully to keyword-only search."""

from __future__ import annotations

from ..llm.config import EmbedConfig


class BaseEmbedder:
    name = "base"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError


class FastEmbedder(BaseEmbedder):
    def __init__(self, model: str):
        from fastembed import TextEmbedding

        self.name = f"fastembed:{model}"
        self._model = TextEmbedding(model_name=model)
        self.dim = len(list(self._model.embed(["dimension probe"]))[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]


class OllamaEmbedder(BaseEmbedder):
    def __init__(self, model: str, base_url: str):
        import httpx

        self.name = f"ollama:{model}"
        self._url = base_url.rstrip("/") + "/api/embed"
        self._client = httpx.Client(timeout=60)
        self._model = model
        resp = self._client.post(self._url, json={"model": model, "input": ["dimension probe"]})
        resp.raise_for_status()
        self.dim = len(resp.json()["embeddings"][0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post(self._url, json={"model": self._model, "input": texts})
        resp.raise_for_status()
        return resp.json()["embeddings"]


def get_embedder(cfg: EmbedConfig) -> BaseEmbedder | None:
    """Return a working embedder, or None if local embeddings are unavailable."""
    if cfg.provider == "fastembed":
        try:
            return FastEmbedder(cfg.model)
        except Exception:
            return None
    if cfg.provider == "ollama":
        try:
            return OllamaEmbedder(cfg.model, cfg.base_url)
        except Exception:
            return None
    return None
