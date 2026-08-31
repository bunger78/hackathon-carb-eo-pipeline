// Pure diff between a review's original extraction snapshot and the edited
// form state (ReviewEditor.tsx) — computes the minimal `corrections` object
// for POST /api/review-action. The pipeline's /admin/resolve-review
// shallow-merges corrections onto the extraction payload one top-level key at
// a time (pipeline/agents/reviewer.py: `{**payload, **corrections}`), so any
// changed key must be sent in full — there is no per-cell patch format, which
// is why a single edited fitment cell requires resubmitting the whole array.
//
// No @google-cloud/firestore import here (same reasoning as lib/vehicleCascade.ts's
// header comment): this runs inside ReviewEditor.tsx, a client-bundled island.

export interface FitmentRowValue {
  year_start: number | null;
  year_end: number | null;
  make: string | null;
  model: string | null;
  trim_note: string | null;
  displacement_l: number | null;
  induction: 'NA' | 'TURBO' | 'SC' | null;
  cylinders: number | null;
  part_numbers: string[];
}

export interface EditablePayload {
  manufacturer: string | null;
  device_name: string | null;
  description: string | null;
  category: string | null;
  issue_date: string | null;
  confidence: number;
  fitment: FitmentRowValue[];
}

const SCALAR_KEYS = ['manufacturer', 'device_name', 'description', 'category', 'issue_date', 'confidence'] as const;

function fitmentRowEqual(a: FitmentRowValue, b: FitmentRowValue): boolean {
  return (
    a.year_start === b.year_start &&
    a.year_end === b.year_end &&
    a.make === b.make &&
    a.model === b.model &&
    a.trim_note === b.trim_note &&
    a.displacement_l === b.displacement_l &&
    a.induction === b.induction &&
    a.cylinders === b.cylinders &&
    a.part_numbers.length === b.part_numbers.length &&
    a.part_numbers.every((pn, i) => pn === b.part_numbers[i])
  );
}

function fitmentEqual(a: FitmentRowValue[], b: FitmentRowValue[]): boolean {
  return a.length === b.length && a.every((row, i) => fitmentRowEqual(row, b[i]));
}

/**
 * Returns only the top-level fields that changed between `original` and
 * `edited`, or `null` if nothing changed (corrections are optional — see
 * lib/corrections.ts's parseCorrections, which treats empty input the same
 * way). `fitment` is all-or-nothing: if any row/cell differs, the full edited
 * array is included, matching the server's wholesale replace-by-key merge.
 */
export function buildCorrections(original: EditablePayload, edited: EditablePayload): Record<string, unknown> | null {
  const corrections: Record<string, unknown> = {};

  for (const key of SCALAR_KEYS) {
    if (original[key] !== edited[key]) {
      corrections[key] = edited[key];
    }
  }

  if (!fitmentEqual(original.fitment, edited.fitment)) {
    corrections.fitment = edited.fitment;
  }

  return Object.keys(corrections).length === 0 ? null : corrections;
}
