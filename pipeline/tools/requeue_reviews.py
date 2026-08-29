"""One-shot requeue: send open review_queue escalations back through extraction
under prompt v2 (table completeness fix -- see prompts/extractor.py).

For every review_queue doc with status == "open" and reason in the given set:
1. Mark the review {"status": "superseded_by_reprocess", "resolved_at": time.time()}
2. Upsert the eo {"state": "discovered"}
3. Create a fresh work item for the EO under one shared "requeue-v2" run
"""
import time


def requeue_open_reviews(repo, reasons) -> int:
    run_id = repo.create_run("requeue-v2")
    count = 0
    for review in repo.open_reviews():
        if review.get("reason") not in reasons:
            continue
        eo = review["eo_number"]
        repo.update_review(review["id"], {"status": "superseded_by_reprocess", "resolved_at": time.time()})
        repo.upsert_eo(eo, {"state": "discovered"})
        repo.create_work_item(eo, run_id)
        print(f"Requeued {eo} (review {review['id']}, reason={review['reason']})")
        count += 1
    repo.finish_run(run_id, {"status": "ok", "requeued": count})
    print(f"\nTotal requeued: {count}")
    return count


if __name__ == "__main__":
    from core.db import Repo

    repo = Repo()
    requeue_open_reviews(repo, ("validation_failure", "legacy_divergence"))
