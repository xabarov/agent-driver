"""Answer-quality judging: rubric (free) + LLM judge (offline).

Beyond terminal-status success, score the *answer* itself. Two domain-neutral
tools on the ``agent_driver.evals`` facade:

  * ``AnswerRubric`` + ``evaluate_answer_rubric`` — deterministic and free: check
    the run's ``answer`` against must-contain / must-not-contain / regex clauses;
    ``score`` is the fraction of clauses that passed.
  * ``LlmJudge`` — a generic 0–1 judge: score an ``(prompt, answer)`` pair via one
    small LLM call (here a canned fake reply); a provider error degrades to a
    conservative ``0.0`` verdict, never a crash.

    python examples/cookbook/23_answer_quality_judge.py
"""

from __future__ import annotations

import asyncio

from agent_driver.evals import AnswerRubric, LlmJudge, evaluate_answer_rubric
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


async def main() -> float:
    question = "What is the West region total?"
    agent = create_agent(
        provider=FakeProvider(response_text="The West total is 42 units."),
        tools=ToolSet.only(),
    )
    output = await agent.query(question, run_id="demo_judge")

    # Deterministic rubric — free, CI-able.
    rubric = AnswerRubric(must_contain=("42",), must_not_contain=("error", "sorry"))
    checked = evaluate_answer_rubric(output, rubric=rubric)
    print("rubric passed:", checked.passed, "score:", checked.score)

    # Generic LLM judge — one aux call, normalized to [0, 1].
    judge = LlmJudge(
        provider=FakeProvider(response_text='{"score": 9, "rationale": "on point"}')
    )
    verdict = await judge.score(prompt=question, answer=output.answer or "")
    print("judge score:", verdict.score, "-", verdict.rationale)
    return verdict.score


if __name__ == "__main__":
    asyncio.run(main())
