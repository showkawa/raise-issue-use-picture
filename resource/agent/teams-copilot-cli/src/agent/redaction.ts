/**
 * Small, precise secret redaction applied to everything sent to the Copilot tenant
 * (tool results, repo map, @file injections). Deliberately conservative — prefers
 * missing a secret over corrupting normal code, which would break edit_file matching.
 * See ADR-0006.
 */

interface RedactionRule {
  kind: string;
  regex: RegExp;
  /** Builds the replacement; may keep capture groups (e.g. the key name). */
  replace: (match: string, ...groups: string[]) => string;
}

const RULES: RedactionRule[] = [
  {
    kind: 'private-key',
    regex: /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----/g,
    replace: () => '[REDACTED:private-key]',
  },
  {
    kind: 'aws-key',
    regex: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
    replace: () => '[REDACTED:aws-key]',
  },
  {
    kind: 'github-token',
    regex: /\bgh[posru]_[A-Za-z0-9]{20,}\b/g,
    replace: () => '[REDACTED:github-token]',
  },
  {
    // KEY/TOKEN/SECRET/PASSWORD-style assignments: keep the name, redact the value.
    kind: 'secret-assignment',
    regex: /\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|APIKEY))(\s*[=:]\s*)(["']?)([^\s"']{6,})\3/gi,
    replace: (_match, name: string, sep: string, quote: string) => `${name}${sep}${quote}[REDACTED:secret]${quote}`,
  },
];

/** Returns text with recognized credential patterns replaced by [REDACTED:<kind>]. */
export function redactSecrets(text: string): string {
  let out = text;
  for (const rule of RULES) {
    out = out.replace(rule.regex, rule.replace as (substring: string, ...args: unknown[]) => string);
  }
  return out;
}

/** Default glob patterns for files that must not be read/egressed by default. */
export const DEFAULT_DENY_READ_GLOBS = [
  '.env',
  '.env.*',
  '*.pem',
  '*.key',
  '*.pfx',
  '*.p12',
  'id_rsa',
  'id_dsa',
  'id_ecdsa',
  'id_ed25519',
  '*.keystore',
  '*.jks',
];

function globToRegExp(glob: string): RegExp {
  const body = glob
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]');
  return new RegExp(`^${body}$`, 'i');
}

/** Whether a relative path matches any deny-read glob (matched against basename and full path). */
export function isSensitivePath(relPath: string, denyGlobs: string[] = DEFAULT_DENY_READ_GLOBS): boolean {
  const normalized = relPath.split('\\').join('/');
  const base = normalized.split('/').pop() ?? normalized;
  return denyGlobs.some((glob) => {
    const re = globToRegExp(glob);
    return re.test(base) || re.test(normalized);
  });
}
