"""Retrieval-augmented generation: evidence assembly and grounded narration."""

from app.rag.chains import generate_narrative  # noqa: F401
from app.rag.evidence import (  # noqa: F401
    EvidenceBundle,
    EvidenceItem,
    retrieve_supporting_evidence,
)
