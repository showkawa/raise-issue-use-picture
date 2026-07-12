import { spawnSync } from 'child_process';
import { realpathSync } from 'fs';
import { fileURLToPath } from 'url';

function canonicalPath(path) {
  const resolved = realpathSync.native(path);
  return process.platform === 'win32'
    ? `${resolved[0].toUpperCase()}${resolved.slice(1)}`
    : resolved;
}

const cwd = canonicalPath(process.cwd());
const vitestPath = canonicalPath(
  fileURLToPath(new URL('../node_modules/vitest/vitest.mjs', import.meta.url)),
);
const result = spawnSync(process.execPath, [vitestPath, ...process.argv.slice(2)], {
  cwd,
  env: { ...process.env, INIT_CWD: cwd, PWD: cwd },
  stdio: 'inherit',
});

if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
