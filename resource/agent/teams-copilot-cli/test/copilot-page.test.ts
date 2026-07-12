import { describe, expect, it, vi } from 'vitest';
import { CopilotPage } from '../src/runtime/copilot-page.js';
import type { CopilotConfig } from '../src/types.js';

const config: CopilotConfig = {
  copilotUrl: 'https://m365.cloud.microsoft/chat',
  selectors: {
    inputArea: '.input',
    sendButton: '.send',
    responseContainer: '.response',
    loginIndicator: '.login',
  },
  timeouts: {
    pageLoad: 1000,
    copilotLoad: 1000,
    streaming: 1000,
    pollingInterval: 10,
  },
};

describe('CopilotPage', () => {
  it.each([
    ['https://m365.cloud.microsoft/chat', true],
    ['https://m365.cloud.microsoft/chat/conversation/123', true],
    ['https://login.microsoftonline.com/common/oauth2/authorize', false],
    ['https://m365.cloud.microsoft/chat#error=interaction_required', false],
  ])('detects login state for %s', async (url, expected) => {
    const page = {
      url: vi.fn(() => url),
      waitForTimeout: vi.fn(async () => undefined),
      locator: vi.fn(() => ({
        first: vi.fn(() => ({ isVisible: vi.fn(async () => false) })),
      })),
    };
    const copilotPage = new CopilotPage(
      page as unknown as ConstructorParameters<typeof CopilotPage>[0],
      config,
    );

    await expect(copilotPage.isLoggedIn()).resolves.toBe(expected);
  });

  it('selects the main frame when it contains the chat input', async () => {
    const mainFrame = {
      locator: vi.fn(() => ({
        first: vi.fn(() => ({
          isVisible: vi.fn(async () => true),
          waitFor: vi.fn(async () => undefined),
        })),
      })),
    };
    const page = {
      frames: vi.fn(() => [mainFrame]),
      mainFrame: vi.fn(() => mainFrame),
      waitForTimeout: vi.fn(async () => undefined),
    };
    const copilotPage = new CopilotPage(
      page as unknown as ConstructorParameters<typeof CopilotPage>[0],
      config,
    );

    await expect(copilotPage.getChatFrame()).resolves.toBe(mainFrame);
  });
});
