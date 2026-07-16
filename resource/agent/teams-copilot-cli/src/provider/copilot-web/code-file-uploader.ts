import { basename, extname, resolve } from 'path';
import { readFileSync, statSync } from 'fs';
import type { Page, Response } from 'playwright-core';

const DIRECT_UPLOAD_EXTENSIONS = new Set([
  '.bash', '.c', '.config', '.cpp', '.cs', '.css', '.csv', '.dart', '.h',
  '.htm', '.html', '.ini', '.java', '.js', '.json', '.jsx', '.log', '.lua',
  '.md', '.php', '.pl', '.py', '.rs', '.sh', '.sql', '.tsx', '.txt', '.utf8',
  '.xml', '.yaml', '.yml',
]);
const MAX_ALIASED_FILE_BYTES = 20 * 1024 * 1024;

export interface PreparedCodeUpload {
  filePath: string;
  originalName: string;
  uploadName: string;
  aliased: boolean;
  input: string | {
    name: string;
    mimeType: string;
    buffer: Buffer;
  };
}

export function prepareCodeUpload(filePath: string): PreparedCodeUpload {
  const absolutePath = resolve(filePath);
  const stats = statSync(absolutePath);
  if (!stats.isFile()) throw new Error(`Not a file: ${filePath}`);
  if (stats.size === 0) throw new Error(`Code file is empty: ${filePath}`);

  const originalName = basename(absolutePath);
  const extension = extname(originalName).toLowerCase();
  if (DIRECT_UPLOAD_EXTENSIONS.has(extension)) {
    return {
      filePath: absolutePath,
      originalName,
      uploadName: originalName,
      aliased: false,
      input: absolutePath,
    };
  }

  if (stats.size > MAX_ALIASED_FILE_BYTES) {
    throw new Error(
      `Unsupported extension "${extension || '(none)'}" requires a text alias, `
      + `but the file exceeds ${MAX_ALIASED_FILE_BYTES / 1024 / 1024} MB`,
    );
  }
  const buffer = readFileSync(absolutePath);
  if (buffer.subarray(0, 8192).includes(0)) {
    throw new Error(`Unsupported binary code file: ${filePath}`);
  }
  const uploadName = `${originalName}.txt`;
  return {
    filePath: absolutePath,
    originalName,
    uploadName,
    aliased: true,
    input: {
      name: uploadName,
      mimeType: 'text/plain',
      buffer,
    },
  };
}

export async function uploadCodeFile(
  page: Page,
  filePath: string,
  timeout: number,
  selector: string,
): Promise<PreparedCodeUpload> {
  const prepared = prepareCodeUpload(filePath);
  const input = page.locator(selector).first();
  await input.waitFor({ state: 'attached', timeout });
  const attachments = page.getByText(prepared.uploadName, { exact: true });
  const existingAttachmentCount = await attachments.count();

  let uploadError: Error | null = null;
  const onResponse = async (response: Response): Promise<void> => {
    if (
      response.status() < 400
      || !/graph\.microsoft\.com\/.*(?:copilotuploads|createUploadSession|\/content)/i.test(
        response.url(),
      )
    ) {
      return;
    }
    let detail = `HTTP ${response.status()}`;
    try {
      const body = await response.json() as {
        error?: {
          code?: string;
          message?: string;
        };
      };
      detail = [body.error?.code, body.error?.message].filter(Boolean).join(': ') || detail;
    } catch {}
    uploadError = new Error(`Microsoft 365 Copilot file upload failed: ${detail}`);
  };

  page.on('response', onResponse);
  try {
    await input.setInputFiles([]);
    await input.setInputFiles(prepared.input);
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (uploadError) throw uploadError;
      const attachmentCount = await attachments.count();
      if (attachmentCount > existingAttachmentCount) {
        const attachment = attachments.last();
        if (await attachment.isVisible().catch(() => false)) return prepared;
      }
      await page.waitForTimeout(250);
    }
    throw new Error(
      `Microsoft 365 Copilot did not finish attaching "${prepared.uploadName}"`,
    );
  } catch (error) {
    await input.setInputFiles([]).catch(() => undefined);
    throw error;
  } finally {
    page.off('response', onResponse);
  }
}
