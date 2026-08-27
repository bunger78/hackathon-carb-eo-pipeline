import re
from collections import defaultdict

_WORD_BOUNDARY = re.compile(r"[ \-/]")

def model_matches(fitment_model: str, vehicle_model: str) -> bool:
    a, b = (fitment_model or "").casefold().strip(), (vehicle_model or "").casefold().strip()
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not long_.startswith(short):
        return False
    rest = long_[len(short):]
    return rest == "" or bool(_WORD_BOUNDARY.match(rest))

def _engine_tier(row, v) -> str | None:
    if row.displacement_l is None:
        return "generic"
    vd = v.get("displacement_l")
    if vd is None or abs(vd - row.displacement_l) > 0.15:
        return None
    tier = "medium"
    if row.induction and v.get("induction") == row.induction:
        tier = "high"
        if row.cylinders and v.get("cylinders") == row.cylinders:
            tier = "exact"
    return tier

class VehicleIndex:
    def __init__(self, vehicles: list[dict]):
        self.by_make = defaultdict(list)
        for v in vehicles:
            self.by_make[(v["make"] or "").casefold()].append(v)

    def candidates(self, row) -> list[dict]:
        vs = self.by_make.get((row.make or "").casefold(), [])
        lo = row.year_start or 0
        hi = row.year_end or 9999
        return [v for v in vs if lo <= v["year"] <= hi]

def match_row(row, index: VehicleIndex) -> list[tuple[dict, str]]:
    out = []
    for v in index.candidates(row):
        if not model_matches(row.model or "", v["model"] or ""):
            continue
        tier = _engine_tier(row, v)
        if tier:
            out.append((v, tier))
    return out
