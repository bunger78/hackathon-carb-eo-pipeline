from core.db import is_claimable
from tests.fakes import FakeRepo

def test_pending_is_claimable():
    assert is_claimable({"status": "pending", "lease_expires": 0}, now=100.0)

def test_active_lease_blocks():
    assert not is_claimable({"status": "in_progress", "lease_expires": 200.0}, now=100.0)

def test_expired_lease_reclaimable():
    assert is_claimable({"status": "in_progress", "lease_expires": 50.0}, now=100.0)

def test_done_failed_never_claimable():
    for s in ("done", "failed"):
        assert not is_claimable({"status": s, "lease_expires": 0}, now=100.0)

def test_fake_repo_claim_cycle():
    r = FakeRepo()
    run = r.create_run("test")
    r.create_work_item("D-1-1", run)
    item = r.claim_next("w1", now=100.0)
    assert item["eo_number"] == "D-1-1" and item["status"] == "in_progress"
    assert r.claim_next("w2", now=100.0) is None  # leased
    r.update_work_item(item["id"], {"status": "done"})
    assert r.claim_next("w2", now=100.0) is None  # nothing left
