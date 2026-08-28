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

// Shared Intl.DateTimeFormat setup (timezone + 24-hour clock) for both
// timestamp formatters below; each supplies only the date/time fields it needs.
function localTimeFormatter(fields: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat('en-US', { timeZone: TIMEZONE, hour12: false, ...fields });
}

/**
 * Format a Unix timestamp in seconds (as produced by Python's time.time()) in the
 * America/Los_Angeles timezone. Returns an em dash for missing/invalid input.
 */
export function formatTimestamp(tsSeconds: number | null | undefined): string {
  if (typeof tsSeconds !== 'number' || !isFinite(tsSeconds)) return '—';
  const d = new Date(tsSeconds * 1000);
  return localTimeFormatter({
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(d);
}

/**
 * Format a Unix timestamp in seconds as HH:MM:SS (24-hour, America/Los_Angeles).
 * Used by the live agent console feed. Returns '--:--:--' for missing/invalid input.
 */
export function formatEventTime(tsSeconds: number | null | undefined): string {
  if (typeof tsSeconds !== 'number' || !isFinite(tsSeconds)) return '--:--:--';
  const d = new Date(tsSeconds * 1000);
  return localTimeFormatter({ hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(d);
}

/**
 * Format the age of a Unix timestamp in seconds (as produced by Python's
 * time.time()) as a short relative string: "just now", "5m ago", "3h ago",
 * "2d ago". Used by the review queue index's "age"/"resolved" columns.
 * `nowSeconds` defaults to the current time but is overridable for tests.
 * Returns an em dash for missing/invalid input.
 */
export function formatAge(tsSeconds: number | null | undefined, nowSeconds: number = Date.now() / 1000): string {
  if (typeof tsSeconds !== 'number' || !isFinite(tsSeconds)) return '—';
  const deltaSec = Math.max(0, nowSeconds - tsSeconds);
  if (deltaSec < 60) return 'just now';
  const mins = Math.floor(deltaSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * Format a non-negative elapsed duration in seconds as "+Ns" (under a minute)
 * or "+Mm SSs". Used by the run detail page's per-EO timelines to show the gap
 * between one stage's event and the next. Returns an em dash for missing,
 * non-finite, or negative input (e.g. a timeline's first step has no prior
 * stage to measure from).
 */
export function formatElapsed(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !isFinite(seconds) || seconds < 0) return '—';
  const total = Math.round(seconds);
  if (total < 60) return `+${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `+${m}m ${String(s).padStart(2, '0')}s`;
}
