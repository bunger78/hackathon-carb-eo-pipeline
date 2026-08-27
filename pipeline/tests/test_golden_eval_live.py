"""Live golden-set regression check. Skipped under plain `pytest` (no Firestore
   ADC/network available in CI); runs under `pytest -m golden`."""
import json

import pytest
from google.cloud import firestore

from config import settings

pytestmark = pytest.mark.golden


def _firestore_reachable() -> bool:
    try:
        client = firestore.Client(project=settings.project_id)
        list(client.collection("eos").limit(1).stream())
        return True
    except Exception:
        return False


if not _firestore_reachable():
    pytest.skip("Firestore ADC/network unavailable", allow_module_level=True)

from core.db import Repo
from tools.golden_eval import GOLD, score


def _aggregate():
    repo = Repo()
    agg = {"agent": [], "legacy": []}
    for f in sorted(GOLD.glob("*.json")):
        eo = f.stem.upper()
        expected = json.loads(f.read_text())
        ext = [d.to_dict() for d in repo.db.collection("extractions")
               .where("eo_number", "==", eo).stream()]
        agent = max(ext, key=lambda d: d.get("created_at", 0))["payload"] if ext else None
        legacy = repo.get_legacy(eo)
        for src, got in (("agent", agent), ("legacy", legacy)):
            agg[src].append(score(expected, got))
    return agg


def test_agent_meets_or_beats_legacy_baseline():
    agg = _aggregate()
    assert agg["agent"] and agg["legacy"], "no golden EOs scored"

    def avg(key, src):
        return sum(x[key] for x in agg[src]) / len(agg[src])

    assert avg("pn_f1", "agent") >= avg("pn_f1", "legacy")
    assert avg("assoc_f1", "agent") >= avg("assoc_f1", "legacy")
