"""Schemas for chat message streaming endpoint."""

from __future__ import annotations

from typing import Literal

from app.config import ToolPreset
from pydantic import BaseModel, Field

from agent_driver.contracts import ControlKind, ControlPriority

ResearchMode = Literal["chat", "web", "deep"]
ResearchProfile = Literal["light", "medium", "hard"]
ProfileSource = Literal[
    "user_selected",
    "auto_suggested",
    "backend_classified",
    "scenario_forced",
]


class HardResearchOptions(BaseModel):
    """Explicit opt-ins for high-cost hard research fallbacks."""

    allow_pdf_read: bool = True
    allow_browser_read: bool = False
    allow_browser_action: bool = False


class ChatMessageRequest(BaseModel):
    """Input payload for starting one streamed chat run."""

    session_id: str | None = None
    message: str = Field(min_length=1)
    tool_preset: ToolPreset | None = None
    force_planning: bool | None = None
    model: str | None = None
    retry_from_run_id: str | None = None
    client_request_id: str | None = None
    scenario_id: str | None = None
    research_depth: Literal["deep_parallel_research"] | None = None
    research_mode: ResearchMode | None = None
    research_profile: ResearchProfile | None = None
    profile_source: ProfileSource | None = None
    hard_options: HardResearchOptions | None = None


class ResumeRequest(BaseModel):
    """Resume payload for a paused run awaiting human input."""

    interrupt_id: str
    action: str
    tool_preset: ToolPreset | None = None
    model: str | None = None
    edited_tool_args: dict[str, object] | None = None
    message: str | None = None


class InterruptView(BaseModel):
    """Pending interrupt details for UI."""

    run_id: str
    interrupt_id: str
    reason: str
    title: str | None = None
    description: str | None = None
    proposed_action: dict[str, object] = Field(default_factory=dict)
    allowed_actions: list[str] = Field(default_factory=list)


class ReplayResponse(BaseModel):
    """Replay payload with normalized stream events."""

    run_id: str
    events: list[dict[str, object]]


class CancelRunResponse(BaseModel):
    """Response for cooperative run cancellation."""

    ok: bool = True
    run_id: str
    cancelled: bool


class ChatControlRequest(BaseModel):
    """Steering control payload for a live or resumable chat run."""

    kind: ControlKind
    priority: ControlPriority = ControlPriority.NEXT
    payload: dict[str, object] = Field(default_factory=dict)
    thread_id: str | None = None
    agent_id: str | None = None
    dedupe_key: str | None = None


class ChatControlResponse(BaseModel):
    """Accepted/cancelled steering command response."""

    ok: bool
    control_id: str | None = None
    queue_id: str | None = None
    error: str | None = None


class DeepResearchArtifactState(BaseModel):
    """UI-facing state for one research artifact."""

    path: str
    kind: str
    size_bytes: int = Field(alias="sizeBytes")
    modified_at: str | None = Field(default=None, alias="modifiedAt")
    lifecycle: str = "not_started"
    preview_available: bool = Field(default=False, alias="previewAvailable")

    model_config = {"populate_by_name": True}


class DeepResearchArtifactsState(BaseModel):
    """UI-facing Deep Research artifact summary."""

    report: DeepResearchArtifactState | None = None
    source_ledger: DeepResearchArtifactState | None = Field(
        default=None,
        alias="sourceLedger",
    )
    claims: DeepResearchArtifactState | None = None

    model_config = {"populate_by_name": True}


class DeepResearchSourceCounts(BaseModel):
    """Source status counters for the research cockpit."""

    verified: int = 0
    candidates: int = 0
    blocked: int = 0
    failed: int = 0
    distinct_domains: int = Field(default=0, alias="distinctDomains")
    required_verified: int = Field(default=0, alias="requiredVerified")
    quality_status: str = Field(default="unknown", alias="qualityStatus")
    quality_ok: bool = Field(default=False, alias="qualityOk")
    rows: list["DeepResearchSourceRow"] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class DeepResearchSourceRow(BaseModel):
    """One source ledger row for the research cockpit."""

    status: str
    title: str | None = None
    url: str | None = None
    domain: str | None = None
    reason: str | None = None

    model_config = {"populate_by_name": True}


class DeepResearchTodoState(BaseModel):
    """Todo progress projection for Deep Research UI."""

    done: int = 0
    total: int = 0
    current: str | None = None
    stale: bool = False


class DeepResearchSubagentState(BaseModel):
    """Parent-level subagent summary."""

    total_children: int = Field(default=0, alias="totalChildren")
    running_children: int = Field(default=0, alias="runningChildren")
    completed_children: int = Field(default=0, alias="completedChildren")
    failed_children: int = Field(default=0, alias="failedChildren")
    duplicated_queries: int = Field(default=0, alias="duplicatedQueries")
    tool_names: list[str] = Field(default_factory=list, alias="toolNames")
    summary_chars: int = Field(default=0, alias="summaryChars")
    source_records: int = Field(default=0, alias="sourceRecords")

    model_config = {"populate_by_name": True}


class DeepResearchMetricsState(BaseModel):
    """Token/tool/artifact metrics for the research cockpit."""

    prompt_tokens: int | None = Field(default=None, alias="promptTokens")
    completion_tokens: int | None = Field(default=None, alias="completionTokens")
    total_tokens: int | None = Field(default=None, alias="totalTokens")
    web_search_count: int = Field(default=0, alias="webSearchCount")
    web_fetch_count: int = Field(default=0, alias="webFetchCount")
    report_full_write_count: int = Field(default=0, alias="reportFullWriteCount")
    report_patch_count: int = Field(default=0, alias="reportPatchCount")
    long_chat_before_report_chars: int = Field(
        default=0,
        alias="longChatBeforeReportChars",
    )

    model_config = {"populate_by_name": True}


class DeepResearchTraceState(BaseModel):
    """Trace summary subset for the research cockpit."""

    run_id: str = Field(alias="runId")
    verdict: str | None = None
    terminal_event: str | None = Field(default=None, alias="terminalEvent")
    failure_flags: list[str] = Field(default_factory=list, alias="failureFlags")

    model_config = {"populate_by_name": True}


class DeepResearchViewState(BaseModel):
    """Canonical run-level Deep Research UI projection."""

    run_id: str = Field(alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    research_mode: str = Field(default="unknown", alias="researchMode")
    profile: str = "unknown"
    profile_source: str = Field(default="unknown", alias="profileSource")
    phase: str = "starting"
    phase_source: str = Field(default="trace_summary", alias="phaseSource")
    readiness: str = "needs_review"
    todos: DeepResearchTodoState = Field(default_factory=DeepResearchTodoState)
    artifacts: DeepResearchArtifactsState = Field(
        default_factory=DeepResearchArtifactsState
    )
    sources: DeepResearchSourceCounts = Field(default_factory=DeepResearchSourceCounts)
    subagents: DeepResearchSubagentState = Field(
        default_factory=DeepResearchSubagentState
    )
    metrics: DeepResearchMetricsState = Field(default_factory=DeepResearchMetricsState)
    warnings: list[str] = Field(default_factory=list)
    trace: DeepResearchTraceState

    model_config = {"populate_by_name": True}
