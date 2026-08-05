"""EPIC-03 WP-D — bridge backend execution artifacts (scenario 9).

A large command output that the backend spilled to a content-addressed artifact
becomes a digest- and size-bearing reference with only a bounded preview in
model context — never an implicit full-content load.
"""

from pathlib import Path

import pytest

import agent_driver.execution as ex
from agent_driver.execution.adapters import BackendCommandRunner
from agent_driver.tools.builtin.shell import _bash_handler
from agent_driver.tools.context import command_runner_scope, workspace_cwd_scope


def _artifact():
    return ex.ArtifactRef(
        artifact_id="a1",
        digest="sha256:abc",
        size_bytes=1_048_576,
        media_type="text/plain",
        backend_id="fake",
        execution_id="e1",
    )


def test_execution_artifact_to_context_ref_preserves_digest_and_size():
    ref = ex.execution_artifact_to_context_ref(_artifact())
    assert ref.artifact_id == "a1"
    assert ref.size_bytes == 1_048_576
    assert ref.metadata["digest"] == "sha256:abc"
    assert ref.metadata["media_type"] == "text/plain"
    assert ref.metadata["backend_id"] == "fake"
    assert ref.metadata["execution_id"] == "e1"


def test_reference_payload_is_bounded_and_carries_identity():
    payload = ex.execution_artifact_reference_payload(
        _artifact(), preview="x" * 5000, max_preview_chars=100
    )
    assert payload["persisted"] is True
    assert payload["digest"] == "sha256:abc"
    assert payload["size_bytes"] == 1_048_576
    assert len(payload["preview"]) == 100  # bounded, not the full content
    assert payload["preview_truncated"] is True


@pytest.mark.asyncio
async def test_backend_command_runner_propagates_artifact():
    fake = ex.FakeExecutionBackend(
        commands={"c": ex.CommandOutcome(stdout="s", artifact=_artifact())}
    )
    out = await BackendCommandRunner(fake).run_command("c", cwd="/w", timeout_seconds=1)
    assert out["artifact"] is not None
    assert out["artifact"].digest == "sha256:abc"


@pytest.mark.asyncio
async def test_bash_handler_surfaces_bounded_artifact_reference():
    fake = ex.FakeExecutionBackend(
        commands={
            "echo hi": ex.CommandOutcome(stdout="preview text", artifact=_artifact())
        }
    )
    with (
        workspace_cwd_scope(Path("/tmp")),
        command_runner_scope(BackendCommandRunner(fake)),
    ):
        result = await _bash_handler({"command": "echo hi"})
    art = result["artifact"]
    assert art["digest"] == "sha256:abc" and art["size_bytes"] == 1_048_576
    assert art["persisted"] is True
    assert "preview" in art  # bounded reference, not full content


@pytest.mark.asyncio
async def test_bash_handler_without_artifact_has_no_reference():
    fake = ex.FakeExecutionBackend(commands={"echo hi": ex.CommandOutcome(stdout="ok")})
    with (
        workspace_cwd_scope(Path("/tmp")),
        command_runner_scope(BackendCommandRunner(fake)),
    ):
        result = await _bash_handler({"command": "echo hi"})
    assert "artifact" not in result
