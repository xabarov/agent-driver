.PHONY: test selftest selftest-fake eval-deep-offline eval-regression eval-nightly-live-deep eval-scientific test-plan-ui

test:
	uv run pytest -q

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
