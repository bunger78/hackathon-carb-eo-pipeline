import { describe, expect, it } from 'vitest';
import { parseCorrections } from './corrections';

describe('parseCorrections', () => {
  it('treats empty/whitespace input as "no corrections" without an error', () => {
    expect(parseCorrections('')).toEqual({ value: null, error: null });
    expect(parseCorrections('   \n  ')).toEqual({ value: null, error: null });
  });

  it('accepts a shallow field:value object', () => {
    expect(parseCorrections('{"manufacturer": "Acme", "confidence": 0.9}')).toEqual({
      value: { manufacturer: 'Acme', confidence: 0.9 },
      error: null,
    });
  });

  it('accepts a nested array value (e.g. a full replacement fitment array)', () => {
    const fitment = [{ year_start: 2018, year_end: 2020, make: 'Toyota', model: 'Supra', part_numbers: ['A1'] }];
    expect(parseCorrections(JSON.stringify({ fitment }))).toEqual({
      value: { fitment },
      error: null,
    });
  });

  it('accepts a nested object value', () => {
    expect(parseCorrections('{"sections_confidence": {"fitment": 0.4}}')).toEqual({
      value: { sections_confidence: { fitment: 0.4 } },
      error: null,
    });
  });

  it('rejects invalid JSON', () => {
    const result = parseCorrections('{not json');
    expect(result.value).toBeNull();
    expect(result.error).toMatch(/valid JSON/);
  });

  it('rejects a top-level array', () => {
    const result = parseCorrections('["a", "b"]');
    expect(result.value).toBeNull();
    expect(result.error).toMatch(/JSON object/);
  });

  it('rejects a top-level primitive', () => {
    const result = parseCorrections('"just a string"');
    expect(result.value).toBeNull();
    expect(result.error).toMatch(/JSON object/);
  });

  it('accepts a null value (explicit field clear)', () => {
    expect(parseCorrections('{"notes": null}')).toEqual({ value: { notes: null }, error: null });
  });
});
