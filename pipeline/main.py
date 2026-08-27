from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ValidationError
from config import settings
from core.costs import BudgetExceeded
from runner import Deps
from workflow_graph import run_workflow

app = FastAPI(title="carb-eo-pipeline")

# Settings is a frozen dataclass, so tests can't monkeypatch settings.admin_token in
# place; mirror it into a module-level name that monkeypatch can swap freely.
ADMIN_TOKEN = settings.admin_token

def build_deps() -> Deps:
    from core.db import Repo
    from core.llm import LLM
    from core.gcs import GCSStore
    from core.costs import BudgetGuard
    from carb.powerbi import CarbClient
    from matching.engine import VehicleIndex
    repo = Repo()
    return Deps(repo=repo, llm=LLM(), gcs=GCSStore(settings.bucket), carb=CarbClient(),
                index=VehicleIndex(repo.vehicles_all()), budget=BudgetGuard(settings.run_budget_usd))

# /health, not /healthz: Google's frontend reserves /healthz on *.run.app and
# answers 404 before the request reaches the container.
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/run")
def run_scheduled():
    return run_workflow(build_deps(), "scheduled")

@app.post("/admin/run-now")
def run_now(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401)
    return run_workflow(build_deps(), "manual")

class ReviewResolution(BaseModel):
    review_id: str
    action: str  # "approve" | "reject"
    corrections: dict | None = None

@app.post("/admin/resolve-review")
def admin_resolve_review(body: ReviewResolution, x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401)
    if body.action not in ("approve", "reject"):
        raise HTTPException(422)
    from agents.reviewer import resolve_review, ReviewNotOpen
    try:
        return resolve_review(build_deps(), body.review_id, body.action, body.corrections)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ReviewNotOpen as e:
        raise HTTPException(409, detail={"review_id": e.review_id, "status": e.status})
    except ValidationError as e:
        raise HTTPException(422, str(e))
    except BudgetExceeded:
        raise HTTPException(503, detail={"status": "budget_exceeded"})
