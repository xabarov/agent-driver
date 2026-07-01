"""Deterministic fake providers used by the offline CLI eval harness."""

from __future__ import annotations

from agent_driver.contracts import ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider


class _EvalGammaStdlibFakeProvider(FakeProvider):
    """Offline gamma stats: scipy attempt (policy block) then stdlib math."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="eval-gamma"),
                provider="fake",
                model="eval-gamma",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="python",
                            args={
                                "code": (
                                    "from scipy.stats import gamma\n"
                                    "import numpy as np"
                                ),
                                "session_id": "gamma_eval",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self._calls == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="eval-gamma"),
                provider="fake",
                model="eval-gamma",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="python",
                            args={
                                "code": (
                                    "import math\n"
                                    "m1, m2 = 3.2, 67.0\n"
                                    "var = m2 - m1 * m1\n"
                                    "theta = var / m1\n"
                                    "a = m1 / theta\n"
                                    "z = 5.0 / theta\n"
                                    "print((a, theta, z))"
                                ),
                                "session_id": "gamma_eval",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role="assistant",
                content=(
                    "Gamma parameters from moments (shape a, scale theta). "
                    "P(X>5) should be computed with math/statistics only; "
                    "scipy/numpy are blocked by sandbox policy."
                ),
            ),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="eval-gamma"),
            provider="fake",
            model="eval-gamma",
        )


class _EvalGammaScipyFakeProvider(FakeProvider):
    """Offline gamma stats with scipy allowed: scipy.stats then numeric tail prob."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(
                    model_provider="fake", model_name="eval-gamma-scipy"
                ),
                provider="fake",
                model="eval-gamma-scipy",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="python",
                            args={
                                "code": (
                                    "import scipy.stats as stats\n"
                                    "m1, m2 = 3.2, 66.0\n"
                                    "var = m2 - m1 * m1\n"
                                    "theta = var / m1\n"
                                    "a = m1 / theta\n"
                                    "print((a, theta))"
                                ),
                                "session_id": "gamma_scipy_eval",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self._calls == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(
                    model_provider="fake", model_name="eval-gamma-scipy"
                ),
                provider="fake",
                model="eval-gamma-scipy",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="python",
                            args={
                                "code": (
                                    "import scipy.stats as stats\n"
                                    "m1, m2 = 3.2, 66.0\n"
                                    "var = m2 - m1 * m1\n"
                                    "theta = var / m1\n"
                                    "a = m1 / theta\n"
                                    "p = 1.0 - stats.gamma.cdf(5.0, a, scale=theta)\n"
                                    "print(p)"
                                ),
                                "session_id": "gamma_scipy_eval",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role="assistant",
                content=(
                    "Gamma tail P(X>5) with m1=3.2, m2=66 using scipy.stats.gamma.cdf: "
                    "approximately 0.826."
                ),
            ),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="eval-gamma-scipy"),
            provider="fake",
            model="eval-gamma-scipy",
        )


class _EvalPandasLinalgFakeProvider(FakeProvider):
    """Offline 2x2 linear solve using pandas/numpy."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(
                    model_provider="fake", model_name="eval-pandas-linalg"
                ),
                provider="fake",
                model="eval-pandas-linalg",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="python",
                            args={
                                "code": (
                                    "import numpy as np\n"
                                    "import pandas as pd\n"
                                    "A = np.array([[3.0, 1.0], [1.0, 2.0]])\n"
                                    "b = np.array([9.0, 8.0])\n"
                                    "x = np.linalg.solve(A, b)\n"
                                    "print(tuple(float(v) for v in x))"
                                ),
                                "session_id": "pandas_linalg_eval",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(
                role="assistant",
                content="Solution x ≈ (2.0, 1.5) from numpy.linalg.solve.",
            ),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="eval-pandas-linalg"),
            provider="fake",
            model="eval-pandas-linalg",
        )


class _EvalInterruptFakeProvider(FakeProvider):
    """Deterministic provider: one gated file_write, then final answer."""

    def __init__(self, *, target_path: str) -> None:
        super().__init__(response_text="done")
        self._target_path = target_path
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="eval-interrupt"),
                provider="fake",
                model="eval-interrupt",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={
                                "path": self._target_path,
                                "content": "interrupt-resume-ok\n",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="write completed"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="eval-interrupt"),
            provider="fake",
            model="eval-interrupt",
        )
