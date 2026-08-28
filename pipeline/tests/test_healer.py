import pytest

from agents.healer import _is_transient, requeue_transient_failures
from tests.fakes import FakeRepo


# --- classifier ---

@pytest.mark.parametrize("last_error", [
    "429 Too Many Requests",
    "RESOURCE_EXHAUSTED: quota exceeded",
    "500 Internal Server Error",
    "503 Service Unavailable",
    "DEADLINE_EXCEEDED: request timed out",
])
def test_is_transient_true_for_known_transient_markers(last_error):
    assert _is_transient(last_error) is True


@pytest.mark.parametrize("last_error", [
    "",
    None,
    "input token count (2000000) exceeds the maximum number of tokens allowed (1048576)",
    "exceeds max of 1,048,576 tokens",
    "INVALID_ARGUMENT: malformed request",
    "boom",  # unrecognized error string -- not transient, stays failed
])
def test_is_transient_false_for_permanent_or_unknown_markers(last_error):
    assert _is_transient(last_error) is False


def test_permanent_marker_wins_even_with_transient_marker_present():
    # A message naming both INVALID_ARGUMENT and 429 digits is permanent.
    assert _is_transient("429: INVALID_ARGUMENT bad request") is False


# --- requeue behavior ---

def _make_failed(repo, eo, last_error, run_id="run0"):
    item_id = repo.create_work_item(eo, run_id)
    repo.update_work_item(item_id, {"status": "failed", "attempts": 3, "last_error": last_error})
    return item_id


def test_429_failure_is_requeued():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-1-1", "429 rate limited")

    count = requeue_transient_failures(repo, run_id)

    assert count == 1
    item = repo.work_items[item_id]
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert item["last_error"] == ""
    assert repo.get_eo("D-1-1")["state"] == "discovered"


def test_500_failure_is_requeued():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-2-2", "500 Internal Server Error")

    count = requeue_transient_failures(repo, run_id)

    assert count == 1
    assert repo.work_items[item_id]["status"] == "pending"


def test_token_cap_failure_not_requeued():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-3-3", "input exceeds the maximum number of tokens allowed (1048576)")

    count = requeue_transient_failures(repo, run_id)

    assert count == 0
    assert repo.work_items[item_id]["status"] == "failed"
    assert repo.work_items[item_id]["attempts"] == 3


def test_invalid_argument_failure_not_requeued():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-4-4", "INVALID_ARGUMENT: schema mismatch")

    count = requeue_transient_failures(repo, run_id)

    assert count == 0
    assert repo.work_items[item_id]["status"] == "failed"


def test_cap_respected_leaves_excess_items_failed():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    ids = [_make_failed(repo, f"D-{i}-{i}", "429 rate limited") for i in range(5)]

    count = requeue_transient_failures(repo, run_id, cap=2)

    assert count == 2
    requeued_ids = [i for i in ids if repo.work_items[i]["status"] == "pending"]
    still_failed_ids = [i for i in ids if repo.work_items[i]["status"] == "failed"]
    assert len(requeued_ids) == 2
    assert len(still_failed_ids) == 3


def test_events_written_for_each_requeued_item():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    _make_failed(repo, "D-5-5", "429 rate limited")
    _make_failed(repo, "D-6-6", "503 Service Unavailable")

    requeue_transient_failures(repo, run_id)

    events = repo.events[run_id]
    assert len(events) == 2
    for event in events:
        assert event["agent"] == "healer"
        assert event["action"] == "requeued_transient"
    assert {e["eo"] for e in events} == {"D-5-5", "D-6-6"}


def test_no_failed_items_returns_zero():
    repo = FakeRepo()
    run_id = repo.create_run("test")

    count = requeue_transient_failures(repo, run_id)

    assert count == 0
    assert repo.events[run_id] == []


def test_non_failed_items_are_ignored():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = repo.create_work_item("D-7-7", run_id)  # status stays "pending"

    count = requeue_transient_failures(repo, run_id)

    assert count == 0
    assert repo.work_items[item_id]["status"] == "pending"
