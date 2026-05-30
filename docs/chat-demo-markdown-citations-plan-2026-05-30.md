# Chat Demo Markdown And Citations Plan

Date: 2026-05-30

## Goal

Make chat-demo answers feel like a modern assistant surface: readable Markdown, math, code and Python blocks, and compact source cards under answers when the assistant references links or uses web tools.

This is not only a frontend polish task. The UI should render what the runtime actually knows: fetched URLs, search results, tool evidence, and final answer text should converge into one clean user-facing message instead of separate raw JSON blocks.

## Product Principles

- Keep `examples/chat-demo` clean: reusable parsing, source evidence, and runtime metadata belong in `agent_driver`; the demo should mostly compose those contracts into UI.
- Prefer simple model + prompt + evidence contracts over a heavy DAG. If a small normalized source model solves citation display, do that before adding orchestration layers.
- Render untrusted model output safely. Markdown should become React elements, not raw HTML.
- Preserve the “agent is working” feeling without exposing noisy internals by default. Tool JSON stays available for debugging, but the main path should show readable summaries and source cards.
- Keep streaming stable: partial code fences, Python blocks, tables, and math blocks must not flicker, collapse, or commit broken rendering.

## Current State

- The frontend already uses `react-markdown`, `remark-gfm`, `rehype-highlight`, and `highlight.js` in `examples/chat-demo/frontend/src/lib/markdown.tsx`.
- Assistant messages render Markdown only after completion. While `pending`, `MessageBubble.tsx` renders plain `whitespace-pre-wrap`, so streaming Markdown/math/code is not visible as rich content.
- Math is not supported. The current screenshot shows raw `$$ ... $$` instead of rendered formulas.
- Code blocks are highlighted but have no header, language badge, copy button, or Python-specific framing.
- `ToolCallCard.tsx` already has a custom Python execution panel, but normal Markdown Python fences and Python tool calls are separate experiences.
- Web tool calls still surface mostly as generic tool cards with raw/debug JSON. There is no normalized source/citation model attached to the final answer.
- `events.ts` extracts generic tool state, but not `SourceEvidence` from `web_fetch` / `web_search` results.

## External Best Practices

- Keep `react-markdown`: its docs emphasize safe React element rendering, plugin support, custom components, and GFM via `remark-gfm`. It is already in the project and fits our stack.
  Source: https://github.com/remarkjs/react-markdown
- Add `remark-math` + `rehype-katex` for formulas. The `react-markdown` docs show exactly this pair for math and note that KaTeX CSS must be imported explicitly.
  Source: https://github.com/remarkjs/react-markdown#use-remark-and-rehype-plugins-math
- Keep `remark-gfm` for autolink literals, tables, task lists, strikethrough, and footnotes. Users expect bare URLs and tables to work.
  Source: https://github.com/remarkjs/remark-gfm
- Add `rehype-sanitize` with an explicit schema after unsafe transforms. `react-markdown` and `rehype-sanitize` both warn that plugins/components can reopen XSS surfaces.
  Sources: https://github.com/remarkjs/react-markdown#security, https://github.com/rehypejs/rehype-sanitize#security
- Follow OWASP’s rule that data rendered into UI must be encoded/sanitized in the correct context; do not rely on one global interceptor or CSP alone.
  Source: https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html
- Evaluate Shiki later for richer code blocks. Shiki has a rehype plugin with light/dark themes and inline-code options, but we should not switch highlighters until the baseline renderer and tests are stable.
  Source: https://shiki.style/packages/rehype

## Neighbor Project Findings

### OpenClaude

Files reviewed:

- `/home/roman/pyprojects/ML/openclaude/src/components/Markdown.tsx`
- `/home/roman/pyprojects/ML/openclaude/src/components/Messages.tsx`
- `/home/roman/pyprojects/ML/openclaude/src/components/MarkdownTable.tsx`

Useful ideas:

