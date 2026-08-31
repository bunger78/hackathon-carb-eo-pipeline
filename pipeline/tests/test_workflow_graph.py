import pytest

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
                        "healed": 0, "status": "ok", "time_capped": False, "cost_usd": 0.0}
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

def test_heal_requeues_transient_failure_and_daily_run_reprocesses_it(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    llm = FakeLLM([LLMResult(GOOD, 100, 50)])
    deps = _deps(llm, listings=[])
    item_id = deps.repo.create_work_item("D-9-9", "priorrun")
    deps.repo.update_work_item(item_id, {"status": "failed", "attempts": 3,
                                          "last_error": "429 rate limited"})
    deps.repo.upsert_eo("D-9-9", {"gcs_uri": "gs://b/pdfs/d-9-9.pdf", "state": "failed"})

    summary = run_workflow(deps, "test")

    assert summary["healed"] == 1
    assert summary["completed"] == 1
    assert deps.repo.work_items[item_id]["status"] == "done"
    assert deps.repo.get_eo("D-9-9")["state"] == "complete"

def test_budget_exceeded_releases_item_to_pending(monkeypatch):
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    listings = [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    llm = FakeLLM([LLMResult(GOOD, 100, 50)])
    deps = _deps(llm, listings=listings, budget_usd=0)
    summary = run_workflow(deps, "test")
    assert summary["status"] == "budget_exceeded"
    item = next(iter(deps.repo.work_items.values()))
    assert item["status"] == "pending"

class _FakeClock:
    """First call returns `start`; every later call jumps far past the cap."""
    def __init__(self, start, cap):
        self.start = start
        self.cap = cap
        self.n = 0

    def time(self):
        self.n += 1
        return self.start if self.n == 1 else self.start + self.cap + 1

def test_unhandled_exception_finishes_run_as_error_not_stuck_running(monkeypatch):
    """Robustness: a bug anywhere in the graph (e.g. scout's listing call
    itself, not just per-EO download) must not leave the run doc "running"
    forever -- it must finish as "error", and the exception must still
    surface to the caller (e.g. /run)."""
    llm = FakeLLM([])
    deps = _deps(llm, listings=[])

    def boom(*a, **k):
        raise RuntimeError("carb site down")
    monkeypatch.setattr("workflow_graph.discover", boom)

    with pytest.raises(RuntimeError):
        run_workflow(deps, "test")

    run = next(iter(deps.repo.runs.values()))
    assert run["status"] == "error"

def test_time_cap_exceeded_routes_to_summarize_without_claiming(monkeypatch):
    from config import settings
    monkeypatch.setattr("workflow_graph.time", _FakeClock(1_000_000.0, settings.run_time_cap_seconds))
    listings = [{"eo_number": "D-9-9", "pdf_url": "http://x/d-9-9.pdf"}]
    llm = FakeLLM([])  # must not be called: claim must not fire once capped
    deps = _deps(llm, listings=listings)
    summary = run_workflow(deps, "test")
    assert summary["time_capped"] is True
    assert summary["completed"] == 0
    assert summary["needs_review"] == 0
    assert summary["failed"] == 0
    assert summary["status"] == "ok"
    assert llm.calls == []
    assert deps.repo.get_eo("D-9-9")["state"] == "discovered"
