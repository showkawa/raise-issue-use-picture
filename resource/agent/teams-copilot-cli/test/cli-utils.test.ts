import { describe, expect, it } from 'vitest';
import { browserFlagsFromOptions } from '../src/cli/utils.js';

describe('browserFlagsFromOptions', () => {
  it('does not override configured port when --port is omitted', () => {
    expect(browserFlagsFromOptions({})).toEqual({});
  });

  it('parses a valid port', () => {
    expect(browserFlagsFromOptions({ port: '9333' })).toEqual({ port: 9333 });
  });

  it('rejects an invalid port', () => {
    expect(() => browserFlagsFromOptions({ port: 'abc' })).toThrow('Invalid CDP port');
  });
});
