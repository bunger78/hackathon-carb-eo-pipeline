import json, random, re, time
from pydantic import ValidationError
from schemas.extraction import Extraction, CritiqueVerdict
from prompts.critic import CRITIC_PROMPT
from core.costs import cost_usd
from config import settings

_EO_RE = re.compile(r"^[A-Z0-9]{1,3}(-[A-Z0-9]{1,6}){1,3}$")

# The vehicle reference table covers passenger cars/trucks. CARB also exempts
# parts for powersports vehicles -- real makes that legitimately match zero
# table rows and must not read as hallucinations.
_POWERSPORTS_MAKES = {
    "harley-davidson", "polaris", "can-am", "yamaha", "kawasaki", "suzuki",
    "ducati", "triumph", "aprilia", "victory", "indian", "ktm", "husqvarna",
    "arctic cat", "sea-doo", "ski-doo", "moto guzzi", "royal enfield",
}
# Naming variants of makes the table DOES know (the ported legacy
# normalization is not yet wired into the matcher; this is its minimal core).
_MAKE_ALIASES = {
    "vw": "volkswagen", "chevy": "chevrolet", "mercedes": "mercedes-benz",
    "mercedes benz": "mercedes-benz", "gm": "chevrolet", "landrover": "land rover",
    "mini cooper": "mini", "infinity": "infiniti", "roush": "ford",
    "daimlerchrysler": "chrysler", "harley": "harley-davidson",
    "cummins": "dodge",  # Cummins EOs apply to Dodge/Ram diesel applications
}

def _norm_make(s: str) -> str:
    # "Harley Davidson" / "HARLEY-DAVIDSON" / "harley  davidson" -> "harley davidson"
    return re.sub(r"[-\s]+", " ", s.casefold().strip())

def _make_known(make: str, known_makes: set[str]) -> bool:
    m = _norm_make(make)
    norm_known = {_norm_make(k) for k in known_makes}
    if m in norm_known or m in {_norm_make(k) for k in _POWERSPORTS_MAKES}:
        return True
    alias = _MAKE_ALIASES.get(m) or _MAKE_ALIASES.get(m.replace(" ", ""))
    if not alias:
        return False
    a = _norm_make(alias)
    return a in norm_known or a in {_norm_make(k) for k in _POWERSPORTS_MAKES}

def _pn_ok(pn: str) -> bool:
    # Real printed part numbers include spaced annotations ("300-221 MXP"),
    # digit-less kit names ("GO Kit", "ORA"), and 2-char codes ("3C") -- reject
    # only template/placeholder junk, not unfamiliar formats.
    s = pn.strip()
    return 2 <= len(s) <= 48 and not any(c in s for c in "<>_{}")

def dedupe_exact_rows(ex: Extraction) -> Extraction:
    """Drop fitment rows identical in EVERY field -- page-break table repeats
    faithfully extracted twice. Lossless by construction."""
    seen, rows = set(), []
    for r in ex.fitment:
        key = (r.model, r.make, r.year_start, r.year_end, r.displacement_l,
               r.induction, r.cylinders, r.trim_note, tuple(sorted(r.part_numbers or [])))
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    if len(rows) == len(ex.fitment):
        return ex
    return ex.model_copy(update={"fitment": rows})

def deterministic_issues(ex: Extraction, known_makes: set[str]) -> list[str]:
    issues = []
    if not _EO_RE.match(ex.eo_number or ""):
        issues.append("bad_eo_number")
    # Floor 1900: universal retrofit parts genuinely cover vintage vehicles
    # ("1928 and later" -- D-540). Inverted ranges are still impossible.
    years = [y for r in ex.fitment for y in (r.year_start, r.year_end) if y is not None]
    if any(y < 1900 or y > 2035 for y in years) or any(
            r.year_start and r.year_end and r.year_start > r.year_end for r in ex.fitment):
        issues.append("bad_year")
    # Floor covers 49cc scooters (0.049L); ceiling covers industrial/off-road
    # engines CARB certifies (Cat 3512E well-service V12 = 58.6L) with margin.
    if any(r.displacement_l is not None and not 0.04 <= r.displacement_l <= 120.0 for r in ex.fitment):
        issues.append("bad_displacement")
    if any(not _pn_ok(p) for p in ex.part_numbers):
        issues.append("bad_part_number")
    # NOTE: an unfamiliar make is NOT a gate issue. CARB regulates everything
    # with an engine (heavy trucks, motorcycles, industrial); our vehicle table
    # covers passenger cars. Unmatched makes simply produce zero vehicle
    # matches -- a coverage fact, not extraction doubt. (_make_known remains
    # for the matching layer.)
    # Exact-duplicate rows are removed losslessly by dedupe_exact_rows before
    # this gate runs, so no separate duplicate check remains: rows that differ
    # in ANY field (make, cylinders, trim, PN set...) are legitimate variants.
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

