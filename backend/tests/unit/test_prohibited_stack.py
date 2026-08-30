"""Architectural constraint tests.

This project's premise is that it performs *evidence retrieval*, not valuation
modelling. That is a design commitment, and design commitments decay unless
something enforces them — so the prohibited stack is a test, not a paragraph in a
README that nobody re-reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("app", "scripts", "evals")

FORBIDDEN_PACKAGES = (
    "scikit-learn",
    "sklearn",
    "torch",
    "tensorflow",
    "keras",
    "xgboost",
    "lightgbm",
    "catboost",
    "statsmodels",
    "sentence-transformers",
    "onnxruntime",
    "transformers",
)

#: Call sites that would indicate training, fitting or model-based prediction.
FORBIDDEN_CALLS = re.compile(
    r"\.fit\(|\.partial_fit\(|\.train\(|\.predict\(|\.predict_proba\(|"
    r"\.backward\(|fine_tune|finetune|LinearRegression|RandomForest|"
    r"GradientBoosting|nn\.Module",
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        files.extend((BACKEND / directory).rglob("*.py"))
    return files


def test_no_machine_learning_libraries_are_installed():
    lock = (BACKEND / "requirements.lock.txt").read_text(encoding="utf-8").lower()
    installed = {line.split("==")[0].strip() for line in lock.splitlines() if "==" in line}
    offenders = sorted(installed & {package.lower() for package in FORBIDDEN_PACKAGES})
    assert not offenders, f"prohibited ML packages are installed: {offenders}"


def test_no_training_or_model_prediction_call_sites():
    offenders: list[str] = []
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            # Prose in docstrings and comments explains what is *absent*; only
            # executable lines are evidence of a prohibited call.
            if stripped.startswith(("#", '"', "'", "*")):
                continue
            if FORBIDDEN_CALLS.search(line):
                offenders.append(f"{path.relative_to(BACKEND)}:{number}: {stripped[:80]}")
    assert not offenders, "prohibited ML call sites found:\n" + "\n".join(offenders)


def test_price_evidence_uses_only_descriptive_statistics():
    """The evidence range must be arithmetic over observed sale prices."""
    source = (BACKEND / "app" / "assessment" / "pricing.py").read_text(encoding="utf-8")
    assert "statistics.median" in source
    assert "statistics.quantiles" in source
    for banned in ("numpy", "sklearn", "polyfit", "regress", "coefficient"):
        assert banned not in source.lower().replace(
            "no regression", ""
        ), f"pricing must not reference {banned!r}"


def test_the_narrative_schema_cannot_carry_a_price():
    """Structural guarantee: the LLM has no field in which to put a number."""
    from app.schemas.assessment import AssessmentNarrative

    for name, field in AssessmentNarrative.model_fields.items():
        annotation = str(field.annotation)
        assert (
            "int" not in annotation and "float" not in annotation
        ), f"AssessmentNarrative.{name} exposes a numeric field to the model"


def test_no_module_scrapes_the_property_portals():
    """The legal boundary: portal URLs are parsed, never fetched."""
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for host in ("realestate.com.au", "domain.com.au"):
            for number, line in enumerate(text.splitlines(), start=1):
                if host not in line:
                    continue
                lowered = line.lower()
                if any(
                    token in lowered
                    for token in ("requests.get", "httpx.get", "urlopen", "aiohttp", "fetch(")
                ):
                    offenders.append(f"{path.relative_to(BACKEND)}:{number}")
    assert not offenders, f"HTTP access to a property portal found at: {offenders}"
