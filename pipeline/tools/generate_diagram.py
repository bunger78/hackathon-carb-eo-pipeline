"""Architecture diagram generator.

The claim is "this diagram is generated from the running code" -- so the ADK2
node/edge/route list below comes from introspecting the real
`google.adk.workflow.Workflow` object that `workflow_graph.build_workflow`
returns, not from a hand-drawn box diagram. Only the outer frame (trigger /
persistence / batch / dashboard layers) is hand-written -- see
`render_static_frame`, clearly marked below.

No GCP client is constructed: `build_workflow` never dereferences `deps` at
construction time -- `deps` is only touched inside the `@node` closures,
which run during workflow *execution*, never during graph *construction*
(this script only builds the graph, it never runs it). So `None` is passed
for `deps`, which is the simplest possible zero-GCP construction and mirrors
tests/test_workflow_graph.py's pattern of building the graph with fakes
instead of real GCP clients.

    $env:PYTHONPATH='.'; py -3 tools/generate_diagram.py
"""
from __future__ import annotations

import pathlib

from workflow_graph import build_workflow

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "architecture.mmd"


def introspect_graph() -> tuple[list[str], list[tuple[str, str, object]]]:
    """Builds the real Workflow (GCP-free) and returns its graph as plain data.

    nodes: node names in graph order, excluding the synthetic `__START__`
           sentinel (the static frame's `api --> wf_scout` edge below stands
           in for it).
    edges: (from_name, to_name, route) exactly as they exist on the built
           graph -- `route` is `None` for an unconditional edge, else the
           actual routed value (e.g. `True`, `"loop"`).
    """
    wf = build_workflow(None, "diagram")
    nodes = [n.name for n in wf.graph.nodes if n.name != "__START__"]
    edges = [(e.from_node.name, e.to_node.name, e.route) for e in wf.graph.edges
             if e.from_node.name != "__START__"]
    return nodes, edges


def _id(name: str) -> str:
    """Mermaid node id for an introspected workflow node (namespaced so it
    can't collide with the static frame's own ids, e.g. `dash`, `gcs`)."""
    return f"wf_{name}"


def render_workflow_subgraph(nodes: list[str], edges: list[tuple[str, str, object]]) -> list[str]:
    """Renders the introspected ADK2 graph as a mermaid subgraph. Every node
    and edge line below is derived from `nodes`/`edges` -- nothing here is
    hardcoded against the current scout/heal/claim/process/summarize names."""
    lines = ['  subgraph ADK2["ADK2 workflow (google.adk.workflow.Workflow)"]']
    for name in nodes:
        lines.append(f'    {_id(name)}["{name}"]')
    for frm, to, route in edges:
        arrow = "-->" if route is None else f'-- "{route}" -->'
        lines.append(f"    {_id(frm)} {arrow} {_id(to)}")
    lines.append("  end")
    return lines


# --- Static outer frame ------------------------------------------------------
# Hand-written, NOT introspected (spec: this part is static). Mirrors:
#   - infra/deploy.sh: Cloud Scheduler carb-daily -> Cloud Run carb-api /run,
#     and the carb-dash service + its run.invoker binding.
#   - main.py: build_deps() wires Firestore (Repo) + GCS (GCSStore); the
#     admin endpoints (/admin/run-now, /admin/resolve-review, /admin/retry-eo)
#     are what carb-dash calls back into carb-api for.
#   - batchfill.py: --prepare writes request JSONL to the GCS bucket,
#     --submit hands it to a Vertex AI batch job, --ingest streams the
#     job's output back through Firestore.
#   - the `process` node calls runner.process_work_item, whose extractor
#     rung is core/llm.py's Gemini call (settings.model_id = gemini-3.7-flash,
#     see infra/deploy.sh's MODEL_ID default).
def render_static_frame() -> list[str]:
    return [
        '  scheduler["Cloud Scheduler (carb-daily)"]',
        '  api["Cloud Run carb-api (/run)"]',
        '  firestore[("Firestore (eos, work_items, runs)")]',
        '  gcs[("GCS bucket (pdfs/)")]',
        '  gemini["Gemini 3.7 Flash"]',
        '  vertex["Vertex AI batch job"]',
        '  batchfill["batchfill --ingest"]',
        '  dash["carb-dash"]',
        "",
        "  scheduler --> api",
        f"  api --> {_id('scout')}",
        f"  {_id('scout')} --> firestore",
        f"  {_id('scout')} --> gcs",
        f"  {_id('process')} -.-> gemini",
        f"  {_id('summarize')} --> firestore",
        "  gcs --> vertex",
        "  vertex --> batchfill",
        "  batchfill --> firestore",
        "  dash --> firestore",
        '  dash -- "admin" --> api',
    ]


def check_mermaid_syntax(text: str) -> None:
    """Pure-python sanity check (no mermaid renderer needed): the first
    non-blank/non-comment line opens a flowchart, and every bracket type
    balances. Raises AssertionError with a descriptive message on failure."""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("%%")]
    assert lines, "diagram is empty"
    first_word = lines[0].strip().split()[0]
    assert first_word in ("flowchart", "graph"), f"first line must open a flowchart, got: {lines[0]!r}"
    for open_c, close_c in (("(", ")"), ("[", "]"), ("{", "}")):
        n_open, n_close = text.count(open_c), text.count(close_c)
        assert n_open == n_close, f"unbalanced {open_c!r}/{close_c!r}: {n_open} vs {n_close}"


def build_diagram() -> str:
    nodes, edges = introspect_graph()
    lines = [
        "flowchart TD",
        "  %% AUTO-GENERATED -- do not hand-edit the ADK2 subgraph below.",
        "  %% Regenerate: $env:PYTHONPATH='.'; py -3 tools/generate_diagram.py",
        "",
        *render_static_frame(),
        "",
        *render_workflow_subgraph(nodes, edges),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    text = build_diagram()
    check_mermaid_syntax(text)
    print(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
