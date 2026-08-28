"""SELF-HEAL stage (spec: daily runs requeue transient failures on their own).

A work item that exhausted its `settings.max_attempts` on a transient error
(Vertex 429 quota, 5xx, DEADLINE) otherwise sits "failed" until a human
retries it. `is_transient` is the single source of truth for this
classification -- batchfill.py's --prepare 429-reset imports and uses it too,
so the two "mirrored" ladders can't drift out of sync.

Classification is anchored on the LEADING status code, matching the shape of
the real last_error strings this field actually holds (the raw str() of a
google-genai APIError, e.g. "429 RESOURCE_EXHAUSTED. {...}" or
"400 INVALID_ARGUMENT. {...}" -- see runner.py:34-42 / core/llm.py).
"""

# Leading-code prefixes: only meaningful as the first token of the message.
_TRANSIENT_PREFIXES = ("429 ", "500 ", "503 ")
_PERMANENT_PREFIXES = ("400 ",)

# Substrings that are unambiguous regardless of position (gRPC status names /
# the token-cap number, in either spelling).
_TRANSIENT_CONTAINS = ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE")
_PERMANENT_CONTAINS = ("1048576", "1,048,576")

# Per-item auto-heal ceiling: once a work item has been auto-requeued this
# many times and failed again, the healer stops touching it -- it stays
# "failed" and becomes a human's job via the dashboard's failure panel.
_HEAL_LIMIT = 3


def is_transient(last_error: str) -> bool:
    """Permanent wins on conflict; empty/unknown last_error is NOT transient."""
    last_error = last_error or ""
    if last_error.startswith(_PERMANENT_PREFIXES) or any(m in last_error for m in _PERMANENT_CONTAINS):
        return False
    return last_error.startswith(_TRANSIENT_PREFIXES) or any(m in last_error for m in _TRANSIENT_CONTAINS)


def requeue_transient_failures(repo, run_id, cap=25) -> int:
    """Requeues up to `cap` "failed" work items whose last_error looks
    transient back to pending (attempts reset to 0, last_error cleared), so
    the daily run retries them without a human intervening. Permanent
    failures (token-cap, 400/INVALID_ARGUMENT) are left failed, untouched.

    Each requeue increments the item's heal_count (never cleared elsewhere --
    attempts is the only field reset). Once heal_count reaches _HEAL_LIMIT,
    the item is skipped instead of requeued again -- it stays "failed" for a
    human to handle via the dashboard's failure panel -- and a one-time
    "heal_limit_reached" event is logged the first run that hits the limit,
    so a permanently-flaky EO doesn't get auto-requeued forever.

    Returns the number of items requeued (items skipped at the heal limit
    don't count toward this, or toward `cap`).
    """
    requeued = 0
    for item in repo.failed_work_items():
        if requeued >= cap:
            break
        if not is_transient(item.get("last_error")):
            continue
        heal_count = item.get("heal_count", 0)
        if heal_count >= _HEAL_LIMIT:
            if heal_count == _HEAL_LIMIT:
                repo.add_event(run_id, {"agent": "healer", "action": "heal_limit_reached",
                                        "eo": item["eo_number"]})
                repo.update_work_item(item["id"], {"heal_count": heal_count + 1})
            continue
        repo.update_work_item(item["id"], {"status": "pending", "attempts": 0, "last_error": "",
                                            "heal_count": heal_count + 1})
        repo.upsert_eo(item["eo_number"], {"state": "discovered"})
        repo.add_event(run_id, {"agent": "healer", "eo": item["eo_number"], "action": "requeued_transient"})
        requeued += 1
    return requeued
