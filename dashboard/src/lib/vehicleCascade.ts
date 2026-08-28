// Pure helpers for the vehicle-search cascade (year -> make -> model -> trim ->
// engine). Deliberately free of any Firestore import so it can be shared by
// lib/db.ts (which builds the cascade from the cached full vehicle scan — see
// db.ts's vehicleCascade()) and VehiclePicker.tsx (a browser-side React
// island) without pulling @google-cloud/firestore into the client bundle —
// same reasoning as prefixRange.ts.

export interface VehicleEngineOption {
  vehicleId: string;
  label: string;
  displacement_l: number | null;
  induction: 'NA' | 'TURBO' | 'SC' | null;
  cylinders: number | null;
}

export interface VehicleCascade {
  years: number[];
  // Keyed by the plain string of the year (JSON object keys are always
  // strings anyway — see cascadeKey() for the multi-segment keys below).
  makesByYear: Record<string, string[]>;
  modelsByYearMake: Record<string, string[]>;
  trimsByYearMakeModel: Record<string, string[]>;
  enginesByYearMakeModelTrim: Record<string, VehicleEngineOption[]>;
}

// Built via String.fromCharCode rather than string-literal escapes so these
// sentinels are plain ASCII in this source file (greppable/diffable), the
// same tactic prefixRange.ts uses for its own sentinel.
const NUL = String.fromCharCode(0x0); // control char; can't appear in a real trim string
const UNIT_SEP = String.fromCharCode(0x1); // control char; can't appear in year/make/model/trim strings

// Sentinel trim value for vehicles with no trim in the source data (legacy
// `trim` can be null/empty — see pipeline/seed/seed_vehicles.py). Never
// collides with an actual trim string (e.g. "Base"). Rendered as "Base" by
// trimLabel() below.
export const NO_TRIM = NUL;

export function trimLabel(trim: string): string {
  return trim === NO_TRIM ? 'Base' : trim;
}

// Joins cascade path segments (year/make/model/trim) with a separator that
// can't appear in any of those strings, so two different paths can never
// collide (e.g. make "A" + model "B:C" vs. make "A:B" + model "C").
export function cascadeKey(...parts: (string | number)[]): string {
  return parts.join(UNIT_SEP);
}

const INDUCTION_LABELS: Record<string, string> = { NA: 'N/A', TURBO: 'Turbo', SC: 'Supercharged' };

// There's no separate "engine" field on a vehicle doc — "engine" here means
// the (displacement_l, induction, cylinders) triple carried directly on each
// VehicleDoc. Mirrors eos/[eo].astro's engineLabel() for a fitment row.
export function engineLabel(v: { displacement_l?: number | null; cylinders?: number | null; induction?: string | null }): string {
  const parts: string[] = [];
  if (v.displacement_l) parts.push(`${v.displacement_l}L`);
  if (v.cylinders) parts.push(`${v.cylinders}-cyl`);
  if (v.induction) parts.push(INDUCTION_LABELS[v.induction] ?? v.induction);
  return parts.length ? parts.join(' ') : 'Unspecified engine';
}
