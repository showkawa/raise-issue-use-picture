import { appendFileSync, mkdirSync } from 'fs';
import { join } from 'path';

export interface AuditEntry {
  tool: string;
  target?: string;
  allowed: boolean;
  ok?: boolean;
  exitCode?: number;
  reason?: string;
}

export type AuditLogger = (entry: AuditEntry) => void;

/**
 * Creates an append-only audit logger that records every tool call and permission
 * decision as one JSON line under <projectRoot>/.teams-copilot/audit.log (ADR-0008).
 */
export function createAuditLogger(
  projectRoot: string,
  now: () => Date = () => new Date(),
): AuditLogger {
  const dir = join(projectRoot, '.teams-copilot');
  const path = join(dir, 'audit.log');
  mkdirSync(dir, { recursive: true });
  return (entry) => {
    const line = `${JSON.stringify({ ts: now().toISOString(), ...entry })}\n`;
    try {
      appendFileSync(path, line, 'utf8');
    } catch {
      // auditing must never break the agent run
    }
  };
}
