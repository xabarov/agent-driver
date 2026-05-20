.PHONY: test selftest selftest-fake

test:
	uv run pytest -q

selftest:
	uv run python tools/selftest/run.py --scenarios A,B,C,D

selftest-fake:
	uv run python tools/selftest/run.py --provider fake --matrix m1=fake --scenarios B --smoke-only

eval-regression:
	uv run agent-driver eval run --provider fake --offline --suite regression --output-dir .agent-driver/evals/ci-regression
