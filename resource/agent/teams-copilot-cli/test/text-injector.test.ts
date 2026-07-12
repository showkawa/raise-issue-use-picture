import { describe, expect, it, vi } from 'vitest';
import { injectText } from '../src/runtime/text-injector.js';

function createFrame(evaluateResults: unknown[]) {
  const keyboard = {
    press: vi.fn(async () => undefined),
    insertText: vi.fn(async () => undefined),
  };
  const locator = {
    first: vi.fn(() => locator),
    click: vi.fn(async () => undefined),
  };
  const evaluate = vi.fn(async () => evaluateResults.shift());
  return {
    frame: {
      locator: vi.fn(() => locator),
      page: vi.fn(() => ({ keyboard })),
      evaluate,
    } as unknown as Parameters<typeof injectText>[0],
    evaluate,
  };
}

describe('injectText', () => {
  it('reports clipboard verification success', async () => {
    const { frame } = createFrame([{ success: true }, true]);
    await expect(injectText(frame, 'hello', '.input')).resolves.toEqual({
      success: true,
      method: 'clipboard',
    });
  });

  it('reports failure when paste does not change the editor', async () => {
    const { frame } = createFrame([
      { success: false, error: 'Text did not appear after paste' },
    ]);
    await expect(injectText(frame, 'hello', '.input')).resolves.toEqual({
      success: false,
      method: 'clipboard',
      error: 'Text did not appear after paste',
    });
  });
});
