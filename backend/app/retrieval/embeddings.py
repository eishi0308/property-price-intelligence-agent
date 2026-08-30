"""Embedding backends behind LangChain's `Embeddings` interface.

Two implementations, chosen by configuration:

``OpenAI`` (preferred)
    A hosted embedding API via `langchain_openai.OpenAIEmbeddings`. No model is
    trained, fine-tuned or served by this project.

``OfflineConceptEncoder`` (fallback, no credentials)
    A deterministic text-to-vector encoder so the entire pipeline — pgvector,
    hybrid fusion, reranking, evals — is runnable and testable with zero API
    keys. It is NOT a learned model:

        * concept dimensions fire when a phrase from the hand-written lexicon in
          `concepts.py` appears, which is what lets "sun-drenched northerly
          aspect" land near "north-facing";
        * the remaining dimensions are a hashed bag-of-words with sublinear term
          weighting, so plain lexical overlap still contributes;
        * nothing is fitted to data, and there are no parameters to learn.

    Its limitation is stated everywhere it is used: it generalises only to
    paraphrases the lexicon anticipates. A real embedding API generalises to
    paraphrases nobody wrote down. `embedding_mode` is surfaced on every API
    response so a reader always knows which one produced the numbers.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from functools import lru_cache
from itertools import pairwise

from langchain_core.embeddings import Embeddings

from app.config import EmbeddingBackend, Settings, get_settings
from app.observability import get_logger
from app.retrieval.concepts import CONCEPTS, detect_concepts, tokenize

logger = get_logger(__name__)

#: Fraction of the vector reserved for lexicon concept signal.
_CONCEPT_BLOCK_RATIO = 0.25
#: Dimensions each concept occupies (spread so concepts rarely collide).
_DIMS_PER_CONCEPT = 8


class OfflineConceptEncoder(Embeddings):
    """Deterministic, dependency-free encoder. No training, no model hosting."""

    model_name = "offline-concept-lexicon-v1"

    def __init__(self, dimension: int = 1536) -> None:
        if dimension < 128:
            raise ValueError("Embedding dimension must be at least 128.")
        self.dimension = dimension
        self._concept_block = max(
            len(CONCEPTS) * _DIMS_PER_CONCEPT, int(dimension * _CONCEPT_BLOCK_RATIO)
        )
        self._concept_block = min(self._concept_block, dimension // 2)
        self._hash_block = dimension - self._concept_block
        self._concept_offsets = {
            concept.key: (index * _DIMS_PER_CONCEPT)
            % max(self._concept_block - _DIMS_PER_CONCEPT, 1)
            for index, concept in enumerate(CONCEPTS)
        }

    def _hash_index(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self._hash_block

    def _sign(self, token: str) -> float:
        """Signed hashing keeps unrelated collisions from always reinforcing."""
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
        return 1.0 if digest[0] % 2 == 0 else -1.0

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension

        # --- concept block: meaning, however it happens to be worded -----------
        for key, register in detect_concepts(text).items():
            offset = self._concept_offsets.get(key)
            if offset is None:
                continue
            # A keyword mention is stronger evidence than a paraphrase match.
            weight = 1.0 if register == "keyword" else 0.85
            for step in range(_DIMS_PER_CONCEPT):
                index = offset + step
                if index < self._concept_block:
                    # Fixed intra-concept pattern: two concepts sharing a start
                    # offset still produce distinguishable sub-patterns.
                    vector[index] += weight * (1.0 if step % 2 == 0 else 0.6)

        # --- hashed lexical block: literal wording -----------------------------
        tokens = tokenize(text)
        counts = Counter(tokens)
        bigrams = Counter(f"{first}_{second}" for first, second in pairwise(tokens))
        for term, count in list(counts.items()) + list(bigrams.items()):
            if len(term) < 3:
                continue
            index = self._concept_block + self._hash_index(term)
            vector[index] += self._sign(term) * (1.0 + math.log(count))

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.encode(text)


@lru_cache(maxsize=1)
def get_embeddings(settings_key: str | None = None) -> Embeddings:
    """Resolve the configured embedding backend."""
    settings = get_settings()
    backend = settings.resolved_embedding_backend
    if backend is EmbeddingBackend.OPENAI:
        from langchain_openai import OpenAIEmbeddings

        logger.info("embeddings.backend", backend="openai", model=settings.openai_embedding_model)
        return OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            dimensions=settings.embedding_dim,
        )
    logger.warning(
        "embeddings.offline_fallback",
        reason="no OPENAI_API_KEY configured",
        impact="semantic recall limited to the hand-written concept lexicon",
    )
    return OfflineConceptEncoder(dimension=settings.embedding_dim)


def embedding_model_name(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.resolved_embedding_backend is EmbeddingBackend.OPENAI:
        return settings.openai_embedding_model
    return OfflineConceptEncoder.model_name


def embedding_quality_note(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.resolved_embedding_backend is EmbeddingBackend.OPENAI:
        return (
            f"Semantic similarity computed with {settings.openai_embedding_model} "
            f"({settings.embedding_dim}-d, cosine)."
        )
    return (
        "Semantic similarity computed with a deterministic offline concept encoder "
        "(no embedding API key configured). It matches paraphrases covered by the "
        "built-in property lexicon; novel phrasing may be missed. Treat semantic "
        "scores as indicative only."
    )


def reset_embeddings_cache() -> None:
    get_embeddings.cache_clear()
