"""seed_vehicles.py — one-time: legacy SQLite -> Firestore `vehicles`. Run:
   py -3 seed/seed_vehicles.py [--dry-run]

Legacy `vehicles.engine_induction` is lowercase free text (na/turbo/supercharged/
carb/diesel). The schema vocabulary is NA|TURBO|SC|None, so values are mapped at
write time via INDUCTION_MAP (Ruling J): carb -> NA (carbureted engines are
naturally aspirated), diesel -> None (a fueling type, not an induction, so the
induction is unknown).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import sqlite3
from config import settings

SQLITE = r"C:\Users\lee\OneDrive\Documents\CARBSearch\pipeline\output\smoglegal.db"

INDUCTION_MAP = {"na": "NA", "turbo": "TURBO", "supercharged": "SC",
                 "carb": "NA",   # carbureted engines are naturally aspirated
                 "diesel": None}  # fueling type, not induction — unknown induction


def rows():
    db = sqlite3.connect(SQLITE)
    db.row_factory = sqlite3.Row
    for r in db.execute("SELECT id, year, make, model, trim, engine_displacement_l, "
                        "engine_induction, engine_cylinders FROM vehicles"):
        yield str(r["id"]), {"year": r["year"], "make": r["make"], "model": r["model"],
                             "trim": r["trim"], "displacement_l": r["engine_displacement_l"],
                             "induction": INDUCTION_MAP.get(r["engine_induction"], None),
                             "cylinders": r["engine_cylinders"]}


def main(dry: bool):
    data = list(rows())
    print(f"{len(data)} vehicles")
    if dry:
        print(data[:3]); return
    from google.cloud import firestore
    db = firestore.Client(project=settings.project_id)
    batch, n = db.batch(), 0
    for doc_id, fields in data:
        batch.set(db.collection("vehicles").document(doc_id), fields)
        n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch(); print(f"  {n}...")
    batch.commit()
    print("done")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
