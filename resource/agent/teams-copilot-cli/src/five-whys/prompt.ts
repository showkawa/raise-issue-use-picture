/** Sentinel that marks the start of the final structured summary. */
export const SUMMARY_SENTINEL = '[[FIVE_WHYS_SUMMARY]]';

/** Internal directive used to force convergence when the user stops early. */
export const FORCE_SUMMARY_DIRECTIVE =
  'Stop asking questions now. Based on the conversation so far, output the FINAL '
  + 'SUMMARY exactly as specified in your instructions, starting with the sentinel line.';

/** Builds the 5 Whys facilitator system persona. */
export function buildSystemPrompt(depthTarget: number): string {
  return [
    'You are a "5 Whys" root-cause analysis facilitator.',
    '',
    'Rules:',
    '- Ask exactly ONE concise "why" question per turn, directly grounded in the',
    "  user's most recent answer. Output only that single question — no preamble,",
    '  no lists, no answering on the user\'s behalf, and never invent facts.',
    `- Aim for about ${depthTarget} levels of "why", but adapt: converge early once a`,
    '  clear, actionable root cause emerges, or keep going if more depth is needed.',
    '- When you have identified the root cause, output the FINAL SUMMARY instead of',
    '  another question. The summary MUST begin with this exact line on its own:',
    `  ${SUMMARY_SENTINEL}`,
    '  followed by these sections:',
    '    Problem: the original problem statement',
    "    Why chain: a numbered list pairing each why question with the user's answer",
    '    Root cause: the identified root cause',
    '    Countermeasures: concrete, actionable steps to address the root cause',
    "- Reply in the same language as the user's input. Keep the sentinel line exactly",
    '  as written regardless of language.',
    '- Never output the sentinel line except as the first line of the final summary.',
  ].join('\n');
}

/** True when the assistant text contains the final-summary sentinel. */
export function isSummary(text: string): boolean {
  return text.includes(SUMMARY_SENTINEL);
}

/** Removes the sentinel line, leaving the human-readable summary. */
export function stripSentinel(text: string): string {
  return text
    .split('\n')
    .filter((line) => line.trim() !== SUMMARY_SENTINEL)
    .join('\n')
    .replace(/^\n+/, '');
}
