import { dirname, resolve } from 'path';
import { mkdirSync, writeFileSync } from 'fs';
import { createInterface } from 'readline';

/** Reads a multiline prompt from a stream, ending on a lone terminator line. */
export async function readDelimitedQuestion(
  input: NodeJS.ReadableStream = process.stdin,
  terminator = '@',
): Promise<string> {
  if (input === process.stdin && process.stdin.isTTY) {
    process.stderr.write(`Enter the multiline prompt; finish with ${terminator} on its own line.\n`);
  }

  const lines: string[] = [];
  let terminated = false;
  const reader = createInterface({ input, crlfDelay: Infinity, terminal: false });
  for await (const line of reader) {
    if (line === terminator) {
      terminated = true;
      break;
    }
    lines.push(line);
  }
  reader.close();

  if (!terminated) {
    throw new Error(`Multiline prompt must end with ${terminator} on its own line`);
  }
  if (lines.length === 0) {
    throw new Error('Multiline prompt is empty');
  }
  return lines.join('\n');
}

/** Writes text to a path, creating parent directories as needed. */
export function writeTextOutput(outputPath: string, content: string): string {
  const resolvedPath = resolve(outputPath);
  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, content, 'utf8');
  return resolvedPath;
}
