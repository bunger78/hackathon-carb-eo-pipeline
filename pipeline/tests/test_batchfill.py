import json
import time

from config import settings
from core.costs import BudgetGuard, BudgetExceeded
from matching.engine import VehicleIndex
from prompts.extractor import EXTRACTOR_PROMPT
from runner import Deps
from tests.fakes import FakeLLM, FakeRepo

import batchfill

VEHICLES = [{"id": "v1", "year": 1999, "make": "Toyota", "model": "Celica",
             "displacement_l": 1.8, "induction": "NA", "cylinders": 4}]
GOOD = {"eo_number": "D-9-9", "confidence": 0.95, "category": "exhaust",
        "part_numbers": ["EX100"], "fitment": [{"make": "Toyota", "model": "Celica",
        "year_start": 1999, "year_end": 1999, "displacement_l": 1.8,
        "induction": "NA", "cylinders": 4, "part_numbers": ["EX100"]}]}


def _response(payload, finish_reason="STOP", tok_in=1000, tok_out=200):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(payload)}]},
                            "finishReason": finish_reason}],
            "usageMetadata": {"promptTokenCount": tok_in, "candidatesTokenCount": tok_out}}


def _line(request_uri, payload=None, finish_reason="STOP", tok_in=1000, tok_out=200):
    request = {"contents": [{"role": "user", "parts": [
        {"text": EXTRACTOR_PROMPT},
        {"fileData": {"fileUri": request_uri, "mimeType": "application/pdf"}}]}]}
    line = {"request": request}
    if payload is not None:
        line["response"] = _response(payload, finish_reason, tok_in, tok_out)
    return line


def _deps(llm=None, budget_usd=5):
    return Deps(repo=FakeRepo(), llm=llm or FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex(VEHICLES), budget=BudgetGuard(budget_usd))


# --- request-building (--prepare) ---

def test_build_request_line_carries_uri_prompt_and_schema():
    line = batchfill.build_request_line("gs://b/pdfs/d-9-9.pdf")
    req = line["request"]
    parts = req["contents"][0]["parts"]
    assert parts[0]["text"] == EXTRACTOR_PROMPT
    assert parts[1]["fileData"] == {"fileUri": "gs://b/pdfs/d-9-9.pdf", "mimeType": "application/pdf"}
    cfg = req["generationConfig"]
    assert cfg["temperature"] == 0
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["maxOutputTokens"] == 65535
    schema = cfg["responseSchema"]
    assert schema["properties"]["eo_number"]["type"] == "STRING"
    assert "fitment" in schema["properties"]


def test_select_batch_items_resets_429_and_excludes_1m_cap(monkeypatch):
    repo = FakeRepo()
    pending_id = repo.create_work_item("D-1-1", "run0")
    reset_id = repo.create_work_item("D-2-2", "run0")
    repo.update_work_item(reset_id, {"status": "failed", "attempts": 3, "last_error": "429 rate limited"})
    excluded_id = repo.create_work_item("D-3-3", "run0")
    repo.update_work_item(excluded_id, {"status": "failed", "attempts": 3,
                                        "last_error": "input token count (2000000) exceeds the "
                                                      "maximum number of tokens allowed (1048576)"})
    other_failed_id = repo.create_work_item("D-4-4", "run0")
    repo.update_work_item(other_failed_id, {"status": "failed", "attempts": 3, "last_error": "boom"})

    candidates = list(repo.work_items.values())
    selected = batchfill.select_batch_items(candidates, repo)

    selected_eos = {i["eo_number"] for i in selected}
    assert selected_eos == {"D-1-1", "D-2-2"}
    assert repo.work_items[reset_id]["status"] == "pending"
    assert repo.work_items[reset_id]["attempts"] == 0
    # 1M-cap item and the "other" failure are left alone, still failed.
    assert repo.work_items[excluded_id]["status"] == "failed"
    assert repo.work_items[other_failed_id]["status"] == "failed"


def test_select_batch_items_leaves_permanent_400_error_failed_even_if_it_mentions_429():
    """Regression: select_batch_items now shares agents.healer.is_transient
    with the daily self-heal stage, so a permanent 400/INVALID_ARGUMENT error
    that happens to mention "429" in its body is no longer wrongly reset."""
    repo = FakeRepo()
    mixed_id = repo.create_work_item("D-5-5", "run0")
    repo.update_work_item(mixed_id, {"status": "failed", "attempts": 3,
                                     "last_error": "400 INVALID_ARGUMENT. body mentions 429 but is permanent"})

    selected = batchfill.select_batch_items(list(repo.work_items.values()), repo)

    assert selected == []
    assert repo.work_items[mixed_id]["status"] == "failed"


def test_build_requests_looks_up_gcs_uri_per_item():
    repo = FakeRepo()
    repo.upsert_eo("D-1-1", {"gcs_uri": "gs://b/pdfs/d-1-1.pdf"})
    items = [{"eo_number": "D-1-1"}]
    lines = batchfill.build_requests(items, repo)
    assert len(lines) == 1
    uri = lines[0]["request"]["contents"][0]["parts"][1]["fileData"]["fileUri"]
    assert uri == "gs://b/pdfs/d-1-1.pdf"


