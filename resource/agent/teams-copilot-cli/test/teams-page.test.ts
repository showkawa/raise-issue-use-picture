import { describe, expect, it, vi } from 'vitest';
import { TeamsPage } from '../src/runtime/teams-page.js';
import type { CopilotConfig } from '../src/types.js';

const config: CopilotConfig = {
  teamsUrl: 'https://teams.example.com',
  copilotUrl: 'https://teams.example.com/copilot',
  selectors: {
    inputArea: '.input',
    sendButton: '.send',
    responseContainer: '.response',
    copilotEntry: '.copilot',
    loginIndicator: '.login',
  },
  timeouts: {
    pageLoad: 1000,
    copilotLoad: 1000,
    streaming: 1000,
    pollingInterval: 10,
  },
};

describe('TeamsPage', () => {
  it.each([
    ['https://teams.example.com', false],
    ['https://login.microsoftonline.com/common/oauth2/authorize', false],
    ['https://teams.example.com/v2/authv2#error=interaction_required', false],
    ['https://teams.example.com/v2/', true],
  ])('detects login state for %s', async (url, expected) => {
    const page = {
      url: vi.fn(() => url),
      waitForTimeout: vi.fn(async () => undefined),
      $: vi.fn(async () => null),
    };
    const teamsPage = new TeamsPage(
      page as unknown as ConstructorParameters<typeof TeamsPage>[0],
      config,
    );

    await expect(teamsPage.isLoggedIn()).resolves.toBe(expected);
  });

  it('selects an iframe containing both input and send controls', async () => {
    const mainFrame = {
      locator: vi.fn(() => ({ count: vi.fn(async () => 1) })),
    };
    const copilotFrame = {
      locator: vi.fn((selector: string) => ({
        count: vi.fn(async () => (selector === '.input' || selector === '.send') ? 1 : 0),
        first: vi.fn(() => ({ waitFor: vi.fn(async () => undefined) })),
      })),
    };
    const page = {
      frames: vi.fn(() => [mainFrame, copilotFrame]),
      mainFrame: vi.fn(() => mainFrame),
      waitForTimeout: vi.fn(async () => undefined),
      locator: vi.fn(() => ({
        first: vi.fn(() => ({ click: vi.fn(async () => undefined) })),
      })),
    };
    const teamsPage = new TeamsPage(
      page as unknown as ConstructorParameters<typeof TeamsPage>[0],
      config,
    );

    await teamsPage.navigateToCopilot();
    await expect(teamsPage.getCopilotFrame()).resolves.toBe(copilotFrame);
    expect(page.locator).not.toHaveBeenCalled();
  });
});