- `StreamingMarkdown` splits streaming text into stable prefix + unstable suffix, so only the tail is reparsed per delta.
- Markdown lexer results are cached by content hash for long history performance.
- Plain text gets a fast path when no Markdown syntax is detected.
- Tables are treated as first-class layout, not left to arbitrary wrapping.
- Syntax highlighting is optional/lazy because it can be expensive.

### Hermes Agent

Files reviewed:

- `/home/roman/pyprojects/ML/hermes-agent/ui-tui/src/components/streamingMarkdown.tsx`
- `/home/roman/pyprojects/ML/hermes-agent/ui-tui/src/components/markdown.tsx`
- `/home/roman/pyprojects/ML/hermes-agent/agent/markdown_tables.py`

Useful ideas:

- Streaming boundaries account for fenced code blocks and display math blocks (`$$`, `\[`), preventing broken partial rendering.
- Inline math is intentionally prioritized before emphasis parsing, avoiding corruption of formulas containing `*`.
- Wide/CJK-aware table alignment exists for terminal contexts. For our web UI the direct equivalent is responsive table containers, stable min widths, and no horizontal page scrollbar.
- Links are normalized and displayed with meaningful labels rather than raw URLs when possible.

## Target UX

Assistant prose:

- Markdown is rendered both during streaming and after completion.
- Headings, lists, tables, blockquotes, task lists, footnotes, inline code, links, and horizontal rules work.
- Math renders inline and block formulas with KaTeX.
- Raw HTML is ignored or sanitized; scripts and dangerous attributes never execute.

Code blocks:

- Every fenced block gets a small header with language label, copy button, and optional filename if the model uses a fence info string like `python title=analysis.py`.
- Python code blocks get slightly richer treatment: `Python` badge, copy, and optionally “Run with Python tool” later if we add an explicit user action.
- Long code scrolls inside the block, not the whole chat page.
- Inline code stays compact and readable.

Python tool calls:

- Keep the existing Python execution panel, but visually align it with Markdown code blocks.
- Show executed code and result in a readable notebook-like block, with debug JSON hidden behind a small details affordance.
- When the final answer contains code and the agent also used the Python tool, the two should feel connected: same code typography, same copy behavior, same dark/light colors.

Citations:

- If the assistant final text contains HTTP(S) links, show a “Sources” shelf under the answer.
- If the agent called `web_fetch`, show fetched pages as source cards even if the final text forgot to include links.
- `web_search` results can be shown as “Search results used” only when no fetched page exists, or as secondary evidence. Fetched pages have higher citation confidence.
- Cards show source number, favicon/domain, title, short excerpt/snippet, and a small badge: `fetched`, `linked`, or `search`.
- Deduplicate by canonical URL and limit visible cards to 3-5 with “show all”.
- Clicking a card opens a new tab with `rel="noopener noreferrer"`.

Tool cards:

- `web_fetch` should no longer look like raw JSON in the main chat lane. It should collapse into a source/evidence card with debug payload behind details.
- `web_search` should show query and result count; raw result JSON stays hidden.
- Debug mode may still expose exact payloads for development.

## Proposed Data Contract

Add a reusable source model in `agent_driver`, not only in chat-demo:

```python
class SourceEvidence(BaseModel):
    id: str
    url: str
    canonical_url: str
    title: str | None = None
    domain: str | None = None
    excerpt: str | None = None
    source_type: Literal["assistant_link", "web_fetch", "web_search"]
    tool_call_id: str | None = None
    rank: int | None = None
    fetched_at: str | None = None
    published_at: str | None = None
```

Runtime/backend responsibilities:

- Normalize evidence from `web_fetch` and `web_search` tool results.
- Store evidence in run terminal metadata and/or stream it in tool completion events.
- Preserve enough source metadata for frontend cards without exposing full fetched text.
- Prefer `web_fetch` evidence over `web_search` evidence when deduplicating.

Frontend responsibilities:

