import pytest
from agents.extractor import extract, ExtractionFailed
from agents.healer import is_transient
from core.llm import LLMResult
from core.costs import BudgetGuard, BudgetExceeded, cost_usd
from tests.fakes import FakeLLM, FakeRepo

GOOD = {"eo_number": "D-5-5", "confidence": 0.9}

class FakeGCS:
    def download(self, uri): return b"%PDF-fake"
    def upload_page_images(self, eo, images): return ["gs://b/pages/d-5-5/0.png"]

def _repo_with_eo():
    r = FakeRepo()
    r.upsert_eo("D-5-5", {"gcs_uri": "gs://b/pdfs/d-5-5.pdf", "state": "discovered"})
    return r, r.create_run("t")

def test_native_pdf_first_try(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([LLMResult(GOOD, 100, 50)])
    ex = extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)
    assert ex.eo_number == "D-5-5"
    doc = repo.get_extraction("D-5-5", 1)
    assert doc["ladder_step"] == 1 and doc["cost_usd"] > 0

def test_falls_back_to_images(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([RuntimeError("boom"), LLMResult(GOOD, 200, 80)])
    ex = extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)
    assert repo.get_extraction("D-5-5", 1)["ladder_step"] == 2

def test_both_rungs_fail(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(ExtractionFailed):
        extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)

def test_both_rungs_fail_preserves_api_error_text_for_healer(monkeypatch):
    """M1: ExtractionFailed must carry the real API error string (not just
    the EO number) so runner.py's last_error lets the healer classify a
    quota/5xx failure as transient."""
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    api_error = RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded for quota metric.")
    llm = FakeLLM([api_error, api_error])
    with pytest.raises(ExtractionFailed) as exc_info:
        extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)
    assert "429" in str(exc_info.value)
    assert is_transient(str(exc_info.value))

def test_truncated_native_falls_back_to_images(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([LLMResult(GOOD, 100, 50, "MAX_TOKENS"), LLMResult(GOOD, 200, 80)])
    ex = extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)
    assert repo.get_extraction("D-5-5", 1)["ladder_step"] == 2
    assert repo.get_extraction("D-5-5", 1)["finish_reason"] == "STOP"
    # C2: rung 1's truncated (but usage-bearing) call must still be metered,
    # not silently discarded when the ladder falls through to rung 2.
    expected_total = cost_usd(100, 50) + cost_usd(200, 80)
    assert repo.runs[run]["cost_usd"] == pytest.approx(expected_total)
    assert repo.runs[run]["tok_in"] == 300 and repo.runs[run]["tok_out"] == 130

def test_budget_exceeded_call_is_still_metered_into_run_cost(monkeypatch):
    """C2: the call that trips BudgetGuard must still land in the run's cost
    record -- cost recording happens before the raise, not after."""
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([LLMResult(GOOD, 1_000_000, 1_000_000)])  # far exceeds a tiny budget
    with pytest.raises(BudgetExceeded):
        extract(llm, FakeGCS(), repo, BudgetGuard(0.01), "D-5-5", run)
    assert repo.runs[run]["cost_usd"] == pytest.approx(cost_usd(1_000_000, 1_000_000))
