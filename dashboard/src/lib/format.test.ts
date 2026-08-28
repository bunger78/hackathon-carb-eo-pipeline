import { describe, expect, it } from 'vitest';
import { formatAge, formatElapsed } from './format';

describe('formatAge', () => {
  const now = 1_000_000;

  it('formats missing/invalid input as an em dash', () => {
    expect(formatAge(null, now)).toBe('—');
    expect(formatAge(undefined, now)).toBe('—');
    expect(formatAge(NaN, now)).toBe('—');
  });

  it('formats sub-minute and future/clock-skew deltas as "just now"', () => {
    expect(formatAge(now, now)).toBe('just now');
    expect(formatAge(now - 30, now)).toBe('just now');
    expect(formatAge(now + 5, now)).toBe('just now'); // future timestamp clamps to 0
  });

  it('formats minutes, hours, and days', () => {
    expect(formatAge(now - 5 * 60, now)).toBe('5m ago');
    expect(formatAge(now - 3 * 3600, now)).toBe('3h ago');
    expect(formatAge(now - 2 * 86400, now)).toBe('2d ago');
  });
});

describe('formatElapsed', () => {
  it('formats missing/invalid/negative input as an em dash', () => {
    expect(formatElapsed(null)).toBe('—');
    expect(formatElapsed(undefined)).toBe('—');
    expect(formatElapsed(NaN)).toBe('—');
    expect(formatElapsed(-5)).toBe('—');
  });

  it('formats sub-minute deltas as +Ns, rounding to the nearest second', () => {
    expect(formatElapsed(0)).toBe('+0s');
    expect(formatElapsed(12.4)).toBe('+12s');
  });

  it('formats minute-plus deltas as +Mm SSs, including when rounding crosses the minute boundary', () => {
    expect(formatElapsed(59.6)).toBe('+1m 00s');
    expect(formatElapsed(125)).toBe('+2m 05s');
  });
});
