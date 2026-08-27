"""One-shot repair script for superseded EOs written with wrong state field.

Finds all eos docs with "eo_status" == "superseded" and repairs them by:
1. Setting {"state": "superseded"} (merge=True)
2. Deleting the "eo_status" field
"""
from google.cloud import firestore

if __name__ == "__main__":
    db = firestore.Client()
    eos = db.collection("eos")

    query = eos.where("eo_status", "==", "superseded")
    docs = query.stream()

    repaired_count = 0
    for doc in docs:
        doc_id = doc.id
        eos.document(doc_id).set(
            {"state": "superseded", "eo_status": firestore.DELETE_FIELD},
            merge=True
        )
        print(f"Repaired {doc_id}")
        repaired_count += 1

    print(f"\nTotal repaired: {repaired_count}")
