# Chat Demo Design Improvement Plan

Date: 2026-05-29

Scope: `examples/chat-demo/frontend`, reviewed against the running local app at `http://localhost:5174`.

## Playwright Audit

The audit used Playwright against the live Vite app on `localhost:5174` with desktop `1440x900` and mobile `390x844` viewports. Scenarios covered:

- New-session empty state at `/sessions/new`.
- Composer focus and typed prompt state, without submitting to the live `openrouter` provider.
- Tools preset popover from the composer.
- Model picker dropdown and search affordance.
- Sidebar session search.
- Mobile shell, header wrapping, composer, and sidebar toggle.

Observed state:

- Desktop layout is stable and functional, but the first-run surface feels under-designed: the empty state is a single card floating in a large black canvas, while the composer and header carry most of the interaction weight.
- Mobile layout is usable, but the header grows to about 145px because provider badge, model picker, and theme control wrap into multiple rows. This pushes the chat content down and makes the first viewport feel cramped.
- The model picker exposes a long provider model list in a narrow dropdown. Search exists, but there is no grouping, recent/default section, provider context, or selected-state hierarchy beyond subtle background color.
- The tools popover is understandable, but it opens low over the composer with a full-screen dim layer. This makes tool selection feel modal even though it is a compact setting.
- Sidebar search works, and sessions filter correctly, but session cards compete with the delete action visually. The current destructive icon is always visible and red, which adds noise to a scanning-heavy area.
- Empty/loading/error states are text-first and generic. They communicate mechanics, but they do not teach the demo's capabilities or make the agent-driver runtime concepts visible.
- The palette is almost entirely neutral dark UI. It is appropriate for an operational tool, but it needs stronger hierarchy, accent semantics, and calmer surfaces to avoid reading as unfinished.

## Design Direction

Use a restrained "agent operations console" direction: dense, quiet, highly legible, with subtle runtime telemetry. The demo should feel like a working chat client for inspecting agent behavior, not a landing page or decorative showcase.

Product scope update, 2026-05-29:

- The public web demo is intentionally web-tools-only. Filesystem, workspace mutation, shell, glob, and grep controls are hidden from the user-facing Tools picker.
- Planning remains an available agent/runtime capability, not a user-facing tool preset. The agent can use it when a task needs planning, while simple answers can stay direct. The UI should show planning outcomes, approvals, snapshots, and policy-denied states when they occur, but should not expose raw planning tool handles as manual controls.
- Internal presets may still exist for runtime development, but filesystem-write workspace scenarios are no longer covered as chat-demo web integration behavior.

Principles:

- Preserve the current practical shell: sidebar, header controls, message stream, composer.
- Make runtime concepts visible where users act: provider health, model, tool preset, run count, workspace state, streaming state.
- Reduce empty black space by giving the empty state useful starting affordances and system context.
- Keep controls compact and predictable; avoid hero-style UI, oversized cards, and decorative gradients.
- Improve mobile by prioritizing active task controls over static labels.

## Phase 1 - Triage And Layout Foundations

- [x] Tighten app shell spacing.
  - Target files: `AppShell.tsx`, `Header.tsx`, `ChatPage.tsx`, `ChatComposer.tsx`.
  - Desktop: keep the current split, but reduce the visual gap between header, empty state, and composer.
  - Mobile: collapse the header into a two-row maximum: title/menu row, then horizontally scrollable runtime controls.

- [x] Replace the empty state card with a useful start panel.
  - Target files: `EmptyState.tsx`, `ChatPage.tsx`.
  - Include 3-4 prompt chips for demo-relevant tasks such as planning, tool use, workspace inspection, and replay.
- Show current web tool mode and provider status in small inline metadata.
  - Keep the panel compact enough that the composer remains visually connected on desktop and mobile.

- [x] Improve composer hierarchy.
  - Target file: `ChatComposer.tsx`.
  - Make send disabled/enabled state clearer through opacity, focus ring, and icon contrast.
  - Keep tool preset button compact on mobile, using icon plus preset label.
  - Add a subtle top edge or shadow only when content scrolls behind the composer.

