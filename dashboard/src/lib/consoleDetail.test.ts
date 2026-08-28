import { describe, expect, it } from 'vitest';
import { eventDetailSuffix } from './consoleDetail';

describe('eventDetailSuffix', () => {
  it('extracted rung 1: native PDF', () => {
    expect(eventDetailSuffix('extracted', { ladder_step: 1, confidence: 0.873 })).toBe('rung 1 · native PDF · conf 0.87');
  });

  it('extracted rung 2: image fallback', () => {
    expect(eventDetailSuffix('extracted', { ladder_step: 2, confidence: 0.5 })).toBe('rung 2 · image fallback · conf 0.50');
  });

  it('matched with gemini_resolved > 0 appends the resolved clause', () => {
    expect(eventDetailSuffix('matched', { count: 12, gemini_resolved: 3 })).toBe('12 vehicles, 3 resolved by Gemini');
  });

  it('matched with gemini_resolved 0 or absent omits the resolved clause', () => {
    expect(eventDetailSuffix('matched', { count: 12, gemini_resolved: 0 })).toBe('12 vehicles');
    expect(eventDetailSuffix('matched', { count: 12 })).toBe('12 vehicles');
  });

  it('escalated includes the reason; discover_failed includes the error', () => {
    expect(eventDetailSuffix('escalated', { reason: 'low_confidence' })).toBe('→ human review (low_confidence)');
    expect(eventDetailSuffix('discover_failed', { error: 'timeout' })).toBe('download failed: timeout');
  });

  it('static-text actions render regardless of (empty) extras', () => {
    expect(eventDetailSuffix('requeued_transient', {})).toBe('forgave transient failure');
    expect(eventDetailSuffix('heal_limit_reached', {})).toBe('parked for human (3 strikes)');
    expect(eventDetailSuffix('failed_both_rungs', {})).toBe('extraction failed on both rungs');
    expect(eventDetailSuffix('resolver_output_invalid', {})).toBe('resolver output rejected');
    expect(eventDetailSuffix('resolver_hallucinated_ids', {})).toBe('hallucinated ids filtered');
  });

  it('unknown action returns empty string', () => {
    expect(eventDetailSuffix('some_future_action', { count: 5 })).toBe('');
  });

  it('missing required fields returns empty string, never "undefined"', () => {
    expect(eventDetailSuffix('extracted', {})).toBe('');
    expect(eventDetailSuffix('extracted', { ladder_step: 1 })).toBe('');
    expect(eventDetailSuffix('matched', {})).toBe('');
    expect(eventDetailSuffix('escalated', {})).toBe('');
    expect(eventDetailSuffix('discover_failed', {})).toBe('');
  });
});
