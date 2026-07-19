import { afterEach, describe, expect, it } from 'vitest';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'http';
import type { AddressInfo } from 'net';
import { ProxyProvider } from '../src/provider/proxy.js';
import type { ProxyConfig } from '../src/types.js';

interface CapturedRequest {
  body: Record<string, unknown>;
}

type Handler = (req: IncomingMessage, res: ServerResponse, body: Record<string, unknown>) => void;

let server: Server | undefined;

async function startServer(handler: Handler): Promise<{ baseUrl: string; captured: CapturedRequest[] }> {
  const captured: CapturedRequest[] = [];
  server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>;
      captured.push({ body });
      handler(req, res, body);
    });
  });
  await new Promise<void>((resolve) => server!.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return { baseUrl: `http://127.0.0.1:${port}/v1`, captured };
}

function config(baseUrl: string, overrides: Partial<ProxyConfig> = {}): ProxyConfig {
  return { baseUrl, model: 'm365-copilot', apiKey: 'unused', timeoutMs: 5000, ...overrides };
}

afterEach(async () => {
  if (server) {
    await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
  }
});

function jsonReply(content: string): string {
  return JSON.stringify({ choices: [{ message: { content } }] });
}

describe('ProxyProvider', () => {
  it('POSTs an OpenAI chat request with no tools and returns assistant text', async () => {
    const { baseUrl, captured } = await startServer((req, res, _body) => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(jsonReply('why did it fail?'));
    });
    const provider = new ProxyProvider(config(baseUrl));
    const session = await provider.createSession({ systemPrompt: 'You are a 5 Whys facilitator.' });
    const result = await session.send('the build broke');

    expect(result.text).toBe('why did it fail?');
    const { body } = captured[0];
    expect(body.model).toBe('m365-copilot');
    expect('tools' in body).toBe(false);
    expect(body.messages).toEqual([
      { role: 'system', content: 'You are a 5 Whys facilitator.' },
      { role: 'user', content: 'the build broke' },
    ]);
  });

  it('replays the full conversation history each turn', async () => {
    let turn = 0;
    const { baseUrl, captured } = await startServer((req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(jsonReply(`why-${turn++}`));
    });
    const provider = new ProxyProvider(config(baseUrl));
    const session = await provider.createSession({ systemPrompt: 'SYS' });
    await session.send('answer 1');
    await session.send('answer 2');

    expect(captured[1].body.messages).toEqual([
      { role: 'system', content: 'SYS' },
      { role: 'user', content: 'answer 1' },
      { role: 'assistant', content: 'why-0' },
      { role: 'user', content: 'answer 2' },
    ]);
  });

  it('streams deltas through onUpdate and requests stream mode', async () => {
    const { baseUrl, captured } = await startServer((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      const delta = (c: string) => `data: ${JSON.stringify({ choices: [{ delta: { content: c } }] })}\n\n`;
      res.write(delta('why '));
      res.write(delta('did '));
      res.write(delta('it fail?'));
      res.write('data: [DONE]\n\n');
      res.end();
    });
    const provider = new ProxyProvider(config(baseUrl));
    const session = await provider.createSession();
    const chunks: string[] = [];
    const result = await session.send('problem', { onUpdate: (c) => chunks.push(c) });

    expect(captured[0].body.stream).toBe(true);
    expect(chunks).toEqual(['why ', 'did ', 'it fail?']);
    expect(result.text).toBe('why did it fail?');
  });

  it('surfaces a clear error on upstream failure', async () => {
    const { baseUrl } = await startServer((req, res) => {
      res.writeHead(502, { 'Content-Type': 'text/plain' });
      res.end('bad gateway');
    });
    const provider = new ProxyProvider(config(baseUrl));
    const session = await provider.createSession();
    await expect(session.send('x')).rejects.toThrow(/Proxy request failed \(502/);
  });

  it('surfaces a clear error when the proxy is unreachable', async () => {
    const provider = new ProxyProvider(config('http://127.0.0.1:1/v1'));
    const session = await provider.createSession();
    await expect(session.send('x')).rejects.toThrow(/Failed to reach proxy/);
  });
});
