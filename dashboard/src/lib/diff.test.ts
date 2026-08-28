import { describe, expect, it } from 'vitest';
import { diffLegacyAgent } from './diff';

describe('diffLegacyAgent', () => {
  it('both present: flags changed fields and computes part-number set differences', () => {
    const legacy = {
      device_name: 'Turbo Kit',
      manufacturer: 'Acme',
      category: 'boost',
      part_numbers: ['P1', 'P2', 'P3'],
      fitment_count: 2,
    };
    const agent = {
      device_name: 'Turbo Kit V2',
      manufacturer: 'Acme',
      category: 'boost',
      part_numbers: ['P2', 'P3', 'P4'],
      fitment: [{}, {}, {}],
    };

    const result = diffLegacyAgent(legacy, agent);

    expect(result.fields).toEqual([
      { name: 'device_name', legacy: 'Turbo Kit', agent: 'Turbo Kit V2', changed: true },
      { name: 'manufacturer', legacy: 'Acme', agent: 'Acme', changed: false },
      { name: 'category', legacy: 'boost', agent: 'boost', changed: false },
    ]);
    expect(result.partNumbers).toEqual({ added: ['P4'], removed: ['P1'], kept: ['P2', 'P3'] });
    expect(result.fitmentCounts).toEqual({ legacy: 2, agent: 3, agentRowsWithPns: 0 });
  });

  it('legacy missing: legacy side is null for every field, agent side unaffected', () => {
    const agent = {
      device_name: 'Cat-Back Exhaust',
      manufacturer: 'Beta Corp',
      category: 'exhaust',
      part_numbers: ['X1'],
      fitment: [{}],
    };

    const result = diffLegacyAgent(null, agent);

    expect(result.fields).toEqual([
      { name: 'device_name', legacy: null, agent: 'Cat-Back Exhaust', changed: true },
      { name: 'manufacturer', legacy: null, agent: 'Beta Corp', changed: true },
      { name: 'category', legacy: null, agent: 'exhaust', changed: true },
    ]);
    expect(result.partNumbers).toEqual({ added: ['X1'], removed: [], kept: [] });
    expect(result.fitmentCounts).toEqual({ legacy: 0, agent: 1, agentRowsWithPns: 0 });
  });

  it('identical: no field changes and every part number is kept', () => {
    const legacy = {
      device_name: 'Intake System',
      manufacturer: 'Gamma LLC',
      category: 'intake',
      part_numbers: ['Q1', 'Q2'],
      fitment_count: 4,
    };
    const agent = {
      device_name: 'Intake System',
      manufacturer: 'Gamma LLC',
      category: 'intake',
      part_numbers: ['Q1', 'Q2'],
      fitment: [{}, {}, {}, {}],
    };

    const result = diffLegacyAgent(legacy, agent);

    expect(result.fields.every((f) => !f.changed)).toBe(true);
    expect(result.partNumbers).toEqual({ added: [], removed: [], kept: ['Q1', 'Q2'] });
    expect(result.fitmentCounts).toEqual({ legacy: 4, agent: 4, agentRowsWithPns: 0 });
  });

  it('agentRowsWithPns counts only fitment rows carrying at least one part number', () => {
    const agent = {
      fitment: [
        { part_numbers: ['A1', 'A2'] },
        { part_numbers: [] },
        { part_numbers: ['A3'] },
        {},
      ],
    };

    const result = diffLegacyAgent(null, agent);

    expect(result.fitmentCounts).toEqual({ legacy: 0, agent: 4, agentRowsWithPns: 2 });
  });
});
