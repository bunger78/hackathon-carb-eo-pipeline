import time
from config import settings

def is_claimable(item: dict, now: float) -> bool:
    if item.get("status") == "pending":
        return True
    return item.get("status") == "in_progress" and item.get("lease_expires", 0) < now

class Repo:
    def __init__(self, client=None):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client(project=settings.project_id)
        self.db = client

    # --- eos ---
    def get_eo(self, eo):
        snap = self.db.collection("eos").document(eo).get()
        return snap.to_dict() if snap.exists else None

    def upsert_eo(self, eo, fields):
        self.db.collection("eos").document(eo).set(fields, merge=True)

    def known_eo_numbers(self):
        return {d.id for d in self.db.collection("eos").select([]).stream()}

    # --- extractions ---
    def next_extraction_version(self, eo):
        docs = self.db.collection("extractions").where("eo_number", "==", eo).select([]).stream()
        return sum(1 for _ in docs) + 1

    def write_extraction(self, eo, version, doc):
        self.db.collection("extractions").document(f"{eo}_v{version}").set(doc)

    def get_extraction(self, eo, version):
        snap = self.db.collection("extractions").document(f"{eo}_v{version}").get()
        return snap.to_dict() if snap.exists else None

    def get_legacy(self, eo):
        snap = self.db.collection("legacy_extractions").document(eo).get()
        return snap.to_dict() if snap.exists else None

    # --- matches ---
    def replace_matches(self, eo, matches):
        old = self.db.collection("matches").where("eo_number", "==", eo).select([]).stream()
        batch = self.db.batch()
        n = 0
        for d in old:
            batch.delete(d.reference); n += 1
            if n % 400 == 0: batch.commit(); batch = self.db.batch()
        for m in matches:
            ref = self.db.collection("matches").document(f'{eo}_{m["vehicle_id"]}')
            batch.set(ref, m); n += 1
            if n % 400 == 0: batch.commit(); batch = self.db.batch()
        batch.commit()

    # --- review ---
    def add_review(self, item):
        ref = self.db.collection("review_queue").document()
        item = {**item, "status": "open", "created_at": time.time()}
        ref.set(item)
        return ref.id

    def get_review(self, review_id):
        snap = self.db.collection("review_queue").document(review_id).get()
        return snap.to_dict() if snap.exists else None

    def update_review(self, review_id, fields):
        self.db.collection("review_queue").document(review_id).set(fields, merge=True)

    def open_reviews(self):
        """All review_queue docs with status == "open" (single-field query, no
        composite index needed). Reason filtering happens in Python -- feeds
        tools.requeue_reviews.requeue_open_reviews."""
        return [d.to_dict() | {"id": d.id} for d in
                self.db.collection("review_queue").where("status", "==", "open").stream()]

    # --- runs ---
    def create_run(self, trigger):
        ref = self.db.collection("runs").document()
        ref.set({"trigger": trigger, "started_at": time.time(), "status": "running",
                 "cost_usd": 0.0, "tok_in": 0, "tok_out": 0})
        return ref.id

    def finish_run(self, run_id, fields):
        self.db.collection("runs").document(run_id).set({**fields, "finished_at": time.time()}, merge=True)

    def add_event(self, run_id, event):
        self.db.collection("runs").document(run_id).collection("events").document().set(
            {**event, "ts": time.time()})

    def add_run_cost(self, run_id, usd, tok_in, tok_out):
        from google.cloud import firestore
        self.db.collection("runs").document(run_id).update({
            "cost_usd": firestore.Increment(usd), "tok_in": firestore.Increment(tok_in),
            "tok_out": firestore.Increment(tok_out)})

    def incr_run(self, run_id, field, n=1):
        from google.cloud import firestore
        self.db.collection("runs").document(run_id).update({field: firestore.Increment(n)})

    # --- work items ---
    def create_work_item(self, eo, run_id):
        self.db.collection("work_items").document(f"{run_id}_{eo}").set({
            "eo_number": eo, "run_id": run_id, "status": "pending", "stage": "extract",
            "attempts": 0, "lease_expires": 0, "created_at": time.time()})

    def claim_next(self, worker, now=None):
        from google.cloud import firestore
        now = now or time.time()
        cands = (self.db.collection("work_items")
                 .where("status", "in", ["pending", "in_progress"])
                 .order_by("created_at").limit(100).stream())
        for snap in cands:
            item = snap.to_dict() | {"id": snap.id}
            if not is_claimable(item, now):
                continue
            tx = self.db.transaction()

            @firestore.transactional
            def _claim(tx, ref):
                cur = ref.get(transaction=tx).to_dict()
                if not is_claimable(cur, now):
                    return False
                tx.update(ref, {"status": "in_progress", "worker": worker,
                                "lease_expires": now + settings.lease_seconds})
                return True

            if _claim(tx, snap.reference):
                item.update({"status": "in_progress", "worker": worker,
                             "lease_expires": now + settings.lease_seconds})
                return item
        return None

    def update_work_item(self, item_id, fields):
        self.db.collection("work_items").document(item_id).set(fields, merge=True)

    def failed_work_items(self):
        """Single-field equality query (status == "failed"), no ordering --
        no composite index needed. Feeds agents.healer.requeue_transient_failures.
        Capped at 200 so this stays a bounded read regardless of how large the
        failed set grows."""
        return [d.to_dict() | {"id": d.id} for d in
                self.db.collection("work_items").where("status", "==", "failed").limit(200).stream()]

    def latest_work_item(self, eo):
        """Newest work_items doc for this EO (more than one can exist across
        runs -- see batchfill.py's _resolve_work_item), ordered by created_at
        descending so a stale duplicate is never mistaken for the current one."""
        from google.cloud import firestore
        docs = list(self.db.collection("work_items").where("eo_number", "==", eo)
                    .order_by("created_at", direction=firestore.Query.DESCENDING)
                    .limit(1).stream())
        if not docs:
            return None
        d = docs[0]
        return d.to_dict() | {"id": d.id}

    def vehicles_all(self):
        return [d.to_dict() | {"id": d.id} for d in self.db.collection("vehicles").stream()]
