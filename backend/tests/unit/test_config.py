"""Configuration resolution rules."""

from __future__ import annotations

import pytest

from app.config import EmbeddingBackend, LLMBackend, Settings

pytestmark = pytest.mark.unit


def test_comma_separated_cors_origins_parse_from_env_style_strings():
    """Regression: pydantic-settings JSON-decodes complex types from the
    environment unless `NoDecode` is applied, which made `A,B` a startup crash."""
    settings = Settings(api_cors_origins="http://localhost:3000, http://localhost:3100")
    assert settings.api_cors_origins == ["http://localhost:3000", "http://localhost:3100"]


def test_cors_origins_accept_a_real_list_too():
    assert Settings(api_cors_origins=["http://a"]).api_cors_origins == ["http://a"]


def test_backends_resolve_to_offline_without_credentials():
    settings = Settings(openai_api_key=None, anthropic_api_key=None)
    assert settings.resolved_llm_backend is LLMBackend.OFFLINE
    assert settings.resolved_embedding_backend is EmbeddingBackend.OFFLINE


def test_anthropic_is_preferred_when_both_keys_are_present():
    settings = Settings(openai_api_key="sk-a", anthropic_api_key="sk-b")
    assert settings.resolved_llm_backend is LLMBackend.ANTHROPIC


def test_explicitly_requested_backend_falls_back_when_its_key_is_missing():
    """An explicit request must not silently succeed with the wrong provider."""
    settings = Settings(llm_backend=LLMBackend.OPENAI, openai_api_key=None)
    assert settings.resolved_llm_backend is LLMBackend.OFFLINE


def test_embeddings_use_openai_when_a_key_exists():
    assert Settings(openai_api_key="sk-a").resolved_embedding_backend is EmbeddingBackend.OPENAI


def test_tracing_requires_both_the_flag_and_the_key():
    assert not Settings(langchain_tracing_v2=True, langchain_api_key=None).tracing_enabled
    assert not Settings(langchain_tracing_v2=False, langchain_api_key="k").tracing_enabled
    assert Settings(langchain_tracing_v2=True, langchain_api_key="k").tracing_enabled
