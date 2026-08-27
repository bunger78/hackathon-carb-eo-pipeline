from agents.auditor import (deterministic_issues, legacy_divergence, needs_critique,
                            apply_corrections, audit)
from schemas.extraction import Extraction, FitmentRow
from core.llm import LLMResult
from core.costs import BudgetGuard
from tests.fakes import FakeLLM, FakeRepo

MAKES = {"chevrolet", "toyota"}

def _ex(**kw):
    base = dict(eo_number="D-5-5", confidence=0.9, category="exhaust",
                part_numbers=["ABC123"], fitment=[FitmentRow(make="Toyota", model="Celica",
                year_start=1999, year_end=2000, part_numbers=["ABC123"])])
    return Extraction(**(base | kw))

def test_clean_extraction_no_issues():
    assert deterministic_issues(_ex(), MAKES) == []

def test_catches_bad_pn_and_make_and_year():
    ex = _ex(part_numbers=["A B", "x"], fitment=[FitmentRow(make="Yugo", model="GV",
             year_start=1849, year_end=2000)])
    issues = deterministic_issues(ex, MAKES)
    assert "bad_part_number" in issues and "unknown_make" in issues and "bad_year" in issues

def test_eo_number_formats():
    for good in ("D-1", "B-20", "D-269-30", "70-84-A", "IM-007-0002"):
        assert "bad_eo_number" not in deterministic_issues(_ex(eo_number=good), MAKES)
    for bad in ("", "D 269 30", "d-269-30", "-D-1-"):
        assert "bad_eo_number" in deterministic_issues(_ex(eo_number=bad), MAKES)

def test_divergence():
    assert legacy_divergence(_ex(), None) == 0.0
    legacy = {"part_numbers": ["ABC123"], "fitment_count": 1}
    assert legacy_divergence(_ex(), legacy) < 0.1
    legacy2 = {"part_numbers": ["ZZZ999"], "fitment_count": 40}
    assert legacy_divergence(_ex(), legacy2) > 0.6

def test_needs_critique_paths():
    assert needs_critique(["bad_year"], 0.0, 0.99, 0.0, 0.5)
    assert needs_critique([], 0.9, 0.99, 0.0, 0.5)
    assert needs_critique([], 0.0, 0.5, 0.0, 0.5)
    assert needs_critique([], 0.0, 0.99, 0.05, 0.01)      # QA sample
    assert not needs_critique([], 0.0, 0.99, 0.05, 0.9)

def test_audit_accepts_clean_without_llm():
    repo, run = FakeRepo(), None
    run = repo.create_run("t")
    llm = FakeLLM([])  # must not be called
    out = audit(llm, repo, BudgetGuard(5), "D-5-5", _ex(), MAKES, run, rand=0.9)
    assert out == "accepted"
    assert repo.get_eo("D-5-5")["state"] == "matching"
    assert llm.calls == []

def test_audit_escalates_on_verdict():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"verdict": "escalate", "reasons": ["p2 table unreadable"]}, 50, 20)])
    ex = _ex(confidence=0.4)  # forces critique
    out = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert repo.get_eo("D-5-5")["state"] == "needs_review"
    assert repo.reviews[0]["reason"] == "low_confidence"

def test_supersession_marks_predecessor():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-4", {"state": "complete"})
    llm = FakeLLM([])
    audit(llm, repo, BudgetGuard(5), "D-5-5", _ex(supersedes=["D-5-4"]), MAKES, run, rand=0.9)
    assert repo.get_eo("D-5-4")["eo_status"] == "superseded"
