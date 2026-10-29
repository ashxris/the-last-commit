"""Configuration for The Last Commit.

Resolution order (highest wins):
  1. environment variables (LASTCOMMIT_LLM_BASE_URL, LASTCOMMIT_LLM_MODEL,
     GROQ_API_KEY / OPENAI_API_KEY / LASTCOMMIT_API_KEY,
     LASTCOMMIT_EMBED_PROVIDER, LASTCOMMIT_EMBED_MODEL)
  2. ~/.config/lastcommit/config.toml
  3. built-in defaults (Groq chat, fastembed local embeddings)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "lastcommit" / "config.toml"

DEFAULT_LLM_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    api_key: str = ""


@dataclass
class EmbedConfig:
    provider: str = "fastembed"  # 'fastembed' | 'ollama' | 'none'
    model: str = "BAAI/bge-small-en-v1.5"
    base_url: str = "http://localhost:11434"


@dataclass
class AppConfig:
    llm: LLMConfig = None  # type: ignore[assignment]
    embed: EmbedConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.llm is None:
            self.llm = LLMConfig()
        if self.embed is None:
            self.embed = EmbedConfig()


def load_config() -> AppConfig:
    cfg = AppConfig()
    llm, embed = cfg.llm, cfg.embed

    if CONFIG_PATH.exists():
        try:
            import tomllib

            with open(CONFIG_PATH, "rb") as fh:
                data = tomllib.load(fh)
            llm_data = data.get("llm", {})
            embed_data = data.get("embed", {})
            llm.base_url = llm_data.get("base_url", llm.base_url)
            llm.model = llm_data.get("model", llm.model)
            llm.api_key = llm_data.get("api_key", llm.api_key)
            embed.provider = embed_data.get("provider", embed.provider)
            embed.model = embed_data.get("model", embed.model)
            embed.base_url = embed_data.get("base_url", embed.base_url)
        except (OSError, ValueError):
            pass

    llm.base_url = os.environ.get("LASTCOMMIT_LLM_BASE_URL", llm.base_url)
    llm.model = os.environ.get("LASTCOMMIT_LLM_MODEL", llm.model)
    if not llm.api_key:
        llm.api_key = (
            os.environ.get("GROQ_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("LASTCOMMIT_API_KEY")
            or ""
        )
    embed.provider = os.environ.get("LASTCOMMIT_EMBED_PROVIDER", embed.provider)
    embed.model = os.environ.get("LASTCOMMIT_EMBED_MODEL", embed.model)
    return cfg
