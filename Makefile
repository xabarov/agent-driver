BLACK ?= ./.venv/bin/black
ISORT ?= ./.venv/bin/isort
PYLINT ?= ./.venv/bin/pylint
PYTEST ?= uv run pytest
RUFF ?= ./.venv/bin/ruff
PYRIGHT ?= ./.venv/bin/pyright
VENV_PYTEST ?= ./.venv/bin/python -m pytest
RELEASE_PYTHON ?= ./.venv/bin/python
RELEASE_WHEEL_DIR ?= dist
PLAYWRIGHT_PY ?= ./.venv/bin/python
CHAT_DEMO_URL ?= http://localhost:5174
POLICY_SUPERVISION_ENV_FILE ?= .env
PHOENIX_PORT ?= 6006
PHOENIX_OTLP_GRPC_PORT ?= 4317
PHOENIX_ENDPOINT ?= http://127.0.0.1:$(PHOENIX_PORT)
PHOENIX_PROJECT_NAME ?= agent-driver-policy-supervision
LINT_PATHS ?= agent_driver/subagents tests/subagents agent_driver/runtime/single_agent/subagent_stage.py tests/runtime/test_subagent_integration.py
POLICY_SUPERVISION_PYTESTS ?= tests/llm/test_provider_route_profiles.py tests/runtime/test_runner_multistep_react.py tests/runtime/test_runtime_runner_core.py tests/runtime/test_run_agent_span.py tests/runtime/test_policy_evaluator.py tests/runtime/test_supervision.py tests/runtime/test_validation_artifacts.py tests/runtime/test_openrouter_preflight.py tests/runtime/test_phoenix_gate.py tests/runtime/test_playwright_gate.py tests/runtime/test_benchmark_gate.py tests/runtime/test_policy_supervision_audit.py tests/observability/test_support_bundle.py tests/observability/test_run_trace_summary.py tests/observability/test_openinference_emitter.py tests/batch/test_batch_runner.py tests/evals/test_aggregate.py tests/evals/test_compare.py tests/contracts/test_public_exports.py tests/contracts/test_schema_snapshots.py tests/test_public_api.py
POLICY_SUPERVISION_RUFF_PATHS ?= agent_driver/llm/provider_route_profiles.py agent_driver/llm/__init__.py agent_driver/contracts/policy.py agent_driver/contracts/runtime_decisions.py agent_driver/contracts/__init__.py agent_driver/runtime/policy.py agent_driver/runtime/supervision.py agent_driver/runtime/validation.py agent_driver/runtime/validation_artifacts.py agent_driver/runtime/openrouter_preflight.py agent_driver/runtime/phoenix_gate.py agent_driver/runtime/playwright_gate.py agent_driver/runtime/benchmark_gate.py agent_driver/runtime/policy_supervision_audit.py agent_driver/runtime/__init__.py agent_driver/runtime/runner.py agent_driver/runtime/single_agent/lifecycle/journal.py agent_driver/runtime/single_agent/lifecycle/steps.py agent_driver/runtime/single_agent/tool_stage/__init__.py agent_driver/observability/support_bundle.py agent_driver/observability/run_trace/summary.py agent_driver/observability/provenance.py agent_driver/observability/phoenix.py agent_driver/observability/openinference.py agent_driver/observability/__init__.py agent_driver/batch/contracts.py agent_driver/evals/aggregate.py agent_driver/evals/compare.py tests/llm/test_provider_route_profiles.py tests/runtime/test_policy_evaluator.py tests/runtime/test_supervision.py tests/runtime/test_validation_artifacts.py tests/runtime/test_openrouter_preflight.py tests/runtime/test_phoenix_gate.py tests/runtime/test_playwright_gate.py tests/runtime/test_benchmark_gate.py tests/runtime/test_policy_supervision_audit.py tests/runtime/test_runtime_runner_core.py tests/runtime/test_runner_multistep_react.py tests/runtime/test_run_agent_span.py tests/observability/test_support_bundle.py tests/observability/test_run_trace_summary.py tests/observability/test_openinference_emitter.py tests/batch/test_batch_runner.py tests/evals/test_aggregate.py tests/evals/test_compare.py tests/contracts/test_public_exports.py tests/contracts/test_schema_snapshots.py examples/chat-demo/backend/app/workspace.py examples/chat-demo/backend/tests/test_workspace.py examples/chat-demo/backend/tests/test_run_trace_summary.py tools/policy_supervision/openrouter_preflight.py tools/policy_supervision/openrouter_trace_scenarios.py tools/policy_supervision/openrouter_trace_ui_review.py tools/policy_supervision/product_trace_smoke.py tools/policy_supervision/product_trace_ui_review.py tools/policy_supervision/phoenix_gate.py tools/policy_supervision/phoenix_smoke.py tools/policy_supervision/phoenix_ui_review.py tools/policy_supervision/playwright_gate.py tools/policy_supervision/benchmark_gate.py tools/policy_supervision/audit.py
CHAT_DEMO_BACKEND_PY ?= examples/chat-demo/backend/.venv/bin/python

