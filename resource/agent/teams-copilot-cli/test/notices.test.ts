import { describe, expect, it } from 'vitest';
import { resolvePermissionMode } from '../src/cli/notices.js';

describe('resolvePermissionMode', () => {
  it('keeps the current mode when no flags are given', () => {
    expect(resolvePermissionMode('allowlist', {})).toBe('allowlist');
    expect(resolvePermissionMode('yolo', {})).toBe('yolo');
  });

  it('maps --yolo and --ask shortcuts', () => {
    expect(resolvePermissionMode('allowlist', { yolo: true })).toBe('yolo');
    expect(resolvePermissionMode('allowlist', { ask: true })).toBe('ask');
  });

  it('lets --permission-mode win over shortcuts', () => {
    expect(resolvePermissionMode('allowlist', { permissionMode: 'ask', yolo: true })).toBe('ask');
  });

  it('rejects an invalid --permission-mode value', () => {
    expect(() => resolvePermissionMode('allowlist', { permissionMode: 'nope' })).toThrow('Invalid permission mode');
  });

  it('rejects combining --yolo and --ask', () => {
    expect(() => resolvePermissionMode('allowlist', { yolo: true, ask: true })).toThrow('Cannot combine');
  });
});