- [x] Reduce sidebar visual noise.
  - Target files: `Sidebar.tsx`, `SessionList.tsx`, `SessionItem.tsx`.
  - Show destructive delete action on hover/focus for desktop and via an overflow/menu affordance on touch.
  - Make active session selection stronger than inactive cards.
  - Add an empty search result state.

- [ ] Verification checklist.
  - [x] Desktop screenshot at `1440x900`: empty state, composer, sidebar.
  - [x] Mobile screenshot at `390x844`: header does not exceed two rows.
  - [ ] Keyboard tab order reaches sidebar search, new session, model picker, tools picker, composer, send.
  - [x] No text overlaps in English and with an existing Cyrillic session title.

Phase 1 implementation notes, 2026-05-29:

- Added prompt starter actions to `EmptyState.tsx`; selecting one sends the prompt through the existing stream path.
- Moved the mobile sidebar trigger into the main header row and made runtime controls horizontally scrollable on narrow screens.
- Tightened composer radius/surface treatment and made the tool preset label compact on mobile.
- Reduced permanent destructive noise in `SessionItem.tsx`; delete is hover/focus-forward on desktop and still reachable on touch.
- Verified with `pnpm build`, Vitest, and Playwright screenshots at `1440x900` and `390x844`.

## Phase 2 - Runtime Controls And Menus

- [ ] Redesign the model picker.
  - Target file: `ModelPicker.tsx`.
  - Add sections for selected/default, recent, and all models.
  - Keep a sticky search input inside the menu.
  - Show provider/model distinction with a compact monospace ID and optional display name.
  - Cap dropdown height and make scroll behavior obvious.

- [ ] Improve provider health presentation.
  - Target file: `Header.tsx`.
  - Replace "unknown" as a normal-looking state with explicit loading/offline/healthy states.
  - Add a tooltip or accessible label with provider health details.
  - Avoid red status on first paint while health is still loading.

- [x] Refine tools preset popover.
  - Target files: `ChatComposer.tsx`, `ToolsPicker.tsx`.
  - Remove or soften the full-screen dim layer for compact desktop use.
  - On mobile, use a bottom sheet or full-width popover anchored above composer.
  - Keep only user-facing web toggles in the public demo.
- Communicate that planning is available to the agent without exposing raw planning tool names.

- [ ] Add run context where it helps.
  - Target files: `Header.tsx`, `SessionRunsMenu.tsx`, `MessageMetadataPopover.tsx`.
  - Surface current run ID/count in the header when a session is active.
  - Keep token metadata out of the primary header until an assistant message exists.

- [ ] Verification checklist.
  - [ ] Open model picker, search, select a model, reopen and confirm selected state.
  - [x] Open tools picker, change presets, confirm popover placement and enabled tool count.
  - [ ] Simulate loading/error health state and verify visual copy.
  - [ ] Confirm all icon-only controls have accessible names and tooltips where useful.

Tools implementation note, 2026-05-29:

- Replaced the old `Off/Safe/Workspace/Dev/All` web picker with user-facing **Web Search** and **Web Fetch** toggles.
- Planning tools remain available inside the agent toolset when useful, but are no longer shown or configured in the web UI.
- Local file, glob/grep, and shell tools are not exposed by the web picker or the public `/api/tools` response.
- Backend still accepts legacy/internal presets for test and development scenarios.

## Phase 3 - Message Stream Polish

- [ ] Improve message rhythm and density.
  - Target files: `MessageList.tsx`, `MessageBubble.tsx`.
  - Reduce avatar prominence or align it with message metadata.
  - Make assistant messages feel like readable documents, not generic bubbles, especially for markdown/code.
  - Keep user messages compact and clearly right-aligned.

- [ ] Make streaming state more legible.
  - Target files: `AssistantStreaming.tsx`, `MessageBubble.tsx`, `ChatComposer.tsx`.
  - Add a subtle streaming row that differentiates "thinking", "tool running", and "writing" if event data supports it.
  - Ensure stop action is visually clear while streaming.

- [ ] Upgrade planning/tool cards.
  - Target files: `PlanningCard.tsx`, `ToolCallCard.tsx`, `InterruptCard.tsx`.
  - Use consistent status badges, compact headers, and collapsible details.
  - Make tool risk/approval states scannable without long explanatory text.
  - Show hidden runtime activity, such as plan approvals and policy-denied filesystem attempts, as replayable outcomes rather than as manual tool controls.
  - Align card radius and borders with the rest of the UI.