.PHONY: test format format-check lint lint-python lint-fast type docs-check release-wheel selftest selftest-fake eval-deep-offline eval-regression eval-nightly-live-deep eval-scientific test-plan-ui test-chat-concepts policy-supervision-test policy-supervision-lint policy-supervision-doctor policy-supervision-phoenix-up policy-supervision-phoenix-down policy-supervision-phoenix-status policy-supervision-phoenix-smoke policy-supervision-phoenix-ui-review policy-supervision-openrouter-preflight policy-supervision-openrouter-live-preflight policy-supervision-openrouter-trace-scenarios policy-supervision-openrouter-trace-ui-review policy-supervision-excel-trace-smoke policy-supervision-excel-trace-ui-review policy-supervision-chat-demo-trace-smoke policy-supervision-chat-demo-trace-ui-review policy-supervision-phoenix-gate policy-supervision-playwright-gate policy-supervision-benchmark-gate policy-supervision-artifacts policy-supervision-audit policy-supervision-acceptance

test:
	$(PYTEST) -q

format:
	$(ISORT) $(LINT_PATHS)
	$(BLACK) $(LINT_PATHS)

format-check:
	$(ISORT) --check-only --diff $(LINT_PATHS)
	$(BLACK) --check --diff $(LINT_PATHS)

lint-fast:
	$(RUFF) check $(LINT_PATHS)

lint-python: format-check
	$(PYLINT) agent_driver/subagents agent_driver/runtime/single_agent/subagent_stage.py --fail-under=8.0

lint: lint-fast lint-python

# Type-check the supported embedding surface + durable control plane
# (scope in pyrightconfig.json). Not a repo-wide gate — see pyrightconfig.json.
type:
	$(PYRIGHT)

# Docs-consistency gate: the supported facade `__all__` matches docs/embedding.md
# (export snapshot + public-exports subset) and version/docs agree. This is the
# repo's "docs check" — there is no separate docs-site build.
docs-check:
	$(VENV_PYTEST) tests/contracts/test_export_snapshot.py \
		tests/contracts/test_public_exports.py \
		tests/test_version.py -q

# Build only from the committed clean Git tree. The builder exports HEAD into a
# disposable tree, normalizes Git file modes and process umask, and derives
# SOURCE_DATE_EPOCH from the selected commit. It never packages mutable
# worktree metadata or caller-specific permission bits.
release-wheel:
	RELEASE_PYTHON="$(RELEASE_PYTHON)" ./scripts/build_release_wheel.sh "$(RELEASE_WHEEL_DIR)"

selftest:
	uv run python tools/selftest/run.py --scenarios A,B,C,D

selftest-fake:
	uv run python tools/selftest/run.py --provider fake --matrix m1=fake --scenarios B --smoke-only

