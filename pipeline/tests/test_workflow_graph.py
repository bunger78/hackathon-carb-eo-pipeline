from runner import Deps
from workflow_graph import run_workflow
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
LOW_CONF = GOOD | {"confidence": 0.4}

class FakeCarb:
    def __init__(self, listings=None):
        self.listings = listings if listings is not None else []
    def list_all(self): return self.listings
    def download_pdf(self, url): return b"%PDF-fake"

class FakeGCS:
    def upload_pdf(self, eo, data): return f"gs://b/pdfs/{eo.lower()}.pdf"
    def download(self, uri): return b"%PDF-fake"
    def upload_page_images(self, eo, images): return ["gs://b/p/0.png"]

def _deps(llm, listings=None, budget_usd=5):
    return Deps(repo=FakeRepo(), llm=llm, gcs=FakeGCS(), carb=FakeCarb(listings),
                index=VehicleIndex(VEHICLES), budget=BudgetGuard(budget_usd))

def test_no_work_day_reaches_summarize_with_zero_llm_calls():
    llm = FakeLLM([])  # must not be called
    deps = _deps(llm, listings=[])
    summary = run_workflow(deps, "test")
    assert summary == {"new_eos": 0, "completed": 0, "needs_review": 0, "failed": 0,
                        "status": "ok", "cost_usd": 0.0}
    assert llm.calls == []
    assert list(deps.repo.runs.values())[0]["status"] == "ok"

def test_two_item_queue_loops_and_counts_correctly(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    listings = [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"},
                {"eo_number": "D-9-10", "pdf_url": "http://x/d-9-10.pdf"}]
    llm = FakeLLM([LLMResult(GOOD, 100, 50), LLMResult(GOOD, 100, 50)])
    deps = _deps(llm, listings=listings)
    summary = run_workflow(deps, "test")
    assert summary["new_eos"] == 2
    assert summary["completed"] == 2
    assert summary["needs_review"] == 0
    assert summary["failed"] == 0
    assert summary["status"] == "ok"
    assert deps.repo.get_eo("D-9-9")["state"] == "complete"
    assert deps.repo.get_eo("D-9-10")["state"] == "complete"

def test_escalation_path_counts_and_updates_work_item(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    listings = [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    llm = FakeLLM([LLMResult(LOW_CONF, 100, 50),
                   LLMResult({"verdict": "escalate", "reasons": ["p2 unreadable"]}, 50, 20)])
    deps = _deps(llm, listings=listings)
    summary = run_workflow(deps, "test")
    assert summary["needs_review"] == 1
    assert summary["completed"] == 0
    item = next(iter(deps.repo.work_items.values()))
    assert item["status"] == "done" and item["stage"] == "review"
    assert deps.repo.get_eo("D-9-9")["state"] == "needs_review"

def test_budget_exceeded_releases_item_to_pending(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    listings = [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    llm = FakeLLM([LLMResult(GOOD, 100, 50)])
    deps = _deps(llm, listings=listings, budget_usd=0)
    summary = run_workflow(deps, "test")
    assert summary["status"] == "budget_exceeded"
    item = next(iter(deps.repo.work_items.values()))
    assert item["status"] == "pending"
