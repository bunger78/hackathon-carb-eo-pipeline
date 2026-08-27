import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Vehicle model name normalization
# ---------------------------------------------------------------------------

# FuelEconomy.gov appends drivetrain, body style, payload specs to model names.
# Strip them so "F150 Pickup 2WD" → "F-150" matches EO text "F-150".
_SUFFIX_PAT = re.compile(
    r"""
    \s+\b(?:
      2WD | 4WD | AWD | FWD | RWD | 4x4 |
      Pickup | Van | Wagon | Cab(?:\s+Chassis)? | Chassis |
      Convertible | Coupe | Sedan | Cabriolet | Roadster | Hatchback |
      FFV | Hybrid | Plug-in(?:\s+Hybrid)? | EV | Electric | CNG | Bifuel |
      TrailBoss | SFE | M6 |
      # full and abbreviated ton-class suffixes (Silverado 1500 / Silverado 15)
      1500 | 2500 | 3500 | HD | 15 | 25 | 35
    )\b
    (?:\s+.*)?$   # consume anything after the matched suffix word
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Trailing isolated digit(s) — PDF extraction artifacts like "F-150 5"
_TRAILING_DIGIT_PAT = re.compile(r"\s+\d{1,2}\s*$")

_GVWR_PAT   = re.compile(r"\s+(?:GVWR|PAYLOAD|BASE\s+PAYLOAD)\b.*$",        re.IGNORECASE)
_DISPL_PAT  = re.compile(r"\s+\d+\.\d+L\b.*$",                               re.IGNORECASE)
_PARENS_PAT = re.compile(r"\s*\(.*?\)\s*$")                                  # trailing (...)

# Word-level canonical renames applied after suffix stripping.
# Covers Ford/GM letter+number runs that dropped the hyphen.
_WORD_REMAP = {
    "F150": "F-150", "F250": "F-250", "F350": "F-350",
    "F450": "F-450", "F550": "F-550",
    "E150": "E-150", "E250": "E-250", "E350": "E-350", "E450": "E-450",
    "C1500": "C/K 1500", "K1500": "C/K 1500",
    "C2500": "C/K 2500", "K2500": "C/K 2500",
    "C3500": "C/K 3500", "K3500": "C/K 3500",
}


def normalize_vehicle_model(model: str) -> str:
    """
    Return a canonical model name suitable for cross-source matching.

    Applied to both FuelEconomy.gov vehicle rows and EO fitment rows so both
    sides of the match speak the same dialect.

    Examples:
      "F150 Pickup 2WD"                 → "F-150"
      "F150 2.7L 2WD GVWR>6649 LBS"    → "F-150"
      "Silverado 1500 2WD"              → "Silverado"
      "Mustang Convertible"             → "Mustang"
      "F-150"                           → "F-150"  (already canonical)
    """
    m = model.strip()
    m = _PARENS_PAT.sub("", m)     # strip trailing (...)
    m = _GVWR_PAT.sub("", m)       # strip GVWR/PAYLOAD specs
    m = _DISPL_PAT.sub("", m)      # strip displacement suffix ("2.7L ...")

    # Strip trailing isolated digit (PDF artifact: "F-150 5")
    m = _TRAILING_DIGIT_PAT.sub("", m)

    # Iteratively strip drivetrain / body-style / class suffixes
    prev = None
    while prev != m:
        prev = m
        m = _SUFFIX_PAT.sub("", m).strip()

    # Word-level remap (F150 → F-150 etc.)
    words = [_WORD_REMAP.get(w, w) for w in m.split()]
    return " ".join(words).strip()


def expand_slash_models(model: str) -> list[str]:
    """
    Expand slash-delimited multi-model strings into individual model names.

    "F-150/250/350"  → ["F-150", "F-250", "F-350"]   (numeric suffix shares prefix)
    "Civic / CRX"    → ["Civic", "CRX"]
    "Camaro/Firebird" → ["Camaro", "Firebird"]
    "F-150"           → ["F-150"]                      (no slash → pass-through)
    """
    if "/" not in model:
        return [normalize_vehicle_model(model)]

    parts = [p.strip() for p in model.split("/")]

    # Detect numeric-suffix sharing: "F-150" / "250" / "350"
    # If the first part ends with digits and later parts are purely digits,
    # strip the digits from the first part and prepend that prefix to each.
    first = parts[0]
    prefix_m = re.match(r"^(.*?)(\d+)$", first)
    if prefix_m:
        prefix = prefix_m.group(1)
        if all(re.match(r"^\d+$", p) for p in parts[1:]):
            # First part is already the full canonical name; expand the rest
            return [normalize_vehicle_model(parts[0])] + [
                normalize_vehicle_model(prefix + p) for p in parts[1:]
            ]

    return [normalize_vehicle_model(p) for p in parts]

def expand_slash_makes(make_str: str) -> list[str]:
    """
    Expand slash-delimited make strings into individual names.
    "Chev/Pontiac" → ["Chev", "Pontiac"]
    "Chevrolet"    → ["Chevrolet"]
    """
    if '/' not in make_str:
        return [make_str.strip()]
    return [p.strip() for p in make_str.split('/') if p.strip()]


CID_MAP = {
    283: 4.64, 302: 4.95, 305: 5.0,  307: 5.03,
    327: 5.36, 350: 5.74, 360: 5.9,  390: 6.39,
    400: 6.55, 427: 6.99, 454: 7.44, 460: 7.54,
    496: 8.13, 502: 8.23,
}

# Order matters: longer/more-specific phrases first
INDUCTION_PHRASES = [
    ("naturally aspirated", "na"),
    ("non-turbo",           "na"),
    ("supercharged",        "supercharged"),
    ("super charged",       "supercharged"),
    ("turbocharged",        "turbo"),
    ("turbo charged",       "turbo"),
    ("turbo",               "turbo"),
    ("fuel injected",       "fi"),
    ("fuel-injected",       "fi"),
    ("carburetted",         "carb"),
    ("carbureted",          "carb"),
    ("carburetor",          "carb"),
]


@dataclass
class EngineSpec:
    displacement_l: Optional[float]
    induction:      Optional[str]
    cylinders:      Optional[int]
    raw:            str = ""


def cid_to_liters(cid: int) -> float:
    return CID_MAP.get(cid, round(cid * 0.016387, 2))


def normalize_displacement(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.lower()

    # Skip generic descriptions
    if re.search(r'\bup\s+to\b|\ball\b|\bany\b', t):
        return None

    # Explicit liters first: "6.2L", "6.2 liter", "6.2-liter"
    m = re.search(r'(\d+\.?\d*)\s*-?\s*(?:l(?:iter)?s?)\b', t)
    if m:
        return float(m.group(1))

    # CID in parentheses: "(460 CID)"
    m = re.search(r'\((\d+)\s*cid\)', t)
    if m:
        return cid_to_liters(int(m.group(1)))

    # Standalone CID: "460 CID", "460-CID"
    m = re.search(r'\b(\d{3,4})\s*-?\s*cid\b', t)
    if m:
        return cid_to_liters(int(m.group(1)))

    return None


def normalize_induction(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.lower()
    for phrase, value in INDUCTION_PHRASES:
        if phrase in t:
            return value
    return None


def normalize_engine_spec(raw: str) -> EngineSpec:
    displacement = normalize_displacement(raw)
    induction    = normalize_induction(raw)

    cylinders = None
    # "8 cylinder", "8-cylinder", "8cyl"
    m = re.search(r'\b(\d+)\s*-?\s*cyl(?:inder)?s?\b', raw, re.IGNORECASE)
    if m:
        cylinders = int(m.group(1))
    # V8, V6, I4, I6
    if not cylinders:
        m = re.search(r'\b[Vv]-?(\d+)\b|\b[Ii]-?(\d+)\b', raw)
        if m:
            cylinders = int(m.group(1) or m.group(2))

    return EngineSpec(
        displacement_l=displacement,
        induction=induction,
        cylinders=cylinders,
        raw=raw,
    )
