import { describe, expect, it, vi } from 'vitest';
import { askWithBrowserApi } from '../src/runtime/browser-api-bridge.js';

describe('askWithBrowserApi', () => {
  it('returns null when the page has no captured request template', async () => {
    const page = {
      evaluate: vi.fn()
        .mockResolvedValueOnce(false)
        .mockResolvedValueOnce(null),
    };

    await expect(askWithBrowserApi(
      page as unknown as Parameters<typeof askWithBrowserApi>[0],
      'hello',
      1000,
      10,
    )).resolves.toBeNull();
  });

  it('returns a completed in-page API request', async () => {
    const page = {
      evaluate: vi.fn()
        .mockResolvedValueOnce(true)
        .mockResolvedValueOnce('request-id')
        .mockResolvedValueOnce({
          status: 'completed',
          text: 'M365_BROWSER_API_OK',
        }),
      waitForTimeout: vi.fn(async () => undefined),
    };
    const chunks: string[] = [];

    await expect(askWithBrowserApi(
      page as unknown as Parameters<typeof askWithBrowserApi>[0],
      'hello',
      1000,
      10,
      (chunk) => chunks.push(chunk),
    )).resolves.toMatchObject({
      text: 'M365_BROWSER_API_OK',
      truncated: false,
    });
    expect(chunks.join('')).toBe('M365_BROWSER_API_OK');
  });
});