eval-deep-offline:
	uv run agent-driver eval run --provider fake --offline --suite deep --output-dir .agent-driver/evals/ci-deep

eval-regression:
	uv run agent-driver eval run --provider fake --offline --suite regression --output-dir .agent-driver/evals/ci-regression

eval-scientific:
	uv run pytest tests/tools/test_python_scientific_imports.py tests/cli/test_eval_python_scientific_providers.py -q

test-plan-ui:
	uv run pytest tests/cli/test_plan_panel_render.py tests/cli/test_chat_stream_planning_snapshot.py tests/runtime/test_planning_state_seed.py tests/prompts/test_chat_plan_policy_guard.py tests/runtime/test_todo_progress_hint.py tests/runtime/test_todo_reminder_loops.py tests/tools/test_todo_write_structured_output.py -q

test-chat-concepts:
	CHAT_DEMO_URL=$(CHAT_DEMO_URL) $(PLAYWRIGHT_PY) examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py

policy-supervision-test:
	$(PYTEST) $(POLICY_SUPERVISION_PYTESTS) -q
	@if [ -x "$(CHAT_DEMO_BACKEND_PY)" ]; then \
		PYTHONPATH=examples/chat-demo/backend $(CHAT_DEMO_BACKEND_PY) -m pytest examples/chat-demo/backend/tests/test_workspace.py examples/chat-demo/backend/tests/test_run_trace_summary.py::test_trace_summary_exposes_policy_supervisor_and_validation_gates -q; \
	else \
		echo "SKIP chat-demo backend adapter test: $(CHAT_DEMO_BACKEND_PY) not found"; \
	fi

policy-supervision-lint:
	uv run ruff check $(POLICY_SUPERVISION_RUFF_PATHS)

policy-supervision-phoenix-up:
	cd examples/chat-demo && PHOENIX_PORT=$(PHOENIX_PORT) PHOENIX_OTLP_GRPC_PORT=$(PHOENIX_OTLP_GRPC_PORT) \
		docker compose -f docker-compose.dev.yml up -d phoenix
	@echo "Phoenix: http://127.0.0.1:$(PHOENIX_PORT)"

policy-supervision-phoenix-down:
	cd examples/chat-demo && PHOENIX_PORT=$(PHOENIX_PORT) PHOENIX_OTLP_GRPC_PORT=$(PHOENIX_OTLP_GRPC_PORT) \
		docker compose -f docker-compose.dev.yml stop phoenix

policy-supervision-phoenix-status:
	@if command -v curl >/dev/null 2>&1 && curl -fsS "$(PHOENIX_ENDPOINT)/healthz" >/dev/null 2>&1; then echo "Phoenix reachable: $(PHOENIX_ENDPOINT)"; else echo "Phoenix not reachable: $(PHOENIX_ENDPOINT)"; exit 1; fi
	@uv run python -c "import phoenix.otel, opentelemetry.exporter.otlp.proto.http.trace_exporter; print('Phoenix OTEL deps: ready')"

policy-supervision-phoenix-smoke:
	uv run python tools/policy_supervision/phoenix_smoke.py --endpoint "$(PHOENIX_ENDPOINT)" --project-name "$(PHOENIX_PROJECT_NAME)" --output-dir .agent-driver/policy-supervision/phoenix-smoke

policy-supervision-phoenix-ui-review: policy-supervision-phoenix-smoke
	uv run python tools/policy_supervision/phoenix_ui_review.py --base-url "$(PHOENIX_ENDPOINT)" --project-name "$(PHOENIX_PROJECT_NAME)" --output-dir .agent-driver/policy-supervision/phoenix-ui-review

policy-supervision-openrouter-preflight:
	set -a; if [ -f "$(POLICY_SUPERVISION_ENV_FILE)" ]; then case "$(POLICY_SUPERVISION_ENV_FILE)" in /*|*/*) . "$(POLICY_SUPERVISION_ENV_FILE)" ;; *) . ./$(POLICY_SUPERVISION_ENV_FILE) ;; esac; fi; set +a; \
	uv run python tools/policy_supervision/openrouter_preflight.py --output-dir .agent-driver/policy-supervision/openrouter-preflight

