"""Backfill. Bootstrap locally: py -3 backfill.py --bootstrap
   Worker mode runs as Cloud Run Job (env CLOUD_RUN_TASK_INDEX/TASK_COUNT)."""
import os, sys, time
from config import settings
from core.costs import BudgetGuard, BudgetExceeded
from runner import Deps, process_work_item
from main import build_deps

def bootstrap():
    deps = build_deps()
    run_id = deps.repo.create_run("backfill")
    bucket = deps.gcs.bucket
    n = 0
    for blob in bucket.list_blobs(prefix="pdfs/"):
        eo = blob.name.split("/")[-1].removesuffix(".pdf").upper()
        cur = deps.repo.get_eo(eo)
        if cur and cur.get("state") == "complete":
            continue
        deps.repo.upsert_eo(eo, {"state": "discovered", "gcs_uri": f"gs://{settings.bucket}/{blob.name}"})
        deps.repo.create_work_item(eo, run_id)
        n += 1
    print(f"bootstrap: {n} work items under run {run_id}")

def worker():
    # No shard filter: claim_next's transactional lease already gives exclusive
    # ownership, so any worker may process any item. A crc32 pre-filter with
    # release-back-to-pending live-locks the queue head (all workers bounce the
    # oldest foreign item instead of advancing past it).
    idx = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    deps = build_deps()
    deps.budget = BudgetGuard(float(os.environ.get("RUN_BUDGET_USD", "200")))
    run_id = deps.repo.create_run(f"backfill-worker-{idx}")
    done = 0
    while True:
        item = deps.repo.claim_next(f"bf-{idx}", now=time.time())
        if item is None:
            break
        try:
            process_work_item(item, deps, run_id)
            done += 1
        except BudgetExceeded:
            print("budget exceeded — stopping shard")
            break
    deps.repo.finish_run(run_id, {"processed": done, "status": "ok"})
    print(f"worker {idx}: {done} processed, ${deps.budget.spent:.2f}")

if __name__ == "__main__":
    bootstrap() if "--bootstrap" in sys.argv else worker()
