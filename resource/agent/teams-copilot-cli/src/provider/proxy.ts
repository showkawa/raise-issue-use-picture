import type { ProxyConfig } from '../types.js';
import type {
  ChatSession,
  ChatTurnOptions,
  ChatTurnResult,
  CreateSessionOptions,
  Provider,
  ProviderCapabilities,
} from './types.js';

interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface ChatCompletionChoice {
  message?: { content?: string };
  delta?: { content?: string };
  finish_reason?: string | null;
}

interface ChatCompletionResponse {
  choices?: ChatCompletionChoice[];
}

/**
 * Talks to teams-copilot-proxy's OpenAI-compatible `/v1/chat/completions`.
 * The stateless `m365-copilot` model requires the full conversation to be
 * replayed on every request, so each session keeps its own message history
 * and sends it in full. No `tools` field is ever sent.
 */
export class ProxyProvider implements Provider {
  readonly id = 'proxy';

  constructor(private readonly config: ProxyConfig) {}

  async init(): Promise<void> {}

  async createSession(options: CreateSessionOptions = {}): Promise<ChatSession> {
    const history: ChatMessage[] = [];
    if (options.systemPrompt) {
      history.push({ role: 'system', content: options.systemPrompt });
    }
    const config = this.config;
    return {
      async send(message: string, turnOptions: ChatTurnOptions = {}): Promise<ChatTurnResult> {
        history.push({ role: 'user', content: message });
        const started = Date.now();
        const { text, truncated } = await postChat(config, history, turnOptions);
        history.push({ role: 'assistant', content: text });
        return { text, truncated, duration: Date.now() - started };
      },
      async healthy(): Promise<boolean> {
        return true;
      },
      async close(): Promise<void> {},
    };
  }

  async close(): Promise<void> {}

  capabilities(): ProviderCapabilities {
    return {
      maxMessageChars: 1_000_000,
      supportsStreaming: true,
      supportsSystemPrompt: true,
    };
  }
}

async function postChat(
  config: ProxyConfig,
  messages: ChatMessage[],
  options: ChatTurnOptions,
): Promise<{ text: string; truncated: boolean }> {
  const url = `${config.baseUrl.replace(/\/+$/, '')}/chat/completions`;
  const stream = Boolean(options.onUpdate);
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? config.timeoutMs;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.apiKey}`,
      },
      body: JSON.stringify({ model: config.model, messages, stream }),
      signal: controller.signal,
    });
  } catch (error: unknown) {
    clearTimeout(timer);
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error(`Proxy request timed out after ${timeoutMs}ms at ${url}`);
    }
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Failed to reach proxy at ${url}: ${detail}. Is teams-copilot-proxy running?`,
    );
  }

  try {
    if (!response.ok) {
      const body = (await response.text().catch(() => '')).slice(0, 500);
      throw new Error(`Proxy request failed (${response.status} ${response.statusText}): ${body}`);
    }
    if (stream && response.body) {
      return await readStream(response.body, options.onUpdate);
    }
    const json = (await response.json()) as ChatCompletionResponse;
    const choice = json.choices?.[0];
    const text = choice?.message?.content ?? '';
    return { text, truncated: choice?.finish_reason === 'length' };
  } finally {
    clearTimeout(timer);
  }
}

async function readStream(
  body: ReadableStream<Uint8Array>,
  onUpdate?: (chunk: string) => void,
): Promise<{ text: string; truncated: boolean }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let full = '';
  let truncated = false;

  const handleLine = (line: string): void => {
    const trimmed = line.trim();
    if (!trimmed.startsWith('data:')) return;
    const data = trimmed.slice(5).trim();
    if (data === '' || data === '[DONE]') return;
    let json: ChatCompletionResponse;
    try {
      json = JSON.parse(data) as ChatCompletionResponse;
    } catch {
      return;
    }
    const choice = json.choices?.[0];
    const delta = choice?.delta?.content ?? '';
    if (delta) {
      full += delta;
      onUpdate?.(delta);
    }
    if (choice?.finish_reason === 'length') truncated = true;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) handleLine(line);
  }
  if (buffer) handleLine(buffer);
  return { text: full, truncated };
}
