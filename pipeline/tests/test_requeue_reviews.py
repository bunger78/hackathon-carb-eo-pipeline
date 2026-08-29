from tests.fakes import FakeRepo
from tools.requeue_reviews import requeue_open_reviews

REASONS = ("validation_failure", "legacy_divergence")


def test_requeues_open_matching_reviews():
    repo = FakeRepo()
    repo.add_review({"eo_number": "D-1-1", "reason": "validation_failure"})
    repo.add_review({"eo_number": "D-2-2", "reason": "legacy_divergence"})

    n = requeue_open_reviews(repo, REASONS)

    assert n == 2
    assert {r["eo_number"]: r["status"] for r in repo.reviews} == {
        "D-1-1": "superseded_by_reprocess", "D-2-2": "superseded_by_reprocess"}
    assert all("resolved_at" in r for r in repo.reviews)
    assert repo.eos["D-1-1"]["state"] == "discovered"
    assert repo.eos["D-2-2"]["state"] == "discovered"
    work_items = list(repo.work_items.values())
    assert {w["eo_number"] for w in work_items} == {"D-1-1", "D-2-2"}
    assert all(w["status"] == "pending" for w in work_items)
    run_ids = {w["run_id"] for w in work_items}
    assert len(run_ids) == 1
    run_id = next(iter(run_ids))
    assert repo.runs[run_id]["trigger"] == "requeue-v2"
    assert repo.runs[run_id]["status"] == "ok"
    assert repo.runs[run_id]["requeued"] == 2


def test_skips_non_matching_reason_and_closed_reviews():
    repo = FakeRepo()
    repo.add_review({"eo_number": "D-3-3", "reason": "ambiguous_match"})
    r4 = repo.add_review({"eo_number": "D-4-4", "reason": "validation_failure"})
    repo.update_review(r4, {"status": "resolved"})

    n = requeue_open_reviews(repo, REASONS)

    assert n == 0
    assert repo.work_items == {}
    assert "D-3-3" not in repo.eos
    assert "D-4-4" not in repo.eos
    run = next(iter(repo.runs.values()))
    assert run["status"] == "ok"
    assert run["requeued"] == 0
