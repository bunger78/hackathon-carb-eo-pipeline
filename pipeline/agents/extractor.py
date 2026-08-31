import time
from pydantic import ValidationError
from schemas.extraction import Extraction
from prompts.extractor import EXTRACTOR_PROMPT, PROMPT_VERSION
from core.costs import cost_usd
from core.gcs import render_pdf_to_images

class ExtractionFailed(Exception):
    pass

def _attempt(fn):
    """Runs one rung. Returns (ok, ex_or_None, res_or_None, err_or_None):
    - ok=True only when the response validates AND finish_reason == STOP.
    - res is returned whenever the LLM call itself succeeded (even on
      truncation/validation failure), so its usage/cost can always be
      metered -- a failed rung that still spent tokens must not be invisible
      to the budget or the run record.
    - err carries the underlying failure text (the raw API error string on an
      exception, or a truncation note) so callers -- and ultimately the
      healer's transient-error classifier -- see the real reason, not just
      the EO number.
    """
    try:
        res = fn()
    except (ValidationError, Exception) as e:
        return False, None, None, str(e)
    try:
        ex = Extraction.model_validate(res.data)
    except ValidationError as e:
        return False, None, res, str(e)
    if res.finish_reason != "STOP":
        return False, None, res, f"truncated (finish_reason={res.finish_reason})"
    return True, ex, res, None

def _meter(repo, budget, run_id, res) -> float:
    usd = cost_usd(res.tok_in, res.tok_out)
    repo.add_run_cost(run_id, usd, res.tok_in, res.tok_out)
    budget.add(usd)
    return usd

def extract(llm, gcs, repo, budget, eo: str, run_id) -> Extraction:
    uri = repo.get_eo(eo)["gcs_uri"]
    step = 1
    repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "reading", "rung": step})
    ok, ex, res, err = _attempt(lambda: llm.extract_pdf(uri, EXTRACTOR_PROMPT, Extraction))
    if not ok and res is not None:
        _meter(repo, budget, run_id, res)
    if not ok:
        step = 2
        repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "reading", "rung": step})
        images = gcs.upload_page_images(eo, render_pdf_to_images(gcs.download(uri)))
        ok, ex, res, err = _attempt(lambda: llm.extract_images(images, EXTRACTOR_PROMPT, Extraction))
        if not ok and res is not None:
            _meter(repo, budget, run_id, res)
    if not ok:
        repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "failed_both_rungs"})
        raise ExtractionFailed(err or eo)
    usd = _meter(repo, budget, run_id, res)
    version = repo.next_extraction_version(eo)
    repo.write_extraction(eo, version, {
        "eo_number": eo, "payload": ex.model_dump(), "prompt_version": PROMPT_VERSION,
        "ladder_step": step, "finish_reason": res.finish_reason, "tok_in": res.tok_in, "tok_out": res.tok_out,
        "cost_usd": usd, "created_at": time.time()})
    repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "extracted",
                            "ladder_step": step, "confidence": ex.confidence})
    return ex
