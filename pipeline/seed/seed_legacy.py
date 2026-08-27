"""seed_legacy.py — one-time: legacy SQLite -> Firestore `legacy_extractions`. Run:
   py -3 seed/seed_legacy.py [--dry-run]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import sqlite3
from config import settings

SQLITE = r"C:\Users\lee\OneDrive\Documents\CARBSearch\pipeline\output\smoglegal.db"


def rows():
    db = sqlite3.connect(SQLITE)
    db.row_factory = sqlite3.Row
    parts = db.execute(
        "SELECT p.id, p.eo_number, p.device_name, p.category, m.name AS manufacturer, "
        "(SELECT COUNT(*) FROM part_eo_fitment f WHERE f.part_id = p.id) AS fitment_count "
        "FROM parts p LEFT JOIN manufacturers m ON m.id = p.manufacturer_id"
    ).fetchall()
    for p in parts:
        part_numbers = [n["number"] for n in db.execute(
            "SELECT number FROM part_numbers WHERE part_id = ?", (p["id"],))]
        yield str(p["eo_number"]), {"device_name": p["device_name"], "manufacturer": p["manufacturer"],
                                    "category": p["category"], "part_numbers": part_numbers,
                                    "fitment_count": p["fitment_count"]}


def main(dry: bool):
    data = list(rows())
    print(f"{len(data)} legacy extractions")
    if dry:
        print(data[:3]); return
    from google.cloud import firestore
    db = firestore.Client(project=settings.project_id)
    batch, n = db.batch(), 0
    for doc_id, fields in data:
        batch.set(db.collection("legacy_extractions").document(doc_id), fields)
        n += 1
        if n % 400 == 0:
            batch.commit(); batch = db.batch(); print(f"  {n}...")
    batch.commit()
    print("done")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