# --- small parsing helpers ---

def test_eo_from_request_recovers_upper_eo():
    req = {"contents": [{"role": "user", "parts": [
        {"text": "x"}, {"fileData": {"fileUri": "gs://b/pdfs/d-9-9.pdf", "mimeType": "application/pdf"}}]}]}
    assert batchfill._eo_from_request(req) == "D-9-9"


def test_is_token_cap_error_detects_marker():
    assert batchfill._is_token_cap_error("exceeds the maximum number of tokens allowed (1048576)")
    assert not batchfill._is_token_cap_error("429 too many requests")
    assert not batchfill._is_token_cap_error(None)


# --- --ingest ---

def test_ingest_line_happy_path_writes_extraction_and_completes(monkeypatch):
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=1000, tok_out=200)

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "complete"
    assert deps.repo.work_items[item_id]["status"] == "done"
    assert deps.repo.work_items[item_id]["stage"] == "complete"
    extraction = deps.repo.extractions["D-9-9_v1"]
    assert extraction["eo_number"] == "D-9-9"
    assert extraction["payload"]["eo_number"] == "D-9-9"
    assert extraction["ladder_step"] == 1
    assert extraction["finish_reason"] == "STOP"
    assert extraction["tok_in"] == 1000 and extraction["tok_out"] == 200
    assert deps.repo.get_eo("D-9-9")["state"] == "complete"
    assert len(deps.repo.matches["D-9-9"]) == 1


def test_ingest_line_bad_finish_reason_bounces_to_pending_without_writing(monkeypatch):
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    deps.repo.update_work_item(item_id, {"status": "in_progress", "attempts": 1})
    work_item = dict(deps.repo.work_items[item_id])
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, finish_reason="MAX_TOKENS")

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "bounced"
    assert deps.repo.work_items[item_id]["status"] == "pending"
    assert deps.repo.work_items[item_id]["attempts"] == 1  # unchanged
    assert deps.repo.extractions == {}
    assert deps.repo.runs[run_id]["cost_usd"] == 0.0


def test_ingest_line_missing_response_bounces():
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    line = {"request": {"contents": [{"role": "user", "parts": [
        {"fileData": {"fileUri": "gs://b/pdfs/d-9-9.pdf", "mimeType": "application/pdf"}}]}]}}

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "bounced"
    assert deps.repo.work_items[item_id]["status"] == "pending"
    assert deps.repo.extractions == {}


def test_ingest_line_invalid_json_bounces():
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD)
    line["response"]["candidates"][0]["content"]["parts"][0]["text"] = "not json"

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "bounced"
    assert deps.repo.work_items[item_id]["status"] == "pending"
    assert deps.repo.extractions == {}


def test_ingest_line_budget_exceeded_bounces_and_raises(monkeypatch):
    deps = _deps(budget_usd=0)
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    tok_in, tok_out = 1000, 200
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=tok_in, tok_out=tok_out)

    try:
        batchfill.ingest_line(line, work_item, deps, run_id)
        assert False, "expected BudgetExceeded"
    except BudgetExceeded:
        pass
    assert deps.repo.work_items[item_id]["status"] == "pending"
    assert deps.repo.extractions == {}
    # The call that tripped the budget is still metered into the run record
    # (cost recording happens before the raise -- see batchfill.ingest_line).
    expected = tok_in * settings.price_in_per_mtok / 2 / 1e6 + tok_out * settings.price_out_per_mtok / 2 / 1e6
    assert deps.repo.runs[run_id]["cost_usd"] == expected


def test_ingest_line_leases_work_item_at_start():
    deps = _deps()
    item_id = deps.repo.create_work_item("D-9-9", "run0")
    work_item = dict(deps.repo.work_items[item_id])
    before = time.time()

    batchfill._lease(deps.repo, work_item)

    assert deps.repo.work_items[item_id]["status"] == "in_progress"
    assert deps.repo.work_items[item_id]["worker"] == "batch-ingest"
    assert deps.repo.work_items[item_id]["lease_expires"] >= before + settings.lease_seconds


def test_already_done_true_only_when_extraction_exists():
    repo = FakeRepo()
    assert batchfill._already_done(repo, "D-9-9") is False
    repo.write_extraction("D-9-9", 1, {"eo_number": "D-9-9"})
    assert batchfill._already_done(repo, "D-9-9") is True


# --- crash-safety: tail (audit/match) exceptions never crash --ingest,
# and never re-book an already-committed extraction on re-run ---

