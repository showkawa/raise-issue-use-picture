const COMMANDS = new Set(['ask', 'review', 'prd', 'arch', 'tasks', 'repl', 'help']);
const OPTIONS_WITH_VALUE = new Set(['--config', '--browser', '--port']);
const HELP_OPTIONS = new Set(['--help', '-h', '--version', '-V']);

export function normalizeCliArgv(argv: string[]): string[] {
  const normalized = [...argv];
  let positionalIndex = -1;
  let insertionIndex = -1;

  for (let index = 2; index < normalized.length; index++) {
    const argument = normalized[index];
    if (argument === '--') {
      if (index + 1 < normalized.length) {
        positionalIndex = index + 1;
        insertionIndex = index;
      }
      break;
    }
    if (HELP_OPTIONS.has(argument)) return normalized;
    if (OPTIONS_WITH_VALUE.has(argument)) {
      index++;
      continue;
    }
    if (argument.startsWith('--config=')
      || argument.startsWith('--browser=')
      || argument.startsWith('--port=')
      || argument.startsWith('-')) {
      continue;
    }
    positionalIndex = index;
    insertionIndex = index;
    break;
  }

  if (positionalIndex < 0 || COMMANDS.has(normalized[positionalIndex])) return normalized;
  normalized.splice(insertionIndex, 0, 'ask');
  return normalized;
}
