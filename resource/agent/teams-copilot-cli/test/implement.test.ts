import { describe, expect, it } from 'vitest';
import { ensureCleanWorktree } from '../src/cli/implement.js';

describe('ensureCleanWorktree', () => {
  it('passes on a clean worktree', () => {
    expect(() => ensureCleanWorktree([], false)).not.toThrow();
  });

  it('refuses a dirty worktree by default', () => {
    expect(() => ensureCleanWorktree(['src/a.ts'], false)).toThrow('工作树不干净');
  });

  it('allows a dirty worktree with --allow-dirty', () => {
    expect(() => ensureCleanWorktree(['src/a.ts'], true)).not.toThrow();
  });
});
