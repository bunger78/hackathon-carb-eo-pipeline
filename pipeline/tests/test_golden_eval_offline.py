"""Offline golden-set regression check: scores the committed golden/actual/
fixtures (snapshotted once via tools/export_golden_actuals.py) against
golden/expected/ using the identical scoring functions as the live eval.
No Firestore import, no credentials, no network -- runs under plain `pytest`,
reproducible by a third party who only has the cloned repo."""
import json

from tools.golden_eval import GOLD, load_actual_agent, load_actual_legacy, score


def _aggregate_offline():
    agg = {"agent": [], "legacy": []}
    for f in sorted(GOLD.glob("*.json")):
        eo = f.stem.upper()
        expected = json.loads(f.read_text())
        agent = load_actual_agent(eo)
        legacy = load_actual_legacy(eo)
        for src, got in (("agent", agent), ("legacy", legacy)):
            agg[src].append(score(expected, got))
    return agg


def test_offline_scoring_covers_every_expected_eo():
    expected_files = list(GOLD.glob("*.json"))
    assert expected_files, "no golden/expected fixtures found"

    agg = _aggregate_offline()
    assert len(agg["agent"]) == len(expected_files)
    assert len(agg["legacy"]) == len(expected_files)


def test_agent_meets_or_beats_legacy_baseline_offline():
    agg = _aggregate_offline()
    assert agg["agent"] and agg["legacy"], "no golden EOs scored"

    def avg(key, src):
        return sum(x[key] for x in agg[src]) / len(agg[src])

    assert avg("pn_f1", "agent") >= avg("pn_f1", "legacy")
