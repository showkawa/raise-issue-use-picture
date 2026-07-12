import { realpathSync } from 'fs';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  root: realpathSync.native(process.cwd()),
  test: {
    exclude: ['node_modules/**', 'dist/**', 'test/e2e/**'],
  },
});
