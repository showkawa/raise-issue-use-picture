import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('fs', () => ({
  accessSync: vi.fn(),
  constants: { X_OK: 1 },
}));

import { accessSync } from 'fs';
import { findChromiumBrowser, getDefaultUserDataDir } from '../src/runtime/browser-finder.js';

describe('findChromiumBrowser', () => {
  const originalPlatform = process.platform;

  beforeEach(() => {
    vi.clearAllMocks();
    delete process.env.TEAMS_COPILOT_BROWSER;
    Object.defineProperty(process, 'platform', { value: 'win32' });
  });

  afterEach(() => {
    Object.defineProperty(process, 'platform', { value: originalPlatform });
  });

  it('returns env var path when set', () => {
    process.env.TEAMS_COPILOT_BROWSER = '/custom/chrome';
    vi.mocked(accessSync).mockReturnValue(undefined);
    expect(findChromiumBrowser()).toBe('/custom/chrome');
  });

  it('returns preferred path when env var not set', () => {
    vi.mocked(accessSync).mockReturnValue(undefined);
    expect(findChromiumBrowser('/preferred/edge')).toBe('/preferred/edge');
  });

  it('returns null when no browser found', () => {
    vi.mocked(accessSync).mockImplementation(() => { throw new Error('ENOENT'); });
    expect(findChromiumBrowser()).toBeNull();
  });
});

describe('getDefaultUserDataDir', () => {
  it('returns correct path', () => {
    const dir = getDefaultUserDataDir('edge');
    expect(dir).toContain('.teams-copilot');
    expect(dir).toContain('edge');
  });
});
