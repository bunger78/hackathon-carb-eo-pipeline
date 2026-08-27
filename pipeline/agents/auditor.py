import json, random, re, time
from pydantic import ValidationError
from schemas.extraction import Extraction, CritiqueVerdict
from prompts.critic import CRITIC_PROMPT
from core.costs import cost_usd
from config import settings

_EO_RE = re.compile(r"^[A-Z0-9]{1,3}(-[A-Z0-9]{1,6}){1,3}$")

def _pn_ok(pn: str) -> bool:
    return " " not in pn and len(pn) >= 3 and any(c.isdigit() for c in pn)

def deterministic_issues(ex: Extraction, known_makes: set[str]) -> list[str]:
    issues = []
    if not _EO_RE.match(ex.eo_number or ""):
        issues.append("bad_eo_number")
    years = [y for r in ex.fitment for y in (r.year_start, r.year_end) if y is not None]
    if any(y < 1950 or y > 2035 for y in years) or any(
            r.year_start and r.year_end and r.year_start > r.year_end for r in ex.fitment):
        issues.append("bad_year")
    if any(r.displacement_l is not None and not 0.5 <= r.displacement_l <= 9.0 for r in ex.fitment):
        issues.append("bad_displacement")
    if any(not _pn_ok(p) for p in ex.part_numbers):
        issues.append("bad_part_number")
    if any(r.make and r.make.casefold() not in known_makes for r in ex.fitment):
        issues.append("unknown_make")
    keys = [(r.model, r.year_start, r.year_end, r.displacement_l) for r in ex.fitment]
    if len(keys) != len(set(keys)):
        issues.append("duplicate_fitment")
    if ex.category is None:
        issues.append("no_category")
    return issues

def legacy_divergence(ex: Extraction, legacy: dict | None) -> float:
    if legacy is None:
        return 0.0
    a, b = set(ex.part_numbers), set(legacy.get("part_numbers") or [])
    pn_sim = 1.0 if not a and not b else (len(a & b) / len(a | b) if (a | b) else 1.0)
    ca, cb = len(ex.fitment), int(legacy.get("fitment_count") or 0)
    cnt_sim = 1.0 if ca == cb == 0 else min(ca, cb) / max(ca, cb) if max(ca, cb) else 1.0
    return round(((1 - pn_sim) + (1 - cnt_sim)) / 2, 3)

def needs_critique(issues, divergence, confidence, qa_rate, rand: float) -> bool:
    return bool(issues) or divergence > 0.4 or confidence < settings.confidence_threshold or rand < qa_rate

def apply_corrections(ex: Extraction, corrections: dict) -> Extraction:
    return Extraction.model_validate(ex.model_dump() | (corrections or {}))

def _accept(repo, eo, ex, run_id):
    repo.upsert_eo(eo, {"state": "matching", "device_name": ex.device_name,
                        "manufacturer": ex.manufacturer, "category": ex.category,
                        "part_numbers": ex.part_numbers, "supersedes": ex.supersedes,
                        "confidence": ex.confidence, "audited_at": time.time()})
    for old in ex.supersedes:
        if repo.get_eo(old):
            repo.upsert_eo(old, {"state": "superseded", "superseded_by": eo})
    repo.add_event(run_id, {"agent": "auditor", "eo": eo, "action": "accepted"})

def _escalate(repo, eo, ex, reason, notes, run_id):
    repo.add_review({"eo_number": eo, "reason": reason, "agent_notes": notes,
                     "payload": ex.model_dump()})
    repo.upsert_eo(eo, {"state": "needs_review"})
    repo.add_event(run_id, {"agent": "auditor", "eo": eo, "action": "escalated", "reason": reason})

def audit(llm, repo, budget, eo, ex, known_makes, run_id, rand=None) -> str:
    rand = random.random() if rand is None else rand
    issues = deterministic_issues(ex, known_makes)
    div = legacy_divergence(ex, repo.get_legacy(eo))
    if not needs_critique(issues, div, ex.confidence, settings.critique_qa_rate, rand):
        _accept(repo, eo, ex, run_id)
        return "accepted"
    reason = ("validation_failure" if issues else
              "low_confidence" if ex.confidence < settings.confidence_threshold else
              "legacy_divergence" if div > 0.4 else "qa_sample")
    uri = repo.get_eo(eo)["gcs_uri"]
    res = llm.extract_pdf(uri, CRITIC_PROMPT + json.dumps(ex.model_dump()), CritiqueVerdict)
    usd = cost_usd(res.tok_in, res.tok_out)
    budget.add(usd)
    repo.add_run_cost(run_id, usd, res.tok_in, res.tok_out)
    try:
        verdict = CritiqueVerdict.model_validate(res.data)
    except ValidationError:
        _escalate(repo, eo, ex, "validation_failure", "critique output failed validation", run_id)
        return "escalated"
    if verdict.verdict == "fix":
        try:
            fixed = apply_corrections(ex, verdict.corrections)
        except ValidationError:
            _escalate(repo, eo, ex, "validation_failure",
                      "; ".join(verdict.reasons) + " (corrections failed validation)", run_id)
            return "escalated"
        if not deterministic_issues(fixed, known_makes):
            _accept(repo, eo, fixed, run_id)
            return "accepted"
        _escalate(repo, eo, fixed, "validation_failure",
                  "; ".join(verdict.reasons), run_id)
        return "escalated"
    if verdict.verdict == "accept":
        _accept(repo, eo, ex, run_id)
        return "accepted"
    _escalate(repo, eo, ex, reason, "; ".join(verdict.reasons), run_id)
    return "escalated"
