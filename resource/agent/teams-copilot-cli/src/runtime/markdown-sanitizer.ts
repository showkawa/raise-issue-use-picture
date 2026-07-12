export function sanitizeMarkdown(text: string): string {
  if (!text) return '';

  let result = text;
  result = result.replace(
    /^(好的[，,:：]?|Sure[,!:]?|Here(?:'s| is| are)|Of course|Certainly|Let me|I'd be happy to)[^\n]*\n/i,
    '',
  );
  result = result.replace(
    /\n+(如果你需要|如果你希望|有什么问题|如需进一步|Feel free|Let me know|Is there anything else|Do you have any other)[^\n]*$/i,
    '',
  );
  result = result.replace(/^```markdown\n((?:(?!```)[\s\S])*)\n```$/, '$1');
  result = result.replace(/请严格从你断开的地方继续输出[^\n]*\n?/g, '');
  return result.trim();
}
