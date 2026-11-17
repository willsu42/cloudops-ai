"""Checks on the committed corpus manifest and evaluation cases."""

from __future__ import annotations

import pytest

from app.core.config import REPO_ROOT
from app.evaluation.cases import EvaluationCase, Split, load_cases, validate_cases
from app.seed import load_manifest
from tests.conftest import DATASETS_DIR

CORPUS_DIR = DATASETS_DIR / "corpus" / "otel-demo"


def test_manifest_matches_corpus_files() -> None:
    manifest = load_manifest(DATASETS_DIR)
    listed = {REPO_ROOT / d.local_path for d in manifest.documents}
    on_disk = set(CORPUS_DIR.glob("*"))
    assert listed == on_disk, "corpus files and manifest entries differ"
    assert len({d.source_key for d in manifest.documents}) == len(manifest.documents)


def test_corpus_excludes_fault_injection_material() -> None:
    for path in CORPUS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        # Flag names from the demo's Feature Flags page (e.g. paymentFailure).
        assert "paymentFailure" not in text, path.name
        assert "Implemented feature flags" not in text, path.name


def _case(case_id: str, group: str, split: Split) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        incident_group=group,
        split=split,
        kind="answerable",
        question="Which language is checkout written in?",
        expected_answer="Go",
        relevant_source_keys=["otel-demo-architecture"],
        should_abstain=False,
    )


def test_committed_cases_are_valid() -> None:
    keys = {d.source_key for d in load_manifest(DATASETS_DIR).documents}
    cases = load_cases(DATASETS_DIR / "evaluation")
    assert cases, "expected development cases"
    validate_cases(cases, keys)


def test_group_spanning_splits_is_rejected() -> None:
    cases = [_case("a", "checkout-x", "dev"), _case("b", "checkout-x", "test")]
    with pytest.raises(ValueError, match="spans splits"):
        validate_cases(cases, {"otel-demo-architecture"})


def test_unknown_source_and_duplicate_ids_are_rejected() -> None:
    bad = _case("a", "g", "dev").model_copy(update={"relevant_source_keys": ["nope"]})
    with pytest.raises(ValueError) as exc:
        validate_cases([bad, _case("a", "g", "dev")], {"otel-demo-architecture"})
    assert "unknown source key 'nope'" in str(exc.value)
    assert "duplicate case_id a" in str(exc.value)


def test_insufficient_evidence_must_abstain() -> None:
    with pytest.raises(ValueError, match="should_abstain=true"):
        EvaluationCase(
            case_id="x",
            incident_group="g",
            split="dev",
            kind="insufficient_evidence",
            question="What caused last night's outage?",
            expected_answer="Abstain",
            relevant_source_keys=[],
            should_abstain=False,
        )
