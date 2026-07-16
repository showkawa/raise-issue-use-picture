import { describe, expect, it } from 'vitest';
import { mergeContinuation } from '../src/provider/copilot-web/continuation.js';

describe('mergeContinuation', () => {
  it('joins non-overlapping parts with a newline', () => {
    expect(mergeContinuation('first half', 'second half')).toBe('first half\nsecond half');
  });

  it('drops a re-emitted overlapping prefix', () => {
    const tail = 'const value = computeSomething(input);';
    const merged = mergeContinuation(`start\n${tail}`, `${tail}\nreturn value;`);
    expect(merged).toBe(`start\n${tail}\nreturn value;`);
  });

  it('prefers the longest overlap', () => {
    const base = 'aaaa needle needle needle';
    const merged = mergeContinuation(base, 'needle needle needle end');
    expect(merged).toBe('aaaa needle needle needle end');
  });

  it('ignores short accidental overlaps below the threshold', () => {
    const merged = mergeContinuation('ends with abc', 'abc but unrelated continuation');
    expect(merged).toBe('ends with abc\nabc but unrelated continuation');
  });

  it('handles empty sides', () => {
    expect(mergeContinuation('', 'next')).toBe('next');
    expect(mergeContinuation('prev', '  ')).toBe('prev');
  });
});
