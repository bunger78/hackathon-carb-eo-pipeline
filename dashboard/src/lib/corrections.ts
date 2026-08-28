// Client-side validation for the review queue's optional "corrections" field
// (ReviewActions.tsx). Enforces a shallow {field: value} object — arrays and
// nested objects are rejected here with a friendly message; the pipeline's
// /admin/resolve-review endpoint enforces everything else (field names, value
// types/schema per-field) and reports those failures as its own 422.

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

  for (const v of Object.values(parsed as Record<string, unknown>)) {
    if (v !== null && typeof v === 'object') {
      return { value: null, error: 'Corrections must be shallow — no arrays or nested objects as values.' };
    }
  }

  return { value: parsed as Record<string, unknown>, error: null };
}
