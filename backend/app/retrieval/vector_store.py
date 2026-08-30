"""STAGE B — semantic retrieval over pgvector.

Why pgvector rather than a dedicated vector database: the structured filter in
Stage A and the semantic search here operate on the *same* rows. Keeping both in
PostgreSQL means the candidate set can be narrowed by SQL and searched by cosine
similarity in one query, with one transaction and one consistency model. A
separate vector service would force a two-system join and an eventual-consistency
problem for no benefit at this scale.

The `property_ids` filter is the important detail: we never search the whole
corpus. Stage A has already decided what is *eligible*; this stage only ranks
what is *similar*, which is what embeddings are actually good at.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvidenceChunk
from app.observability import get_logger
from app.retrieval.embeddings import embedding_model_name, get_embeddings

logger = get_logger(__name__)

SOURCE_LISTING = "listing_description"
SOURCE_MARKET = "market_context"
SOURCE_LOCATION = "location_context"
SOURCE_PROPERTY = "property_summary"


@dataclass
class VectorHit:
    chunk_id: uuid.UUID
    property_id: uuid.UUID | None
    source_type: str
    source_id: str
    title: str | None
    content: str
    similarity: float
    metadata: dict[str, Any]
    source_url: str | None
    is_demo_data: bool

    def to_document(self) -> Document:
        return Document(
            page_content=self.content,
            metadata={
                "chunk_id": str(self.chunk_id),
                "property_id": str(self.property_id) if self.property_id else None,
                "source_type": self.source_type,
                "source_id": self.source_id,
                "title": self.title,
                "similarity": self.similarity,
                "source_url": self.source_url,
                "is_demo_data": self.is_demo_data,
                **self.metadata,
            },
        )


async def similarity_search(
    session: AsyncSession,
    query_embedding: Sequence[float],
    *,
    source_types: Sequence[str] | None = None,
    property_ids: Sequence[uuid.UUID] | None = None,
    k: int = 30,
    min_similarity: float | None = None,
) -> list[VectorHit]:
    """Cosine similarity search with metadata filtering, in one SQL statement."""
    distance = EvidenceChunk.embedding.cosine_distance(list(query_embedding))
    statement = (
        select(EvidenceChunk, distance.label("distance"))
        .where(EvidenceChunk.embedding.isnot(None))
        # `source_id` breaks ties so identical inputs always produce an identical
        # ranking, which is what makes the evaluation suite reproducible.
        .order_by(distance.asc(), EvidenceChunk.source_id.asc())
        .limit(k)
    )
    if source_types:
        statement = statement.where(EvidenceChunk.source_type.in_(list(source_types)))
    if property_ids is not None:
        if not property_ids:
            return []
        statement = statement.where(EvidenceChunk.property_id.in_(list(property_ids)))

    rows = (await session.execute(statement)).all()
    hits: list[VectorHit] = []
    for chunk, distance_value in rows:
        similarity = 1.0 - float(distance_value)
        if min_similarity is not None and similarity < min_similarity:
            continue
        hits.append(
            VectorHit(
                chunk_id=chunk.id,
                property_id=chunk.property_id,
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                title=chunk.title,
                content=chunk.content,
                similarity=round(similarity, 6),
                metadata=dict(chunk.chunk_metadata or {}),
                source_url=chunk.source_url,
                is_demo_data=chunk.is_demo_data,
            )
        )
    return hits


async def similarity_search_by_text(
    session: AsyncSession,
    query: str,
    *,
    source_types: Sequence[str] | None = None,
    property_ids: Sequence[uuid.UUID] | None = None,
    k: int = 30,
    min_similarity: float | None = None,
) -> list[VectorHit]:
    embeddings = get_embeddings()
    vector = await embeddings.aembed_query(query)
    return await similarity_search(
        session,
        vector,
        source_types=source_types,
        property_ids=property_ids,
        k=k,
        min_similarity=min_similarity,
    )


class PgVectorRetriever(BaseRetriever):
    """LangChain retriever over pgvector.

    Exposed as a `BaseRetriever` so the RAG chains in `app/rag` compose it with
    LangChain's document handling rather than passing bare tuples around. It is
    async-only by design; a sync path would need a second engine and would be
    dead code in this application.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: AsyncSession
    source_types: list[str] | None = None
    property_ids: list[uuid.UUID] | None = None
    k: int = 30
    min_similarity: float | None = None
    metadata_tag: dict[str, Any] = Field(default_factory=dict)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        raise NotImplementedError(
            "PgVectorRetriever is async-only; use `ainvoke`/`aget_relevant_documents`."
        )

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: Any = None
    ) -> list[Document]:
        hits = await similarity_search_by_text(
            self.session,
            query,
            source_types=self.source_types,
            property_ids=self.property_ids,
            k=self.k,
            min_similarity=self.min_similarity,
        )
        logger.info(
            "retrieval.vector",
            query_chars=len(query),
            hits=len(hits),
            source_types=self.source_types,
            model=embedding_model_name(),
            top_similarity=hits[0].similarity if hits else None,
        )
        return [hit.to_document() for hit in hits]
