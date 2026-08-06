"""Option B1 foundation: Condenser protocol + cost-ordered pipeline."""

import pytest

from agent_driver.context.compaction.condenser import (
    CondenseContext,
    CondenseResult,
    Condenser,
    CondenserPipeline,
    message_chars,
)
from agent_driver.contracts.messages import ChatMessage


def _msgs(*sizes: int) -> list[ChatMessage]:
    return [ChatMessage(role="user", content="x" * n) for n in sizes]


class _FreeChars:
    """A fake condenser that drops the given number of chars from the last message."""

    def __init__(self, name: str, free: int, *, applies: bool = True):
        self.name = name
        self._free = free
        self._applies = applies
        self.called = False

    def applies(self, ctx: CondenseContext) -> bool:
        return self._applies

    async def condense(self, messages, *, ctx):
        self.called = True
        out = list(messages)
        if out:
            content = str(out[-1].content or "")
            keep = max(0, len(content) - self._free)
            out[-1] = ChatMessage(role=out[-1].role, content=content[:keep])
        freed = message_chars(messages) - message_chars(out)
        return CondenseResult(messages=out, changed=freed > 0, chars_freed=freed)


def test_pipeline_result_type_is_a_condenser():
    assert isinstance(_FreeChars("c", 1), Condenser)


@pytest.mark.asyncio
async def test_cheap_tier_short_circuits_expensive_one():
    cheap = _FreeChars("cheap", free=600)
    expensive = _FreeChars("expensive", free=10_000)
    pipe = CondenserPipeline([cheap, expensive])
    ctx = CondenseContext(target_chars=500)
    result = await pipe.run(_msgs(1000), ctx=ctx)
    assert cheap.called and not expensive.called  # stopped once it fit
    assert result.fit and not result.exhausted
    assert message_chars(result.messages) <= 500


@pytest.mark.asyncio
async def test_pipeline_reports_exhausted_when_nothing_fits():
    weak = _FreeChars("weak", free=10)
    pipe = CondenserPipeline([weak])
    result = await pipe.run(_msgs(1000), ctx=CondenseContext(target_chars=100))
    assert not result.fit and result.exhausted


@pytest.mark.asyncio
async def test_minimum_progress_rejects_ineffective_pass():
    # frees 50 of a 1000-char input = 5% < 10% floor -> rejected, unchanged.
    weak = _FreeChars("weak", free=50)
    pipe = CondenserPipeline([weak], minimum_progress=0.1)
    result = await pipe.run(_msgs(1000), ctx=CondenseContext(target_chars=100))
    assert message_chars(result.messages) == 1000  # rejected -> no change
    assert result.applied[0]["rejected"] == "insufficient_progress"


@pytest.mark.asyncio
async def test_applies_gate_skips_condenser():
    skipped = _FreeChars("skip", free=10_000, applies=False)
    pipe = CondenserPipeline([skipped])
    result = await pipe.run(_msgs(1000), ctx=CondenseContext(target_chars=100))
    assert not skipped.called
    assert result.exhausted


@pytest.mark.asyncio
async def test_already_fitting_input_runs_nothing():
    c = _FreeChars("c", free=10_000)
    pipe = CondenserPipeline([c])
    result = await pipe.run(_msgs(50), ctx=CondenseContext(target_chars=100))
    assert not c.called and result.fit
