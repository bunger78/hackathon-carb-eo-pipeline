"""Demo staging tool: hold back N most-recently-discovered, fully-processed EOs
so the next live run's Scout genuinely re-discovers and reprocesses them on
camera.

    py -3 tools/stage_holdback.py --stage N              # dry-run plan only
    py -3 tools/stage_holdback.py --stage N --yes        # actually stage
    py -3 tools/stage_holdback.py --restore holdback/<timestamp>   # undo

Selection: among EOs with state in {"complete", "superseded"} that still
appear in the live CARB registry (Scout will only re-discover EOs the
registry still lists), pick the N most recently discovered (by first_seen
desc), guaranteeing at least one staged EO supersedes a predecessor that
remains in the corpus -- that predecessor flipping state live, on camera, is
the point of the demo ("demo star").

Staging snapshots each EO's full record (eos doc + its extractions + its
matches + its work_items + any review_queue docs) to
holdback/<timestamp>/<eo>.json, then deletes all of that from Firestore. If
the EO is the demo star, its predecessor's superseded marker is also reset
(state -> complete, superseded_by removed) so the supersession is
rediscovered live; the predecessor's pre-reset doc is captured in the same
snapshot file so --restore can undo that too.

Refuses to stage while a run is currently in progress (status == "running").
"""
import argparse
import json
import sys
import time
from pathlib import Path

HOLDBACK_ROOT = Path(__file__).resolve().parent.parent / "holdback"
SNAPSHOT_COLLECTIONS = ("extractions", "matches", "work_items", "review_queue")


# --- pure selection logic ---------------------------------------------------

def is_fully_processed(eo_doc: dict) -> bool:
    return eo_doc.get("state") in ("complete", "superseded")


def supersedes_existing(eo_doc: dict, eos: dict) -> bool:
    """True if eo_doc's supersedes[] names an EO number still present in `eos`."""
    return any(pred in eos for pred in (eo_doc.get("supersedes") or []))


def select_candidates(eos: dict, registry: set, n: int) -> list:
    """Pick n EO numbers to stage: fully processed, present in the live
    registry, most recently discovered (first_seen desc), with at least one
    selected EO superseding a predecessor that remains in the corpus.

    Raises ValueError if fewer than n eligible EOs exist, or if no
    combination of n most-recent eligible EOs can satisfy the supersession
    requirement.
    """
    eligible = [eo for eo, d in eos.items() if is_fully_processed(d) and eo in registry]
    eligible.sort(key=lambda eo: eos[eo].get("first_seen", 0), reverse=True)

    if len(eligible) < n:
        raise ValueError(f"only {len(eligible)} eligible EOs in registry (need {n})")

    window = eligible[:n]
    if any(supersedes_existing(eos[eo], eos) for eo in window):
        return window

    superseder = next((eo for eo in eligible[n:] if supersedes_existing(eos[eo], eos)), None)
    if superseder is None:
        raise ValueError("no eligible EO supersedes a predecessor still in the corpus -- "
                          "demo requires at least one live supersession")
    return window[:-1] + [superseder]


def demo_stars(selected: list, eos: dict) -> list:
    """Which of the selected EOs supersede a predecessor that remains in the corpus."""
    return [eo for eo in selected if supersedes_existing(eos[eo], eos)]


def any_run_running(runs: dict) -> bool:
    return any(r.get("status") == "running" for r in runs.values())


# --- snapshot / stage / restore on a plain in-memory store ------------------
# store shape: {"eos": {eo: doc}, "extractions": {id: doc}, "matches": {id: doc},
#               "work_items": {id: doc}, "review_queue": {id: doc}}

def snapshot_eo(store: dict, eo: str) -> dict:
    """Gather the full record for one EO: its eos doc plus every extractions/
    matches/work_items/review_queue doc referencing it (eo_number == eo). If
    this EO supersedes a predecessor still in the corpus, also capture that
    predecessor's current doc so staging's live-reset of it can be undone.
    """
    eos = store.get("eos", {})
    eo_doc = eos.get(eo)
    snap = {
        "eo": eo,
        "eos_doc": dict(eo_doc) if eo_doc is not None else None,
        "predecessor_reset": None,
    }
    for coll in SNAPSHOT_COLLECTIONS:
        snap[coll] = {k: dict(v) for k, v in store.get(coll, {}).items() if v.get("eo_number") == eo}
    if eo_doc:
        pred = next((p for p in (eo_doc.get("supersedes") or []) if p in eos), None)
        if pred is not None:
            snap["predecessor_reset"] = {"eo": pred, "doc": dict(eos[pred])}
    return snap


def stage_eo(store: dict, eo: str) -> dict:
    """Snapshot one EO, then delete its docs from `store` and (if it is a
    demo star) reset its predecessor's superseded marker back to complete.
    Mutates `store` in place; returns the snapshot to persist for --restore.
    """
    snap = snapshot_eo(store, eo)
    for coll in SNAPSHOT_COLLECTIONS:
        for doc_id in snap[coll]:
            del store[coll][doc_id]
    store.get("eos", {}).pop(eo, None)
    if snap["predecessor_reset"]:
        pred = snap["predecessor_reset"]["eo"]
        pred_doc = {k: v for k, v in store["eos"][pred].items() if k != "superseded_by"}
        pred_doc["state"] = "complete"
        store["eos"][pred] = pred_doc
    return snap


