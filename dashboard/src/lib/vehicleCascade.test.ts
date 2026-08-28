import { describe, expect, it } from 'vitest';
import { cascadeKey, engineLabel, NO_TRIM, trimLabel } from './vehicleCascade';

describe('cascadeKey', () => {
  it('joins segments so that different splits of the same characters never collide', () => {
    const a = cascadeKey('A', 'B:C');
    const b = cascadeKey('A:B', 'C');
    expect(a).not.toBe(b);
  });

  it('is stable for the same segments', () => {
    expect(cascadeKey(1998, 'Honda', 'Civic')).toBe(cascadeKey(1998, 'Honda', 'Civic'));
  });
});

describe('trimLabel', () => {
  it('renders the no-trim sentinel as "Base"', () => {
    expect(trimLabel(NO_TRIM)).toBe('Base');
  });

  it('passes real trim strings through unchanged', () => {
    expect(trimLabel('Si')).toBe('Si');
  });
});

describe('engineLabel', () => {
  it('formats displacement, cylinders, and induction together', () => {
    expect(engineLabel({ displacement_l: 2.0, cylinders: 4, induction: 'TURBO' })).toBe('2L 4-cyl Turbo');
  });

  it('omits missing fields', () => {
    expect(engineLabel({ displacement_l: 1.6, cylinders: null, induction: null })).toBe('1.6L');
  });

  it('falls back to a placeholder when nothing is known', () => {
    expect(engineLabel({})).toBe('Unspecified engine');
  });

  it('falls back to the raw induction string for an unrecognized value', () => {
    expect(engineLabel({ induction: 'DIESEL' })).toBe('DIESEL');
  });
});
