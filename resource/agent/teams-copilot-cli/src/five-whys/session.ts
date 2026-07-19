import { writeFileSync } from 'fs';
import type { ChatSession, Provider } from '../provider/types.js';
import {
  FORCE_SUMMARY_DIRECTIVE,
  buildSystemPrompt,
  isSummary,
  stripSentinel,
} from './prompt.js';

export type AssistantTurnKind = 'question' | 'summary';

/** Terminal / test seam for the interactive 5 Whys loop. */
export interface FiveWhysIO {
  /** Streaming deltas, when live streaming is enabled. */
  onDelta?(chunk: string): void;
  /** One complete assistant turn (a "why" question or the final summary). */
  onAssistant(text: string, kind: AssistantTurnKind): void;
  /** The user's next answer; return null to stop the session early. */
  readAnswer(): Promise<string | null>;
  /** At the depth cap, ask whether to keep drilling deeper. */
  confirmContinue(depth: number): Promise<boolean>;
}

export interface FiveWhysOptions {
  /** Target number of "why" levels before offering to continue. */
  depthCap?: number;
}

export class FiveWhysSession {
  private readonly depthCap: number;

  constructor(
    private readonly provider: Provider,
    options: FiveWhysOptions = {},
  ) {
    this.depthCap = options.depthCap ?? 5;
  }

  /** Runs the guided dialogue and returns the final summary text. */
  async run(problem: string, io: FiveWhysIO): Promise<string> {
    const session = await this.provider.createSession({
      systemPrompt: buildSystemPrompt(this.depthCap),
    });
    try {
      let input = problem;
      let depth = 0;
      for (;;) {
        const result = await session.send(input, this.turnOptions(io));
        if (isSummary(result.text)) {
          const summary = stripSentinel(result.text);
          io.onAssistant(summary, 'summary');
          return summary;
        }
        io.onAssistant(result.text, 'question');
        depth += 1;
        if (depth >= this.depthCap && !(await io.confirmContinue(depth))) {
          return await this.forceSummary(session, io);
        }
        const answer = await io.readAnswer();
        if (answer === null) {
          return await this.forceSummary(session, io);
        }
        input = answer;
      }
    } finally {
      await session.close();
    }
  }

  private async forceSummary(session: ChatSession, io: FiveWhysIO): Promise<string> {
    const result = await session.send(FORCE_SUMMARY_DIRECTIVE, this.turnOptions(io));
    const summary = stripSentinel(result.text);
    io.onAssistant(summary, 'summary');
    return summary;
  }

  private turnOptions(io: FiveWhysIO): { onUpdate?: (chunk: string) => void } {
    return io.onDelta ? { onUpdate: io.onDelta } : {};
  }
}

/** Writes the final summary to disk as Markdown. */
export function saveSummary(path: string, summary: string): void {
  const body = summary.endsWith('\n') ? summary : `${summary}\n`;
  writeFileSync(path, body, 'utf8');
}
