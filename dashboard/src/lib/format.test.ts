import { describe, expect, it } from 'vitest';
import { formatElapsed } from './format';

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
