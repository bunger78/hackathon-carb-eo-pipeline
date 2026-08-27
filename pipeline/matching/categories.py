"""
Classify a part's device_name into one of eight user-facing category slugs.
"""

# (slug, keywords) — first match wins, case-insensitive substring search
_RULES: list[tuple[str, list[str]]] = [
    ("intake",  ["air intake", "intake kit", "intake system", "cold air",
                 "short ram", "filtercharger", "air filter"]),
    ("boost",   ["supercharger", "turbocharger", "turbo kit", "intercooler",
                 "boost controller"]),
    ("cat",     ["catalytic converter", "cat conv", "catalytic"]),
    ("exhaust", ["exhaust header", "header kit", "muffler", "exhaust system",
                 "exhaust pipe", "down pipe", "cat-back", "shorty header",
                 "shorty headers"]),
    ("tune",    ["programmer", "power programmer", "map kit", "calibration",
                 " ecu ", "tune kit"]),
    ("engine",  ["cylinder head", "rocker arm", "camshaft", "cubic inch",
                 "fuel injector", "injector", "throttle body", "carburetor",
                 "carb kit", "fuel system", "intake manifold"]),
    ("ignition",["ignition", "spark plug", "coil", "distributor", "ignitor"]),
]


def categorize(device_name: str) -> str:
    """Return category slug for a device_name. Falls back to 'other'."""
    lower = device_name.lower()
    for slug, keywords in _RULES:
        if any(kw in lower for kw in keywords):
            return slug
    return "other"
