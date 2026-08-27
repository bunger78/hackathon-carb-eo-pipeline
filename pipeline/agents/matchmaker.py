import json
from schemas.extraction import ResolverBatch
from prompts.resolver import RESOLVER_PROMPT
from matching.engine import match_row
from core.costs import cost_usd
from config import settings

_RANK = {"exact": 3, "high": 2, "medium": 1, "generic": 0}

def _doc(eo, ex, vehicle_id, tier, method, pns, rationale=None):
    d = {"vehicle_id": vehicle_id, "eo_number": eo, "tier": tier, "method": method,
         "part_numbers": pns or ex.part_numbers, "category": ex.category,
         "device_name": ex.device_name, "manufacturer": ex.manufacturer}
    if rationale:
        d["rationale"] = rationale
    return d

def run_matching(llm, repo, budget, eo, ex, index, run_id) -> dict:
    best: dict[str, dict] = {}
    unresolved = []
    for i, row in enumerate(ex.fitment):
        hits = match_row(row, index)
        if not hits:
            cands = index.candidates(row)
            if cands:
                unresolved.append((i, row, cands))
            continue
        for v, tier in hits:
            cur = best.get(v["id"])
            if cur is None or _RANK[tier] > _RANK[cur["tier"]]:
                best[v["id"]] = _doc(eo, ex, v["id"], tier, "deterministic", row.part_numbers)
    resolved_via_llm = 0
    if unresolved:
        payload = {"fitment": [{"index": i, **r.model_dump()} for i, r, _ in unresolved],
                   "candidates": {str(i): [{"id": c["id"], "year": c["year"], "make": c["make"],
                                            "model": c["model"], "displacement_l": c.get("displacement_l")}
                                           for c in cands] for i, _, cands in unresolved}}
        res = llm.generate_json(RESOLVER_PROMPT + json.dumps(payload), ResolverBatch)
        usd = cost_usd(res.tok_in, res.tok_out)
        budget.add(usd)
        repo.add_run_cost(run_id, usd, res.tok_in, res.tok_out)
        rows = {i: r for i, r, _ in unresolved}
        for d in ResolverBatch.model_validate(res.data).decisions:
            row = rows.get(d.fitment_index)
            if row is None:
                continue
            if d.confidence < settings.confidence_threshold:
                repo.add_review({"eo_number": eo, "reason": "ambiguous_match",
                                 "agent_notes": d.rationale,
                                 "payload": {"fitment": row.model_dump(),
                                             "proposed_vehicle_ids": d.vehicle_ids}})
                continue
            for vid in d.vehicle_ids:
                if vid not in best:
                    best[vid] = _doc(eo, ex, vid, "generic", "gemini_resolved",
                                     row.part_numbers, d.rationale)
                    resolved_via_llm += 1
    repo.replace_matches(eo, list(best.values()))
    repo.upsert_eo(eo, {"state": "complete", "match_count": len(best)})
    repo.add_event(run_id, {"agent": "matchmaker", "eo": eo, "action": "matched",
                            "count": len(best), "gemini_resolved": resolved_via_llm})
    return {"matches": len(best), "gemini_resolved": resolved_via_llm}
