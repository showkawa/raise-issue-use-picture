import { afterEach, describe, expect, it } from 'vitest';
import { existsSync, readFileSync, unlinkSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { MockProvider } from '../src/provider/mock.js';
import { FiveWhysSession, saveSummary, type FiveWhysIO } from '../src/five-whys/session.js';
import { FORCE_SUMMARY_DIRECTIVE, SUMMARY_SENTINEL, stripSentinel } from '../src/five-whys/prompt.js';

interface RecordedTurn {
  text: string;
  kind: 'question' | 'summary';
}

function scriptedIO(answers: string[], confirm = true): FiveWhysIO & { turns: RecordedTurn[]; confirms: number[] } {
  const turns: RecordedTurn[] = [];
  const confirms: number[] = [];
  let cursor = 0;
  return {
    turns,
    confirms,
    onAssistant(text, kind) {
      turns.push({ text, kind });
    },
    async readAnswer() {
      return cursor < answers.length ? answers[cursor++] : null;
    },
    async confirmContinue(depth) {
      confirms.push(depth);
      return confirm;
    },
  };
}

const tempFiles: string[] = [];
afterEach(() => {
  for (const f of tempFiles.splice(0)) {
    if (existsSync(f)) unlinkSync(f);
  }
});

describe('FiveWhysSession', () => {
  it('asks one question per turn and stops when the assistant converges', async () => {
    const summary = `${SUMMARY_SENTINEL}\nProblem: build broke\nRoot cause: flaky cache`;
    const provider = new MockProvider(['why 1', 'why 2', 'why 3', summary]);
    const io = scriptedIO(['ans 1', 'ans 2', 'ans 3']);
    const session = new FiveWhysSession(provider, { depthCap: 5 });

    const result = await session.run('build broke', io);

    const questions = io.turns.filter((t) => t.kind === 'question');
    expect(questions.map((t) => t.text)).toEqual(['why 1', 'why 2', 'why 3']);
    expect(io.turns.at(-1)).toEqual({
      text: 'Problem: build broke\nRoot cause: flaky cache',
      kind: 'summary',
    });
    expect(result).not.toContain(SUMMARY_SENTINEL);
    expect(io.confirms).toEqual([]);
    expect(provider.sent).toEqual(['build broke', 'ans 1', 'ans 2', 'ans 3']);
    expect(provider.systemPrompts[0]).toContain('5 Whys');
  });

  it('seeds the facilitator system prompt into the session', async () => {
    const provider = new MockProvider([`${SUMMARY_SENTINEL}\nRoot cause: x`]);
    await new FiveWhysSession(provider).run('problem', scriptedIO([]));
    expect(provider.systemPrompts[0]).toContain(SUMMARY_SENTINEL);
    expect(provider.systemPrompts[0]).toContain('facilitator');
  });

  it('offers to continue at the depth cap and forces a summary when declined', async () => {
    const provider = new MockProvider([], {
      respond: (message) =>
        message === FORCE_SUMMARY_DIRECTIVE
          ? `${SUMMARY_SENTINEL}\nRoot cause: root`
          : 'why?',
    });
    const io = scriptedIO(['a1', 'a2', 'a3'], false);
    const session = new FiveWhysSession(provider, { depthCap: 2 });

    const result = await session.run('problem', io);

    expect(io.confirms).toEqual([2]);
    expect(result).toBe('Root cause: root');
    expect(provider.sent.at(-1)).toBe(FORCE_SUMMARY_DIRECTIVE);
  });

  it('forces a summary when the user stops early (null answer)', async () => {
    const provider = new MockProvider([], {
      respond: (message) =>
        message === FORCE_SUMMARY_DIRECTIVE ? `${SUMMARY_SENTINEL}\nRoot cause: y` : 'why?',
    });
    const io = scriptedIO([]); // first readAnswer returns null
    const result = await new FiveWhysSession(provider, { depthCap: 5 }).run('problem', io);
    expect(result).toBe('Root cause: y');
    expect(provider.sent).toEqual(['problem', FORCE_SUMMARY_DIRECTIVE]);
  });

  it('streams deltas through onDelta when provided', async () => {
    const provider = new MockProvider([`${SUMMARY_SENTINEL}\nRoot cause: z`]);
    const chunks: string[] = [];
    const io: FiveWhysIO = {
      onDelta: (c) => chunks.push(c),
      onAssistant() {},
      async readAnswer() {
        return null;
      },
      async confirmContinue() {
        return true;
      },
    };
    await new FiveWhysSession(provider).run('problem', io);
    expect(chunks.join('')).toContain('Root cause: z');
  });
});

describe('saveSummary', () => {
  it('writes the summary as Markdown with a trailing newline', () => {
    const path = join(tmpdir(), `five-whys-${Date.now()}.md`);
    tempFiles.push(path);
    saveSummary(path, stripSentinel(`${SUMMARY_SENTINEL}\nRoot cause: cache`));
    const content = readFileSync(path, 'utf8');
    expect(content).toBe('Root cause: cache\n');
    expect(content).not.toContain(SUMMARY_SENTINEL);
  });
});
