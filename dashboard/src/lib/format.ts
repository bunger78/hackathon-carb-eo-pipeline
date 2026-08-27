// Pure formatting helpers. No I/O, no Firestore.

const TIMEZONE = 'America/Los_Angeles';

/** Format a USD amount as "$X.XX". Non-finite/missing input formats as "$0.00". */
export function formatCost(usd: number | null | undefined): string {
  const n = typeof usd === 'number' && isFinite(usd) ? usd : 0;
  return `$${n.toFixed(2)}`;
}

/** Format an integer count with thousands separators. Non-finite/missing input formats as "0". */
export function formatCount(n: number | null | undefined): string {
  const v = typeof n === 'number' && isFinite(n) ? n : 0;
  return v.toLocaleString('en-US');
}

/**
 * Format a Unix timestamp in seconds (as produced by Python's time.time()) in the
 * America/Los_Angeles timezone. Returns an em dash for missing/invalid input.
 */
export function formatTimestamp(tsSeconds: number | null | undefined): string {
  if (typeof tsSeconds !== 'number' || !isFinite(tsSeconds)) return '—';
  const d = new Date(tsSeconds * 1000);
  return new Intl.DateTimeFormat('en-US', {
    timeZone: TIMEZONE,
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d);
}
