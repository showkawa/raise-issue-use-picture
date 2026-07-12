import type { StreamResult, CopilotConfig } from '../types.js';
import type { Frame } from 'playwright-core';

export interface ExtractOptions {
  baseline?: string;
  onUpdate?: (chunk: string) => void;
}

export async function extractStream(
  frame: Frame,
  config: CopilotConfig,
  options: ExtractOptions = {},
): Promise<StreamResult> {
  const start = Date.now();
  let lastText = '';
  let lastEmitted = '';
  let stableCount = 0;
  const interval = config.timeouts.pollingInterval;
  const maxStable = Math.max(3, Math.ceil(2500 / interval));
  const timeout = config.timeouts.streaming;

  while (Date.now() - start < timeout) {
    const text = await readResponseText(frame, config.selectors.responseContainer, options.baseline);
    if (options.onUpdate && text.startsWith(lastEmitted) && text.length > lastEmitted.length) {
      const chunk = text.slice(lastEmitted.length);
      options.onUpdate(chunk);
      lastEmitted = text;
    }

    if (text === lastText && text.length > 0) {
      stableCount++;
      if (stableCount >= maxStable) {
        const truncated = isTruncated(text);
        return { text, truncated, duration: Date.now() - start };
      }
    } else {
      stableCount = 0;
    }
    lastText = text;
    await new Promise((r) => setTimeout(r, interval));
  }

  // Timeout: return what we have
  return { text: lastText, truncated: true, duration: Date.now() - start };
}

export async function readResponseText(
  frame: Frame,
  selector: string,
  baseline = '',
): Promise<string> {
  const text = await frame.evaluate(
    (sel) => {
      const containers = Array.from(document.querySelectorAll(sel));
      const texts = containers
        .map((container) => (container.textContent || '').trim())
        .filter(Boolean);
      return texts.length > 0 ? texts[texts.length - 1] : '';
    },
    selector,
  );

  if (baseline && text.startsWith(baseline)) {
    return text.slice(baseline.length).trimStart();
  }
  return text;
}

export function isTruncated(text: string): boolean {
  if (!text) return false;
  const trimmed = text.trim();
  const codeBlockCount = (trimmed.match(/```/g) || []).length;
  const codeBlockUnclosed = codeBlockCount % 2 !== 0;
  const endsMidClause = /[,，;；:：、]$/.test(trimmed);
  return codeBlockUnclosed || endsMidClause;
}
