import { describe, expect, it } from 'vitest';
import { MockProvider } from '../src/provider/mock.js';

describe('MockProvider', () => {
  it('replays scripted responses in order and records sent messages', async () => {
    const provider = new MockProvider(['first', 'second']);
    const session = await provider.createSession();
    const chunks: string[] = [];
    const first = await session.send('hello', { onUpdate: (chunk) => chunks.push(chunk) });
    const second = await session.send('again');
    expect(first).toEqual({ text: 'first' });
    expect(second.text).toBe('second');
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

  it('records the seeded system prompt', async () => {
    const provider = new MockProvider(['ok']);
    await provider.createSession({ systemPrompt: 'persona' });
    expect(provider.systemPrompts).toEqual(['persona']);
  });
});
