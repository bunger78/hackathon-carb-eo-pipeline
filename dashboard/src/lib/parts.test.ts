import { describe, expect, it } from 'vitest';
import { categoryKey, categoryLabel, groupByCategory, sortCategoryKeys, tierBadge, tierLabel, type PartLike } from './parts';

const UNCATEGORIZED = categoryKey(null);

describe('sortCategoryKeys', () => {
  it('orders known categories in the fixed sensible order regardless of input order', () => {
    expect(sortCategoryKeys(['other', 'cat', 'intake', 'exhaust'])).toEqual(['intake', 'exhaust', 'cat', 'other']);
  });

  it('appends categories outside the fixed list alphabetically, after the known ones', () => {
    expect(sortCategoryKeys(['zzz-custom', 'intake', 'aaa-custom'])).toEqual(['intake', 'aaa-custom', 'zzz-custom']);
  });

  it('always places a missing/null category last, after known and unknown categories', () => {
    const keys = sortCategoryKeys(['other', UNCATEGORIZED, 'intake', 'zzz-custom']);
    expect(keys).toEqual(['intake', 'other', 'zzz-custom', UNCATEGORIZED]);
  });
});

describe('categoryLabel', () => {
  it('title-cases a known category', () => {
    expect(categoryLabel('intake')).toBe('Intake');
  });

  it('labels the uncategorized sentinel as "Uncategorized"', () => {
    expect(categoryLabel(UNCATEGORIZED)).toBe('Uncategorized');
  });
});

describe('groupByCategory', () => {
  const parts: PartLike[] = [
    { eo_number: 'D-1', category: 'exhaust', device_name: 'Header B', manufacturer: 'Acme', part_numbers: ['2'], tier: 'high' },
    { eo_number: 'D-2', category: 'exhaust', device_name: 'Header A', manufacturer: 'Acme', part_numbers: ['1'], tier: 'exact' },
    { eo_number: 'D-3', category: 'intake', device_name: 'Intake kit', manufacturer: 'Acme', part_numbers: ['3'], tier: 'medium' },
    { eo_number: 'D-4', category: null, device_name: 'Mystery part', manufacturer: 'Acme', part_numbers: [], tier: 'generic' },
  ];

  it('groups by category in fixed display order, uncategorized last', () => {
    const sections = groupByCategory(parts);
    expect(sections.map((s) => s.label)).toEqual(['Intake', 'Exhaust', 'Uncategorized']);
  });

  it('sorts rows within a category by device name', () => {
    const sections = groupByCategory(parts);
    const exhaust = sections.find((s) => s.label === 'Exhaust')!;
    expect(exhaust.parts.map((p) => p.device_name)).toEqual(['Header A', 'Header B']);
  });
});

describe('tierBadge', () => {
  it('maps the strongest deterministic tiers to green', () => {
    expect(tierBadge('exact')).toBe('green');
    expect(tierBadge('high')).toBe('green');
  });

  it('maps medium to amber', () => {
    expect(tierBadge('medium')).toBe('amber');
  });

  it('maps generic (including every gemini-resolved match) to red', () => {
    expect(tierBadge('generic')).toBe('red');
  });

  it('falls back to red for an unrecognized tier', () => {
    expect(tierBadge('unknown-tier')).toBe('red');
  });
});

describe('tierLabel', () => {
  it('labels each known tier', () => {
    expect(tierLabel('exact')).toBe('Exact match');
    expect(tierLabel('generic')).toBe('Generic fit');
  });

  it('falls back to the raw tier string', () => {
    expect(tierLabel('mystery')).toBe('mystery');
  });
});
