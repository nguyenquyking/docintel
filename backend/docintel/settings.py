"""Runtime configuration. Everything comes from the environment or a git-ignored ``.env``.

Secrets (tokens, client secrets, API keys) are ``SecretStr``: they never show up in
``repr()``, logs, or the ``/api/settings`` payload. No default here points at
private infrastructure — a fresh clone runs with the offline ``fake`` LLM until
someone configures a real provider locally.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCINTEL_", env_file=(REPO_ROOT / ".env",), extra="ignore")

    # ── storage / service ────────────────────────────────────────────────
    data_dir: Path = REPO_ROOT / "data"
    database_url: str | None = None          # default: SQLite file inside data_dir
    api_host: str = "127.0.0.1"
    api_port: int = 8077
    cors_origins: list[str] = ["http://localhost:5177", "http://127.0.0.1:5177"]
    run_worker_in_api: bool = True           # one process for local use; split in production

    # ── LLM (any OpenAI-compatible chat endpoint) ────────────────────────
    llm_provider: Literal["databricks", "openai", "fake"] = "fake"
    llm_base_url: str | None = None          # openai provider: e.g. https://api.openai.com/v1, Ollama, vLLM
    llm_api_key: SecretStr | None = None
    llm_model_fast: str = ""                 # cheap tier: first pass on every document
    llm_model_strong: str = ""               # escalation tier; may be the same model at higher effort
    llm_model_vision: str = ""               # page transcription for scans; defaults to the strong model
    llm_effort_fast: str | None = "low"      # reasoning_effort per tier ("" disables the parameter)
    llm_effort_strong: str | None = "high"
    llm_effort_vision: str | None = "low"
    llm_send_temperature: bool = False       # some reasoning models reject any temperature but the default
    llm_display_name: str = "LLM"
    llm_rpm: int = 120
    llm_tpm: int = 600_000
    llm_max_concurrency: int = 8
    llm_timeout_s: float = 180.0
    llm_max_retries: int = 5
    llm_cache: bool = True                   # exact-request cache: re-runs of unchanged stages cost nothing

    # ── Databricks AI Gateway auth (provider = databricks) ───────────────
    databricks_profile: str | None = None    # ~/.databrickscfg profile, e.g. a personal CLI login
    databricks_host: str | None = None       # optional: otherwise read from the profile
    databricks_token: SecretStr | None = None
    databricks_client_id: str | None = None  # service principal (OAuth M2M) for containers
    databricks_client_secret: SecretStr | None = None
    databricks_gateway_path: str = "/ai-gateway/openai/v1"

    # ── embeddings (hybrid retrieval / search) ───────────────────────────
    embeddings_provider: Literal["fastembed", "openai", "none"] = "none"
    embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embeddings_base_url: str | None = None
    embeddings_api_key: SecretStr | None = None

    # ── parsing / OCR / VLM ──────────────────────────────────────────────
    ocr_backend: Literal["rapidocr", "tesseract", "none"] = "rapidocr"
    ocr_languages: str = "eng+vie"           # tesseract only
    ocr_min_confidence: float = 0.85
    vlm_enabled: bool = True
    render_dpi: int = 170
    parse_concurrency: int = 4

    # ── extraction ───────────────────────────────────────────────────────
    full_doc_max_tokens: int = 24_000        # above this, retrieve-then-extract per field group
    group_context_max_tokens: int = 12_000
    retrieval_top_k: int = 6
    escalation: bool = True
    escalate_below: float = 0.6              # confidence under which a group goes to the strong tier
    review_below: float = 0.8                # confidence under which a field goes to human review
    max_repair_attempts: int = 2

    # ── worker / queue ───────────────────────────────────────────────────
    worker_concurrency: int = 2
    job_lease_s: int = 900
    job_max_attempts: int = 4
    job_backoff_base_s: float = 5.0
    job_backoff_cap_s: float = 600.0
    canary_interval_hours: float = 0.0       # 0 disables the scheduled canary

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'docintel.db'}"

    @property
    def vision_model(self) -> str:
        return self.llm_model_vision or self.llm_model_strong or self.llm_model_fast

    def public_view(self) -> dict:
        """What the UI may show: provider and tiers, never hosts, keys or tokens."""
        return {
            "llm_provider": self.llm_provider,
            "llm_display_name": self.llm_display_name,
            "llm_configured": self.llm_provider == "fake" or bool(self.llm_model_fast),
            "tiers": {
                "fast": {"effort": self.llm_effort_fast or None},
                "strong": {"effort": self.llm_effort_strong or None},
                "vision": {"effort": self.llm_effort_vision or None, "enabled": self.vlm_enabled},
            },
            "embeddings_provider": self.embeddings_provider,
            "ocr_backend": self.ocr_backend,
            "ocr_min_confidence": self.ocr_min_confidence,
            "full_doc_max_tokens": self.full_doc_max_tokens,
            "escalation": self.escalation,
            "escalate_below": self.escalate_below,
            "review_below": self.review_below,
        }


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("DOCINTEL_NO_DOTENV"):      # tests: the environment only, never a developer's .env
        return Settings(_env_file=None)
    return Settings()
