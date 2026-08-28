PROMPT_VERSION = "v2"

EXTRACTOR_PROMPT = """You are extracting structured data from a California Air Resources Board (CARB) Executive Order (EO) PDF that certifies an aftermarket vehicle part or part family.

Rules:
- eo_number: exactly as printed (e.g. "D-269-30").
- supersedes: EO numbers this order supersedes/cancels (look for "supersedes", "cancels"); empty list if none.
- category: one of intake, boost, cat, engine, exhaust, ignition, tune, other. cat = catalytic converters.
- fitment: one row per vehicle-application line in the document's tables or prose.
  CRITICAL: when the document associates specific part numbers with specific vehicle rows, put those part numbers in THAT row's part_numbers. Do not pool them.
  CRITICAL - TABLE COMPLETENESS: enumerate EVERY application/fitment row in every table and appendix. These tables often continue across many pages -- keep reading and extracting to the final page of the document. Never summarize, sample, or stop early, even if the table is long or repetitive. If the document states a total row count (e.g. "142 applications listed"), your extracted fitment row count MUST match it exactly.
  CRITICAL - ONE MODEL PER ROW: a single line listing multiple models or a model range (e.g. "B150, B250, B350" or "C10-C30") must be split into one fitment row PER individual model. Never keep multiple models bundled into one row.
  CRITICAL - RESTRICTIONS: when a row or section carries a restriction or limitation clause (e.g. "approval limited to Kit K8625", or restricted to specific part numbers/configurations), capture that restriction in the row's description and reflect the referenced kit/part numbers in that row's part_numbers.
- part_numbers (top level): every part number appearing anywhere in the document.
- Years: expand ranges ("1996-2000" -> year_start 1996, year_end 2000; single year -> both equal).
- displacement_l: numeric liters (e.g. "5.7L" -> 5.7). induction: NA (naturally aspirated), TURBO, SC (supercharged). Leave null when not stated.
- confidence: your honest overall confidence 0-1. sections_confidence: per-section (metadata, fitment, part_numbers).
- illegible_pages: 1-based page numbers you could not read reliably.
- Do not invent data. Null/empty beats guessed.
Return ONLY the JSON object."""
