import time
from schemas.extraction import Extraction
from agents.matchmaker import run_matching
from core.costs import BudgetExceeded

class ReviewNotOpen(Exception):
    """Raised when a review has already been resolved (idempotency guard)."""
    def __init__(self, review_id: str, status: str):
        self.review_id = review_id
        self.status = status
        super().__init__(f"review {review_id} already resolved (status={status})")

class EoNotFailed(Exception):
    """Raised when the EO's newest work item isn't in status "failed" (idempotency guard)."""
    def __init__(self, eo_number: str, status: str):
        self.eo_number = eo_number
        self.status = status
        super().__init__(f"eo {eo_number} not failed (status={status})")

def retry_eo(deps, eo_number: str) -> dict:
    item = deps.repo.latest_work_item(eo_number)
    if item is None:
        raise KeyError(f"no work item for {eo_number}")
    if item.get("status") != "failed":
        raise EoNotFailed(eo_number, item.get("status"))
    deps.repo.update_work_item(item["id"], {"status": "pending", "attempts": 0, "last_error": ""})
    deps.repo.upsert_eo(eo_number, {"state": "discovered"})
    return {"eo_number": eo_number, "requeued": True}

def resolve_review(deps, review_id: str, action: str, corrections: dict | None) -> dict:
    review = deps.repo.get_review(review_id)
    if review is None:
        raise KeyError(f"review {review_id} not found")
    if review.get("status") != "open":
        raise ReviewNotOpen(review_id, review.get("status"))
    eo = review["eo_number"]
    if action == "reject":
        deps.repo.upsert_eo(eo, {"state": "failed"})
        deps.repo.update_review(review_id, {"status": "rejected", "resolved_at": time.time()})
        return {"review_id": review_id, "action": action, "matches": 0}
    version = deps.repo.next_extraction_version(eo)
    # next_extraction_version is a non-atomic count-then-write shared with extractor.py's writer for
    # the same EO; accepted as-is (single human operator, pipeline writers only append post-completion).
    envelope = deps.repo.get_extraction(eo, version - 1) if version > 1 else None
    if envelope is None:
        raise KeyError(f"no extraction for {eo}")
    payload = {**envelope.get("payload", {}), **(corrections or {})}
    ex = Extraction.model_validate(payload)  # raises ValidationError before anything is persisted
    new_envelope = {**envelope, "payload": ex.model_dump(), "created_at": time.time()}
    deps.repo.write_extraction(eo, version, new_envelope)
    run_id = deps.repo.create_run("review-resolve")
    try:
        result = run_matching(deps.llm, deps.repo, deps.budget, eo, ex, deps.index, run_id)
    except BudgetExceeded:
        deps.repo.finish_run(run_id, {"status": "budget_exceeded"})
        raise
    deps.repo.upsert_eo(eo, {"state": "complete"})
    deps.repo.update_review(review_id, {"status": "approved", "resolved_at": time.time()})
    deps.repo.finish_run(run_id, {"status": "ok", "reviewed": eo})
    return {"review_id": review_id, "action": action, "matches": result["matches"]}
