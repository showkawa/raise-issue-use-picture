import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { writeFileSync, unlinkSync, existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TEST_CONFIG_PATH = join(__dirname, 'test-config.yaml');

describe('loadConfig', () => {
  beforeEach(() => {
    if (existsSync(TEST_CONFIG_PATH)) unlinkSync(TEST_CONFIG_PATH);
  });
  afterEach(() => {
    if (existsSync(TEST_CONFIG_PATH)) unlinkSync(TEST_CONFIG_PATH);
  });

  it('returns defaults when no config file exists', async () => {
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    const config = loadConfig('/nonexistent/path.yaml');
    expect(config.browser.port).toBe(9222);
  });

  it('parses a valid config.yaml', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'browser:\n  port: 9333\ncopilot:\n  copilotUrl: "https://test.example.com/copilot"\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    const config = loadConfig(TEST_CONFIG_PATH);
    expect(config.browser.port).toBe(9333);
    expect(config.copilot.copilotUrl).toBe('https://test.example.com/copilot');
    expect(config.copilot.requestMode).toBe('auto');
    expect(config.copilot.responseMode).toBe('auto');
  });

  it('parses a configured request mode', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'copilot:\n  requestMode: "browser-api"\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(loadConfig(TEST_CONFIG_PATH).copilot.requestMode).toBe('browser-api');
  });

  it('parses a configured response mode', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'copilot:\n  responseMode: "signalr"\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(loadConfig(TEST_CONFIG_PATH).copilot.responseMode).toBe('signalr');
  });

  it('uses defaults for an empty config file', async () => {
    writeFileSync(TEST_CONFIG_PATH, '');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(loadConfig(TEST_CONFIG_PATH).browser.port).toBe(9222);
  });

  it('parses legacy v1 config.yaml fields', async () => {
    writeFileSync(TEST_CONFIG_PATH, [
      'edge:',
      '  executablePath: "/custom/edge"',
      '  debuggingPort: 9555',
      '  userDataDir: "/tmp/profile"',
      'copilot:',
      '  url: "https://teams.example.com"',
      '  inputSelector: ".input"',
      '  sendButtonSelector: ".send"',
      '  messageSelector: ".message"',
      '  timeout: 12345',
      '',
    ].join('\n'));
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    const config = loadConfig(TEST_CONFIG_PATH);
    expect(config.browser.path).toBe('/custom/edge');
    expect(config.browser.port).toBe(9555);
    expect(config.browser.userDataDir).toBe('/tmp/profile');
    expect(config.copilot.copilotUrl).toBe('https://teams.example.com');
    expect(config.copilot.selectors.inputArea).toBe('.input');
    expect(config.copilot.selectors.sendButton).toBe('.send');
    expect(config.copilot.selectors.responseContainer).toBe('.message');
    expect(config.copilot.timeouts.streaming).toBe(12345);
  });

  it('throws when copilot section has empty required fields', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'browser:\n  port: 9333\ncopilot:\n  copilotUrl: ""\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(() => loadConfig(TEST_CONFIG_PATH)).toThrow('Missing required field');
  });

  it('rejects an invalid response mode', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'copilot:\n  responseMode: "api"\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(() => loadConfig(TEST_CONFIG_PATH)).toThrow('Invalid field: copilot.responseMode');
  });

  it('rejects an invalid request mode', async () => {
    writeFileSync(TEST_CONFIG_PATH, 'copilot:\n  requestMode: "api"\n');
    const { loadConfig } = await import('../src/provider/copilot-web/config.js');
    expect(() => loadConfig(TEST_CONFIG_PATH)).toThrow('Invalid field: copilot.requestMode');
  });
});

describe('mergeCliFlags', () => {
  it('overrides browser config with CLI flags', async () => {
    const { mergeCliFlags, loadConfig } = await import('../src/provider/copilot-web/config.js');
    const config = loadConfig('/nonexistent/path.yaml');
    const merged = mergeCliFlags(config, { port: 9444 });
    expect(merged.browser.port).toBe(9444);
  });

  it('rejects an invalid programmatic port override', async () => {
    const { mergeCliFlags, loadConfig } = await import('../src/provider/copilot-web/config.js');
    const config = loadConfig('/nonexistent/path.yaml');
    expect(() => mergeCliFlags(config, { port: Number.NaN })).toThrow('Invalid field');
  });
});
