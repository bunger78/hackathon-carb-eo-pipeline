"""One-shot export: snapshot live Firestore extraction/legacy docs for every golden
EO into golden/actual/ fixtures so golden_eval.py --offline (and
tests/test_golden_eval_offline.py) can reproduce the golden report with NO GCP
credentials -- a third-party reviewer scores the committed fixtures, not live data.

Run once whenever the live golden set meaningfully changes (e.g. a v2 re-export
after a batch re-run); NOT part of CI, NOT run automatically. The fixtures
committed alongside this tool are v1-era -- the v2 re-export is a follow-up
after the next extraction batch, not part of this change.

Usage (from pipeline/):
    $env:PYTHONPATH='.'; py -3 tools/export_golden_actuals.py
"""
import json
import pathlib

from core.db import Repo
from tools.golden_eval import GOLD

ACTUAL = GOLD.parent / "actual"


def latest_envelope(repo: Repo, eo: str) -> dict | None:
    docs = [d.to_dict() for d in repo.db.collection("extractions")
            .where("eo_number", "==", eo).stream()]
    return max(docs, key=lambda d: d.get("created_at", 0)) if docs else None


def main():
    repo = Repo()
    ACTUAL.mkdir(parents=True, exist_ok=True)
    n_agent = n_legacy = n_missing_agent = n_missing_legacy = 0
    for f in sorted(GOLD.glob("*.json")):
        eo = f.stem.upper()

        envelope = latest_envelope(repo, eo)
        if envelope:
            agent_doc = {**envelope["payload"], "_prompt_version": envelope.get("prompt_version")}
            (ACTUAL / f"{eo}.agent.json").write_text(
                json.dumps(agent_doc, indent=2, sort_keys=True), encoding="utf-8")
            n_agent += 1
        else:
            n_missing_agent += 1

        legacy_doc = repo.get_legacy(eo)
        if legacy_doc:
            # Raw doc, no eo_number field -- Firestore doc ID (== eo here) carries it
            # instead (see golden_eval.legacy_with_id). _doc_id lets the offline path
            # reconstruct that injection without a Firestore doc ID to read from.
            legacy_doc = {**legacy_doc, "_doc_id": eo}
            (ACTUAL / f"{eo}.legacy.json").write_text(
                json.dumps(legacy_doc, indent=2, sort_keys=True), encoding="utf-8")
            n_legacy += 1
        else:
            n_missing_legacy += 1

    print(f"agent fixtures written: {n_agent} (missing: {n_missing_agent})")
    print(f"legacy fixtures written: {n_legacy} (missing: {n_missing_legacy})")


if __name__ == "__main__":
    main()
