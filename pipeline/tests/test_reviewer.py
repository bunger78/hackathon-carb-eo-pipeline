import pytest
from runner import Deps
from core.costs import BudgetGuard
from matching.engine import VehicleIndex
from tests.fakes import FakeLLM, FakeRepo
from agents.reviewer import resolve_review

def _deps(repo, llm=None):
    return Deps(repo=repo, llm=llm or FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex([]), budget=BudgetGuard(5))

def test_approve_applies_corrections_and_matches():
    repo = FakeRepo()
    repo.reviews.append({"id": "r1", "eo_number": "D-100-1", "status": "open"})
    repo.write_extraction("D-100-1", 1, {"eo_number": "D-100-1", "confidence": 0.5, "fitment": []})
    deps = _deps(repo)
    out = resolve_review(deps, "r1", "approve", {"confidence": 0.9})
    assert repo.get_extraction("D-100-1", 2)["confidence"] == 0.9
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
