import { describe, expect, it } from 'vitest';
import { StreamSentinelFilter } from '../src/five-whys/stream-filter.js';

const SENTINEL = '[[FIVE_WHYS_SUMMARY]]';

function run(chunks: string[]): string {
  const filter = new StreamSentinelFilter(SENTINEL);
  let out = chunks.map((c) => filter.push(c)).join('');
  out += filter.flush();
  return out;
}

describe('StreamSentinelFilter', () => {
  it('passes a normal question through unchanged', () => {
    expect(run(['Why did ', 'the build ', 'break?'])).toBe('Why did the build break?');
  });

  it('strips the sentinel line when it arrives in one chunk', () => {
    expect(run([`${SENTINEL}\nProblem: x\nRoot cause: y`])).toBe('Problem: x\nRoot cause: y');
  });

  it('strips the sentinel when split across many deltas', () => {
    expect(run(['[[', 'FIVE_WHYS', '_SUMMARY]]', '\nRoot cause: z'])).toBe('Root cause: z');
  });

  it('does not swallow content that merely resembles the sentinel', () => {
    expect(run(['[[NOT_IT]] still text'])).toBe('[[NOT_IT]] still text');
  });

  it('emits a lone first line via flush when no newline arrives', () => {
    expect(run(['just one line'])).toBe('just one line');
  });
});
