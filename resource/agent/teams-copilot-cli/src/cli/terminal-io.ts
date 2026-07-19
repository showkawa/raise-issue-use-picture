import { createInterface, type Interface } from 'readline';
import type { AssistantTurnKind, FiveWhysIO } from '../five-whys/session.js';
import { SUMMARY_SENTINEL } from '../five-whys/prompt.js';
import { StreamSentinelFilter } from '../five-whys/stream-filter.js';

const STOP_WORDS = new Set(['/stop', '/quit', '/exit', 'q']);

/** Interactive terminal implementation of the 5 Whys IO seam. */
export class TerminalIO implements FiveWhysIO {
  onDelta?: (chunk: string) => void;
  private readonly rl: Interface;
  private readonly lineQueue: string[] = [];
  private readonly lineWaiters: Array<(line: string | null) => void> = [];
  private readonly filter = new StreamSentinelFilter(SUMMARY_SENTINEL);
  private closed = false;

  constructor(private readonly streaming: boolean) {
    if (streaming) {
      this.onDelta = (chunk) => process.stdout.write(this.filter.push(chunk));
    }
    this.rl = createInterface({ input: process.stdin, terminal: false });
    this.rl.on('line', (line) => {
      const waiter = this.lineWaiters.shift();
      if (waiter) waiter(line);
      else this.lineQueue.push(line);
    });
    this.rl.on('close', () => {
      this.closed = true;
      let waiter = this.lineWaiters.shift();
      while (waiter) {
        waiter(null);
        waiter = this.lineWaiters.shift();
      }
    });
  }

  onAssistant(text: string, kind: AssistantTurnKind): void {
    if (this.streaming) {
      process.stdout.write(this.filter.flush());
      process.stdout.write('\n');
      this.filter.reset();
    } else {
      if (kind === 'summary') process.stdout.write('\n');
      process.stdout.write(`${text}\n`);
    }
  }

  async readAnswer(): Promise<string | null> {
    for (;;) {
      process.stdout.write('\n> ');
      const line = await this.nextLine();
      if (line === null) return null;
      const trimmed = line.trim();
      if (STOP_WORDS.has(trimmed.toLowerCase())) return null;
      if (trimmed.length === 0) continue;
      return line;
    }
  }

  async confirmContinue(depth: number): Promise<boolean> {
    process.stdout.write(`\nReached ${depth} "why" levels. Go deeper? [y/N] `);
    const line = await this.nextLine();
    if (line === null) return false;
    return /^y(es)?$/i.test(line.trim());
  }

  close(): void {
    if (!this.closed) this.rl.close();
  }

  private nextLine(): Promise<string | null> {
    if (this.lineQueue.length > 0) return Promise.resolve(this.lineQueue.shift() as string);
    if (this.closed) return Promise.resolve(null);
    return new Promise((resolve) => this.lineWaiters.push(resolve));
  }
}
