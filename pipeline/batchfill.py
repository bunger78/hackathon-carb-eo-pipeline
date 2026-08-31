"""Batch backfill for the remaining CARB EO corpus.

Runs extraction through a Vertex AI batch job at half price (batch pricing is
a 50% discount on the per-token rate); audit + matching stay on the normal
online path (they're the cheap tail, and keeping them online means no new
critique/resolver code paths to maintain). Same prompt and schema as the
online extractor -- imported, never copied (see prompts/extractor.py and
schemas/extraction.py).

Three subcommands:
  py -3 batchfill.py --prepare
      Builds requests-<runid>.jsonl from pending (+ reset 429-failed) work
      items and uploads it to GCS. No LLM spend.
  py -3 batchfill.py --submit <jsonl-uri>
      Creates the Vertex batch job. Spend starts here -- the controller runs
      this once human spend approval is granted. NEVER run by this script's
      author/tests.
  py -3 batchfill.py --ingest <job-out-prefix>
      Streams the batch job's output JSONL(s), writes extractions for valid
      lines (running audit + matching online at full price), and bounces
      truncated/invalid lines back to the online ladder.
"""
import argparse
import json
import os
import sys
import time

from pydantic import ValidationError

from config import settings
from core.costs import BudgetGuard, BudgetExceeded
from agents.auditor import audit
from agents.healer import is_transient
from agents.matchmaker import run_matching
from prompts.extractor import EXTRACTOR_PROMPT, PROMPT_VERSION
from runner import Deps
from schemas.extraction import Extraction

# --- schema: reuse the SDK's own pydantic-model -> Gemini-schema conversion,
# the same machinery core/llm.py relies on when it hands response_schema=Extraction
# to types.GenerateContentConfig (see google/genai/models.py:_GenerateContentConfig_to_vertex,
# which calls this exact function). Passing client=None is safe: t_schema only
# touches the client to check Gemini-Developer-API-only restrictions that don't
# apply here (verified locally -- no network/credentials involved).
def _extraction_schema_json() -> dict:
    from google.genai import _transformers as t
    schema = t.t_schema(None, Extraction)
    return schema.model_dump(mode="json", exclude_none=True, by_alias=True)


# The Gemini/Vertex 1M-token input cap. When a prior attempt's last_error names
# this limit, no amount of retrying (online or batch) will help -- the PDF is
# just too big for the model's context window. Leave it failed.
_TOKEN_CAP_MARKERS = ("1048576", "1,048,576")


def _is_token_cap_error(last_error: str) -> bool:
    return any(m in (last_error or "") for m in _TOKEN_CAP_MARKERS)


def build_request_line(gcs_uri: str) -> dict:
    """One JSONL line for the batch input file: identical prompt/generation
    config to the online extractor's PDF rung (core/llm.py LLM.extract_pdf /
    LLM._call), expressed as the raw Vertex batch-prediction request shape
    (`{"request": {"contents": [...], "generationConfig": {...}}}`)."""
    return {
        "request": {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": EXTRACTOR_PROMPT},
                    {"fileData": {"fileUri": gcs_uri, "mimeType": "application/pdf"}},
                ],
            }],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": _extraction_schema_json(),
                "maxOutputTokens": 65535,
            },
        }
    }


def select_batch_items(candidates: list[dict], repo) -> list[dict]:
    """candidates: work items already queried with status pending or failed.
    Resets failed items classified transient by agents.healer.is_transient
    (the same classification the daily self-heal stage uses) back to pending
    with attempts=0 and includes them; excludes (leaves failed, untouched)
    any item whose last_error mentions the 1M-token input cap; returns the
    final pending set eligible for this batch."""
    selected = []
    for item in candidates:
        last_error = item.get("last_error") or ""
        if _is_token_cap_error(last_error):
            continue
        if item["status"] == "failed":
            if not is_transient(last_error):
                continue
            repo.update_work_item(item["id"], {"status": "pending", "attempts": 0})
            item = {**item, "status": "pending", "attempts": 0}
        if item["status"] == "pending":
            selected.append(item)
    return selected


