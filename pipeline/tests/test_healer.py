import pytest

from agents.healer import is_transient, requeue_transient_failures
from tests.fakes import FakeRepo


# --- classifier: anchored on the leading status code ---

@pytest.mark.parametrize("last_error", [
    "429 Too Many Requests",
    "500 Internal Server Error",
    "503 Service Unavailable",
    "RESOURCE_EXHAUSTED: quota exceeded",
    "DEADLINE_EXCEEDED: request timed out",
    "UNAVAILABLE: backend temporarily down",
])
def test_is_transient_true_for_known_transient_markers(last_error):
    assert is_transient(last_error) is True


@pytest.mark.parametrize("last_error", [
    "",
    None,
    "400 INVALID_ARGUMENT: schema mismatch",
    "input token count (2000000) exceeds the maximum number of tokens allowed (1048576)",
    "exceeds max of 1,048,576 tokens",
    "boom",  # unrecognized error string -- not transient, stays failed
])
def test_is_transient_false_for_permanent_or_unknown_markers(last_error):
    assert is_transient(last_error) is False


@pytest.mark.parametrize("last_error", [
    "invoice #1503 rejected",       # contains "503" but not as a leading code
    "processing error code 1500",   # contains "500" but not as a leading code
    "user id 4294429 not found",    # contains "429" but not as a leading code
])
def test_is_transient_false_for_unanchored_substring_matches(last_error):
    """Regression guard: the code markers must be LEADING prefixes, not bare
    substrings anywhere in the message."""
    assert is_transient(last_error) is False


def test_permanent_marker_wins_even_with_transient_marker_present():
    # Starts with the permanent "400 " prefix and also contains the token-cap
    # number, despite mentioning RESOURCE_EXHAUSTED deeper in the payload --
    # permanent wins on conflict.
    msg = ("400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
           "'exceeds maximum tokens 1048576 (unrelated RESOURCE_EXHAUSTED mention)'}}")
    assert is_transient(msg) is False


def test_is_transient_true_for_verbatim_production_429_string():
    msg = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
           "'Resource exhausted. Please try again later....'}}")
    assert is_transient(msg) is True


def test_is_transient_false_for_verbatim_production_400_string():
    msg = ("400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
           "'The input token count exceeds the maximum number of tokens allowed 1048576.'...}}")
    assert is_transient(msg) is False


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
    assert item["heal_count"] == 1
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
    item_id = _make_failed(repo, "D-4-4", "400 INVALID_ARGUMENT: schema mismatch")

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


# --- per-item heal_count dampener ---

def test_heal_count_increments_on_each_requeue():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-8-8", "429 rate limited")

    requeue_transient_failures(repo, run_id)
    assert repo.work_items[item_id]["heal_count"] == 1

    # Simulate it failing again the same transient way on a later day.
    repo.update_work_item(item_id, {"status": "failed", "last_error": "429 rate limited"})
    requeue_transient_failures(repo, run_id)
    assert repo.work_items[item_id]["heal_count"] == 2


def test_heal_limit_skips_item_emits_one_event_then_stays_silent():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    item_id = _make_failed(repo, "D-9-9", "429 rate limited")

    # Three successful requeue cycles bring heal_count to 3.
    for _ in range(3):
        count = requeue_transient_failures(repo, run_id)
        assert count == 1
        repo.update_work_item(item_id, {"status": "failed", "last_error": "429 rate limited"})
    assert repo.work_items[item_id]["heal_count"] == 3

    # Fourth encounter: heal_count == 3 -> skipped, stays failed, one
    # heal_limit_reached event, heal_count bumped to 4.
    count = requeue_transient_failures(repo, run_id)
    assert count == 0
    item = repo.work_items[item_id]
    assert item["status"] == "failed"
    assert item["heal_count"] == 4
    limit_events = [e for e in repo.events[run_id] if e["action"] == "heal_limit_reached"]
    assert limit_events == [{"agent": "healer", "action": "heal_limit_reached", "eo": "D-9-9"}]

    # Fifth encounter: still skipped, but silent -- no second event.
    count = requeue_transient_failures(repo, run_id)
    assert count == 0
    assert repo.work_items[item_id]["heal_count"] == 4
    limit_events = [e for e in repo.events[run_id] if e["action"] == "heal_limit_reached"]
    assert len(limit_events) == 1


def test_heal_limit_skip_does_not_count_toward_cap():
    repo = FakeRepo()
    run_id = repo.create_run("test")
    limited_id = _make_failed(repo, "D-limited", "429 rate limited")
    repo.update_work_item(limited_id, {"heal_count": 3})
    fresh_id = _make_failed(repo, "D-fresh", "429 rate limited")

    count = requeue_transient_failures(repo, run_id, cap=1)

    assert count == 1
    assert repo.work_items[limited_id]["status"] == "failed"
    assert repo.work_items[fresh_id]["status"] == "pending"
