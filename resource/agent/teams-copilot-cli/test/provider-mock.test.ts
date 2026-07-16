import { describe, expect, it } from 'vitest';
import { MockProvider } from '../src/provider/mock.js';

describe('MockProvider', () => {
  it('replays scripted responses in order and records sent messages', async () => {
    const provider = new MockProvider(['first', { response: 'second', truncated: true }]);
    const session = await provider.createSession();
    const chunks: string[] = [];
    const first = await session.send('hello', { onUpdate: (chunk) => chunks.push(chunk) });
    const second = await session.send('again');
    expect(first).toEqual({ text: 'first', truncated: false, duration: 0 });
    expect(second.text).toBe('second');
    expect(second.truncated).toBe(true);
    expect(chunks).toEqual(['first']);
    expect(provider.sent).toEqual(['hello', 'again']);
  });

  it('throws when the script is exhausted', async () => {
    const provider = new MockProvider(['only']);
    const session = await provider.createSession();
    await session.send('one');
    await expect(session.send('two')).rejects.toThrow('script exhausted');
  });

  it('supports a dynamic responder', async () => {
    const provider = new MockProvider([], {
      respond: (message, index) => `echo:${index}:${message}`,
    });
    const session = await provider.createSession();
    const result = await session.send('ping');
    expect(result.text).toBe('echo:0:ping');
  });

  it('reports health and capability overrides', async () => {
    const provider = new MockProvider([], { capabilities: { maxMessageChars: 42 } });
    const session = await provider.createSession();
    expect(await session.healthy()).toBe(true);
    provider.markUnhealthy();
    expect(await session.healthy()).toBe(false);
    expect(provider.capabilities().maxMessageChars).toBe(42);
    expect(provider.capabilities().supportsSystemPrompt).toBe(false);
  });
});
