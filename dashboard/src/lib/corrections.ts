// Client-side validation for the review queue's optional "corrections" field
// (ReviewActions.tsx). Only checks that the top level is a JSON object —
// values MAY be nested objects/arrays (e.g. `{"fitment": [...]}` to replace a
// full fitment row array wholesale). The pipeline's /admin/resolve-review
// endpoint (Extraction.model_validate) is the real validator for field names
// and per-field value schemas, and reports those failures as its own 422 —
// this guard exists only to catch "not JSON" / "not an object" before a
// network round-trip.

export interface CorrectionsResult {
  value: Record<string, unknown> | null;
  error: string | null;
}

/** Empty/whitespace-only input parses to `{ value: null, error: null }` — corrections are optional. */
export function parseCorrections(text: string): CorrectionsResult {
  const trimmed = text.trim();
  if (!trimmed) return { value: null, error: null };

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { value: null, error: 'Corrections must be valid JSON.' };
  }

  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { value: null, error: 'Corrections must be a JSON object of field: value pairs.' };
  }

  return { value: parsed as Record<string, unknown>, error: null };
}
