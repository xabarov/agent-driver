# Agent Driver Docs

This directory captures the initial architecture analysis for `agent-driver`: a domain-neutral Python LangGraph engine for building agentic chat applications.

## Start Here

- [Agent Driver analysis and extraction plan](agent-driver-analysis-2026-05-18.md) — main overview, boundaries, package shape, and MVP sequence.

## Deep Dives

- [Phase 0 contracts spec](specs/phase-0-contracts.md)
- [Durable runtime, checkpointing, and worker execution](architecture/durable-runtime.md)
- [Human review, interrupts, and guardrails](architecture/hitl-and-guardrails.md)
- [Observability, evaluation, and regression harness](architecture/evaluation-and-observability.md)
- [Context engineering, tools, and MCP integration](architecture/context-tools-and-mcp.md)
- [Implementation roadmap](roadmap.md)

## Development Commands

- `.venv/bin/isort agent_driver tests`
- `.venv/bin/black agent_driver tests`
- `.venv/bin/pylint agent_driver tests`
- `.venv/bin/python -m pytest tests/contracts`

## External References

- LangChain: [Building LangGraph: Designing an Agent Runtime from First Principles](https://blog.langchain.com/building-langgraph)
- LangGraph docs: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- LangGraph docs: [Durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- LangGraph docs: [Human-in-the-loop / interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- LangGraph docs: [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- OpenAI: [Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)
- OpenAI: [Guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
- Anthropic: [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic: [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Langfuse: [AI agent observability with Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- Langfuse: [Agent evaluation](https://langfuse.com/guides/cookbook/example_pydantic_ai_mcp_agent_evaluation)
- CoSAI: [Model Context Protocol security](https://github.com/cosai-oasis/ws4-secure-design-agentic-systems/blob/main/model-context-protocol-security.md)
