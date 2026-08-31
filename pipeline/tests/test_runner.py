from config import settings
from runner import Deps, process_work_item, run_once
from agents.healer import is_transient
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
    def cached_pdf(self, eo): return None
    def pdf_uri(self, eo): return f"gs://b/pdfs/{eo.lower()}.pdf"
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
    assert summary["time_capped"] is False
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

def test_fix_verdict_corrections_reach_matching_and_stored_extraction(monkeypatch):
    """M2: an auditor 'fix' correction must be (i) what run_matching receives
    and (ii) what the stored extraction doc holds -- not the pre-audit
    original the extractor wrote."""
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    extracted = GOOD | {"confidence": 0.4}  # forces critique (below threshold)
    fix_response = {"verdict": "fix", "corrections": {"category": "intake"},
                     "reasons": ["misread section"]}
    llm = FakeLLM([LLMResult(extracted, 100, 50), LLMResult(fix_response, 50, 20)])
    deps = _deps(llm)

    summary = run_once(deps, "test")

    assert summary["completed"] == 1
    assert deps.repo.get_eo("D-9-9")["category"] == "intake"
    stored = deps.repo.get_extraction("D-9-9", 1)
    assert stored["payload"]["category"] == "intake"
    matches = deps.repo.matches["D-9-9"]
    assert matches and matches[0]["category"] == "intake"

def test_extraction_api_error_reaches_healer_as_transient(monkeypatch):
    """M1: an API error surfaced during extraction must survive as the
    work_item's last_error text so the healer can classify it transient."""
    monkeypatch.setattr("agents.extractor.render_pdf_to_images", lambda b: [b"png"])
    api_error = RuntimeError("429 RESOURCE_EXHAUSTED. quota exceeded")
    deps = _deps(FakeLLM([api_error, api_error]))
    run_id = deps.repo.create_run("test")
    deps.repo.upsert_eo("D-9-9", {"gcs_uri": "gs://b/pdfs/d-9-9.pdf", "state": "discovered"})
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    item = dict(deps.repo.work_items[item_id])

    outcome = process_work_item(item, deps, run_id)

    assert outcome == "retry"
    last_error = deps.repo.work_items[item_id]["last_error"]
    assert "429" in last_error
    assert is_transient(last_error)

class _FakeClock:
    """First call returns `start`; every later call jumps far past the cap."""
    def __init__(self, start, cap):
        self.start = start
        self.cap = cap
        self.n = 0

    def time(self):
        self.n += 1
        return self.start if self.n == 1 else self.start + self.cap + 1

def test_time_cap_exceeded_breaks_before_claim(monkeypatch):
    from config import settings
    monkeypatch.setattr("runner.time", _FakeClock(1_000_000.0, settings.run_time_cap_seconds))
    llm = FakeLLM([])  # must not be called: loop must break before claiming
    deps = _deps(llm)
    summary = run_once(deps, "test")
    assert summary["time_capped"] is True
    assert summary["completed"] == 0
    assert summary["needs_review"] == 0
    assert summary["failed"] == 0
    assert summary["status"] == "ok"
    assert llm.calls == []
    assert deps.repo.get_eo("D-9-9")["state"] == "discovered"