def test_tail_exception_bounces_with_attempts_increment_but_keeps_extraction_committed(monkeypatch):
    monkeypatch.setattr(batchfill, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    tok_in, tok_out = 1000, 200
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=tok_in, tok_out=tok_out)

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "retry"
    assert deps.repo.work_items[item_id]["status"] == "pending"
    assert deps.repo.work_items[item_id]["attempts"] == 1
    assert deps.repo.work_items[item_id]["last_error"] == "boom"
    # Extraction + cost were committed BEFORE the tail ran -- not rolled back.
    assert deps.repo.next_extraction_version("D-9-9") == 2  # v1 exists
    expected = tok_in * settings.price_in_per_mtok / 2 / 1e6 + tok_out * settings.price_out_per_mtok / 2 / 1e6
    assert deps.repo.runs[run_id]["cost_usd"] == expected


def test_tail_exception_at_max_attempts_marks_item_and_eo_failed(monkeypatch):
    monkeypatch.setattr(batchfill, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    deps.repo.update_work_item(item_id, {"attempts": settings.max_attempts - 1})
    work_item = dict(deps.repo.work_items[item_id])
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD)

    outcome = batchfill.ingest_line(line, work_item, deps, run_id)

    assert outcome == "failed"
    assert deps.repo.work_items[item_id]["status"] == "failed"
    assert deps.repo.work_items[item_id]["attempts"] == settings.max_attempts
    assert deps.repo.get_eo("D-9-9")["state"] == "failed"


def test_rerun_after_tail_crash_is_idempotent_via_already_done(monkeypatch):
    """The mechanism fix 1a relies on: once extraction+cost are committed,
    _already_done reports True regardless of the work item's status, so the
    ingest() orchestrator never calls ingest_line a second time for this EO --
    one extraction, one cost booking, even though the tail blew up."""
    monkeypatch.setattr(batchfill, "audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=1000, tok_out=200)

    assert batchfill._already_done(deps.repo, "D-9-9") is False
    outcome = batchfill.ingest_line(line, work_item, deps, run_id)
    assert outcome == "retry"

    # Re-run: the orchestrator checks _already_done BEFORE calling ingest_line
    # again -- it must now say "skip", so a naive re-run of --ingest over the
    # same output prefix never reprocesses this line.
    assert batchfill._already_done(deps.repo, "D-9-9") is True
    assert deps.repo.next_extraction_version("D-9-9") == 2  # still just the one (v1)
    expected = 1000 * settings.price_in_per_mtok / 2 / 1e6 + 200 * settings.price_out_per_mtok / 2 / 1e6
    assert deps.repo.runs[run_id]["cost_usd"] == expected  # booked exactly once


def test_ingest_continues_to_next_line_after_tail_failure(monkeypatch):
    """A tail failure for one EO's line must not crash processing of the next
    line in the same --ingest pass (this is what makes 'ingest continues to
    next line' true even without going through the full GCS-streaming loop:
    ingest_line itself never lets a plain exception escape)."""
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")

    real_audit = batchfill.audit
    calls = {"n": 0}
    def flaky_audit(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_audit(*a, **k)
    monkeypatch.setattr(batchfill, "audit", flaky_audit)

    item1 = deps.repo.create_work_item("D-9-9", run_id)
    item2 = deps.repo.create_work_item("D-9-10", run_id)
    work_item1 = dict(deps.repo.work_items[item1])
    work_item2 = dict(deps.repo.work_items[item2])
    line1 = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=1000, tok_out=200)
    line2 = _line("gs://b/pdfs/d-9-10.pdf", GOOD | {"eo_number": "D-9-10"}, tok_in=1000, tok_out=200)

    outcome1 = batchfill.ingest_line(line1, work_item1, deps, run_id)
    outcome2 = batchfill.ingest_line(line2, work_item2, deps, run_id)

    assert outcome1 == "retry"
    assert outcome2 == "complete"
    assert deps.repo.work_items[item1]["status"] == "pending"
    assert deps.repo.work_items[item2]["status"] == "done"


# --- cost accounting: batch tokens at half price ---

def test_batch_extraction_cost_is_half_the_configured_rate(monkeypatch):
    monkeypatch.setattr("agents.auditor.random.random", lambda: 0.99)
    deps = _deps()
    run_id = deps.repo.create_run("batch-backfill")
    item_id = deps.repo.create_work_item("D-9-9", run_id)
    work_item = dict(deps.repo.work_items[item_id])
    tok_in, tok_out = 100_000, 5_000
    line = _line("gs://b/pdfs/d-9-9.pdf", GOOD, tok_in=tok_in, tok_out=tok_out)

    batchfill.ingest_line(line, work_item, deps, run_id)

    expected = (tok_in * settings.price_in_per_mtok / 2 / 1e6
                + tok_out * settings.price_out_per_mtok / 2 / 1e6)
    extraction = deps.repo.extractions["D-9-9_v1"]
    assert extraction["cost_usd"] == expected
    assert deps.repo.runs[run_id]["cost_usd"] == expected
    # Sanity: well under what full online price would have charged.
    full_price = (tok_in * settings.price_in_per_mtok / 1e6
                  + tok_out * settings.price_out_per_mtok / 1e6)
    assert expected < full_price
