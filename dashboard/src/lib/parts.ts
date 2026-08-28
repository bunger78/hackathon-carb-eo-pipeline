// Pure helpers for the vehicle-parts results list: category display
// order/labels and confidence-badge tier mapping. No Firestore import (see
// vehicleCascade.ts's rationale) so this stays shareable with the
// browser-side VehiclePicker.tsx island.

// Fixed display order for known categories. pipeline/schemas/extraction.py's
// `Category` literal is intake/boost/cat/engine/exhaust/ignition/tune/other —
// the brief's sketch named different categories ("header"/"turbo") that don't
// exist in the actual schema; adapted here to the real vocabulary. "other" is
// the pipeline's explicit catch-all, so it's pinned last among known
// categories. Any category value absent from this list is appended after it,
// alphabetically; a missing/null category sorts last of all (see
// sortCategoryKeys below) — together this is the brief's "fixed order,
// other-alphabetical" rule.
const CATEGORY_ORDER = ['intake', 'boost', 'exhaust', 'cat', 'engine', 'ignition', 'tune', 'other'];

// Sentinel key for matches with no category recorded. Built via
// String.fromCharCode (see vehicleCascade.ts) so it stays plain ASCII in this
// file; can't collide with a real category string.
const UNCATEGORIZED = `${String.fromCharCode(0x0)}uncategorized`;

export function categoryKey(category: string | null | undefined): string {
  return category ? category : UNCATEGORIZED;
}

export function categoryLabel(key: string): string {
  if (key === UNCATEGORIZED) return 'Uncategorized';
  return key.charAt(0).toUpperCase() + key.slice(1);
}

// Fixed-order known categories first, then any remaining keys (not in
// CATEGORY_ORDER, and not the uncategorized sentinel) alphabetically, then
// the uncategorized bucket last of all.
export function sortCategoryKeys(keys: string[]): string[] {
  const present = new Set(keys);
  const known = CATEGORY_ORDER.filter((c) => present.has(c));
  const unknown = keys.filter((k) => k !== UNCATEGORIZED && !CATEGORY_ORDER.includes(k)).sort();
  const uncategorized = present.has(UNCATEGORIZED) ? [UNCATEGORIZED] : [];
  return [...known, ...unknown, ...uncategorized];
}

// Mirrors the fields of MatchDoc (lib/db.ts) that the parts list needs;
// redeclared here (rather than imported) to keep this module
// Firestore-import-free — see vehicleCascade.ts's rationale.
export interface PartLike {
  eo_number: string;
  category?: string | null;
  device_name?: string | null;
  manufacturer?: string | null;
  part_numbers: string[];
  tier: string;
}

export interface CategorySection<T extends PartLike> {
  key: string;
  label: string;
  parts: T[];
}

// Groups parts by category (fixed display order via sortCategoryKeys) and,
// within each category, sorts rows by device name then manufacturer for a
// stable, deterministic presentation — partsForVehicle()'s Firestore query
// has no orderBy, so row order is otherwise arbitrary.
export function groupByCategory<T extends PartLike>(parts: T[]): CategorySection<T>[] {
  const groups = new Map<string, T[]>();
  for (const p of parts) {
    const key = categoryKey(p.category);
    const list = groups.get(key);
    if (list) list.push(p);
    else groups.set(key, [p]);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => {
      const byDevice = (a.device_name ?? '').localeCompare(b.device_name ?? '');
      return byDevice !== 0 ? byDevice : (a.manufacturer ?? '').localeCompare(b.manufacturer ?? '');
    });
  }
  return sortCategoryKeys([...groups.keys()]).map((key) => ({ key, label: categoryLabel(key), parts: groups.get(key) as T[] }));
}

export type BadgeTier = 'green' | 'amber' | 'red';

// MatchDoc (lib/db.ts) carries no numeric confidence score — only the
// deterministic-match tier (exact > high > medium > generic; see
// pipeline/agents/matchmaker.py's _RANK), and every gemini-resolved match is
// written with tier "generic" too. That tier is the closest available proxy
// for the brief's requested >=0.9 green / >=0.75 amber / else red confidence
// badge: exact/high (the strongest deterministic fits) -> green, medium ->
// amber, generic (weakest deterministic fit, or any LLM-resolved match) -> red.
export function tierBadge(tier: string): BadgeTier {
  if (tier === 'exact' || tier === 'high') return 'green';
  if (tier === 'medium') return 'amber';
  return 'red';
}

const TIER_LABELS: Record<string, string> = {
  exact: 'Exact match',
  high: 'High confidence',
  medium: 'Medium confidence',
  generic: 'Generic fit',
};

export function tierLabel(tier: string): string {
  return TIER_LABELS[tier] ?? tier;
}