- [ ] Improve message actions.
  - Target files: `MessageActions.tsx`, `MessageMetadataPopover.tsx`.
  - Show actions on hover/focus with stable reserved space to avoid layout shift.
  - Group retry, copy, metadata, and delete by intent.

- [ ] Verification checklist.
  - [ ] Existing session with multiple runs renders without layout jump.
  - [ ] Markdown, code blocks, planning snapshots, and tool call cards remain readable in dark and light themes.
  - [ ] Long assistant output scrolls smoothly with composer pinned.
  - [ ] Retry/delete/copy controls are reachable by keyboard.

## Phase 4 - Responsive And Accessibility Pass

- [ ] Define responsive shell rules.
  - Desktop: persistent sidebar, compact header, centered message column.
  - Tablet: narrower sidebar or collapsible sidebar with retained session context.
  - Mobile: hidden sidebar, compact header controls, composer above safe-area padding.

- [ ] Add viewport-specific QA.
  - `390x844`: common mobile.
  - `768x1024`: tablet.
  - `1440x900`: laptop/desktop.
  - `1920x1080`: wide desktop, ensure content does not feel stranded.

- [ ] Improve focus and contrast.
  - Use visible focus rings on all controls.
  - Check muted text against dark background.
  - Confirm disabled controls are distinguishable from inactive secondary controls.

- [ ] Verify reduced motion compatibility.
  - Any new transitions should respect `prefers-reduced-motion`.
  - Avoid animations that affect reading or streaming output.

- [ ] Verification checklist.
  - [ ] Run Playwright screenshots for all target viewports.
  - [ ] Run keyboard-only flow for new session, search, model, tools, composer.
  - [ ] Check accessible names for icon buttons: menu, theme, send, stop, delete, metadata.
  - [ ] Confirm no header/composer text truncation hides critical state.

## Phase 5 - Test Coverage And Design Regression Guardrails

- [ ] Add Playwright smoke tests for user scenarios.
  - Suggested location: `examples/chat-demo/frontend/tests/e2e/` if Playwright becomes a project dependency, or a documented external script if not.
  - Scenarios: empty state, sidebar search, model search/select, tool preset change, composer typed state, mobile sidebar open/close.

- [ ] Add visual assertions for layout invariants.
  - Header height on mobile stays below the agreed threshold.
  - Composer remains visible and does not overlap message content.
  - Model picker and tools picker stay within viewport.

- [ ] Add component tests where interaction is local.
  - `ModelPicker`: search filtering, selected state, loading/error states.
- `ToolsPicker`: Web Search/Web Fetch toggles, legacy preset normalization, hidden planning/filesystem internals.
  - `SessionItem`: active state, delete affordance, long titles.

- [ ] Document manual QA before releases.
  - Add a short checklist to `examples/chat-demo/README.md` or keep this document linked from `docs/README.md`.

## Acceptance Criteria

- [ ] A new user can understand what to try from the first screen without reading external docs.
- [ ] The app feels like an agent runtime console: provider, model, tools, runs, and workspace state are visible but not noisy.
- [ ] Mobile first viewport shows title, essential controls, empty/message content, and composer without awkward wrapping.
- [ ] Model and tools controls are usable with long real-world OpenRouter model names.
- [ ] Public tools UI exposes web search/fetch controls only; filesystem and shell capabilities stay hidden from the default web demo.
- [ ] Planning is visible through outcomes and approvals, but remains agent-controlled and is not presented as a manual user tool.
- [ ] Sidebar supports scanning and session management without permanent destructive visual noise.
- [ ] Empty, loading, error, streaming, interrupt, tool-call, and planning states share a coherent visual language.
- [ ] Playwright smoke coverage exists for the core scenarios listed above.

## Suggested Implementation Order

1. Phase 1 first, because it fixes the biggest perceived quality issues without changing backend behavior.
2. Phase 2 next, because model/tools/provider controls are the highest-friction interactive surfaces.
3. Phase 3 after real transcripts are visually sampled from existing sessions.
4. Phase 4 as a hardening pass before merging broad UI changes.
5. Phase 5 once the target behavior stabilizes, so tests encode the intended design rather than the current rough edges.
