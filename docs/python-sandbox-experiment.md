# Python sandbox experiment (gamma / scientific stack)

Reproduces interactive chat cases for gamma tail probabilities and linear algebra.

## Modes

| Mode | How to enable | Allowlist |
|------|----------------|-----------|
| **Scientific (default)** | `--enable-python` | stdlib + `numpy`, `scipy`, `pandas` |
| **Stdlib-only** | `--enable-python --no-python-scientific` or `AGENT_DRIVER_PYTHON_SCIENTIFIC=0` | stdlib only |

## Run (scientific stack on)

```bash
uv run agent-driver chat --enable-python
```

### Turn 1 — incompatible moments

```
привет, посчитай вероятность Гамма распределения F(>5) при параметрах распределения,
вычисляемых из двух первых начальных моментов: 3.2, 7.9
```

**Expected:** verbal check that `m2 < m1^2` (no valid distribution).

### Turn 2 — valid moments

```
да, давай m_2 = 67
```

**Expected:**

- `python` may use `scipy.stats.gamma` or stdlib `math`/`statistics`
- Numeric answer for P(X>5) (≈ 0.82–0.83 for m1=3.2, m2=67)
- No «библиотеки не установлены» when scipy is in the allowlist

### Doctor

```
/doctor
```

**Expected:** `python_imports` lists numpy, scipy, pandas (when scientific stack is on).

## Run (stdlib-only regression)

```bash
uv run agent-driver chat --enable-python --no-python-scientific
```

**Expected:** same as before scientific stack — `math`/`statistics` only after policy block; `/doctor` shows no scipy/numpy.

## Acceptance (scientific on, 3 consecutive runs)

| # | Criterion |
|---|-----------|
| 1 | `import scipy.stats` succeeds in python tool when scientific stack is on |
| 2 | Assistant does not say scipy/numpy are «not installed» when they are allowlisted |
| 3 | Valid moments yield numeric P(X>5) ≈ 0.82–0.83 |
| 4 | `/doctor` allowlist includes numpy, scipy, pandas |

## Offline checks

```bash
make eval-scientific
# or
uv run pytest tests/tools/test_python_scientific_imports.py tests/cli/test_eval_python_scientific_providers.py tests/cli/test_eval_gamma_fake_provider.py -q
```

Eval scenarios:

| ID | Scientific stack | Fake provider contract |
|----|------------------|------------------------|
| `python_gamma_stdlib_only` | off | scipy blocked → math retry |
| `python_gamma_scipy` | on | scipy.stats + tail prob |
| `python_pandas_linalg` | on | numpy.linalg.solve |

Full agent eval loops for gamma scenarios are slow; CI relies on fake-provider unit tests plus handler tests.

## Prompt layers

1. `python_tool_system_addendum.txt` — `{scientific_guidance}` (stack on vs off)
2. `react_chat_tool_policy.txt` — conditional note via `{python_scientific_note}`
3. Tool result + USER hint after `error_kind=policy` (stdlib-only only; skipped when scipy is allowlisted)
4. TUI shows `remediation` on policy failures
