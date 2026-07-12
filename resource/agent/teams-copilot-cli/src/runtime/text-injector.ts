import type { InjectResult } from '../types.js';
import type { Frame } from 'playwright-core';

export async function injectText(
  frame: Frame,
  text: string,
  selector: string,
): Promise<InjectResult> {
  const locator = frame.locator(selector).first();
  try {
    await locator.click({ timeout: 10000 });
    await frame.page().keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
    await frame.page().keyboard.press('Backspace');

    // Primary: ClipboardEvent paste simulation (v1 verified for Lexical)
    const result = await frame.evaluate(
      ({ sel, txt }) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return { success: false, error: 'Element not found' };
        el.focus();
        const dataTransfer = new DataTransfer();
        dataTransfer.setData('text/plain', txt);
        const pasteEvent = new ClipboardEvent('paste', {
          clipboardData: dataTransfer,
          bubbles: true,
          cancelable: true,
        });
        el.dispatchEvent(pasteEvent);
        if (!el.textContent?.includes(txt)) {
          document.execCommand('insertText', false, txt);
        }
        return { success: el.textContent?.includes(txt) ?? false };
      },
      { sel: selector, txt: text },
    );

    if (!result.success) {
      return { success: false, method: 'clipboard', error: result.error ?? 'Text did not appear after paste' };
    }

    const contentMatches = await frame.evaluate(
      ({ sel, txt }) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        return el?.textContent?.includes(txt) ?? false;
      },
      { sel: selector, txt: text },
    );

    if (!contentMatches) {
      return { success: false, method: 'clipboard', error: 'Text verification failed' };
    }
    return { success: true, method: 'clipboard' };
  } catch {
    // Fallback: keyboard.insertText
    try {
      await locator.click({ timeout: 10000 });
      await frame.page().keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
      await frame.page().keyboard.press('Backspace');
      await frame.page().keyboard.insertText(text);
      const verified = await frame.evaluate(
        ({ sel, txt }) => {
          const el = document.querySelector(sel) as HTMLElement | null;
          return el?.textContent?.includes(txt) ?? false;
        },
        { sel: selector, txt: text },
      );
      return verified
        ? { success: true, method: 'insertText' }
        : { success: false, method: 'insertText', error: 'Text verification failed' };
    } catch (fallbackError: unknown) {
      const message = fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
      return { success: false, method: 'insertText', error: message };
    }
  }
}
