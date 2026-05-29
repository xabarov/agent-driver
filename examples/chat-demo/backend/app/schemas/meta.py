"""Response schemas for health/provider/tool endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderStatusView(BaseModel):
    """Public provider status payload."""

    provider_name: str
    provider_kind: str
    healthy: bool
    configured: bool
    latency_ms: float | None = None
    avg_latency_ms: float | None = None
    request_count: int
    error_count: int


class HealthResponse(BaseModel):
    """Health endpoint response."""

    ok: bool = True
    store_kind: str
    provider: ProviderStatusView


class ProviderResponse(BaseModel):
    """Provider endpoint response."""

    name: str
    model: str | None = None
    base_url: str | None = None
    status: ProviderStatusView


class ToolManifestView(BaseModel):
    """Serializable view of selected tool manifest."""

    name: str
    description: str
    risk: str
    side_effect: str = Field(alias="sideEffect")
    approval_mode: str = Field(alias="approvalMode")

    model_config = {"populate_by_name": True}


class WorkspaceStatusView(BaseModel):
    """Public status for the session workspace visible to file tools."""

    mode: str = "session"
    root: str
    session_id: str | None = Field(default=None, alias="sessionId")
    exists: bool
    file_count: int = Field(alias="fileCount")
    sample_available: bool = Field(alias="sampleAvailable")

    model_config = {"populate_by_name": True}


class ToolsResponse(BaseModel):
    """Tool list endpoint response."""

    tools: list[ToolManifestView]
    workspace: WorkspaceStatusView


class WorkspaceImportResponse(BaseModel):
    """Response after importing demo files into a session workspace."""

    ok: bool = True
    files: list[str]
    workspace: WorkspaceStatusView


class ModelView(BaseModel):
    """OpenRouter-compatible model entry."""

    id: str
    name: str | None = None
    description: str | None = None
    context_length: int | None = None


class ModelsResponse(BaseModel):
    """Models list endpoint response."""

    provider: str
    models: list[ModelView]

