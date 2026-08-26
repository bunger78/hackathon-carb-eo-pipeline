PROMPT_VERSION = "v1"

EXTRACTOR_PROMPT = """You are extracting structured data from a California Air Resources Board (CARB) Executive Order (EO) PDF that certifies an aftermarket vehicle part or part family.

Rules:
- eo_number: exactly as printed (e.g. "D-269-30").
- supersedes: EO numbers this order supersedes/cancels (look for "supersedes", "cancels"); empty list if none.
- category: one of intake, boost, cat, engine, exhaust, ignition, tune, other. cat = catalytic converters.
- fitment: one row per vehicle-application line in the document's tables or prose.
  CRITICAL: when the document associates specific part numbers with specific vehicle rows, put those part numbers in THAT row's part_numbers. Do not pool them.
- part_numbers (top level): every part number appearing anywhere in the document.
- Years: expand ranges ("1996-2000" -> year_start 1996, year_end 2000; single year -> both equal).
- displacement_l: numeric liters (e.g. "5.7L" -> 5.7). induction: NA (naturally aspirated), TURBO, SC (supercharged). Leave null when not stated.
- confidence: your honest overall confidence 0-1. sections_confidence: per-section (metadata, fitment, part_numbers).
- illegible_pages: 1-based page numbers you could not read reliably.
- Do not invent data. Null/empty beats guessed.
Return ONLY the JSON object."""