def build_requests(items: list[dict], repo) -> list[dict]:
    """items: already-selected, batch-eligible work items. Looks up each EO's
    PDF gs:// URI and returns one JSONL request-line dict per item."""
    lines = []
    for item in items:
        eo_doc = repo.get_eo(item["eo_number"])
        lines.append(build_request_line(eo_doc["gcs_uri"]))
    return lines


def _fetch_candidate_work_items(repo) -> list[dict]:
    """Bulk read beyond Repo's single-claim API. Repo has no bulk work-item
    query (claim_next only leases one item transactionally), so this reaches
    into the underlying Firestore client directly -- the same idiom backfill.py
    uses for `bucket.list_blobs(...)` when GCSStore's narrow API doesn't cover
    a need. Not exercised by fakes; see select_batch_items/build_requests for
    the tested core logic this feeds."""
    pending = [d.to_dict() | {"id": d.id} for d in
               repo.db.collection("work_items").where("status", "==", "pending").stream()]
    failed = [d.to_dict() | {"id": d.id} for d in
              repo.db.collection("work_items").where("status", "==", "failed").stream()]
    return pending + failed


def prepare(repo, gcs, run_id=None) -> tuple[int, str]:
    """Production entrypoint for --prepare. No LLM spend."""
    run_id = run_id or time.strftime("%Y%m%dT%H%M%S")
    items = select_batch_items(_fetch_candidate_work_items(repo), repo)
    lines = build_requests(items, repo)
    body = "\n".join(json.dumps(line) for line in lines).encode()
    path = f"batch/in/requests-{run_id}.jsonl"
    gcs.bucket.blob(path).upload_from_string(body, content_type="application/jsonl")
    return len(lines), f"gs://{gcs.name}/{path}"


def submit(client, jsonl_uri: str, run_id=None) -> str:
    """Production entrypoint for --submit. Creates the batch job -- spend
    starts here. Never called by the implementer or by tests."""
    run_id = run_id or time.strftime("%Y%m%dT%H%M%S")
    dest = f"gs://{settings.bucket}/batch/out/{run_id}/"
    job = client.batches.create(model=settings.model_id, src=jsonl_uri, config={"dest": dest})
    return job.name


def _finish_reason(response: dict) -> str:
    try:
        return response["candidates"][0].get("finishReason", "STOP")
    except (KeyError, IndexError, TypeError):
        return "STOP"


def _parse_response(response: dict):
    """Returns (Extraction, tok_in, tok_out). Raises on any malformed shape;
    callers treat that identically to a bad finish_reason."""
    cand = response["candidates"][0]
    text = cand["content"]["parts"][0]["text"]
    data = json.loads(text)
    ex = Extraction.model_validate(data)
    usage = response.get("usageMetadata", {})
    return ex, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)


def _eo_from_request(request: dict) -> str:
    """Recover the EO number from the echoed request's file_data URI -- same
    pdfs/<eo>.pdf naming convention as core/gcs.GCSStore.pdf_uri and
    backfill.py's bootstrap()."""
    for part in request["contents"][0]["parts"]:
        # Vertex's echoed requests serialize absent unions as explicit nulls,
        # so "fileData" can be present-but-None (and so can "parts" entries).
        uri = ((part or {}).get("fileData") or {}).get("fileUri")
        if uri:
            return uri.rsplit("/", 1)[-1].removesuffix(".pdf").upper()
    raise ValueError("echoed request has no file_data part")


def _already_done(repo, eo) -> bool:
    """Deliberately extraction-existence ONLY, not work-item status. If a
    crash lands between committing the extraction (write_extraction +
    add_run_cost) and marking the work item "done" (i.e. anywhere in the
    online audit/match tail), the item can be left pending/in_progress/failed
    with the extraction already written. Gating on status=="done" too would
    let a re-run reprocess that same line and double-book its cost via a
    second write_extraction/add_run_cost call; extraction-exists is the one
    fact that's true if and only if this EO's batch cost has actually been
    booked, regardless of what happened to the work item afterwards."""
    return repo.next_extraction_version(eo) > 1


