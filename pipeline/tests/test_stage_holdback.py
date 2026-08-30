import pytest
from tools.stage_holdback import (
    is_fully_processed, supersedes_existing, select_candidates, demo_stars,
    any_run_running, snapshot_eo, stage_eo, restore_snapshot,
)


def _eo(state, first_seen, supersedes=None, superseded_by=None, **extra):
    d = {"state": state, "first_seen": first_seen, "supersedes": supersedes or []}
    if superseded_by is not None:
        d["superseded_by"] = superseded_by
    return {**d, **extra}


# --- is_fully_processed / supersedes_existing -------------------------------

def test_is_fully_processed_true_for_complete_and_superseded():
    assert is_fully_processed({"state": "complete"})
    assert is_fully_processed({"state": "superseded"})


def test_is_fully_processed_false_for_in_progress_states():
    for state in ("discovered", "matching", "needs_review", "failed", None):
        assert not is_fully_processed({"state": state})


def test_supersedes_existing_true_when_predecessor_present():
    eos = {"D-1-1": _eo("superseded", 1), "D-1-2": _eo("complete", 2, supersedes=["D-1-1"])}
    assert supersedes_existing(eos["D-1-2"], eos)


def test_supersedes_existing_false_when_predecessor_gone_or_absent():
    eos = {"D-1-2": _eo("complete", 2, supersedes=["D-1-1"])}
    assert not supersedes_existing(eos["D-1-2"], eos)  # D-1-1 not in corpus
    assert not supersedes_existing(_eo("complete", 2), eos)  # no supersedes at all


# --- select_candidates -------------------------------------------------------

def test_select_candidates_picks_n_most_recent_when_window_already_has_superseder():
    eos = {
        "D-1-1": _eo("superseded", 1),
        "D-1-2": _eo("complete", 5, supersedes=["D-1-1"]),
        "D-2-2": _eo("complete", 4),
        "D-3-3": _eo("complete", 3),
        "D-4-4": _eo("complete", 2),
    }
    registry = set(eos)
    assert select_candidates(eos, registry, 2) == ["D-1-2", "D-2-2"]


def test_select_candidates_swaps_in_superseder_when_window_lacks_one():
    # Two most-recent EOs (by first_seen) don't supersede anything still in
    # the corpus; an older-but-eligible EO does. It must be swapped in for the
    # window's least-recent slot so the demo still gets a live flip.
    eos = {
        "D-1-1": _eo("superseded", 1),
        "D-1-2": _eo("complete", 2, supersedes=["D-1-1"]),  # the superseder, older
        "D-5-5": _eo("complete", 10),
        "D-6-6": _eo("complete", 9),
    }
    registry = set(eos)
    selected = select_candidates(eos, registry, 2)
    assert selected == ["D-5-5", "D-1-2"]
    assert demo_stars(selected, eos) == ["D-1-2"]


def test_select_candidates_filters_ineligible_states():
    eos = {
        "D-1-1": _eo("discovered", 5),
        "D-2-2": _eo("needs_review", 4),
        "D-3-3": _eo("complete", 3),
    }
    registry = set(eos)
    with pytest.raises(ValueError, match="only 1 eligible"):
        select_candidates(eos, registry, 2)


def test_select_candidates_filters_out_eos_not_in_live_registry():
    eos = {
        "D-1-1": _eo("complete", 5),
        "D-2-2": _eo("complete", 4),
    }
    registry = {"D-1-1"}  # D-2-2 has fallen off the live registry
    with pytest.raises(ValueError, match="only 1 eligible"):
        select_candidates(eos, registry, 2)


def test_select_candidates_raises_when_no_supersession_possible_anywhere():
    eos = {
        "D-1-1": _eo("complete", 5),
        "D-2-2": _eo("complete", 4),
        "D-3-3": _eo("complete", 3),
    }
    registry = set(eos)
    with pytest.raises(ValueError, match="supersedes a predecessor"):
        select_candidates(eos, registry, 2)


# --- any_run_running ---------------------------------------------------------

def test_any_run_running_true_when_a_run_is_in_progress():
    assert any_run_running({"run1": {"status": "running"}})


def test_any_run_running_false_when_all_runs_finished():
    assert not any_run_running({"run1": {"status": "ok"}, "run2": {"status": "budget_exceeded"}})
    assert not any_run_running({})


# --- snapshot_eo / stage_eo / restore_snapshot round trip --------------------

def _store():
    return {
        "eos": {
            "D-1-1": {"state": "superseded", "superseded_by": "D-1-2", "gcs_uri": "gs://b/d-1-1.pdf"},
            "D-1-2": {"state": "complete", "supersedes": ["D-1-1"], "first_seen": 5, "match_count": 3},
            "D-9-9": {"state": "complete", "first_seen": 1},
        },
        "extractions": {
            "D-1-2_v1": {"eo_number": "D-1-2", "payload": {"eo_number": "D-1-2"}},
            "D-9-9_v1": {"eo_number": "D-9-9", "payload": {}},
        },
        "matches": {
            "D-1-2_veh1": {"eo_number": "D-1-2", "vehicle_id": "veh1"},
        },
        "work_items": {
            "run1_D-1-2": {"eo_number": "D-1-2", "status": "done"},
        },
        "review_queue": {
            "rev1": {"eo_number": "D-1-2", "status": "resolved"},
        },
    }


def test_snapshot_eo_gathers_full_record_and_predecessor():
    store = _store()
    snap = snapshot_eo(store, "D-1-2")
    assert snap["eos_doc"]["state"] == "complete"
    assert set(snap["extractions"]) == {"D-1-2_v1"}
    assert set(snap["matches"]) == {"D-1-2_veh1"}
    assert set(snap["work_items"]) == {"run1_D-1-2"}
    assert set(snap["review_queue"]) == {"rev1"}
    assert snap["predecessor_reset"] == {"eo": "D-1-1", "doc": store["eos"]["D-1-1"]}


def test_snapshot_eo_no_predecessor_reset_when_not_a_superseder():
    store = _store()
    snap = snapshot_eo(store, "D-9-9")
    assert snap["predecessor_reset"] is None
    assert snap["extractions"] == {"D-9-9_v1": {"eo_number": "D-9-9", "payload": {}}}


def test_stage_eo_deletes_docs_and_resets_predecessor():
    store = _store()
    snap = stage_eo(store, "D-1-2")

    assert "D-1-2" not in store["eos"]
    assert "D-1-2_v1" not in store["extractions"]
    assert "D-1-2_veh1" not in store["matches"]
    assert "run1_D-1-2" not in store["work_items"]
    assert "rev1" not in store["review_queue"]

    pred = store["eos"]["D-1-1"]
    assert pred["state"] == "complete"
    assert "superseded_by" not in pred

    # unrelated EO untouched
    assert store["eos"]["D-9-9"]["state"] == "complete"
    assert "D-9-9_v1" in store["extractions"]

    assert snap["eo"] == "D-1-2"
    assert snap["predecessor_reset"]["eo"] == "D-1-1"


def test_restore_snapshot_round_trips_stage_eo_exactly():
    store = _store()
    original = {
        "eos": {k: dict(v) for k, v in store["eos"].items()},
        "extractions": {k: dict(v) for k, v in store["extractions"].items()},
        "matches": {k: dict(v) for k, v in store["matches"].items()},
        "work_items": {k: dict(v) for k, v in store["work_items"].items()},
        "review_queue": {k: dict(v) for k, v in store["review_queue"].items()},
    }

    snap = stage_eo(store, "D-1-2")
    assert store != original  # staging actually changed something

    restore_snapshot(store, snap)
    assert store == original
