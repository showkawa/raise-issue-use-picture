import { describe, expect, it, vi } from 'vitest';
import { isTruncated, readResponseText } from '../src/runtime/stream-extractor.js';

describe('isTruncated', () => {
  it('detects unclosed markdown fences', () => {
    expect(isTruncated('```ts\nconst value = 1;')).toBe(true);
  });

  it('detects obvious mid-clause endings', () => {
    expect(isTruncated('The next steps are:')).toBe(true);
  });

  it('does not require sentence punctuation for markdown lists', () => {
    expect(isTruncated('- task one\n- task two')).toBe(false);
  });
});

describe('readResponseText', () => {
  it('returns latest selected text and removes baseline', async () => {
    const frame = {
      evaluate: vi.fn(async (_fn, selector: string) => {
        expect(selector).toBe('.message');
        return 'old text new text';
      }),
    };
    const text = await readResponseText(
      frame as unknown as Parameters<typeof readResponseText>[0],
      '.message',
      'old text ',
    );
    expect(text).toBe('new text');
  });
});