def _lease(repo, work_item) -> None:
    """Claim this EO's work item for the duration of --ingest's handling of
    it (own status/worker/lease, same shape core.db.Repo.claim_next uses) so
    a concurrently running online runner -- which leases via its own
    claim_next -- can't reclaim and reprocess the same EO while batch ingest
    is mid-handling it (that would be a second, full-price online
    extraction+cost for an EO batch already paid for at half price)."""
    repo.update_work_item(work_item["id"], {"status": "in_progress", "worker": "batch-ingest",
                                            "lease_expires": time.time() + settings.lease_seconds})


def ingest_line(line: dict, work_item: dict, deps, run_id) -> str:
    """Processes one decoded batch-output JSONL line against its (already
    resolved) work item. Bad/truncated lines bounce back to pending so the
    online ladder (image rung included) reprocesses the EO from scratch.

    Valid lines commit the extraction (write_extraction + add_run_cost, same
    envelope shape as agents/extractor.py's extract()) at HALF price BEFORE
    running the online audit/match tail. That ordering matters: once both
    those calls have run, `_already_done` will correctly skip this EO on any
    future --ingest re-run, so nothing after this point -- including a crash
    or exception in audit/match -- can ever cause a second write_extraction/
    add_run_cost for the same batch tokens. The audit/match tail mirrors
    runner.process_work_item's own outcome semantics exactly: BudgetExceeded
    resets to pending and propagates (stops --ingest cleanly); any other
    exception (a transient online LLM call failure -- network error, timeout,
    5xx) increments attempts and goes to retry/failed, so one bad online call
    can never crash the whole --ingest run.

    Returns 'complete' | 'needs_review' | 'bounced' | 'retry' | 'failed'.
    """
    eo = work_item["eo_number"]
    _lease(deps.repo, work_item)

    response = line.get("response")
    if response is None or _finish_reason(response) != "STOP":
        deps.repo.update_work_item(work_item["id"], {"status": "pending"})
        return "bounced"
    try:
        ex, tok_in, tok_out = _parse_response(response)
    except (KeyError, IndexError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        deps.repo.update_work_item(work_item["id"], {"status": "pending"})
        return "bounced"

    # Batch pricing: HALF the configured rate -- see cost_usd() in
    # core/costs.py for the online (full-price) equivalent.
    usd = (tok_in * settings.price_in_per_mtok / 2 / 1e6
           + tok_out * settings.price_out_per_mtok / 2 / 1e6)
    # Record the cost into the run BEFORE the budget check: if this call trips
    # the budget, it must still be metered -- the exception below stops the
    # NEXT call, not this one (see core/costs.py BudgetGuard.add).
    deps.repo.add_run_cost(run_id, usd, tok_in, tok_out)
    try:
        deps.budget.add(usd)
    except BudgetExceeded:
        deps.repo.update_work_item(work_item["id"], {"status": "pending"})
        raise

    version = deps.repo.next_extraction_version(eo)
    deps.repo.write_extraction(eo, version, {
        "eo_number": eo, "payload": ex.model_dump(), "prompt_version": PROMPT_VERSION,
        "ladder_step": 1, "finish_reason": "STOP", "tok_in": tok_in, "tok_out": tok_out,
        "cost_usd": usd, "created_at": time.time()})
    deps.repo.add_event(run_id, {"agent": "batchfill", "eo": eo, "action": "extracted",
                                 "ladder_step": 1, "confidence": ex.confidence})

    # Extraction + cost are committed as of here -- see _already_done. Anything
    # below is the "risky", online-network-call tail.
    try:
        outcome, audited_ex = audit(deps.llm, deps.repo, deps.budget, eo, ex,
                        set(deps.index.by_make.keys()), run_id)
        if outcome == "escalated":
            deps.repo.update_work_item(work_item["id"], {"status": "done", "stage": "review"})
            return "needs_review"
        run_matching(deps.llm, deps.repo, deps.budget, eo, audited_ex, deps.index, run_id)
        deps.repo.update_work_item(work_item["id"], {"status": "done", "stage": "complete"})
        return "complete"
    except BudgetExceeded:
        deps.repo.update_work_item(work_item["id"], {"status": "pending"})
        raise
    except Exception as e:
        attempts = work_item.get("attempts", 0) + 1
        if attempts >= settings.max_attempts:
            deps.repo.update_work_item(work_item["id"], {"status": "failed", "attempts": attempts,
                                                          "last_error": str(e)[:500]})
            deps.repo.upsert_eo(eo, {"state": "failed"})
            return "failed"
        deps.repo.update_work_item(work_item["id"], {"status": "pending", "attempts": attempts,
                                                      "last_error": str(e)[:500]})
        return "retry"


def _resolve_work_item(repo, eo):
    """Find the work item doc for this EO. Field query beyond Repo's public
    API (same direct-Firestore idiom as _fetch_candidate_work_items) since
    Repo only supports single-item claiming, not lookup-by-eo. More than one
    work_items doc can exist for the same eo_number across runs (e.g.
    backfill.py's bootstrap() creates a fresh item for any EO not yet
    "complete", even if an older item for it already exists) -- order by
    created_at descending and take the newest, so a stale duplicate is never
    the doc that gets updated (which would leave the current doc dangling,
    stuck pending forever while the stale one gets marked done)."""
    from google.cloud import firestore
    docs = list(repo.db.collection("work_items").where("eo_number", "==", eo)
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(1).stream())
    if not docs:
        return None
    d = docs[0]
    return d.to_dict() | {"id": d.id}


def ingest(deps, job_out_prefix: str, run_id=None) -> dict:
    """Production entrypoint for --ingest. Streams every JSONL line under the
    batch output prefix; resumable (skips EOs already extracted + done);
    stops cleanly on BudgetExceeded (work items already bounced to pending
    stay claimable by the normal online run)."""
    run_id = run_id or deps.repo.create_run("batch-backfill")
    counts = {"complete": 0, "needs_review": 0, "bounced": 0, "skipped": 0, "retry": 0, "failed": 0}
    prefix = job_out_prefix.split(f"gs://{deps.gcs.name}/", 1)[-1]
    status = "ok"
    try:
        for blob in deps.gcs.bucket.list_blobs(prefix=prefix):
            if not blob.name.endswith(".jsonl"):
                continue
            for raw in blob.download_as_text().splitlines():
                if not raw.strip():
                    continue
                line = json.loads(raw)
                eo = _eo_from_request(line["request"])
                work_item = _resolve_work_item(deps.repo, eo)
                if work_item is None:
                    continue
                if _already_done(deps.repo, eo):
                    counts["skipped"] += 1
                    continue
                outcome = ingest_line(line, work_item, deps, run_id)
                counts[outcome] += 1
    except BudgetExceeded:
        status = "budget_exceeded"
    counts["status"] = status
    deps.repo.finish_run(run_id, counts)
    return counts


def _build_deps() -> Deps:
    from core.db import Repo
    from core.llm import LLM
    from core.gcs import GCSStore
    from matching.engine import VehicleIndex
    repo = Repo()
    budget = BudgetGuard(float(os.environ.get("RUN_BUDGET_USD", "50")))
    return Deps(repo=repo, llm=LLM(), gcs=GCSStore(settings.bucket), carb=None,
                index=VehicleIndex(repo.vehicles_all()), budget=budget)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true", help="build + upload the batch JSONL")
    group.add_argument("--submit", metavar="JSONL_URI", help="create the Vertex batch job (live spend)")
    group.add_argument("--ingest", metavar="JOB_OUT_PREFIX", help="consume batch job output")
    args = parser.parse_args(argv)

    if args.prepare:
        deps = _build_deps()
        n, uri = prepare(deps.repo, deps.gcs)
        print(f"{n} requests -> {uri}")
    elif args.submit:
        from google import genai
        client = genai.Client(vertexai=True, project=settings.project_id, location=settings.genai_location)
        print(submit(client, args.submit))
    elif args.ingest:
        deps = _build_deps()
        print(ingest(deps, args.ingest))


if __name__ == "__main__":
    main()
