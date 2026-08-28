"""SELF-HEAL stage (spec: daily runs requeue transient failures on their own).

A work item that exhausted its `settings.max_attempts` on a transient error
(Vertex 429 quota, 5xx, DEADLINE) otherwise sits "failed" until a human
retries it. This mirrors batchfill.py's 429-reset classification
(select_batch_items / _is_token_cap_error) but generalizes the transient set
this stage cares about, and applies it automatically inside the daily graph
instead of a human-gated --prepare invocation.
"""

# Any of these substrings in last_error marks the failure as transient (worth
# an automatic retry).
_TRANSIENT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "500", "503", "DEADLINE")

# Any of these substrings marks the failure as permanent (never auto-retry),
# even if a transient marker is also present -- e.g. a 429-mentioning message
# that also names INVALID_ARGUMENT is permanent, not transient.
_PERMANENT_MARKERS = ("1048576", "1,048,576", "INVALID_ARGUMENT")


def _is_transient(last_error: str) -> bool:
    last_error = last_error or ""
    if not last_error:
        return False
    if any(m in last_error for m in _PERMANENT_MARKERS):
        return False
    return any(m in last_error for m in _TRANSIENT_MARKERS)


def requeue_transient_failures(repo, run_id, cap=25) -> int:
    """Requeues up to `cap` "failed" work items whose last_error looks
    transient back to pending (attempts reset to 0, last_error cleared), so
    the daily run retries them without a human intervening. Permanent
    failures (token-cap, INVALID_ARGUMENT) are left failed, untouched.

    Returns the number of items requeued.
    """
    requeued = 0
    for item in repo.failed_work_items():
        if requeued >= cap:
            break
        if not _is_transient(item.get("last_error")):
            continue
        repo.update_work_item(item["id"], {"status": "pending", "attempts": 0, "last_error": ""})
        repo.upsert_eo(item["eo_number"], {"state": "discovered"})
        repo.add_event(run_id, {"agent": "healer", "eo": item["eo_number"], "action": "requeued_transient"})
        requeued += 1
    return requeued
