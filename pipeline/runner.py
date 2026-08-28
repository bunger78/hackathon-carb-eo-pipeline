import time
from dataclasses import dataclass
from config import settings
from core.costs import BudgetExceeded
from agents.scout import discover
from agents.extractor import extract
from agents.auditor import audit
from agents.matchmaker import run_matching

@dataclass
class Deps:
    repo: object
    llm: object
    gcs: object
    carb: object
    index: object
    budget: object

def process_work_item(item, deps, run_id) -> str:
    eo = item["eo_number"]
    try:
        ex = extract(deps.llm, deps.gcs, deps.repo, deps.budget, eo, run_id)
        outcome = audit(deps.llm, deps.repo, deps.budget, eo, ex,
                        set(deps.index.by_make.keys()), run_id)
        if outcome == "escalated":
            deps.repo.update_work_item(item["id"], {"status": "done", "stage": "review"})
            return "needs_review"
        run_matching(deps.llm, deps.repo, deps.budget, eo, ex, deps.index, run_id)
        deps.repo.update_work_item(item["id"], {"status": "done", "stage": "complete"})
        return "complete"
    except BudgetExceeded:
        deps.repo.update_work_item(item["id"], {"status": "pending"})
        raise
    except Exception as e:
        attempts = item.get("attempts", 0) + 1
        if attempts >= settings.max_attempts:
            deps.repo.update_work_item(item["id"], {"status": "failed", "attempts": attempts,
                                                    "last_error": str(e)[:500]})
            deps.repo.upsert_eo(eo, {"state": "failed"})
            return "failed"
        deps.repo.update_work_item(item["id"], {"status": "pending", "attempts": attempts,
                                                "last_error": str(e)[:500]})
        return "retry"

def run_once(deps, trigger: str) -> dict:
    run_id = deps.repo.create_run(trigger)
    start_time = time.time()
    counts = {"new_eos": 0, "completed": 0, "needs_review": 0, "failed": 0}
    status = "ok"
    time_capped = False
    try:
        counts["new_eos"] = discover(deps.repo, deps.carb, deps.gcs, run_id)
        while True:
            if time.time() - start_time > settings.run_time_cap_seconds:
                time_capped = True
                break
            item = deps.repo.claim_next("runner", now=time.time())
            if item is None:
                break
            out = process_work_item(item, deps, run_id)
            if out == "complete":
                counts["completed"] += 1
            elif out == "needs_review":
                counts["needs_review"] += 1
            elif out == "failed":
                counts["failed"] += 1
    except BudgetExceeded:
        status = "budget_exceeded"
    summary = counts | {"status": status, "time_capped": time_capped, "cost_usd": round(deps.budget.spent, 4)}
    deps.repo.finish_run(run_id, summary)
    return summary
