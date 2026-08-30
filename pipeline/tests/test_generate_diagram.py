from tools.generate_diagram import build_diagram, check_mermaid_syntax, main, OUT
from workflow_graph import build_workflow


def test_generator_runs_without_gcp_creds_and_produces_valid_mermaid():
    # build_workflow(None, ...) never dereferences deps at construction time,
    # so this must succeed with no GCS/Firestore/Vertex client anywhere.
    text = build_diagram()
    check_mermaid_syntax(text)


def test_output_contains_every_node_from_the_built_workflow():
    wf = build_workflow(None, "test-diagram")
    expected_names = {n.name for n in wf.graph.nodes if n.name != "__START__"}
    assert expected_names, "workflow graph produced no nodes"

    text = build_diagram()
    for name in expected_names:
        assert name in text, f"node {name!r} from the built Workflow is missing from the diagram"


def test_main_writes_docs_architecture_mmd():
    if OUT.exists():
        OUT.unlink()

    main()

    assert OUT.exists()
    written = OUT.read_text(encoding="utf-8")
    check_mermaid_syntax(written)
    assert written == build_diagram()