def restore_snapshot(store: dict, snap: dict) -> None:
    """Undo stage_eo exactly: put back the eos doc, every collection doc, and
    the predecessor's pre-reset doc, if any.
    """
    if snap["eos_doc"] is not None:
        store.setdefault("eos", {})[snap["eo"]] = dict(snap["eos_doc"])
    for coll in SNAPSHOT_COLLECTIONS:
        store.setdefault(coll, {}).update({k: dict(v) for k, v in snap[coll].items()})
    if snap["predecessor_reset"]:
        pred = snap["predecessor_reset"]
        store.setdefault("eos", {})[pred["eo"]] = dict(pred["doc"])


# --- Firestore wiring (not covered by tests: real GCP client) ---------------

def _load_collection(db, name) -> dict:
    return {d.id: d.to_dict() for d in db.collection(name).stream()}


def _collect_for_eo(db, eo, eos_cache: dict) -> dict:
    """Read one EO's full record straight from Firestore (targeted queries,
    not a full-collection scan) into the snapshot_eo store shape. Also pulls
    in the predecessor's doc (from the already-loaded eos_cache) if this EO
    supersedes one that remains in the corpus.
    """
    store = {"eos": {}, "extractions": {}, "matches": {}, "work_items": {}, "review_queue": {}}
    eo_doc = eos_cache.get(eo)
    if eo_doc is not None:
        store["eos"][eo] = eo_doc
        pred = next((p for p in (eo_doc.get("supersedes") or []) if p in eos_cache), None)
        if pred is not None:
            store["eos"][pred] = eos_cache[pred]
    for coll in SNAPSHOT_COLLECTIONS:
        for d in db.collection(coll).where("eo_number", "==", eo).stream():
            store[coll][d.id] = d.to_dict()
    return store


def _apply_stage_to_firestore(db, eo, snap):
    from google.cloud import firestore
    batch = db.batch()
    for coll in SNAPSHOT_COLLECTIONS:
        for doc_id in snap[coll]:
            batch.delete(db.collection(coll).document(doc_id))
    batch.delete(db.collection("eos").document(eo))
    if snap["predecessor_reset"]:
        pred = snap["predecessor_reset"]["eo"]
        batch.set(db.collection("eos").document(pred),
                  {"state": "complete", "superseded_by": firestore.DELETE_FIELD}, merge=True)
    batch.commit()


def _apply_restore_to_firestore(db, snap):
    batch = db.batch()
    if snap["eos_doc"] is not None:
        batch.set(db.collection("eos").document(snap["eo"]), snap["eos_doc"])
    for coll in SNAPSHOT_COLLECTIONS:
        for doc_id, doc in snap[coll].items():
            batch.set(db.collection(coll).document(doc_id), doc)
    if snap["predecessor_reset"]:
        pred = snap["predecessor_reset"]
        batch.set(db.collection("eos").document(pred["eo"]), pred["doc"])
    batch.commit()


def _cmd_stage(db, n: int, yes: bool):
    runs = _load_collection(db, "runs")
    if any_run_running(runs):
        print("Refusing to stage: a run is currently in progress (status == running).")
        sys.exit(1)

    from carb.powerbi import CarbClient
    registry = {e["eo_number"] for e in CarbClient().list_all()}
    eos_cache = _load_collection(db, "eos")

    try:
        selected = select_candidates(eos_cache, registry, n)
    except ValueError as exc:
        print(f"Cannot select {n} EOs to hold back: {exc}")
        sys.exit(1)
    stars = demo_stars(selected, eos_cache)

    print(f"Plan: stage {len(selected)} EOs -> {', '.join(selected)}")
    for eo in selected:
        pred = next((p for p in (eos_cache[eo].get("supersedes") or []) if p in eos_cache), None)
        star = " (demo star)" if eo in stars else ""
        print(f"  {eo}{star}" + (f" -- supersedes {pred}, which stays in the corpus" if pred else ""))
    if not stars:
        print("WARNING: no demo star in this selection -- the supersession flip will not happen live.")

    if not yes:
        print("\nDry run only. Re-run with --yes to delete from Firestore and write snapshots.")
        return

    out_dir = HOLDBACK_ROOT / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    for eo in selected:
        store = _collect_for_eo(db, eo, eos_cache)
        snap = stage_eo(store, eo)
        (out_dir / f"{eo}.json").write_text(json.dumps(snap, indent=2, default=str))
        _apply_stage_to_firestore(db, eo, snap)
        print(f"staged {eo} -> {out_dir / f'{eo}.json'}")

    print(f"\nStaged: {', '.join(selected)}")
    print(f"Demo star: {', '.join(stars)}" if stars else "Demo star: none")
    print(f"Snapshots: {out_dir}")


def _cmd_restore(db, directory: str):
    d = Path(directory)
    files = sorted(d.glob("*.json"))
    if not files:
        print(f"No snapshot files found in {d}")
        sys.exit(1)
    for f in files:
        snap = json.loads(f.read_text())
        _apply_restore_to_firestore(db, snap)
        print(f"restored {snap['eo']}")
    print(f"\nRestored {len(files)} EOs from {d}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=int, metavar="N", help="hold back the N most recent eligible EOs")
    parser.add_argument("--restore", metavar="DIR", help="restore everything from a holdback/<timestamp> dir")
    parser.add_argument("--yes", action="store_true", help="actually delete/restore (default: dry-run plan)")
    args = parser.parse_args()

    if (args.stage is None) == (args.restore is None):
        parser.error("pass exactly one of --stage N or --restore DIR")

    from google.cloud import firestore
    from config import settings
    db = firestore.Client(project=settings.project_id)

    if args.stage is not None:
        _cmd_stage(db, args.stage, args.yes)
    else:
        _cmd_restore(db, args.restore)


if __name__ == "__main__":
    main()
