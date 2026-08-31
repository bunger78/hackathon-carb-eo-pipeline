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

A third class, "corrupt_source" (a PDFium load failure -- the source PDF
bytes in GCS are unparseable, not a Gemini API error), gets its own repair
path: requeue_corrupt_sources re-downloads the PDF from CARB and overwrites
the corrupt blob before requeuing, rather than just resetting attempts.
"""

# Leading-code prefixes: only meaningful as the first token of the message.
_TRANSIENT_PREFIXES = ("429 ", "500 ", "503 ")
_PERMANENT_PREFIXES = ("400 ",)

# Substrings that are unambiguous regardless of position (gRPC status names /
# the token-cap number, in either spelling).
_TRANSIENT_CONTAINS = ("RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "UNAVAILABLE")
_PERMANENT_CONTAINS = ("1048576", "1,048,576")

# Corrupt/unparseable source PDF markers (from pypdfium2's own error text --
# see core/gcs.render_pdf_to_images) -- unambiguous regardless of position,
# so matched case-insensitively anywhere in the message.
_CORRUPT_SOURCE_CONTAINS = ("data format error", "pdfium", "failed to load document")

# Per-item auto-heal ceiling: once a work item has been auto-requeued this
# many times and failed again, the healer stops touching it -- it stays
# "failed" and becomes a human's job via the dashboard's failure panel.
_HEAL_LIMIT = 3


def classify_failure(last_error: str) -> str:
    """Three-way classification of a work item's last_error: "transient" |
    "corrupt_source" | "permanent".

    Corrupt-source markers (a PDFium load failure -- the PDF bytes in GCS
    are unparseable, not a Gemini API error) are checked first since they're
    unambiguous. Everything else falls through to the original transient/
    permanent ladder (permanent wins on conflict); anything matching neither
    is permanent.
    """
    last_error = last_error or ""
    if any(m in last_error.lower() for m in _CORRUPT_SOURCE_CONTAINS):
        return "corrupt_source"
    if _is_transient(last_error):
        return "transient"
    return "permanent"


def _is_transient(last_error: str) -> bool:
    if last_error.startswith(_PERMANENT_PREFIXES) or any(m in last_error for m in _PERMANENT_CONTAINS):
        return False
    return last_error.startswith(_TRANSIENT_PREFIXES) or any(m in last_error for m in _TRANSIENT_CONTAINS)


def is_transient(last_error: str) -> bool:
    """Permanent wins on conflict; empty/unknown last_error is NOT transient.

    Kept as the single source of truth batchfill.py imports and uses --
    equivalent to classify_failure(last_error) == "transient".
    """
    return classify_failure(last_error) == "transient"


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


def requeue_corrupt_sources(repo, carb, gcs, run_id, cap=10) -> int:
    """Requeues up to `cap` "failed" work items whose last_error looks like a
    corrupt/unparseable source PDF (a PDFium load failure) by re-downloading
    the PDF from CARB and overwriting the corrupt GCS blob, then requeuing
    the work item so the daily run retries extraction against the fresh copy.

    Same per-item heal_count dampener as requeue_transient_failures: once an
    item has been auto-requeued _HEAL_LIMIT times and failed again, it's
    skipped instead (parked for a human via the dashboard), with a one-time
    "heal_limit_reached" event logged the first run that hits the limit.

    A missing eo.pdf_url leaves the item failed untouched (nothing to
    refetch from). A download failure also leaves the item failed untouched,
    but logs a "refetch_failed" event (the corrupt GCS blob is never
    touched -- upload only happens after a successful download).

    Returns the number of items refetched and requeued (items skipped at the
    heal limit or for a missing pdf_url don't count toward this, or `cap`).
    """
    requeued = 0
    for item in repo.failed_work_items():
        if requeued >= cap:
            break
        if classify_failure(item.get("last_error")) != "corrupt_source":
            continue
        heal_count = item.get("heal_count", 0)
        if heal_count >= _HEAL_LIMIT:
            if heal_count == _HEAL_LIMIT:
                repo.add_event(run_id, {"agent": "healer", "action": "heal_limit_reached",
                                        "eo": item["eo_number"]})
                repo.update_work_item(item["id"], {"heal_count": heal_count + 1})
            continue
        eo = item["eo_number"]
        pdf_url = (repo.get_eo(eo) or {}).get("pdf_url")
        if not pdf_url:
            continue
        try:
            pdf = carb.download_pdf(pdf_url)
        except Exception as exc:
            repo.add_event(run_id, {"agent": "healer", "eo": eo, "action": "refetch_failed",
                                    "error": str(exc)[:300]})
            continue
        gcs.upload_pdf(eo, pdf)
        repo.update_work_item(item["id"], {"status": "pending", "attempts": 0, "last_error": "",
                                            "heal_count": heal_count + 1})
        repo.upsert_eo(eo, {"state": "discovered"})
        repo.add_event(run_id, {"agent": "healer", "eo": eo, "action": "refetched_source"})
        requeued += 1
    return requeued
