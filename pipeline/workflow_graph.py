"""ADK2 graph orchestration for daily runs (spec §14).

Shape B: extract->audit->match live inside ONE `process` node that calls
`runner.process_work_item` directly, guaranteeing exact lifecycle parity with
the plain-loop `run_once` runner (which stays in place, untouched, for the
backfill). The graph adds the router (claim -> process/summarize) and the
loop edge (process -> claim) that `run_once`'s while-loop expressed in code.

No real GCP clients are constructed at import time here (see `runner.Deps`,
`agents.scout.discover`, `google.adk`, `google.genai.types` below -- all lazy
or client-free at module scope), matching main.py's build_deps() pattern.
"""
import asyncio
import time

from google.adk.workflow import START, Workflow, node
from google.adk.runners import InMemoryRunner
from google.genai import types as gt

from agents.healer import requeue_transient_failures
from agents.scout import discover
from config import settings
from core.costs import BudgetExceeded
from runner import process_work_item


def build_workflow(deps, run_id) -> Workflow:
    """Build the daily-run graph, closing over `deps`/`run_id` (not session state)."""
    start_time = time.time()

    @node
    def scout(ctx):
        ctx.state["new_eos"] = discover(deps.repo, deps.carb, deps.gcs, run_id)
        ctx.state["completed"] = 0
        ctx.state["needs_review"] = 0
        ctx.state["failed"] = 0
        ctx.state["status"] = "ok"
        ctx.state["time_capped"] = False

    @node
    def heal(ctx):
        ctx.state["healed"] = requeue_transient_failures(deps.repo, run_id)

    @node
    def claim(ctx):
        if time.time() - start_time > settings.run_time_cap_seconds:
            ctx.state["time_capped"] = True
            ctx.state["current_item"] = None
            ctx.route = False
            return
        item = deps.repo.claim_next("runner", now=time.time())
        ctx.state["current_item"] = item
        ctx.route = item is not None

    @node
    def process(ctx, current_item, completed, needs_review, failed):
        try:
            outcome = process_work_item(current_item, deps, run_id)
        except BudgetExceeded:
            ctx.state["status"] = "budget_exceeded"
            ctx.route = "budget"
            return
        if outcome == "complete":
            ctx.state["completed"] = completed + 1
        elif outcome == "needs_review":
            ctx.state["needs_review"] = needs_review + 1
        elif outcome == "failed":
            ctx.state["failed"] = failed + 1
        # outcome == "retry": no count change, item already back to pending
        ctx.route = "loop"

    @node
    def summarize(ctx, new_eos, completed, needs_review, failed, healed, status, time_capped):
        summary = {"new_eos": new_eos, "completed": completed, "needs_review": needs_review,
                   "failed": failed, "healed": healed, "status": status,
                   "time_capped": time_capped,
                   "cost_usd": round(deps.budget.spent, 4)}
        deps.repo.finish_run(run_id, summary)
        ctx.state["summary"] = summary

    return Workflow(name="carblegal_daily", edges=[
        (START, scout),
        (scout, heal),
        (heal, claim),
        (claim, {True: process, False: summarize}),
        (process, {"loop": claim, "budget": summarize}),
    ])


def run_workflow(deps, trigger: str) -> dict:
    """Drop-in replacement for `runner.run_once`'s summary-dict contract."""
    run_id = deps.repo.create_run(trigger)
    wf = build_workflow(deps, run_id)

    async def _run():
        adk_runner = InMemoryRunner(agent=wf, app_name="carblegal")
        session = await adk_runner.session_service.create_session(app_name="carblegal", user_id="pipeline")
        msg = gt.Content(role="user", parts=[gt.Part(text=trigger)])
        async for _ in adk_runner.run_async(user_id="pipeline", session_id=session.id, new_message=msg):
            pass
        s = await adk_runner.session_service.get_session(app_name="carblegal", user_id="pipeline",
                                                           session_id=session.id)
        return dict(s.state)

    state = asyncio.run(_run())
    return state["summary"]
