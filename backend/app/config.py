"""Application configuration.

Everything is environment-driven (12-factor). No secret is ever hard-coded, and
every optional integration degrades to a documented, clearly-labelled fallback
rather than crashing the application.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class LLMBackend(str, Enum):
    AUTO = "auto"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OFFLINE = "offline"


class EmbeddingBackend(str, Enum):
    AUTO = "auto"
    OPENAI = "openai"
    OFFLINE = "offline"


class ProviderName(str, Enum):
    DEMO = "demo"
    PROPTRACK = "proptrack"
    DOMAIN = "domain"


class Settings(BaseSettings):
    """Runtime settings resolved from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database -------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/property_intel"
    )
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- LLM ------------------------------------------------------------------
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_backend: LLMBackend = LLMBackend.AUTO
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_chat_model: str = "claude-sonnet-5"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # --- Embeddings -----------------------------------------------------------
    embedding_backend: EmbeddingBackend = EmbeddingBackend.AUTO
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- Property data providers ---------------------------------------------
    property_provider: ProviderName = ProviderName.DEMO
    proptrack_client_id: str | None = None
    proptrack_client_secret: str | None = None
    proptrack_api_key: str | None = None
    proptrack_base_url: str = "https://data.proptrack.com"
    domain_api_key: str | None = None
    domain_base_url: str = "https://api.domain.com.au"
    google_maps_api_key: str | None = None
    provider_timeout_seconds: float = 20.0

    # --- Observability --------------------------------------------------------
    langchain_tracing_v2: bool = False
    langchain_api_key: str | None = None
    langchain_project: str = "property-price-intelligence"
    log_level: str = "INFO"
    log_format: str = "console"  # console | json

    # --- Retrieval tuning -----------------------------------------------------
    default_radius_km: float = 3.0
    max_radius_km: float = 8.0
    radius_expansion_step_km: float = 2.0
    default_lookback_months: int = 12
    max_lookback_months: int = 24
    lookback_expansion_step_months: int = 6
    hard_filter_limit: int = 120
    hybrid_candidate_limit: int = 24
    min_strong_comparables: int = 5
    target_comparables: int = 8
    max_search_expansions: int = 2
    max_evidence_retries: int = 2
    rrf_k: int = 60

    # --- API ------------------------------------------------------------------
    # `NoDecode` is required: without it pydantic-settings JSON-decodes complex
    # types straight from the environment, so `A,B` raises a parse error before any
    # validator can split it. Comma-separated lists are the normal shape for env
    # vars, so the decoder has to be turned off for this field.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    # --- Derived helpers ------------------------------------------------------
    @property
    def resolved_llm_backend(self) -> LLMBackend:
        """The backend actually usable given the credentials present."""
        if self.llm_backend is not LLMBackend.AUTO:
            if self.llm_backend is LLMBackend.OPENAI and not self.openai_api_key:
                return LLMBackend.OFFLINE
            if self.llm_backend is LLMBackend.ANTHROPIC and not self.anthropic_api_key:
                return LLMBackend.OFFLINE
            return self.llm_backend
        if self.anthropic_api_key:
            return LLMBackend.ANTHROPIC
        if self.openai_api_key:
            return LLMBackend.OPENAI
        return LLMBackend.OFFLINE

    @property
    def resolved_embedding_backend(self) -> EmbeddingBackend:
        if self.embedding_backend is EmbeddingBackend.OPENAI:
            return EmbeddingBackend.OPENAI if self.openai_api_key else EmbeddingBackend.OFFLINE
        if self.embedding_backend is EmbeddingBackend.OFFLINE:
            return EmbeddingBackend.OFFLINE
        return EmbeddingBackend.OPENAI if self.openai_api_key else EmbeddingBackend.OFFLINE

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langchain_tracing_v2 and self.langchain_api_key)

    @property
    def is_fully_offline(self) -> bool:
        return (
            self.resolved_llm_backend is LLMBackend.OFFLINE
            and self.resolved_embedding_backend is EmbeddingBackend.OFFLINE
            and self.property_provider is ProviderName.DEMO
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper — drop the memoised settings instance."""
    get_settings.cache_clear()