- Parse assistant Markdown links as `assistant_link` evidence.
- Merge assistant links with runtime evidence by canonical URL.
- Attach merged evidence to the assistant message that belongs to the run.
- Render `CitationShelf` below `MarkdownRenderer`.

## Library Plan

Immediate dependencies:

- `remark-math`
- `rehype-katex`
- `katex`
- `rehype-sanitize`

Keep for phase 1:

- `react-markdown`
- `remark-gfm`
- `rehype-highlight`
- `highlight.js`

Evaluate after baseline:

- `@shikijs/rehype` or `rehype-pretty-code` for higher quality syntax themes and code annotations.
- A tiny URL canonicalization helper if browser `URL` handling is not enough.

## Implementation Phases

### Phase 1. Renderer Safety And Math

- [x] Add dependencies: `remark-math`, `rehype-katex`, `katex`, `rehype-sanitize`.
- [x] Import KaTeX CSS in the frontend entry CSS.
- [x] Update `MarkdownRenderer` to include GFM + math + KaTeX + sanitization.
- [x] Keep `skipHtml`/safe URL transform behavior explicit.
- [x] Add tests for:
  - raw `<script>` is displayed/removed, not executed;
  - `javascript:` links are not clickable;
  - `$$ P(X > 3) = e^{-7.05} $$` renders as KaTeX;
  - GFM bare URLs still link.

### Phase 2. Code Block UX

- [x] Extract `CodeBlock` and `InlineCode` behavior from `markdown.tsx`.
- [x] Add language badge, copy button, stable max height, horizontal scroll inside the block.
- [x] Register common languages: `python`, `py`, `json`, `bash`, `sh`, `ts`, `tsx`, `js`, `jsx`, `diff`, `markdown`.
- [x] Add Python-specific visual treatment for `python`/`py` fences.
- [x] Align `PythonExecutionPanel` styling with `CodeBlock`.
- [x] Add tests for copy button and language detection.
- [x] Add visual/layout regression for long-line containment.

### Phase 3. Streaming Markdown

- [x] Replace pending assistant plain text rendering with Markdown rendering.
- [x] Replace full pending Markdown rendering with `StreamingMarkdownRenderer`.
- [x] Borrow the stable-prefix / unstable-suffix idea from OpenClaude and Hermes.
- [x] Boundary detection must avoid splitting inside:
  - triple-backtick and tilde code fences;
  - display math blocks;
  - tables that are still being streamed.
- [x] Add tests around incremental chunks:
  - partial code fence;
  - partial Python block;
  - partial `$$` math;
  - normal paragraphs.

### Phase 4. Source Evidence In Agent Driver

- [x] Add `SourceEvidence` normalization in `agent_driver`, near observability/tool result utilities.
- [x] Extract from `web_fetch`:
  - URL;
  - title from metadata when present;
  - excerpt/content preview;
  - published time when present.
- [x] Extract from `web_search`:
  - result title;
  - URL;
  - snippet;
  - rank.
- [x] Include normalized sources in terminal metadata and tool completion stream payloads.
- [x] Add unit tests around web tool payload variations.

### Phase 5. Frontend Citation Shelf

- [x] Add `sourceEvidence.ts` for assistant URL extraction, canonicalization, dedupe, and ranking.
- [x] Extend `ChatMessage` metadata with `sources?: SourceEvidence[]`.
- [x] Extend event parsing so tool completion events can update run evidence.
- [x] Add initial `CitationShelf` and `SourceCard` UI for assistant links.
- [x] Render shelf below assistant Markdown and above message actions.
- [x] Add tests for:
  - [x] assistant Markdown links create cards;
  - [x] `web_fetch` events create cards;
  - [x] duplicate links collapse;
  - [x] invalid/internal links are ignored;
  - [x] cards stay accessible by keyboard.

### Phase 6. Web Tool Card Cleanup

- [x] Add specialized `WebSearchPanel` and `WebFetchPanel`.
- [x] Default collapsed state shows human summary: query, result count, fetched domain/title.
- [x] Raw JSON moves into a small “debug payload” details block.
- [x] Keep Phoenix/run metadata available for trace debugging.
- [x] Cover both web search and web fetch rendering in component tests.

