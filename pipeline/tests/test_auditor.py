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
    out, audited = audit(llm, repo, BudgetGuard(5), "D-5-5", _ex(), MAKES, run, rand=0.9)
    assert out == "accepted"
    assert audited.eo_number == "D-5-5"
    assert repo.get_eo("D-5-5")["state"] == "matching"
    assert llm.calls == []

def test_audit_escalates_on_verdict():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"verdict": "escalate", "reasons": ["p2 table unreadable"]}, 50, 20)])
    ex = _ex(confidence=0.4)  # forces critique
    out, _ = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert repo.get_eo("D-5-5")["state"] == "needs_review"
    assert repo.reviews[0]["reason"] == "low_confidence"

def test_accept_verdict_cannot_override_deterministic_issues():
    """C1: a critic 'accept' is an AI opinion, not a fixed rule -- it must
    never wave through an extraction that failed a deterministic check."""
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"verdict": "accept", "reasons": ["looks fine to me"]}, 50, 20)])
    ex = _ex(part_numbers=["A B"])  # bad_part_number: a real deterministic issue
    assert "bad_part_number" in deterministic_issues(ex, MAKES)
    out, audited = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert audited is ex
    assert repo.get_eo("D-5-5")["state"] == "needs_review"
    assert repo.reviews[0]["reason"] == "validation_failure"

def test_supersession_marks_predecessor():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-4", {"state": "complete"})
    llm = FakeLLM([])
    audit(llm, repo, BudgetGuard(5), "D-5-5", _ex(supersedes=["D-5-4"]), MAKES, run, rand=0.9)
    assert repo.get_eo("D-5-4")["state"] == "superseded"
    assert repo.get_eo("D-5-4")["superseded_by"] == "D-5-5"

def test_fix_verdict_applies_corrections():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"verdict": "fix", "corrections": {"category": "intake"}, "reasons": ["misread section"]}, 50, 20)])
    ex = _ex(confidence=0.4, category=None)  # forces critique, has fixable defect
    out, audited = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "accepted"
    assert audited.category == "intake"
    assert repo.get_eo("D-5-5")["category"] == "intake"

def test_malformed_corrections_escalate():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"verdict": "fix", "corrections": {"confidence": "not-a-number"}, "reasons": ["typo"]}, 50, 20)])
    ex = _ex(confidence=0.4)  # forces critique
    out, _ = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert repo.reviews[0]["reason"] == "validation_failure"
    assert "corrections failed validation" in repo.reviews[0]["agent_notes"]

def test_divergence_only_escalation():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    repo.legacy["D-5-5"] = {"part_numbers": ["ZZZ999"], "fitment_count": 40}  # high divergence
    llm = FakeLLM([LLMResult({"verdict": "escalate", "reasons": ["legacy mismatch"]}, 50, 20)])
    ex = _ex(confidence=0.99)  # high confidence, no deterministic issues
    out, _ = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert repo.reviews[0]["reason"] == "legacy_divergence"

def test_invalid_critique_output_escalates():
    repo = FakeRepo(); run = repo.create_run("t")
    repo.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf"})
    llm = FakeLLM([LLMResult({"garbage": 1}, 10, 5)])
    ex = _ex(confidence=0.4)  # forces critique
    out, _ = audit(llm, repo, BudgetGuard(5), "D-5-5", ex, MAKES, run, rand=0.9)
    assert out == "escalated"
    assert repo.reviews[0]["reason"] == "validation_failure"
