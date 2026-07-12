import type { CopilotConfig, StreamResult } from '../types.js';
import type { Page } from 'playwright-core';

const SIGNALR_SEPARATOR = String.fromCharCode(0x1e);

interface SignalRUpdate {
  writeAtCursor?: string;
  isLastUpdate?: boolean;
  messages?: Array<{
    text?: string;
    author?: string;
  }>;
  patches?: Array<{
    value?: unknown;
  }>;
}

interface SignalRMessage {
  target?: string;
  arguments?: [SignalRUpdate?];
}

export interface SignalRStream {
  wait(): Promise<StreamResult>;
  dispose(): Promise<void>;
}

export async function createSignalRStream(
  page: Page,
  config: CopilotConfig,
  onUpdate?: (chunk: string) => void,
): Promise<SignalRStream> {
  const session = await page.context().newCDPSession(page);
  await session.send('Network.enable');

  const start = Date.now();
  let text = '';
  let emitted = '';
  let seenAssistantMessage = false;
  let settled = false;
  let finish: (result: StreamResult) => void = () => undefined;
  const result = new Promise<StreamResult>((resolve) => {
    finish = resolve;
  });

  function emit(nextText: string): void {
    if (!nextText || nextText === text) return;
    text = nextText;
    if (onUpdate && text.startsWith(emitted)) {
      onUpdate(text.slice(emitted.length));
      emitted = text;
    }
  }

  function complete(truncated = false): void {
    if (settled) return;
    settled = true;
    finish({ text, truncated, duration: Date.now() - start });
  }

  const timeout = setTimeout(() => complete(true), config.timeouts.streaming);

  const onFrame = (event: {
    response: {
      opcode: number;
      payloadData: string;
    };
  }): void => {
    if (settled || event.response.opcode !== 1) return;
    for (const part of event.response.payloadData.split(SIGNALR_SEPARATOR)) {
      if (!part) continue;
      let message: SignalRMessage;
      try {
        message = JSON.parse(part) as SignalRMessage;
      } catch {
        continue;
      }
      if (message.target !== 'update') continue;
      const update = message.arguments?.[0];
      if (!update) continue;

      const assistantMessage = update.messages?.find((candidate) =>
        candidate.author !== 'user' && typeof candidate.text === 'string');
      if (assistantMessage?.text) {
        seenAssistantMessage = true;
        emit(assistantMessage.text);
      } else if (!seenAssistantMessage && typeof update.writeAtCursor === 'string') {
        emit(`${text}${update.writeAtCursor}`);
      } else if (!seenAssistantMessage && Array.isArray(update.patches)) {
        for (const patch of update.patches) {
          if (typeof patch.value === 'string') emit(`${text}${patch.value}`);
        }
      }

      if (update.isLastUpdate && text) complete(false);
    }
  };

  session.on('Network.webSocketFrameReceived', onFrame);

  return {
    wait: async (): Promise<StreamResult> => {
      const final = await result;
      clearTimeout(timeout);
      if (!final.text) {
        throw Object.assign(new Error('No SignalR assistant response was captured'), {
          code: 'SIGNALR_RESPONSE_NOT_CAPTURED',
        });
      }
      return final;
    },
    dispose: async (): Promise<void> => {
      clearTimeout(timeout);
      session.off('Network.webSocketFrameReceived', onFrame);
      await session.detach().catch(() => undefined);
    },
  };
}
