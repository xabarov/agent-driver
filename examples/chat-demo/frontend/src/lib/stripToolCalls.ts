/** Strip text-form tool call blocks from streamed assistant content. */

const TOOL_CALL_BLOCK_RE = /<tool_call>\s*[\s\S]*?\s*<\/tool_call>/gi;
const PYTHON_TAG_BLOCK_RE = /<\|python_tag\|>\s*[\s\S]*?\s*<\|eom_id\|>/gi;
const TOOL_CALL_FENCE_RE =
  /(?:^|\n)\s*(?:<tool_call>|tool_call:)\s*```(?:json)?\s*[\s\S]*?\s*```/gi;

export function stripTextFormToolCalls(text: string): string {
  if (!text.trim()) {
    return text;
  }
  return text
    .replace(TOOL_CALL_BLOCK_RE, "")
    .replace(PYTHON_TAG_BLOCK_RE, "")
    .replace(TOOL_CALL_FENCE_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}
