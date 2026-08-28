import time
from core.llm import LLMResult
from core.db import is_claimable
from config import settings

class FakeLLM:
    """Pops queued results; raise queued exceptions. Records calls."""
    def __init__(self, queued):
        self.queued = list(queued)
        self.calls = []

    def _next(self, kind, args):
        self.calls.append((kind, args))
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def extract_pdf(self, gcs_uri, prompt, schema):
        return self._next("pdf", gcs_uri)

    def extract_images(self, image_uris, prompt, schema):
        return self._next("images", image_uris)

    def generate_json(self, prompt, schema):
        return self._next("json", prompt)

class FakeRepo:
    """Dict-backed Repo double: same method signatures, no Firestore, deterministic."""

    def __init__(self):
        self.eos = {}
        self.extractions = {}
        self.legacy = {}
        self.matches = {}
        self.reviews = []
        self.runs = {}
        self.events = {}
        self.work_items = {}
        self.vehicles = []
        self._run_seq = 0

    # --- eos ---
    def get_eo(self, eo):
        return self.eos.get(eo)

    def upsert_eo(self, eo, fields):
        self.eos[eo] = {**self.eos.get(eo, {}), **fields}

    def known_eo_numbers(self):
        return set(self.eos.keys())

    # --- extractions ---
    def next_extraction_version(self, eo):
        return sum(1 for k in self.extractions if k.startswith(f"{eo}_v")) + 1

    def write_extraction(self, eo, version, doc):
        self.extractions[f"{eo}_v{version}"] = doc

    def get_extraction(self, eo, version):
        return self.extractions.get(f"{eo}_v{version}")

    def get_legacy(self, eo):
        return self.legacy.get(eo)

    # --- matches ---
    def replace_matches(self, eo, matches):
        self.matches[eo] = list(matches)

    # --- review ---
    def add_review(self, item):
        review_id = f"review{len(self.reviews) + 1}"
        self.reviews.append({**item, "id": review_id, "status": "open"})
        return review_id

    def get_review(self, review_id):
        for r in self.reviews:
            if r.get("id") == review_id:
                return r
        return None

    def update_review(self, review_id, fields):
        for r in self.reviews:
            if r.get("id") == review_id:
                r.update(fields)
                return
        raise KeyError(review_id)

    # --- runs ---
    def create_run(self, trigger):
        self._run_seq += 1
        run_id = f"run{self._run_seq}"
        self.runs[run_id] = {"trigger": trigger, "status": "running",
                              "cost_usd": 0.0, "tok_in": 0, "tok_out": 0}
        self.events[run_id] = []
        return run_id

    def finish_run(self, run_id, fields):
        self.runs[run_id] = {**self.runs[run_id], **fields}

    def add_event(self, run_id, event):
        self.events[run_id].append(event)

    def add_run_cost(self, run_id, usd, tok_in, tok_out):
        run = self.runs[run_id]
        run["cost_usd"] += usd
        run["tok_in"] += tok_in
        run["tok_out"] += tok_out

    def incr_run(self, run_id, field, n=1):
        self.runs[run_id][field] = self.runs[run_id].get(field, 0) + n

    # --- work items ---
    def create_work_item(self, eo, run_id):
        item_id = f"{run_id}_{eo}"
        self.work_items[item_id] = {
            "id": item_id, "eo_number": eo, "run_id": run_id, "status": "pending",
            "stage": "extract", "attempts": 0, "lease_expires": 0, "created_at": time.time()}
        return item_id

    def claim_next(self, worker, now):
        for item in self.work_items.values():
            if is_claimable(item, now):
                item["status"] = "in_progress"
                item["worker"] = worker
                item["lease_expires"] = now + settings.lease_seconds
                return dict(item)
        return None

    def update_work_item(self, item_id, fields):
        if item_id not in self.work_items:
            raise KeyError(item_id)
        self.work_items[item_id] = {**self.work_items[item_id], **fields}

    def latest_work_item(self, eo):
        items = [i for i in self.work_items.values() if i.get("eo_number") == eo]
        if not items:
            return None
        return max(items, key=lambda i: i.get("created_at", 0))

    def vehicles_all(self):
        return self.vehicles
