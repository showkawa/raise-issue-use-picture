/**
 * Filters the summary sentinel out of a streamed assistant turn so the live
 * output stays clean. The sentinel, when present, is always the first line.
 */
export class StreamSentinelFilter {
  private buffer = '';
  private resolved = false;

  constructor(private readonly sentinel: string) {}

  /** Feed a streamed delta; returns the text that should be emitted. */
  push(chunk: string): string {
    if (this.resolved) return chunk;
    this.buffer += chunk;
    const newline = this.buffer.indexOf('\n');
    if (newline === -1) {
      const head = this.buffer.replace(/^\s+/, '');
      if (head.length > 0 && !this.sentinel.startsWith(head)) {
        return this.flushBuffer();
      }
      return '';
    }
    const firstLine = this.buffer.slice(0, newline);
    const rest = this.buffer.slice(newline + 1);
    this.buffer = '';
    this.resolved = true;
    return firstLine.trim() === this.sentinel ? rest : `${firstLine}\n${rest}`;
  }

  /** Emit anything still buffered at the end of a turn. */
  flush(): string {
    if (this.resolved) return '';
    const out = this.buffer.trim() === this.sentinel ? '' : this.buffer;
    this.buffer = '';
    this.resolved = true;
    return out;
  }

  /** Reset before the next assistant turn. */
  reset(): void {
    this.buffer = '';
    this.resolved = false;
  }

  private flushBuffer(): string {
    const out = this.buffer;
    this.buffer = '';
    this.resolved = true;
    return out;
  }
}
