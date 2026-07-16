import { describe, expect, it } from 'vitest';
import { sanitizeMarkdown } from '../src/provider/copilot-web/markdown-sanitizer.js';

describe('sanitizeMarkdown', () => {
  it('removes a standalone greeting and follow-up line', () => {
    expect(sanitizeMarkdown(
      'Sure, here is the document:\n# Title\n\nLet me know if you need anything else.',
    )).toBe('# Title');
  });

  it('unwraps a single outer markdown fence', () => {
    expect(sanitizeMarkdown('```markdown\n# Title\n```')).toBe('# Title');
  });

  it('does not remove legitimate content beginning with Here', () => {
    expect(sanitizeMarkdown('Here Maps integration')).toBe('Here Maps integration');
  });

  it('preserves multiple fenced code blocks', () => {
    const value = '```markdown\nfirst\n```\n\n```markdown\nsecond\n```';
    expect(sanitizeMarkdown(value)).toBe(value);
  });
});
