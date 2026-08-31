import { describe, expect, it } from 'vitest';
import { buildCorrections, type EditablePayload, type FitmentRowValue } from './buildCorrections';

const baseFitmentRow: FitmentRowValue = {
  year_start: 2018,
  year_end: 2020,
  make: 'Toyota',
  model: 'Supra',
  trim_note: null,
  displacement_l: 3.0,
  induction: 'TURBO',
  cylinders: 6,
  part_numbers: ['A1'],
};

function payload(overrides: Partial<EditablePayload> = {}): EditablePayload {
  return {
    manufacturer: 'Acme',
    device_name: 'Turbo Kit',
    description: 'A turbo kit',
    category: 'boost',
    issue_date: '2020-01-01',
    confidence: 0.9,
    fitment: [baseFitmentRow],
    ...overrides,
  };
}

describe('buildCorrections', () => {
  it('returns null when nothing changed', () => {
    expect(buildCorrections(payload(), payload())).toBeNull();
  });

  it('returns only the one changed scalar field', () => {
    const edited = payload({ manufacturer: 'Acme Racing' });
    expect(buildCorrections(payload(), edited)).toEqual({ manufacturer: 'Acme Racing' });
  });

  it('returns every changed scalar field, and nothing else', () => {
    const edited = payload({ manufacturer: 'Acme Racing', confidence: 0.5 });
    expect(buildCorrections(payload(), edited)).toEqual({ manufacturer: 'Acme Racing', confidence: 0.5 });
  });

  it('clearing a nullable field to null counts as a change (empty -> null semantics)', () => {
    const edited = payload({ description: null });
    expect(buildCorrections(payload(), edited)).toEqual({ description: null });
  });

  it('leaves corrections null when an edit is a no-op (same value re-typed)', () => {
    const edited = payload({ manufacturer: 'Acme' });
    expect(buildCorrections(payload(), edited)).toBeNull();
  });

  it('includes the full fitment array, with proper types, when one cell changed', () => {
    const editedRow: FitmentRowValue = { ...baseFitmentRow, year_end: 2021 };
    const edited = payload({ fitment: [editedRow] });
    const result = buildCorrections(payload(), edited);
    expect(result).toEqual({ fitment: [editedRow] });
    expect((result!.fitment as FitmentRowValue[])[0].year_end).toBe(2021);
  });

  it('includes the full fitment array when a row is added, with null/array types preserved', () => {
    const newRow: FitmentRowValue = {
      year_start: 2015,
      year_end: null,
      make: null,
      model: null,
      trim_note: null,
      displacement_l: null,
      induction: null,
      cylinders: null,
      part_numbers: [],
    };
    const edited = payload({ fitment: [baseFitmentRow, newRow] });
    const result = buildCorrections(payload(), edited);
    expect(result).toEqual({ fitment: [baseFitmentRow, newRow] });
    const fitment = result!.fitment as FitmentRowValue[];
    expect(fitment[1].year_start).toBe(2015);
    expect(fitment[1].make).toBeNull();
    expect(fitment[1].part_numbers).toEqual([]);
  });

  it('includes the full fitment array when a row is removed', () => {
    const edited = payload({ fitment: [] });
    expect(buildCorrections(payload(), edited)).toEqual({ fitment: [] });
  });

  it('does not include fitment when both scalar and fitment fields are unchanged but re-typed', () => {
    const edited = payload({ fitment: [{ ...baseFitmentRow }] });
    expect(buildCorrections(payload(), edited)).toBeNull();
  });

  it('combines a changed scalar and a changed fitment array in one corrections object', () => {
    const editedRow: FitmentRowValue = { ...baseFitmentRow, part_numbers: ['A1', 'A2'] };
    const edited = payload({ category: 'engine', fitment: [editedRow] });
    expect(buildCorrections(payload(), edited)).toEqual({ category: 'engine', fitment: [editedRow] });
  });
});
