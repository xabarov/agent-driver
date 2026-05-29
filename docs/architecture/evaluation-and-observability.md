# Observability, Evaluation, And Regression Harness

## Why This Matters

Tracing tells us what happened. Evaluation tells us whether it was good enough, whether a change improved behavior, and whether cost/latency stayed acceptable. A serious agent engine needs both.

The current analysis includes Phoenix/Langfuse tracing and test utilities, but it does not yet define an evaluation subsystem.

External references:

- [Langfuse: AI agent observability](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- [Langfuse: Agent evaluation](https://langfuse.com/guides/cookbook/example_pydantic_ai_mcp_agent_evaluation)
- [Langfuse: Automated evaluations](https://langfuse.com/blog/2025-09-05-automated-evaluations)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)

## Observability Contract

The runtime should emit structured spans and events for:

- run lifecycle;
- graph nodes;
- LLM calls;
- tool calls;
- guardrails;
- interrupts;
- subagents;
- memory compaction;
- checkpoint saves;
- queue/worker lifecycle.

Minimum identifiers:

- `trace_id`;
- `run_id`;
- `attempt_id`;
- `thread_id`;
- `checkpoint_id`;
- `agent_id`;
- `graph_id`;
- `tenant_id` or opaque app scope;
- `model_provider`;
- `model_name`.

Exporters should be adapters:

- OpenTelemetry/Phoenix;
- Langfuse;
- LangSmith optional later;
- local JSONL trace sink for tests.

## Evaluation Dimensions

Agent evaluation should cover three layers:

### Final Output

- factual correctness;
- usefulness;
- format adherence;
- refusal correctness;
- citation/evidence correctness when applicable;
- safety policy compliance.

### Trajectory

- did the agent choose the right tools;
- did it avoid unnecessary tools;
- did it ask for approval at the right time;
- did it recover from tool errors;
- did it stop instead of looping;
- did subagent delegation improve the result.

### Individual Steps

- tool arguments valid;
- tool output interpreted correctly;
- guardrail decisions correct;
- compaction preserved required facts;
- planner updated task state correctly.

## Dataset And Experiment Model

Add a small evaluation package:

```text
agent_driver/
  evals/
    datasets.py
    runners.py
    evaluators.py
    baselines.py
    reports.py
```

Core concepts:

- `EvalCase`: input, initial state, expected traits, optional golden output;
- `EvalDataset`: named collection with version;
- `EvalRun`: model/router/settings snapshot plus results;
- `Evaluator`: deterministic or LLM-as-judge scoring function;
- `Baseline`: previous accepted scores, cost, latency, and trajectory metrics.

The first evaluators should be deterministic where possible:

- event schema validity;
- no leaked pending subagent rows;
- terminal state present;
- tool policy respected;
- checkpoint/replay consistency;
- max cost/latency budget.

LLM-as-judge should be added later for answer quality, with careful prompt/version tracking.

## Trace Replay

Durable checkpointing enables powerful regression tests:

- replay from initial input with new model/settings;
- replay from checkpoint before failure;
- compare trajectories;
- compare final answers;
- compare token/cost/latency.

This is a reason to implement checkpointing early, not after all agent features.

## Online Feedback

The runtime should make it easy for applications to attach:

- thumbs up/down;
- correction text;
- selected bad step;
- user-visible issue category;
- human evaluator note.

Feedback should link to `run_id`, `trace_id`, and `checkpoint_id` so failures can become eval cases.

## Cost And Latency Budgets

Production agent failures are often economic, not only semantic. Track:

- total tokens;
- prompt cache reads/creates;
- tool costs;
- subagent costs;
- elapsed time;
- queue wait;
- node duration;
- retries.

Evaluation reports should include quality/cost/latency together.

## MVP Recommendation

Add evaluation from the start:

- local JSONL trace sink;
- deterministic schema evaluators;
- small dataset runner over fake LLM/fake tools;
- trace replay over checkpoints;
- baseline JSON report;
- hooks for Langfuse dataset/score export later.
