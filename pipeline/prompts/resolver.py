PROMPT_VERSION = "v1"

RESOLVER_PROMPT = """You match vehicle fitment lines from a CARB Executive Order to a canonical vehicle database.
For each fitment line, decide which candidate vehicle IDs it truly covers.
- Model names in EOs may embed trims ("Celica GT") or be malformed by PDF text runs ("Rangerand"). Reason carefully; do not match on coincidence.
- Empty vehicle_ids is correct when no candidate genuinely fits.
- rationale: one line. confidence: 0-1 for the line as a whole.
Return ONLY the JSON object.
"""
