/** Strip text-form tool call blocks from streamed assistant content. */

const TOOL_CALL_BLOCK_RE =
  /(^|\n)?[ \t]*<\s*tool_call\s*>\s*[\s\S]*?\s*<\s*\/\s*tool_call\s*>[ \t]*(\n|$)?/gi;
const PYTHON_TAG_BLOCK_RE =
  /(^|\n)?[ \t]*<\|python_tag\|>\s*[\s\S]*?\s*<\|eom_id\|>[ \t]*(\n|$)?/gi;
const TOOL_CALL_FENCE_RE =
  /(^|\n)?[ \t]*(?:<tool_call>|tool_call:)\s*```(?:json)?\s*[\s\S]*?\s*```[ \t]*(\n|$)?/gi;
const TRAILING_TOOL_CALL_RE = /<\s*tool_call\s*>[\s\S]*$/i;
const TRAILING_PYTHON_TAG_RE = /<\|python_tag\|>[\s\S]*$/i;
const TRAILING_TOOL_CALL_FENCE_RE =
  /(?:^|\n)\s*(?:<tool_call>|tool_call:)\s*```(?:json)?\s*[\s\S]*$/i;

function preserveLineBreak(before = "", after = ""): string {
  return before && after ? "\n" : before || after;
}

export function stripTextFormToolCalls(text: string): string {
  if (!text.trim()) {
    return text;
  }
  return text
    .replace(TOOL_CALL_BLOCK_RE, (_match, before: string, after: string) =>
      preserveLineBreak(before, after),
    )
    .replace(PYTHON_TAG_BLOCK_RE, (_match, before: string, after: string) =>
      preserveLineBreak(before, after),
    )
    .replace(TOOL_CALL_FENCE_RE, (_match, before: string, after: string) =>
      preserveLineBreak(before, after),
    )
    .replace(TRAILING_TOOL_CALL_FENCE_RE, "")
    .replace(TRAILING_TOOL_CALL_RE, "")
    .replace(TRAILING_PYTHON_TAG_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}
