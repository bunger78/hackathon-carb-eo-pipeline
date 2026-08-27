"""ADK wrapper exposing the CARB EO pipeline as an agent with three tools.

Smoke-tested only: `python -c "import adk_app; print(adk_app.root_agent.name)"`.
The tools are never invoked outside a real run — each hits Firestore/Vertex via
main.build_deps().
"""
import os

from config import settings

# ADK's plain Gemini model strings route through Vertex when these env vars are
# set; do this before importing google.adk.agents so the default client picks
# them up.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.genai_location)

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

import main
from runner import process_work_item, run_once


def process_eo(eo_number: str) -> dict:
    """Extract, audit, and match a single already-discovered EO through the pipeline."""
    deps = main.build_deps()
    run_id = deps.repo.create_run("adk")
    deps.repo.create_work_item(eo_number, run_id)
    item = {"id": f"{run_id}_{eo_number}", "eo_number": eo_number, "attempts": 0}
    outcome = process_work_item(item, deps, run_id)
    counts = {"completed": int(outcome == "complete"), "needs_review": int(outcome == "needs_review"),
              "failed": int(outcome == "failed"), "retry": int(outcome == "retry")}
    summary = {"eo_number": eo_number, "outcome": outcome, "cost_usd": round(deps.budget.spent, 4), **counts}
    deps.repo.finish_run(run_id, summary)
    return summary


def pipeline_status() -> dict:
    """Count EOs currently in each pipeline state (discovered, matching, complete, needs_review, failed)."""
    deps = main.build_deps()
    counts: dict[str, int] = {}
    for doc in deps.repo.db.collection("eos").select(["state"]).stream():
        state = (doc.to_dict() or {}).get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def run_daily() -> dict:
    """Run one full discover-and-process pass over all new/pending EOs."""
    return run_once(main.build_deps(), "adk")


root_agent = Agent(
    name="carblegal",
    model=settings.model_id,
    instruction=(
        "You are the operator for the CARB Executive Order (EO) compliance pipeline. "
        "You discover new aftermarket-device EOs published by CARB, extract their part "
        "numbers and vehicle fitment data, audit those extractions for quality, and match "
        "them against the vehicle catalog. Use process_eo to reprocess a single "
        "already-discovered EO by number, pipeline_status to report how many EOs are in "
        "each state, and run_daily to trigger a full discovery-and-processing run across "
        "all pending EOs."
    ),
    tools=[
        FunctionTool(process_eo),
        FunctionTool(pipeline_status),
        FunctionTool(run_daily),
    ],
)
