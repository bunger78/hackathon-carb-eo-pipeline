import { describe, expect, it } from 'vitest';
import { groupEventsByEo } from './timeline';

describe('groupEventsByEo', () => {
  it('groups events by eo (preserving first-seen order) and computes elapsed deltas within each group', () => {
    const events = [
      { ts: 100, agent: 'scout', action: 'discover', eo: 'EO-1' },
      { ts: 101, agent: 'scout', action: 'discover', eo: 'EO-2' },
      { ts: 110, agent: 'extractor', action: 'extract', eo: 'EO-1' },
      { ts: 125, agent: 'auditor', action: 'audit', eo: 'EO-1' },
    ];

    const result = groupEventsByEo(events);

    expect(result.map((t) => t.eo)).toEqual(['EO-1', 'EO-2']);
    expect(result[0].steps).toEqual([
      { ts: 100, agent: 'scout', action: 'discover', elapsedSec: null },
      { ts: 110, agent: 'extractor', action: 'extract', elapsedSec: 10 },
      { ts: 125, agent: 'auditor', action: 'audit', elapsedSec: 15 },
    ]);
    expect(result[1].steps).toEqual([{ ts: 101, agent: 'scout', action: 'discover', elapsedSec: null }]);
  });

  it('groups an event with no eo field under an "(unknown)" bucket instead of dropping it or crashing', () => {
    const events = [{ ts: 5, agent: 'scout', action: 'discover' }];

    const result = groupEventsByEo(events);

    expect(result).toEqual([{ eo: '(unknown)', steps: [{ ts: 5, agent: 'scout', action: 'discover', elapsedSec: null }] }]);
  });

  it('treats a missing ts as breaking the elapsed-delta chain rather than producing a false delta', () => {
    const events = [
      { agent: 'scout', action: 'discover', eo: 'EO-1' }, // no ts
      { ts: 200, agent: 'extractor', action: 'extract', eo: 'EO-1' },
    ];

    const result = groupEventsByEo(events);

    expect(result[0].steps[0].elapsedSec).toBeNull();
    expect(result[0].steps[1].elapsedSec).toBeNull();
  });

  it('returns an empty array for an empty event list (zero-event run)', () => {
    expect(groupEventsByEo([])).toEqual([]);
  });
});
