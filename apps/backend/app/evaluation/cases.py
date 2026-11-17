"""Evaluation case schema and split validation.

Cases live in datasets/evaluation/<split>.jsonl, one JSON object per line.
Related cases share an `incident_group`; a group must never appear in more than
one split, so near-duplicate questions cannot leak from development into test.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Split = Literal["dev", "test"]
SPLITS: tuple[Split, ...] = ("dev", "test")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9-]+$")
    incident_group: str = Field(pattern=r"^[a-z0-9-]+$")
    split: Split
    kind: Literal["answerable", "insufficient_evidence", "adversarial"]
    question: str = Field(min_length=10)
    expected_answer: str = Field(min_length=1)
    relevant_source_keys: list[str]
    should_abstain: bool
    notes: str = ""

    @model_validator(mode="after")
    def _consistent_labels(self) -> Self:
        if self.kind == "answerable" and (self.should_abstain or not self.relevant_source_keys):
            raise ValueError("answerable cases need relevant sources and should_abstain=false")
        if self.kind == "insufficient_evidence" and not self.should_abstain:
            raise ValueError("insufficient_evidence cases must set should_abstain=true")
        return self


def load_cases(evaluation_dir: Path) -> list[EvaluationCase]:
    """Load every split file that exists. A missing split file means 'not yet designed'."""
    cases: list[EvaluationCase] = []
    for split in SPLITS:
        path = evaluation_dir / f"{split}.jsonl"
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            case = EvaluationCase.model_validate(json.loads(line))
            if case.split != split:
                raise ValueError(
                    f"{path.name}:{lineno}: case {case.case_id} says split={case.split}"
                )
            cases.append(case)
    return cases


def validate_cases(cases: list[EvaluationCase], known_source_keys: set[str]) -> None:
    """Raise ValueError on duplicate IDs, cross-split groups, or unknown sources."""
    errors: list[str] = []

    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            errors.append(f"duplicate case_id {case.case_id}")
        seen.add(case.case_id)
        for key in case.relevant_source_keys:
            if key not in known_source_keys:
                errors.append(f"{case.case_id}: unknown source key {key!r}")

    group_splits: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        group_splits[case.incident_group].add(case.split)
    for group, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            errors.append(f"incident_group {group!r} spans splits {sorted(splits)}")

    if errors:
        raise ValueError("Invalid evaluation cases:\n  - " + "\n  - ".join(errors))
