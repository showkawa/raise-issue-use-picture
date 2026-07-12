import type { StreamResult } from '../types.js';
import type { Page } from 'playwright-core';

const BRIDGE_KEY = '__m365CopilotCliBridgeV1';

interface BridgeState {
  status: 'running' | 'completed' | 'failed';
  text: string;
  error?: string;
}

export async function installBrowserApiBridge(page: Page): Promise<boolean> {
  return page.evaluate((key) => {
    type ChatPayload = {
      arguments?: Array<{
        clientCorrelationId?: string;
        sessionId?: string;
        traceId?: string;
        isStartOfSession?: boolean;
        clientInfo?: {
          clientSessionId?: string;
        };
        message?: {
          requestId?: string;
          text?: string;
        };
      }>;
      invocationId?: string;
      target?: string;
      type?: number;
    };
    type Bridge = {
      installed: boolean;
      url?: string;
      template?: ChatPayload;
      requests: Record<string, BridgeState>;
      start?: (prompt: string, timeout: number) => string | null;
    };

    const root = window as unknown as Record<string, unknown>;
    const existing = root[key] as Bridge | undefined;
    if (existing?.installed) return Boolean(existing.url && existing.template);

    const bridge: Bridge = existing ?? {
      installed: true,
      requests: {},
    };
    root[key] = bridge;

    const separator = String.fromCharCode(0x1e);
    const nativeSend = WebSocket.prototype.send;
    WebSocket.prototype.send = function send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
      if (typeof data === 'string') {
        for (const part of data.split(separator).filter(Boolean)) {
          try {
            const payload = JSON.parse(part) as ChatPayload;
            if (payload.target === 'chat') {
              bridge.url = this.url;
              bridge.template = structuredClone(payload);
            }
          } catch {}
        }
      }
      nativeSend.call(this, data);
    };

    bridge.start = (prompt: string, timeout: number): string | null => {
      if (!bridge.url || !bridge.template) return null;
      const requestKey = crypto.randomUUID();
      const request: BridgeState = bridge.requests[requestKey] = {
        status: 'running',
        text: '',
      };
      const payload = structuredClone(bridge.template);
      const argument = payload.arguments?.[0];
      if (!argument?.message) {
        request.status = 'failed';
        request.error = 'Captured browser API template is invalid';
        return requestKey;
      }

      const sessionId = crypto.randomUUID();
      const requestId = crypto.randomUUID();
      const url = new URL(bridge.url);
      url.searchParams.set('chatsessionid', sessionId);
      url.searchParams.set('XRoutingParameterSessionKey', sessionId);
      url.searchParams.set('clientrequestid', sessionId);
      url.searchParams.set('X-SessionId', crypto.randomUUID());
      argument.clientCorrelationId = crypto.randomUUID();
      argument.sessionId = sessionId;
      argument.traceId = crypto.randomUUID();
      argument.isStartOfSession = false;
      argument.message.requestId = requestId;
      argument.message.text = prompt;
      if (argument.clientInfo) argument.clientInfo.clientSessionId = crypto.randomUUID();
      payload.invocationId = '0';

      const socket = new WebSocket(url);
      let invocationSent = false;
      let seenAssistantMessage = false;
      const timer = window.setTimeout(() => {
        request.status = 'failed';
        request.error = 'Browser API request timed out';
        socket.close();
      }, timeout);

      const finish = (status: BridgeState['status'], error?: string): void => {
        if (request.status !== 'running') return;
        window.clearTimeout(timer);
        request.status = status;
        request.error = error;
        socket.close();
      };

      const sendInvocation = (): void => {
        if (invocationSent) return;
        invocationSent = true;
        socket.send(`${JSON.stringify(payload)}${separator}`);
      };

      socket.onopen = () => {
        socket.send(`${JSON.stringify({ protocol: 'json', version: 1 })}${separator}`);
      };
      socket.onerror = () => finish('failed', 'Browser API WebSocket connection failed');
      socket.onclose = () => {
        if (request.status === 'running') {
          finish(request.text ? 'completed' : 'failed', request.text ? undefined : 'Browser API WebSocket closed without a response');
        }
      };
      socket.onmessage = (event) => {
        for (const part of String(event.data).split(separator).filter(Boolean)) {
          let message: {
            target?: string;
            arguments?: Array<{
              writeAtCursor?: string;
              isLastUpdate?: boolean;
              messages?: Array<{ author?: string; text?: string }>;
              patches?: Array<{ value?: unknown }>;
            }>;
          };
          try {
            message = JSON.parse(part);
          } catch {
            continue;
          }
          if (!invocationSent && !message.target) {
            sendInvocation();
            continue;
          }
          if (message.target !== 'update') continue;
          const update = message.arguments?.[0];
          if (!update) continue;
          const assistantMessage = update.messages?.find((candidate) =>
            candidate.author !== 'user' && typeof candidate.text === 'string');
          if (assistantMessage?.text) {
            seenAssistantMessage = true;
            request.text = assistantMessage.text;
          } else if (!seenAssistantMessage && typeof update.writeAtCursor === 'string') {
            request.text += update.writeAtCursor;
          } else if (!seenAssistantMessage && Array.isArray(update.patches)) {
            for (const patch of update.patches) {
              if (typeof patch.value === 'string') request.text += patch.value;
            }
          }
          if (update.isLastUpdate && request.text) finish('completed');
        }
      };
      return requestKey;
    };

    return Boolean(bridge.url && bridge.template);
  }, BRIDGE_KEY);
}

export async function askWithBrowserApi(
  page: Page,
  prompt: string,
  timeout: number,
  pollingInterval: number,
  onUpdate?: (chunk: string) => void,
): Promise<StreamResult | null> {
  await installBrowserApiBridge(page);
  const requestKey = await page.evaluate(
    ({ key, text, requestTimeout }) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as {
        start?: (value: string, timeout: number) => string | null;
      } | undefined;
      return bridge?.start?.(text, requestTimeout) ?? null;
    },
    { key: BRIDGE_KEY, text: prompt, requestTimeout: timeout },
  );
  if (!requestKey) return null;

  const start = Date.now();
  let emitted = '';
  while (Date.now() - start < timeout + pollingInterval) {
    const state = await page.evaluate(
      ({ key, id }) => {
        const bridge = (window as unknown as Record<string, unknown>)[key] as {
          requests?: Record<string, BridgeState>;
        } | undefined;
        return bridge?.requests?.[id] ?? null;
      },
      { key: BRIDGE_KEY, id: requestKey },
    );
    if (!state) throw new Error('Browser API request state was lost');
    if (onUpdate && state.text.startsWith(emitted) && state.text.length > emitted.length) {
      onUpdate(state.text.slice(emitted.length));
      emitted = state.text;
    }
    if (state.status === 'completed') {
      return {
        text: state.text,
        truncated: false,
        duration: Date.now() - start,
      };
    }
    if (state.status === 'failed') {
      throw Object.assign(new Error(state.error ?? 'Browser API request failed'), {
        code: 'BROWSER_API_REQUEST_FAILED',
      });
    }
    await page.waitForTimeout(pollingInterval);
  }
  throw Object.assign(new Error('Browser API request timed out'), {
    code: 'BROWSER_API_REQUEST_FAILED',
  });
}
