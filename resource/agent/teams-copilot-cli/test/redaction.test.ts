import { describe, expect, it } from 'vitest';
import { isSensitivePath, redactSecrets } from '../src/agent/redaction.js';

describe('redactSecrets', () => {
  it('redacts a private key block', () => {
    const text = '-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\ndef==\n-----END RSA PRIVATE KEY-----';
    expect(redactSecrets(text)).toBe('[REDACTED:private-key]');
  });

  it('redacts AWS access key ids', () => {
    expect(redactSecrets('id=AKIAIOSFODNN7EXAMPLE end')).toBe('id=[REDACTED:aws-key] end');
  });

  it('redacts GitHub tokens', () => {
    expect(redactSecrets('ghp_0123456789abcdef0123456789abcdefABCD')).toBe('[REDACTED:github-token]');
  });

  it('redacts secret-style assignments but keeps the key name', () => {
    expect(redactSecrets('API_TOKEN=supersecretvalue')).toBe('API_TOKEN=[REDACTED:secret]');
    expect(redactSecrets('db_password: "hunter2xyz"')).toBe('db_password: "[REDACTED:secret]"');
  });

  it('leaves ordinary code untouched', () => {
    const code = 'const total = price * quantity; // no secrets here';
    expect(redactSecrets(code)).toBe(code);
  });
});

describe('isSensitivePath', () => {
  it('flags default sensitive files by basename', () => {
    expect(isSensitivePath('.env')).toBe(true);
    expect(isSensitivePath('config/.env.production')).toBe(true);
    expect(isSensitivePath('certs/server.pem')).toBe(true);
    expect(isSensitivePath('secrets/id_rsa')).toBe(true);
  });

  it('does not flag ordinary source files', () => {
    expect(isSensitivePath('src/index.ts')).toBe(false);
    expect(isSensitivePath('README.md')).toBe(false);
  });
});
