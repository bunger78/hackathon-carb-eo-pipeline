from runner import Deps, process_work_item, run_once
from core.llm import LLMResult
from core.costs import BudgetGuard
from matching.engine import VehicleIndex
from tests.fakes import FakeLLM, FakeRepo

VEHICLES = [{"id": "v1", "year": 1999, "make": "Toyota", "model": "Celica",
             "displacement_l": 1.8, "induction": "NA", "cylinders": 4}]
GOOD = {"eo_number": "D-9-9", "confidence": 0.95, "category": "exhaust",
        "part_numbers": ["EX100"], "fitment": [{"make": "Toyota", "model": "Celica",
        "year_start": 1999, "year_end": 1999, "displacement_l": 1.8,
        "induction": "NA", "cylinders": 4, "part_numbers": ["EX100"]}]}

class FakeCarb:
    def list_all(self): return [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    def download_pdf(self, url): return b"%PDF-fake"

class FakeGCS:
    def upload_pdf(self, eo, data): return f"gs://b/pdfs/{eo.lower()}.pdf"
    def download(self, uri): return b"%PDF-fake"
    def upload_page_images(self, eo, images): return ["gs://b/p/0.png"]

def _deps(llm):
    return Deps(repo=FakeRepo(), llm=llm, gcs=FakeGCS(), carb=FakeCarb(),
                index=VehicleIndex(VEHICLES), budget=BudgetGuard(5))

def test_full_happy_run(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    deps = _deps(FakeLLM([LLMResult(GOOD, 100, 50)]))
    summary = run_once(deps, "test")
    assert summary["new_eos"] == 1 and summary["completed"] == 1
    assert deps.repo.get_eo("D-9-9")["state"] == "complete"
    assert len(deps.repo.matches["D-9-9"]) == 1

def test_failure_retries_then_fails(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    boom = [RuntimeError(f"x{i}") for i in range(6)]  # both rungs fail x3 attempts
    deps = _deps(FakeLLM(boom))
    summary = run_once(deps, "test")
    assert summary["failed"] == 1
    assert deps.repo.get_eo("D-9-9")["state"] == "failed"