### Phase 7. Prompt And Final Answer Convention

- [x] Update dynamic tool-policy prompt fragments to gently ask the model to include source links in final answers after web research.
- [x] Do not require fake citations when no web tool was used.
- [x] Keep the model instruction small: “When you used web_fetch/web_search, cite the concrete pages you relied on with Markdown links.”
- [x] Avoid complex citation DSL unless scenarios show Markdown links are unreliable.

### Phase 8. Live Scenarios And Phoenix Review

Add Playwright/live scenarios:

- [x] `markdown_math`: ask for exponential probability. Assert no raw `$$`, KaTeX is visible, no Python/tool requirement.
- [x] `markdown_code_python`: ask for a Python example. Assert code block header, language badge, copy button.
- [x] `python_tool_answer`: ask exact arithmetic/statistics. Assert Python tool panel appears and final answer is Markdown-rendered.
- [x] `web_fetch_sources`: ask to fetch/current-source answer. Assert source shelf appears under the final assistant answer.
- [x] `web_search_sources`: deterministic SSE smoke covers `web_search` source evidence cards in `web-search-final` and `plan-web-answer`.
- [x] `assistant_link_sources`: fake scenario where answer has Markdown links without tool calls. Assert cards still appear.
- [x] `streaming_partial_fence`: fake streaming chunks with unfinished code/math. Assert no broken layout and final render correct.
- [x] `xss_markdown`: malicious Markdown fixture. Assert no script execution and dangerous links are inert.

Phoenix review criteria:

- The trace shows whether source evidence came from `web_fetch`, `web_search`, or assistant text.
- Final assistant message has a non-empty source list when web tools were used.
- No repeated tool calls only to create citations.
- Markdown rendering changes do not alter runtime decisions.

Latest live check:

- [x] 2026-05-30: Playwright over `http://localhost:5174` verified one live
  answer containing KaTeX math, a Python fenced block with copy button, and a
  `Sources` shelf for a Markdown link. Screenshot:
  `/tmp/chat_markdown_citations_live.png`.
- [x] 2026-05-30: Deterministic Playwright scenarios cover math, Python
  fences, Python tool output, web fetch/search source cards, assistant-link
  source cards, partial streaming fences, and XSS markdown fixtures.

## Acceptance Criteria

- Common Markdown renders in final and streaming assistant messages.
- Math formulas render with KaTeX; raw delimiters do not show for valid formulas.
- Code blocks have headers, language labels, copy buttons, and stable scroll behavior.
- Python tool output and Python Markdown blocks feel visually related.
- Source cards appear when links are in the answer or web pages were fetched.
- Web tool calls no longer expose raw JSON in the main happy path.
- All added rendering paths are covered by unit tests and at least one live Playwright scenario.
- Security tests prove raw HTML/script and unsafe URLs do not execute.
- The implementation keeps reusable evidence logic in `agent_driver`, not buried in chat-demo.

## Risks

- `rehype-sanitize` schema must allow KaTeX/highlight classes without becoming too permissive.
- Streaming Markdown can be expensive if we reparse the whole message per token; use stable-prefix parsing before testing large answers.
- Source cards from assistant links can over-credit arbitrary links; mark them as `linked` and prefer fetched evidence.
- Shiki may increase bundle size; keep it as phase-2 evaluation, not immediate dependency.
- Tables and code blocks can create horizontal page scrollbars if container dimensions are not constrained.

## First Concrete PR Slice

1. Add math/sanitize deps and tests.
2. Upgrade `MarkdownRenderer` and render it during streaming with a conservative fallback.
3. Add `CodeBlock` UI with copy button.
4. Add a frontend-only citation shelf from assistant Markdown links.
5. Then add runtime `SourceEvidence` from web tools and wire it to the shelf.

This order gives a visible UX win early while preserving the deeper runtime contract work for source cards.