policy-supervision-openrouter-live-preflight:
	set -a; if [ -f "$(POLICY_SUPERVISION_ENV_FILE)" ]; then case "$(POLICY_SUPERVISION_ENV_FILE)" in /*|*/*) . "$(POLICY_SUPERVISION_ENV_FILE)" ;; *) . ./$(POLICY_SUPERVISION_ENV_FILE) ;; esac; fi; set +a; \
	uv run python tools/policy_supervision/openrouter_preflight.py --live --output-dir .agent-driver/policy-supervision/openrouter-live-preflight --phoenix-endpoint "$(PHOENIX_ENDPOINT)" --phoenix-project-name "$(PHOENIX_PROJECT_NAME)" --phoenix-gate-output-dir .agent-driver/policy-supervision/phoenix-gate

policy-supervision-openrouter-trace-scenarios:
	set -a; if [ -f "$(POLICY_SUPERVISION_ENV_FILE)" ]; then case "$(POLICY_SUPERVISION_ENV_FILE)" in /*|*/*) . "$(POLICY_SUPERVISION_ENV_FILE)" ;; *) . ./$(POLICY_SUPERVISION_ENV_FILE) ;; esac; fi; set +a; \
	uv run python tools/policy_supervision/openrouter_trace_scenarios.py --output-dir .agent-driver/policy-supervision/openrouter-trace-scenarios --phoenix-endpoint "$(PHOENIX_ENDPOINT)" --phoenix-project-name "$(PHOENIX_PROJECT_NAME)"

policy-supervision-openrouter-trace-ui-review:
	uv run python tools/policy_supervision/openrouter_trace_ui_review.py --base-url "$(PHOENIX_ENDPOINT)" --project-name "$(PHOENIX_PROJECT_NAME)" --output-dir .agent-driver/policy-supervision/openrouter-trace-ui-review

policy-supervision-excel-trace-smoke:
	uv run python tools/policy_supervision/product_trace_smoke.py --profile excel --endpoint "$(PHOENIX_ENDPOINT)" --project-name excel-ai --output-dir .agent-driver/policy-supervision/excel-trace-smoke

policy-supervision-excel-trace-ui-review: policy-supervision-excel-trace-smoke
	uv run python tools/policy_supervision/product_trace_ui_review.py --base-url "$(PHOENIX_ENDPOINT)" --project-name excel-ai --smoke-file .agent-driver/policy-supervision/excel-trace-smoke/product_trace_smoke.json --output-dir .agent-driver/policy-supervision/excel-trace-ui-review

policy-supervision-chat-demo-trace-smoke:
	uv run python tools/policy_supervision/product_trace_smoke.py --profile chat-demo --endpoint "$(PHOENIX_ENDPOINT)" --project-name agent-driver-chat-demo --output-dir .agent-driver/policy-supervision/chat-demo-trace-smoke

policy-supervision-chat-demo-trace-ui-review: policy-supervision-chat-demo-trace-smoke
	uv run python tools/policy_supervision/product_trace_ui_review.py --base-url "$(PHOENIX_ENDPOINT)" --project-name agent-driver-chat-demo --smoke-file .agent-driver/policy-supervision/chat-demo-trace-smoke/product_trace_smoke.json --output-dir .agent-driver/policy-supervision/chat-demo-trace-ui-review

policy-supervision-phoenix-gate:
	uv run python tools/policy_supervision/phoenix_gate.py --output-dir .agent-driver/policy-supervision/phoenix-gate

policy-supervision-playwright-gate:
	uv run python tools/policy_supervision/playwright_gate.py --output-dir .agent-driver/policy-supervision/playwright-gate

policy-supervision-benchmark-gate:
	uv run python tools/policy_supervision/benchmark_gate.py --output-dir .agent-driver/policy-supervision/benchmark-gate

