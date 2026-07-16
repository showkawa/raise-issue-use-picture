import { describe, expect, it, vi } from 'vitest';
import { injectText } from '../src/provider/copilot-web/text-injector.js';

function createFrame(evaluateResults: unknown[], insertTextError?: Error) {
  const keyboard = {
    press: vi.fn(async () => undefined),
    insertText: vi.fn(async () => {
      if (insertTextError) throw insertTextError;
    }),
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
  it('uses keyboard insertText for the M365 Lexical editor', async () => {
    const { frame } = createFrame([true]);
    await expect(injectText(frame, 'hello', '.input')).resolves.toEqual({
      success: true,
      method: 'insertText',
    });
  });

  it('falls back to a clipboard event when insertText fails', async () => {
    const { frame } = createFrame([true], new Error('insertText failed'));
    await expect(injectText(frame, 'hello', '.input')).resolves.toEqual({
      success: true,
      method: 'clipboard',
    });
  });

  it('reports failure when neither method changes the editor', async () => {
    const { frame } = createFrame([false, false]);
    await expect(injectText(frame, 'hello', '.input')).resolves.toEqual({
      success: false,
      method: 'clipboard',
      error: 'Text verification failed',
    });
  });
});
