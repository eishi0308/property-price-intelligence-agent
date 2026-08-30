"""SQLAlchemy models — the single source of truth for the database schema.

Schema notes that matter for retrieval:

* `listings.search_vector` is a STORED generated tsvector, so PostgreSQL
  full-text ranking (the keyword arm of hybrid search) needs no application-side
  maintenance and can never drift from the description.
* `evidence_chunks.embedding` is a pgvector column with an HNSW cosine index —
  this is the semantic arm.
* Structured attributes live on `properties` with btree indexes because Stage A
  of the retrieval pipeline is deliberately deterministic SQL, not embeddings.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[str]: JSONB}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Property(Base):
    """A dwelling, normalised away from any single provider's field names."""

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_properties_provider_external_id"),
        Index("ix_properties_geo", "latitude", "longitude"),
        Index("ix_properties_suburb_type", "suburb", "state", "property_type"),
        Index("ix_properties_structural", "property_type", "bedrooms", "bathrooms"),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="ck_lat"),
        CheckConstraint("longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="ck_lon"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    address: Mapped[str] = mapped_column(String(400), nullable=False)
    suburb: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(8), nullable=False)
    postcode: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    property_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    carspaces: Mapped[int | None] = mapped_column(Integer)
    floor_area: Mapped[float | None] = mapped_column(Float)
    land_area: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)

    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    listings: Mapped[list[Listing]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )


class Listing(Base):
    """A marketing listing — current or historical — attached to a property."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("provider", "external_listing_id", name="uq_listings_provider_external"),
        Index("ix_listings_status_sold_at", "status", "sold_at"),
        Index("ix_listings_property", "property_id"),
        Index(
            "ix_listings_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        CheckConstraint("sold_price IS NULL OR sold_price >= 0", name="ck_sold_price_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    external_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    asking_price: Mapped[int | None] = mapped_column(Integer)
    price_guide_text: Mapped[str | None] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    listed_at: Mapped[date | None] = mapped_column(Date)
    sold_at: Mapped[date | None] = mapped_column(Date)
    sold_price: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    source_url: Mapped[str | None] = mapped_column(String(600))
    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Maintained by PostgreSQL. The keyword arm of hybrid retrieval reads this.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(headline,'') || ' ' || coalesce(description,''))",
            persisted=True,
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    property: Mapped[Property] = relationship(back_populates="listings")


class EvidenceChunk(Base):
    """Embedded text used for semantic retrieval and RAG grounding.

    `source_type` partitions the retrieval domains described in the README:
    listing_description | market_context | location_context | property_summary.
    """

    __tablename__ = "evidence_chunks"
    __table_args__ = (
        Index("ix_evidence_source", "source_type", "source_id"),
        Index("ix_evidence_property", "property_id"),
        Index(
            "ix_evidence_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_evidence_content_fts", "content_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE")
    )
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str | None] = mapped_column(String(600))
    published_at: Mapped[date | None] = mapped_column(Date)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content,''))", persisted=True),
    )


class Analysis(Base):
    """One run of the price-intelligence workflow."""

    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL")
    )
    user_id: Mapped[str | None] = mapped_column(String(64))  # nullable for MVP

    query: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    asking_price: Mapped[int | None] = mapped_column(Integer)
    asking_price_source: Mapped[str | None] = mapped_column(String(64))
    evidence_low: Mapped[int | None] = mapped_column(Integer)
    evidence_high: Mapped[int | None] = mapped_column(Integer)
    evidence_median: Mapped[int | None] = mapped_column(Integer)
    assessment: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[str | None] = mapped_column(String(16))
    evidence_quality: Mapped[str | None] = mapped_column(String(16))

    assessment_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stages: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    missing_information: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    search_radius_km: Mapped[float | None] = mapped_column(Float)
    lookback_months: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    comparables: Mapped[list[ComparableResult]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )
    traces: Mapped[list[AgentTrace]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class ComparableResult(Base):
    """One candidate comparable, with the full retrieval provenance retained.

    Keeping every stage's score (not just the winner) is what makes the retrieval
    evaluation suite and the UI's transparency panel possible.
    """

    __tablename__ = "comparable_results"
    __table_args__ = (
        UniqueConstraint("analysis_id", "comparable_property_id", name="uq_comp_analysis_prop"),
        Index("ix_comp_analysis_rank", "analysis_id", "final_rank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    comparable_property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )

    structured_match_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    vector_similarity: Mapped[float | None] = mapped_column(Float)
    keyword_score: Mapped[float | None] = mapped_column(Float)
    fusion_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)
    final_rank: Mapped[int | None] = mapped_column(Integer)

    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(300))
    excluded_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    important_matches: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    important_differences: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    sold_price: Mapped[int | None] = mapped_column(Integer)
    sold_at: Mapped[date | None] = mapped_column(Date)
    distance_km: Mapped[float | None] = mapped_column(Float)

    analysis: Mapped[Analysis] = relationship(back_populates="comparables")


class AgentTrace(Base):
    """Local, always-on trace of the LangGraph run.

    LangSmith is the richer view when credentials exist; this table guarantees the
    `/api/analysis/{id}/trace` endpoint works with no third-party dependency.
    """

    __tablename__ = "agent_traces"
    __table_args__ = (Index("ix_traces_analysis_seq", "analysis_id", "sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis: Mapped[Analysis] = relationship(back_populates="traces")


class AnalysisEvidence(Base):
    """Which evidence chunks were actually shown to the assessing LLM."""

    __tablename__ = "analysis_evidence"
    __table_args__ = (Index("ix_analysis_evidence_analysis", "analysis_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False
    )
    evidence_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_chunks.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str | None] = mapped_column(String(400))
    similarity: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    source_url: Mapped[str | None] = mapped_column(String(600))
    published_at: Mapped[date | None] = mapped_column(Date)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
