import pytest
from pydantic import ValidationError
from runner import Deps
from core.costs import BudgetGuard, BudgetExceeded
from core.llm import LLMResult
from matching.engine import VehicleIndex
from tests.fakes import FakeLLM, FakeRepo
from agents.reviewer import resolve_review, ReviewNotOpen

def _deps(repo, llm=None, index=None, budget=None):
    return Deps(repo=repo, llm=llm or FakeLLM([]), gcs=None, carb=None,
                index=index or VehicleIndex([]), budget=budget or BudgetGuard(5))

def _envelope(eo, payload_fields):
    """Real extractor.py envelope shape: Extraction fields live nested under "payload"."""
    return {"eo_number": eo, "payload": {"eo_number": eo, **payload_fields},
            "prompt_version": 1, "ladder_step": 1, "finish_reason": "STOP",
            "tok_in": 10, "tok_out": 5, "cost_usd": 0.0001, "created_at": 1000.0}

def test_approve_applies_corrections_and_matches():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    repo.write_extraction("D-100-1", 1, _envelope("D-100-1", {"confidence": 0.5, "fitment": []}))
    deps = _deps(repo)
    out = resolve_review(deps, "r1", "approve", {"confidence": 0.9})
    new_env = repo.get_extraction("D-100-1", 2)
    assert new_env["payload"]["confidence"] == 0.9
    assert new_env["eo_number"] == "D-100-1"  # envelope shape preserved, not a flat Extraction dict
    assert repo.get_eo("D-100-1")["state"] == "complete"
    assert repo.get_review("r1")["status"] == "approved"
    assert out == {"review_id": "r1", "action": "approve", "matches": 0}

def test_reject_marks_failed():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    deps = _deps(repo)
    out = resolve_review(deps, "r1", "reject", None)
    assert repo.get_eo("D-100-1")["state"] == "failed"
    assert repo.get_review("r1")["status"] == "rejected"
    assert out == {"review_id": "r1", "action": "reject", "matches": 0}

def test_unknown_review_raises():
    repo = FakeRepo()
    deps = _deps(repo)
    with pytest.raises(KeyError):
        resolve_review(deps, "nope", "approve", None)

def test_invalid_corrections_validate_before_write():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    repo.write_extraction("D-100-1", 1, _envelope("D-100-1", {"confidence": 0.5, "fitment": []}))
    deps = _deps(repo)
    with pytest.raises(ValidationError):
        resolve_review(deps, "r1", "approve", {"confidence": 1.5})  # out of range, ge=0/le=1
    assert repo.get_extraction("D-100-1", 2) is None  # nothing written
    assert repo.get_review("r1")["status"] == "open"  # review left untouched

def test_double_approve_rejected_and_no_extra_version_written():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    repo.write_extraction("D-100-1", 1, _envelope("D-100-1", {"confidence": 0.5, "fitment": []}))
    deps = _deps(repo)
    resolve_review(deps, "r1", "approve", {"confidence": 0.9})
    with pytest.raises(ReviewNotOpen):
        resolve_review(deps, "r1", "approve", {"confidence": 0.99})
    assert repo.get_extraction("D-100-1", 3) is None  # exactly one new version exists (v2)
    assert repo.get_extraction("D-100-1", 2)["payload"]["confidence"] == 0.9

def test_budget_exceeded_leaves_review_open_and_finishes_run():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    fitment = [{"make": "Toyota", "model": "Celicaand Corolla", "year_start": 1999, "year_end": 1999}]
    repo.write_extraction("D-100-1", 1, _envelope("D-100-1",
        {"confidence": 0.9, "category": "intake", "fitment": fitment}))
    vehicles = [{"id": "v1", "year": 1999, "make": "Toyota", "model": "Celica",
                 "displacement_l": 1.8, "induction": "NA", "cylinders": 4}]
    llm = FakeLLM([LLMResult({"decisions": [{"fitment_index": 0, "vehicle_ids": ["v1"],
                   "rationale": "ambiguous", "confidence": 0.9}]}, 100, 50)])
    deps = _deps(repo, llm=llm, index=VehicleIndex(vehicles), budget=BudgetGuard(0))
    with pytest.raises(BudgetExceeded):
        resolve_review(deps, "r1", "approve", None)
    assert repo.get_review("r1")["status"] == "open"
    assert repo.get_extraction("D-100-1", 2) is not None  # extraction version already written
    run = list(repo.runs.values())[-1]
    assert run["status"] == "budget_exceeded"