policy-supervision-artifacts: policy-supervision-openrouter-preflight policy-supervision-phoenix-gate policy-supervision-playwright-gate policy-supervision-benchmark-gate

policy-supervision-audit:
	uv run python tools/policy_supervision/audit.py --root-dir .agent-driver/policy-supervision

policy-supervision-acceptance:
	uv run python tools/policy_supervision/audit.py --root-dir .agent-driver/policy-supervision --require-passed

policy-supervision-doctor:
	@echo "006 deterministic: make policy-supervision-test && make policy-supervision-lint"
	@echo "006 artifact lab: make policy-supervision-artifacts"
	@echo "006 acceptance audit: make policy-supervision-audit"
	@echo "006 strict acceptance: make policy-supervision-acceptance"
	@echo "006 OpenRouter preflight: make policy-supervision-openrouter-preflight"
	@echo "006 OpenRouter live preflight: make policy-supervision-openrouter-live-preflight"
	@echo "006 OpenRouter trace scenarios: make policy-supervision-openrouter-trace-scenarios"
	@echo "006 OpenRouter trace UI review: make policy-supervision-openrouter-trace-ui-review"
	@echo "006 product trace UI reviews: make policy-supervision-excel-trace-ui-review && make policy-supervision-chat-demo-trace-ui-review"
	@echo "006 Phoenix up/status/smoke: make policy-supervision-phoenix-up && make policy-supervision-phoenix-status && make policy-supervision-phoenix-smoke"
	@echo "006 Phoenix UI review: make policy-supervision-phoenix-ui-review"
	@echo "006 Phoenix gate: make policy-supervision-phoenix-gate"
	@echo "006 Playwright gate: make policy-supervision-playwright-gate"
	@echo "006 benchmark gate: make policy-supervision-benchmark-gate"
	@echo "006 chat-demo UI: make test-chat-concepts CHAT_DEMO_URL=$(CHAT_DEMO_URL)"
	@set -a; if [ -f "$(POLICY_SUPERVISION_ENV_FILE)" ]; then case "$(POLICY_SUPERVISION_ENV_FILE)" in /*|*/*) . "$(POLICY_SUPERVISION_ENV_FILE)" ;; *) . ./$(POLICY_SUPERVISION_ENV_FILE) ;; esac; fi; set +a; if [ -n "$$AGENT_DRIVER_API_KEY" ] || [ -n "$$OPENROUTER_API_KEY" ] || [ -n "$$LLM_API_KEY" ]; then echo "OpenRouter: API key env is set"; else echo "OpenRouter: AGENT_DRIVER_API_KEY/OPENROUTER_API_KEY/LLM_API_KEY not set"; fi
	@if command -v curl >/dev/null 2>&1 && curl -fsS http://127.0.0.1:6006 >/dev/null 2>&1; then echo "Phoenix: http://127.0.0.1:6006 reachable"; else echo "Phoenix: http://127.0.0.1:6006 not reachable"; fi
	@if [ -x "$(CHAT_DEMO_BACKEND_PY)" ]; then echo "chat-demo backend venv: ready"; else echo "chat-demo backend venv: missing"; fi

eval-regression-live:
	@test -f .env || (echo "missing .env" >&2; exit 1)
	set -a && . ./.env && export AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1 && set +a; \
	uv run agent-driver eval run --suite regression --provider openrouter \
		--allow-dangerous-tools --allow-live-without-env --continue-on-error \
		--output-dir .agent-driver/evals

eval-nightly-live-deep:
	@test -n "$$AGENT_DRIVER_API_KEY" || (echo "AGENT_DRIVER_API_KEY required" >&2; exit 1)
	set -a && [ -f .env ] && . ./.env; set +a; \
	AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1 uv run agent-driver eval run \
		--suite deep --provider openrouter --allow-dangerous-tools \
		--allow-live-without-env --timeout-s 300 --continue-on-error \
		--output-dir .agent-driver/evals
