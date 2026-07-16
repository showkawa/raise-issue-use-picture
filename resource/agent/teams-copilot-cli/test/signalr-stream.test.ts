import { EventEmitter } from 'events';
import { describe, expect, it, vi } from 'vitest';
import { createSignalRStream } from '../src/provider/copilot-web/signalr-stream.js';
import type { CopilotConfig } from '../src/types.js';

const config: CopilotConfig = {
  copilotUrl: 'https://m365.cloud.microsoft/chat',
  requestMode: 'auto',
  responseMode: 'signalr',
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

function signalRFrame(value: unknown): { response: { opcode: number; payloadData: string } } {
  return {
    response: {
      opcode: 1,
      payloadData: `${JSON.stringify(value)}${String.fromCharCode(0x1e)}`,
    },
  };
}

describe('createSignalRStream', () => {
  it('returns the final assistant message from SignalR updates', async () => {
    const session = new EventEmitter() as EventEmitter & {
      send: ReturnType<typeof vi.fn>;
      detach: ReturnType<typeof vi.fn>;
    };
    session.send = vi.fn(async () => undefined);
    session.detach = vi.fn(async () => undefined);
    const page = {
      context: vi.fn(() => ({
        newCDPSession: vi.fn(async () => session),
      })),
    };
    const chunks: string[] = [];
    const stream = await createSignalRStream(
      page as unknown as Parameters<typeof createSignalRStream>[0],
      config,
      (chunk) => chunks.push(chunk),
    );

    session.emit('Network.webSocketFrameReceived', signalRFrame({
      type: 1,
      target: 'update',
      arguments: [{
        messages: [{ author: 'bot', text: 'M365_SIGNALR_OK' }],
        isLastUpdate: true,
      }],
    }));

    await expect(stream.wait()).resolves.toMatchObject({
      text: 'M365_SIGNALR_OK',
      truncated: false,
    });
    expect(chunks.join('')).toBe('M365_SIGNALR_OK');
    await stream.dispose();
    expect(session.detach).toHaveBeenCalled();
  });
});
