import { loadConfig } from '../provider/copilot-web/config.js';
import { createProvider } from '../provider/factory.js';
import { FiveWhysSession } from '../five-whys/session.js';
import { writeTextOutput } from './prompt-content.js';
import { TerminalIO } from './terminal-io.js';

export interface FiveWhysCommandOpts {
  config?: string;
  output?: string;
  stream?: boolean;
}

export async function runFiveWhys(problem: string, opts: FiveWhysCommandOpts): Promise<void> {
  if (!problem.trim()) {
    throw new Error('Provide a problem statement, e.g. `tcc "the deploy failed"`');
  }
  const config = loadConfig(opts.config);
  const provider = createProvider(config);
  await provider.init();
  const io = new TerminalIO(opts.stream !== false);
  const session = new FiveWhysSession(provider);
  try {
    const summary = await session.run(problem, io);
    if (opts.output) {
      const body = summary.endsWith('\n') ? summary : `${summary}\n`;
      const outputPath = writeTextOutput(opts.output, body);
      process.stderr.write(`Summary saved to ${outputPath}\n`);
    }
  } finally {
    io.close();
    await provider.close();
  }
}