def _update_extraction_payload(repo, eo, ex):
    """Persist an auditor-corrected extraction into the latest stored
    extraction doc (written by extract() before audit ran), so the extraction
    view and downstream readers see the corrected payload, not the pre-audit
    original."""
    version = repo.next_extraction_version(eo) - 1
    if version < 1:
        return
    doc = repo.get_extraction(eo, version)
    if doc is None:
        return
    repo.write_extraction(eo, version, {**doc, "payload": ex.model_dump()})

def audit(llm, repo, budget, eo, ex, known_makes, run_id, rand=None) -> tuple[str, Extraction]:
    rand = random.random() if rand is None else rand
    # The document's identity IS a datapoint: this extraction was made FOR `eo`
    # (registry-keyed, filename-fetched). A garbled model read of the header
    # never outranks that -- correct it and note the discrepancy.
    if ex.eo_number != eo:
        repo.add_event(run_id, {"agent": "auditor", "eo": eo,
                                "action": "eo_number_corrected_from_identity",
                                "model_read": ex.eo_number})
        ex = ex.model_copy(update={"eo_number": eo})
        _update_extraction_payload(repo, eo, ex)
    deduped = dedupe_exact_rows(ex)
    if deduped is not ex:
        _update_extraction_payload(repo, eo, deduped)
        repo.add_event(run_id, {"agent": "auditor", "eo": eo, "action": "deduped_rows",
                                "removed": len(ex.fitment) - len(deduped.fitment)})
        ex = deduped
    issues = deterministic_issues(ex, known_makes)
    div = legacy_divergence(ex, repo.get_legacy(eo))
    if not needs_critique(issues, div, ex.confidence, settings.critique_qa_rate, rand):
        _accept(repo, eo, ex, run_id)
        return "accepted", ex
    reason = ("validation_failure" if issues else
              "low_confidence" if ex.confidence < settings.confidence_threshold else
              "legacy_divergence" if div > 0.4 else "qa_sample")
    uri = repo.get_eo(eo)["gcs_uri"]
    repo.add_event(run_id, {"agent": "auditor", "eo": eo, "action": "critiquing"})
    res = llm.extract_pdf(uri, CRITIC_PROMPT + json.dumps(ex.model_dump()), CritiqueVerdict)
    usd = cost_usd(res.tok_in, res.tok_out)
    repo.add_run_cost(run_id, usd, res.tok_in, res.tok_out)
    budget.add(usd)
    try:
        verdict = CritiqueVerdict.model_validate(res.data)
    except ValidationError:
        _escalate(repo, eo, ex, "validation_failure", "critique output failed validation", run_id)
        return "escalated", ex
    if verdict.verdict == "fix":
        try:
            fixed = apply_corrections(ex, verdict.corrections)
        except ValidationError:
            _escalate(repo, eo, ex, "validation_failure",
                      "; ".join(verdict.reasons) + " (corrections failed validation)", run_id)
            return "escalated", ex
        if not deterministic_issues(fixed, known_makes):
            _update_extraction_payload(repo, eo, fixed)
            _accept(repo, eo, fixed, run_id)
            return "accepted", fixed
        _escalate(repo, eo, fixed, "validation_failure",
                  "; ".join(verdict.reasons), run_id)
        return "escalated", fixed
    if verdict.verdict == "accept":
        # A critic "accept" is an AI opinion, not a fixed rule -- it must never
        # override a deterministic validation failure. Only cases the
        # deterministic checks already passed reach an LLM-accepted outcome.
        if issues:
            # Name the tripped rules first — the reviewer must see WHY the
            # gate escalated, not only the critic's (possibly positive) notes.
            _escalate(repo, eo, ex, reason,
                      f"gate: {', '.join(issues)}; critic: " + "; ".join(verdict.reasons), run_id)
            return "escalated", ex
        _accept(repo, eo, ex, run_id)
        return "accepted", ex
    _escalate(repo, eo, ex, reason, "; ".join(verdict.reasons), run_id)
    return "escalated", ex
