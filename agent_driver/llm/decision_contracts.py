"""Validated, provider-neutral contracts for bounded semantic decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import ConfigDict, Field, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.usage import UsageSummary

DecisionQuestionType = Literal["choice", "score", "noul"]
DecisionCriteria = dict[str, str] | list[str]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
TokenCount = Annotated[int, Field(strict=True, ge=0)]
Identifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:/~+@-]+$"),
]
ProviderName = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$"),
]


class DecisionError(RuntimeError):
    """Safe error code, without HTTP bodies, credentials or input state."""

    code = "decision_error"

    def __init__(self, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(self.code)


class DecisionTransportError(DecisionError):
    """An HTTP transport failure; completion/billing may be unknown."""

    code = "transport_error"


class DecisionTimeoutError(DecisionError):
    """The total decision deadline expired."""

    code = "timeout"


class DecisionRequestError(DecisionError):
    """The endpoint rejected a request with a non-success HTTP status."""

    code = "request_rejected"


class DecisionProtocolError(DecisionError):
    """The response does not satisfy the requested decision schema."""

    code = "invalid_response"


class DecisionCircuitOpenError(DecisionError):
    """The provider is cooling down after consecutive failures."""

    code = "circuit_open"


class DecisionQuestion(ContractModel):
    """Question ids are correlation keys; instructions carry all semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: DecisionQuestionType
    instructions: str = Field(strict=True, min_length=1)
    criteria: DecisionCriteria | None = None

    @model_validator(mode="after")
    def validate_criteria(self) -> DecisionQuestion:
        """Reject incomplete or mismatched question definitions before HTTP."""
        if not self.instructions.strip():
            raise ValueError("instructions must not be blank")
        criteria = self.criteria
        if self.type == "choice":
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                raise ValueError("choice requires between two and 255 named criteria")
        elif self.type == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError("score requires between two and ten ordered criteria")
        elif criteria is not None:
            if not isinstance(criteria, dict) or set(criteria) != {"true", "false"}:
                raise ValueError("noul criteria must describe true and false")
        if criteria is not None:
            values = criteria.values() if isinstance(criteria, dict) else criteria
            if any(not value.strip() for value in values):
                raise ValueError("criteria descriptions must not be blank")
            if isinstance(criteria, dict) and any(not key.strip() for key in criteria):
                raise ValueError("criterion labels must not be blank")
        return self

    def to_payload(self) -> dict[str, Any]:
        """Return a detached wire payload."""
        return self.model_dump(exclude_none=True)


class DecisionAnswer(ContractModel):
    """One validated choice, ordinal score, or probability of yes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    question_id: str
    type: DecisionQuestionType
    choice: str | None = Field(default=None, strict=True)
    probabilities: dict[str, Probability] | None = None
    confidence: Probability | None = None
    score: NonNegative | None = None
    noul: Probability | None = None
    legend: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_value(self) -> DecisionAnswer:
        """Require the fields appropriate to each primitive, including zero."""
        if self.type == "noul":
            if self.noul is None:
                raise ValueError("noul probability is required")
            if any(
                v is not None
                for v in (
                    self.choice,
                    self.score,
                    self.probabilities,
                    self.confidence,
                    self.legend,
                )
            ):
                raise ValueError("noul contains incompatible fields")
            return self
        probs = self.probabilities
        if not probs or self.confidence is None:
            raise ValueError("distribution and confidence are required")
        if abs(sum(probs.values()) - 1.0) > 0.02:
            raise ValueError("probabilities must sum to one within rounding tolerance")
        if self.noul is not None:
            raise ValueError("choice/score cannot contain noul")
        if self.type == "choice" and self.legend is not None:
            raise ValueError("choice cannot contain a score legend")
        if self.type == "choice":
            if self.choice not in probs or self.score is not None:
                raise ValueError("choice must be one of the distributed labels")
        else:
            if self.score is None or self.choice is not None:
                raise ValueError("score is required and cannot contain choice")
            if set(probs) != {str(i) for i in range(len(probs))}:
                raise ValueError("score distribution requires consecutive levels")
            expected = sum(int(k) * p for k, p in probs.items())
            if self.score > len(probs) - 1 or abs(self.score - expected) > 0.02:
                raise ValueError("score must agree with its distribution")
        return self


class DecisionResponse(ContractModel):
    """No raw request, response body, or free-text criteria in this receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: Identifier
    model_version: Identifier | None = None
    request_id: Identifier | None = None
    answers: dict[str, DecisionAnswer]
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    cost_usd: NonNegative | None = None
    provider: ProviderName | None = None
    latency_ms: NonNegative = 0.0
    attempts: TokenCount = 1

    def validate_questions(self, questions: Mapping[str, DecisionQuestion]) -> None:
        """Require exactly the answers and labels requested by the caller."""
        if set(self.answers) != set(questions):
            raise DecisionProtocolError()
        for key, question in questions.items():
            answer = self.answers[key]
            if answer.question_id != key or answer.type != question.type:
                raise DecisionProtocolError()
            if question.type == "choice":
                labels = set(question.criteria or ())
                if (
                    set(answer.probabilities or ()) != labels
                    or answer.choice not in labels
                ):
                    raise DecisionProtocolError()
            if question.type == "score":
                labels = {str(i) for i in range(len(question.criteria or ()))}
                if set(answer.probabilities or ()) != labels:
                    raise DecisionProtocolError()
                if answer.legend is not None and answer.legend != {
                    str(i): description
                    for i, description in enumerate(question.criteria or ())
                }:
                    raise DecisionProtocolError()

    def usage(self, *, task: str) -> UsageSummary:
        """Map returned usage to the existing cost ledger contract."""
        return UsageSummary(
            input_tokens=self.input_tokens or 0,
            output_tokens=self.output_tokens or 0,
            cost_usd_estimate=self.cost_usd,
            model_name=self.model,
            model_provider=self.provider,
            metadata={"aux_task": task, "usage_known": self.cost_usd is not None},
        )


@runtime_checkable
class DecisionProvider(Protocol):
    """Separate async decision interface; it is not a chat-completion provider."""

    async def decide(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str | None = None,
    ) -> DecisionResponse:
        """Return validated answers or raise a bounded DecisionError."""
