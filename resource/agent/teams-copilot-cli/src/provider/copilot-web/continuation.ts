const MIN_OVERLAP = 20;
const MAX_OVERLAP_SCAN = 2000;

/**
 * Merges a "continue" turn into the accumulated text, dropping any prefix of
 * the continuation that duplicates the tail of what we already have (models
 * often re-emit the last sentence/line despite being told not to).
 */
export function mergeContinuation(existing: string, continuation: string): string {
  const base = existing.replace(/\s+$/, '');
  const next = continuation.replace(/^\s+/, '');
  if (!next) return base;
  if (!base) return next;

  const scan = Math.min(base.length, next.length, MAX_OVERLAP_SCAN);
  for (let overlap = scan; overlap >= MIN_OVERLAP; overlap--) {
    if (base.endsWith(next.slice(0, overlap))) {
      return `${base}${next.slice(overlap)}`;
    }
  }
  return `${base}\n${next}`;
}
