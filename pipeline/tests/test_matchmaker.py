from agents.matchmaker import run_matching
from schemas.extraction import Extraction, FitmentRow
from core.llm import LLMResult
from core.costs import BudgetGuard
from matching.engine import VehicleIndex
from tests.fakes import FakeLLM, FakeRepo

VEHICLES = [
    {"id": "v1", "year": 1999, "make": "Toyota", "model": "Celica", "displacement_l": 1.8,
     "induction": "NA", "cylinders": 4},
    {"id": "v2", "year": 1999, "make": "Toyota", "model": "Corolla", "displacement_l": 1.8,
     "induction": "NA", "cylinders": 4},
]

def _ex(fitment):
    return Extraction(eo_number="D-7-7", confidence=0.9, category="intake",
                      manufacturer="AEM", device_name="Cold Air Intake",
                      part_numbers=["21-8000"], fitment=fitment)

def test_deterministic_match_writes_denormalized_docs():
    repo = FakeRepo(); run = repo.create_run("t")
    ex = _ex([FitmentRow(make="Toyota", model="Celica", year_start=1999, year_end=1999,
                         displacement_l=1.8, induction="NA", cylinders=4, part_numbers=["21-8000"])])
    llm = FakeLLM([])
    counts = run_matching(llm, repo, BudgetGuard(5), "D-7-7", ex, VehicleIndex(VEHICLES), run)
    docs = repo.matches["D-7-7"]
    assert len(docs) == 1 and docs[0]["vehicle_id"] == "v1" and docs[0]["tier"] == "exact"
    assert docs[0]["category"] == "intake" and docs[0]["part_numbers"] == ["21-8000"]
    assert repo.get_eo("D-7-7")["state"] == "complete"
    assert llm.calls == []

def test_ambiguous_row_resolved_by_gemini():
    repo = FakeRepo(); run = repo.create_run("t")
    ex = _ex([FitmentRow(make="Toyota", model="Celicaand Corolla", year_start=1999, year_end=1999)])
    llm = FakeLLM([LLMResult({"decisions": [{"fitment_index": 0, "vehicle_ids": ["v1", "v2"],
                   "rationale": "PDF run-on covers both models", "confidence": 0.9}]}, 100, 40)])
    run_matching(llm, repo, BudgetGuard(5), "D-7-7", ex, VehicleIndex(VEHICLES), run)
    docs = repo.matches["D-7-7"]
    assert {d["vehicle_id"] for d in docs} == {"v1", "v2"}
    assert all(d["method"] == "gemini_resolved" and d["rationale"] for d in docs)

def test_low_confidence_resolution_goes_to_review():
    repo = FakeRepo(); run = repo.create_run("t")
    ex = _ex([FitmentRow(make="Toyota", model="Celicaand Corolla", year_start=1999, year_end=1999)])
    llm = FakeLLM([LLMResult({"decisions": [{"fitment_index": 0, "vehicle_ids": ["v1"],
                   "rationale": "unsure", "confidence": 0.3}]}, 100, 40)])
    run_matching(llm, repo, BudgetGuard(5), "D-7-7", ex, VehicleIndex(VEHICLES), run)
    assert repo.matches.get("D-7-7", []) == []
    assert repo.reviews[0]["reason"] == "ambiguous_match"
