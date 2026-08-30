"""Ranking metrics.

Plain implementations rather than a dependency, because the exact tie-breaking
and truncation semantics matter when comparing retrieval arms and a hidden
convention difference would invalidate the comparison.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import log2


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Of the top k returned, what fraction were relevant."""
    if k <= 0:
        return 0.0
    top = list(retrieved)[:k]
    if not top:
        return 0.0
    return sum(1 for item in top if item in relevant) / len(top)


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Of everything relevant, what fraction appeared in the top k."""
    if not relevant:
        return 0.0
    top = set(list(retrieved)[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant result; 0 if none appear."""
    for index, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-gain nDCG — rewards putting relevant items nearer the top."""
    if not relevant:
        return 0.0
    gains = [1.0 if item in relevant else 0.0 for item in list(retrieved)[:k]]
    dcg = sum(gain / log2(index + 2) for index, gain in enumerate(gains))
    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / log2(index + 2) for index in range(ideal_count))
    return dcg / idcg if idcg else 0.0


@dataclass
class RankingScores:
    precision_at_5: float
    precision_at_10: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_10: float
    retrieved: int
    relevant_total: int

    def as_row(self) -> dict[str, float | int]:
        return {
            "P@5": round(self.precision_at_5, 3),
            "P@10": round(self.precision_at_10, 3),
            "R@5": round(self.recall_at_5, 3),
            "R@10": round(self.recall_at_10, 3),
            "MRR": round(self.mrr, 3),
            "nDCG@10": round(self.ndcg_at_10, 3),
            "n": self.retrieved,
        }


def score_ranking(retrieved: Sequence[str], relevant: set[str]) -> RankingScores:
    return RankingScores(
        precision_at_5=precision_at_k(retrieved, relevant, 5),
        precision_at_10=precision_at_k(retrieved, relevant, 10),
        recall_at_5=recall_at_k(retrieved, relevant, 5),
        recall_at_10=recall_at_k(retrieved, relevant, 10),
        mrr=reciprocal_rank(retrieved, relevant),
        ndcg_at_10=ndcg_at_k(retrieved, relevant, 10),
        retrieved=len(retrieved),
        relevant_total=len(relevant),
    )


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def average_scores(scores: Sequence[RankingScores]) -> dict[str, float | int]:
    if not scores:
        return {
            "P@5": 0.0,
            "P@10": 0.0,
            "R@5": 0.0,
            "R@10": 0.0,
            "MRR": 0.0,
            "nDCG@10": 0.0,
            "n": 0,
        }
    return {
        "P@5": round(mean([item.precision_at_5 for item in scores]), 3),
        "P@10": round(mean([item.precision_at_10 for item in scores]), 3),
        "R@5": round(mean([item.recall_at_5 for item in scores]), 3),
        "R@10": round(mean([item.recall_at_10 for item in scores]), 3),
        "MRR": round(mean([item.mrr for item in scores]), 3),
        "nDCG@10": round(mean([item.ndcg_at_10 for item in scores]), 3),
        "n": sum(item.retrieved for item in scores),
    }
