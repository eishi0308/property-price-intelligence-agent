"""Chat-model access, with an honest offline mode.

The application supports three backends — Anthropic, OpenAI, and *none*. The
third is not a stub that fakes model output: when no key is configured,
`get_chat_model()` returns ``None`` and each LLM-backed component switches to an
explicitly deterministic implementation that is labelled as such in the API
response (`generated_by`) and in the UI. A user must never be shown rule-based
output while believing a language model produced it.

Structured output goes through LangChain's `with_structured_output`, which binds
the Pydantic schema as a tool/JSON-schema constraint at the provider level. That
is what makes "the model may only emit `RerankVerdict`" a real constraint rather
than a hopeful instruction in a prompt.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.config import LLMBackend, Settings, get_settings
from app.observability import get_logger

logger = get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when an LLM is required but no backend is configured."""


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel | None:
    """The configured chat model, or ``None`` when running without credentials."""
    settings = get_settings()
    backend = settings.resolved_llm_backend

    if backend is LLMBackend.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        logger.info("llm.backend", backend="anthropic", model=settings.anthropic_chat_model)
        return ChatAnthropic(
            model=settings.anthropic_chat_model,  # type: ignore[call-arg]
            api_key=settings.anthropic_api_key,  # type: ignore[arg-type]
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_tokens=2048,
        )

    if backend is LLMBackend.OPENAI:
        from langchain_openai import ChatOpenAI

        logger.info("llm.backend", backend="openai", model=settings.openai_chat_model)
        return ChatOpenAI(
            model=settings.openai_chat_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    logger.warning(
        "llm.offline_mode",
        reason="no ANTHROPIC_API_KEY or OPENAI_API_KEY configured",
        impact="reranking and assessment narration use deterministic rule-based components",
    )
    return None


def llm_is_available() -> bool:
    return get_chat_model() is not None


def llm_backend_name(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    backend = settings.resolved_llm_backend
    if backend is LLMBackend.ANTHROPIC:
        return f"anthropic:{settings.anthropic_chat_model}"
    if backend is LLMBackend.OPENAI:
        return f"openai:{settings.openai_chat_model}"
    return "offline:deterministic-rules"


async def structured_call(
    messages: list[BaseMessage],
    schema: type[SchemaT],
    *,
    name: str = "structured_call",
    **kwargs: Any,
) -> SchemaT:
    """Invoke the model with a hard structured-output contract.

    Raises `LLMUnavailable` rather than returning a plausible-looking default —
    the caller must consciously choose its offline path.
    """
    model = get_chat_model()
    if model is None:
        raise LLMUnavailable(
            f"{name} requires an LLM backend; set ANTHROPIC_API_KEY or OPENAI_API_KEY."
        )
    structured = model.with_structured_output(schema, **kwargs)
    result = await structured.ainvoke(messages)
    if not isinstance(result, schema):
        # with_structured_output can return a dict for some providers/settings.
        result = schema.model_validate(result)
    return result


def reset_llm_cache() -> None:
    get_chat_model.cache_clear()
