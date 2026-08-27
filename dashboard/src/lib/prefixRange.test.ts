import { describe, expect, it } from 'vitest';
import { prefixRange } from './prefixRange';

const SENTINEL = String.fromCharCode(0xf8ff);

describe('prefixRange', () => {
  it('sets gte to the bare prefix and lt to the prefix plus the U+F8FF sentinel', () => {
    const { gte, lt } = prefixRange('backfill');
    expect(gte).toBe('backfill');
    expect(lt).toBe(`backfill${SENTINEL}`);
  });

  it('the bare prefix and prefixed values fall inside the range', () => {
    const { gte, lt } = prefixRange('backfill');
    expect('backfill' >= gte && 'backfill' < lt).toBe(true);
    expect('backfill-worker-3' >= gte && 'backfill-worker-3' < lt).toBe(true);
  });

  it('an unrelated trigger value falls outside the range', () => {
    const { gte, lt } = prefixRange('backfill');
    expect('scheduled' >= gte && 'scheduled' < lt).toBe(false);
  });
});
