import time
from schemas.extraction import Extraction
from agents.matchmaker import run_matching

def resolve_review(deps, review_id: str, action: str, corrections: dict | None) -> dict:
    review = deps.repo.get_review(review_id)
    if review is None:
        raise KeyError(f"review {review_id} not found")
    eo = review["eo_number"]
    if action == "reject":
        deps.repo.upsert_eo(eo, {"state": "failed"})
        deps.repo.update_review(review_id, {"status": "rejected", "resolved_at": time.time()})
        return {"review_id": review_id, "action": action, "matches": 0}
    version = deps.repo.next_extraction_version(eo)
    latest = deps.repo.get_extraction(eo, version - 1) if version > 1 else None
    if latest is None:
        raise KeyError(f"no extraction for {eo}")
    doc = {**latest, **(corrections or {})}
    deps.repo.write_extraction(eo, version, doc)
    run_id = deps.repo.create_run("review-resolve")
    ex = Extraction.model_validate(doc)
    result = run_matching(deps.llm, deps.repo, deps.budget, eo, ex, deps.index, run_id)
    deps.repo.upsert_eo(eo, {"state": "complete"})
    deps.repo.update_review(review_id, {"status": "approved", "resolved_at": time.time()})
    deps.repo.finish_run(run_id, {"status": "ok", "reviewed": eo})
    return {"review_id": review_id, "action": action, "matches": result["matches"]}
