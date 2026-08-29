import time
from pydantic import ValidationError
from schemas.extraction import Extraction
from prompts.extractor import EXTRACTOR_PROMPT, PROMPT_VERSION
from core.costs import cost_usd
from core.gcs import render_pdf_to_images

class ExtractionFailed(Exception):
    pass

def _attempt(fn) -> tuple[Extraction, "LLMResult"] | None:
    try:
        res = fn()
        ex = Extraction.model_validate(res.data)
        # Treat truncation as failure
        if res.finish_reason != "STOP":
            return None
        return ex, res
    except (ValidationError, Exception):
        return None

def extract(llm, gcs, repo, budget, eo: str, run_id) -> Extraction:
    uri = repo.get_eo(eo)["gcs_uri"]
    step = 1
    repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "reading", "rung": step})
    got = _attempt(lambda: llm.extract_pdf(uri, EXTRACTOR_PROMPT, Extraction))
    if got is None:
        step = 2
        repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "reading", "rung": step})
        images = gcs.upload_page_images(eo, render_pdf_to_images(gcs.download(uri)))
        got = _attempt(lambda: llm.extract_images(images, EXTRACTOR_PROMPT, Extraction))
    if got is None:
        repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "failed_both_rungs"})
        raise ExtractionFailed(eo)
    ex, res = got
    usd = cost_usd(res.tok_in, res.tok_out)
    budget.add(usd)
    version = repo.next_extraction_version(eo)
    repo.write_extraction(eo, version, {
        "eo_number": eo, "payload": ex.model_dump(), "prompt_version": PROMPT_VERSION,
        "ladder_step": step, "finish_reason": res.finish_reason, "tok_in": res.tok_in, "tok_out": res.tok_out,
        "cost_usd": usd, "created_at": time.time()})
    repo.add_run_cost(run_id, usd, res.tok_in, res.tok_out)
    repo.add_event(run_id, {"agent": "extractor", "eo": eo, "action": "extracted",
                            "ladder_step": step, "confidence": ex.confidence})
    return ex
