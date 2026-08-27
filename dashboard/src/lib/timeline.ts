// Groups a run's flat event list (as returned by db.ts's runWithEvents(),
// already ordered by ts ascending) into per-EO timelines, computing an
// elapsed-seconds delta from the previous event within each EO's own
// timeline. The run detail page renders each group as a
// scout -> extractor -> auditor -> matchmaker row sequence with these deltas.
import type { RunEvent } from './db';

export interface TimelineStep {
  ts?: number;
  agent?: string;
  action?: string;
  /** Seconds since the previous step in this EO's timeline. null for the first step, or whenever this step's or the previous step's ts is missing. */
  elapsedSec: number | null;
}

export interface EoTimeline {
  eo: string;
  steps: TimelineStep[];
}

// Events are expected to always carry an `eo` field (see events doc shape),
// but readers here never assume it — an event missing one is grouped under
// this bucket instead of being dropped or crashing the page.
const UNKNOWN_EO = '(unknown)';

/** `events` should already be ts-ascending (runWithEvents()'s contract); group order and each group's step order both follow input order. */
export function groupEventsByEo(events: RunEvent[]): EoTimeline[] {
  const order: string[] = [];
  const groups = new Map<string, RunEvent[]>();

  for (const e of events) {
    const eo = e.eo ?? UNKNOWN_EO;
    if (!groups.has(eo)) {
      groups.set(eo, []);
      order.push(eo);
    }
    groups.get(eo)!.push(e);
  }

  return order.map((eo) => {
    let prevTs: number | undefined;
    const steps = groups.get(eo)!.map((e): TimelineStep => {
      const ts = typeof e.ts === 'number' ? e.ts : undefined;
      const elapsedSec = ts !== undefined && prevTs !== undefined ? ts - prevTs : null;
      if (ts !== undefined) prevTs = ts;
      return { ts, agent: e.agent, action: e.action, elapsedSec };
    });
    return { eo, steps };
  });
}
