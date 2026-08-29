// Per-event detail suffix for the live agent console feed — a short, dimmer
// annotation appended after an event's main line, mapped by `action`. Pure:
// no I/O, no React, so it's unit-testable in isolation (consoleDetail.test.ts).
//
// Field names/values here mirror exactly what the pipeline's agents write via
// repo.add_event() (pipeline/agents/extractor.py, scout.py, healer.py,
// auditor.py, matchmaker.py) and what api/feed.ts passes through unchanged.
export interface EventExtras {
  ladder_step?: number;
  confidence?: number;
  count?: number;
  gemini_resolved?: number;
  reason?: string;
  error?: string;
  rung?: number;
}

// The extraction ladder only ever has two rungs (pipeline/agents/extractor.py:
// rung 1 = direct PDF extraction, rung 2 = page-image fallback after rung 1
// fails/truncates) — an out-of-range value still renders, just without a label.
const RUNG_LABEL: Record<number, string> = {
  1: 'native PDF',
  2: 'image fallback',
};

/**
 * Returns the detail suffix text for one console line, or '' when the action
 * is unrecognized or its required extras are missing — callers should render
 * nothing in that case (never the literal string "undefined").
 */
export function eventDetailSuffix(action: string, extras: EventExtras): string {
  switch (action) {
    case 'reading': {
      const { rung } = extras;
      return typeof rung === 'number' ? `reading PDF (rung ${rung})…` : '';
    }
    case 'critiquing':
      return 'second-opinion critique…';
    case 'resolving': {
      const { count } = extras;
      return typeof count === 'number' ? `resolving ${count} ambiguous matches via Gemini…` : '';
    }
    case 'extracted': {
      const { ladder_step, confidence } = extras;
      if (typeof ladder_step !== 'number' || typeof confidence !== 'number') return '';
      const label = RUNG_LABEL[ladder_step] ?? `rung ${ladder_step}`;
      return `rung ${ladder_step} · ${label} · conf ${confidence.toFixed(2)}`;
    }
    case 'escalated': {
      const { reason } = extras;
      return reason ? `→ human review (${reason})` : '';
    }
    case 'matched': {
      const { count, gemini_resolved } = extras;
      if (typeof count !== 'number') return '';
      const base = `${count} vehicles`;
      return typeof gemini_resolved === 'number' && gemini_resolved > 0
        ? `${base}, ${gemini_resolved} resolved by Gemini`
        : base;
    }
    case 'requeued_transient':
      return 'forgave transient failure';
    case 'heal_limit_reached':
      return 'parked for human (3 strikes)';
    case 'discover_failed': {
      const { error } = extras;
      return error ? `download failed: ${error}` : '';
    }
    case 'failed_both_rungs':
      return 'extraction failed on both rungs';
    case 'resolver_output_invalid':
      return 'resolver output rejected';
    case 'resolver_hallucinated_ids':
      return 'hallucinated ids filtered';
    default:
      return '';
  }
}
