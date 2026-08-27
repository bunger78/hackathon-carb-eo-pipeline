import pytest
from agents.extractor import extract, ExtractionFailed
from core.llm import LLMResult
from core.costs import BudgetGuard
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

def test_truncated_native_falls_back_to_images(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    repo, run = _repo_with_eo()
    llm = FakeLLM([LLMResult(GOOD, 100, 50, "MAX_TOKENS"), LLMResult(GOOD, 200, 80)])
    ex = extract(llm, FakeGCS(), repo, BudgetGuard(5), "D-5-5", run)
    assert repo.get_extraction("D-5-5", 1)["ladder_step"] == 2
    assert repo.get_extraction("D-5-5", 1)["finish_reason"] == "STOP"
