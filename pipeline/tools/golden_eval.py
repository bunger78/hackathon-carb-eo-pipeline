"""Golden eval: compares agent extraction (Firestore latest) and legacy baseline
   against hand-verified golden/expected/{eo}.json."""
import json
import pathlib

from google.cloud import firestore
from pydantic import ValidationError

from agents.auditor import _EO_RE
from core.db import Repo
from schemas.extraction import Extraction

GOLD = pathlib.Path(__file__).resolve().parents[2] / "golden" / "expected"
REPORT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "golden-report.md"
MAX_RUN_READS = 50


def f1(a: set, b: set) -> float:
    if not a and not b: return 1.0
    if not a or not b: return 0.0
    p, r = len(a & b) / len(b), len(a & b) / len(a)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def row_key(r): return (r.get("year_start"), (r.get("model") or "").casefold())


def score(expected: dict, got: dict | None) -> dict:
    if not got:
        return {"scalar": 0.0, "pn_f1": 0.0, "assoc_f1": 0.0, "fit_delta": -len(expected.get("fitment", []))}
    scal = [expected.get(k) == got.get(k) for k in ("eo_number", "manufacturer", "category")]
    pn = f1(set(expected.get("part_numbers", [])), set(got.get("part_numbers", [])))
    e_rows = {row_key(r): set(r.get("part_numbers", [])) for r in expected.get("fitment", [])}
    g_rows = {row_key(r): set(r.get("part_numbers", [])) for r in got.get("fitment", [])}
    assoc = ([f1(e_rows[k], g_rows[k]) for k in e_rows if k in g_rows] or [0.0])
    return {"scalar": sum(scal) / len(scal), "pn_f1": round(pn, 3),
            "assoc_f1": round(sum(assoc) / len(assoc), 3),
            "fit_delta": len(got.get("fitment", [])) - len(expected.get("fitment", []))}


# --- checkers: deterministic, need no golden answer ---

def check_schema(payload: dict) -> bool:
    try:
        Extraction.model_validate(payload)
        return True
    except ValidationError:
        return False


def check_eo_format(eo_number: str | None) -> bool:
    return bool(_EO_RE.match(eo_number or ""))


def check_required_fields(payload: dict) -> bool:
    return (bool(payload.get("manufacturer")) and bool(payload.get("category"))
            and len(payload.get("part_numbers") or []) >= 1)


def run_checkers(payload: dict) -> dict:
    return {"schema": check_schema(payload), "eo_format": check_eo_format(payload.get("eo_number")),
            "required_fields": check_required_fields(payload)}


# --- trajectory: did the EO actually move extractor -> auditor -> matchmaker, and land on complete? ---

def trajectory_events(repo: Repo, eo: str, max_run_reads: int = MAX_RUN_READS) -> list[dict] | None:
    """Scan recent runs (newest first) for events tagged with this EO, capped at
    max_run_reads run documents. Returns None if no tagged events turned up in that budget."""
    runs = (repo.db.collection("runs").order_by("started_at", direction=firestore.Query.DESCENDING)
            .limit(max_run_reads).stream())
    events = []
    for run in runs:
        for snap in run.reference.collection("events").where("eo", "==", eo).stream():
            events.append(snap.to_dict())
    if not events:
        return None
    events.sort(key=lambda e: e.get("ts", 0))
    return events


def check_trajectory(repo: Repo, eo: str, eo_doc: dict | None) -> tuple[bool, str]:
    # "superseded" is a valid terminal state too: an EO reaches "complete" via matchmaker,
    # then a later EO's audit can overwrite it to "superseded" (see agents/auditor.py _accept).
    state = (eo_doc or {}).get("state")
    state_ok = state in ("complete", "superseded")
    events = trajectory_events(repo, eo)
    if events is None:
        return state_ok, f"state-only (state={state}), no events found"
    agents_seq = [e.get("agent") for e in events]
    try:
        i_e = agents_seq.index("extractor")
        i_a = agents_seq.index("auditor", i_e + 1)
        agents_seq.index("matchmaker", i_a + 1)
        order_ok = True
    except ValueError:
        order_ok = False
    if state_ok and order_ok:
        return True, "ok"
    reasons = [] if state_ok else [f"state={state}"]
    if not order_ok:
        reasons.append("missing/out-of-order stage")
    return False, "; ".join(reasons)


def main():
    repo = Repo()
    lines = ["| EO | src | scalar | PN F1 | assoc F1 | fit Δ | checks | trajectory |",
              "|---|---|---|---|---|---|---|---|"]
    agg = {"agent": [], "legacy": []}
    checker_totals = []
    trajectory_results = []
    for f in sorted(GOLD.glob("*.json")):
        eo = f.stem.upper()
        expected = json.loads(f.read_text())
        ext = [d.to_dict() for d in repo.db.collection("extractions")
               .where("eo_number", "==", eo).stream()]
        agent = max(ext, key=lambda d: d.get("created_at", 0))["payload"] if ext else None
        legacy = repo.get_legacy(eo)
        for src, got in (("agent", agent), ("legacy", legacy)):
            s = score(expected, got)
            agg[src].append(s)
            checks_col, traj_col = "-", "-"
            if src == "agent" and got:
                checks = run_checkers(got)
                n_pass = sum(checks.values())
                checker_totals.append(n_pass)
                traj_ok, traj_note = check_trajectory(repo, eo, repo.get_eo(eo))
                trajectory_results.append(traj_ok)
                checks_col = f"{n_pass}/3"
                traj_col = f"{'pass' if traj_ok else 'fail'} ({traj_note})"
            lines.append(f"| {eo} | {src} | {s['scalar']:.2f} | {s['pn_f1']} | {s['assoc_f1']} | "
                          f"{s['fit_delta']} | {checks_col} | {traj_col} |")
    for src in ("agent", "legacy"):
        ss = agg[src]
        if ss:
            lines.append(f"\n**{src} avg:** scalar {sum(x['scalar'] for x in ss)/len(ss):.2f}, "
                         f"PN F1 {sum(x['pn_f1'] for x in ss)/len(ss):.2f}, "
                         f"assoc F1 {sum(x['assoc_f1'] for x in ss)/len(ss):.2f}")
    if checker_totals:
        n = len(checker_totals)
        lines.append(f"\n**checker pass rate:** {sum(checker_totals)}/{3*n} "
                      f"({sum(checker_totals)/(3*n):.0%})")
    if trajectory_results:
        lines.append(f"\n**trajectory pass rate:** {sum(trajectory_results)}/{len(trajectory_results)}")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
