"""One-shot repair: populate eos.pdf_url from the CARB registry listing.

Bootstrap registered corpus EOs without pdf_url (it derives docs from GCS
blobs, which carry no source URL); only scout-discovered EOs have one.
"""
from google.cloud import firestore
from carb.powerbi import CarbClient

if __name__ == "__main__":
    db = firestore.Client(project="carblegal")
    listing = {e["eo_number"]: e["pdf_url"] for e in CarbClient().list_all() if e.get("pdf_url")}
    print(f"registry listing: {len(listing)} EOs with URLs")

    batch = db.batch()
    n = fixed = 0
    for snap in db.collection("eos").stream():
        d = snap.to_dict()
        if d.get("pdf_url"):
            continue
        url = listing.get(snap.id)
        if not url:
            continue
        batch.set(snap.reference, {"pdf_url": url}, merge=True)
        fixed += 1
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    print(f"repaired pdf_url on {fixed} EOs")
