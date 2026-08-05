"""EPIC-02 WP-D/E — environment brief injection + capability diagnostics.

Driven through a real agent run: a capturing provider records the exact
LlmRequest the model would see, so we assert the brief reaches request-only
context and the redaction-safe capability audit reaches request metadata.
"""

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.execution import (
    CapabilityName,
    CapabilityState,
    CapabilityStatus,
    ExecutionCapabilitySnapshot,
    FakeExecutionBackend,
    ProgramInfo,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent


class _CaptureProvider(FakeProvider):
    def __init__(self):
        super().__init__(response_text="done")
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return await super().complete(request)


def _snapshot():
    return ExecutionCapabilitySnapshot(
        backend_id="fake",
        environment_revision="env-rev",
        digest="dig-42",
        capabilities={
            CapabilityName.COMMAND: CapabilityStatus(state=CapabilityState.SUPPORTED),
            CapabilityName.FILE_READ: CapabilityStatus(state=CapabilityState.SUPPORTED),
            CapabilityName.OUTPUT: CapabilityStatus(state=CapabilityState.DEGRADED),
        },
        programs=(ProgramInfo(name="python", version="3.12"),),
        limitations=("no network access",),
    )


def _run_input(run_id):
    return AgentRunInput(
        input="hello", run_id=run_id, agent_id="a", graph_preset="single_react"
    )


def _messages_text(request):
    return "\n".join(str(getattr(m, "content", "") or "") for m in request.messages)


@pytest.mark.asyncio
async def test_brief_injected_and_names_capability_revision():
    provider = _CaptureProvider()
    backend = FakeExecutionBackend(capability_snapshot=_snapshot())
    agent = create_agent(provider=provider)

    await agent.run(_run_input("brief1"), execution_backend=backend)

    assert provider.requests, "provider should have been called"
    text = _messages_text(provider.requests[0])
    assert "Execution environment (capability revision dig-42)" in text
    assert "command" in text and "file_read" in text  # supported listed
    assert "python 3.12" in text  # program inventory
    assert "no network access" in text  # limitation


@pytest.mark.asyncio
async def test_capability_audit_on_request_metadata_is_redaction_safe():
    provider = _CaptureProvider()
    backend = FakeExecutionBackend(capability_snapshot=_snapshot())
    agent = create_agent(provider=provider)

    await agent.run(_run_input("audit1"), execution_backend=backend)

    audit = provider.requests[0].metadata.get("capability_audit")
    assert audit is not None
    assert audit["backend_id"] == "fake"
    assert audit["capability_revision"] == "dig-42"
    assert "command" in audit["supported"]
    assert "output" in audit["degraded"]
    # no secret/metadata values leaked — only names/revisions/counts
    assert set(audit).issuperset({"backend_id", "supported", "withheld_tools"})


@pytest.mark.asyncio
async def test_no_backend_injects_no_brief():
    # Scenario 9: default run (no backend) is unchanged — no brief, no audit.
    provider = _CaptureProvider()
    agent = create_agent(provider=provider)

    await agent.run(_run_input("nobrief"))

    text = _messages_text(provider.requests[0])
    assert "Execution environment (capability revision" not in text
    assert provider.requests[0].metadata.get("capability_audit") is None
