// Firestore "starts with" prefix-range bounds. Pure — no Firestore import — so
// it's unit-testable without an emulator/network.
//
// U+F8FF (0xf8ff, end of the Unicode private-use area) appended to `prefix`
// gives an exclusive upper bound that captures every string starting with
// `prefix` (e.g. "backfill", "backfill-worker-3") without also matching
// unrelated values that sort after it (e.g. "scheduled"). Built via
// String.fromCharCode rather than a string-literal escape so the sentinel is
// never an actual invisible character sitting in this source file — plain
// ASCII only, so it stays greppable/diffable/readable in any tool.
const PREFIX_RANGE_SENTINEL = String.fromCharCode(0xf8ff);

export function prefixRange(prefix: string): { gte: string; lt: string } {
  return { gte: prefix, lt: prefix + PREFIX_RANGE_SENTINEL };
}
