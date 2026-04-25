# Code Quality

THAA enforces two quality gates: **test coverage** (pytest-cov) and
**type checking** (mypy). Both run via:

```bash
scripts\check.bat            # Windows one-shot
python -m pytest --cov       # coverage only
python -m mypy               # type-check only
```

## Coverage

Configured in `.coveragerc`. Baseline at the time of writing: **63%
statement / 70% branch** across 369 tests. The gate is set to
**`fail_under = 60`** -- a small floor below baseline, so genuine
regressions trip the build without flapping over normal churn.

### What's measured

- `src/` core: state, graph, nodes, tools, twin, parallel agents
- `scripts/`: preflight, cleanup (only the testable helpers; one-shot
  CLIs like `index_knowledge.py` are excluded)

### What's omitted (`.coveragerc` `omit`)

- `main.py` (argparse boilerplate, exercised by manual smoke runs)
- HTAF codegen pipeline (`graph_codegen.py`, `nodes/parse_tse.py`,
  etc.) -- pending its own coverage push
- Frontend (separate Jest suite TBD)
- One-shot demo scripts (`demo_sqlite_resume.py`, `index_knowledge.py`)

### Hot paths (>=90% covered)

| Module | Coverage |
|--------|----------|
| `src/state.py` | 100% |
| `src/constants.py` | 100% |
| `src/nodes/advance_scenario.py` | 100% |
| `src/nodes/simulate_fix.py` | 100% |
| `src/nodes/generate_report.py` | 100% |
| `src/graph.py` | 98.4% |
| `src/graph_orchestrator.py` | 98.4% |
| `src/signal_validator.py` | 95.8% |
| `src/nodes/analyze_failure.py` | 93.2% |
| `src/domain_classifier.py` | 90.7% |
| `src/audit.py` | 90.0% |
| `src/nodes/load_model.py` | 90.4% |

### Cold paths (<50% covered)

| Module | Coverage | Why |
|--------|----------|-----|
| `src/nodes/apply_fix.py` | 25% | Only validator-rejection path tested; happy-path needs LLM mock + DUT integration. |
| `src/tools/hil_tools.py` | 26% | Real-hardware paths -- mock branches covered, Typhoon-API branches not. |
| `src/tools/rag_tools.py` | 45% | Mock + Chroma-empty branches covered; Qdrant + populated-Chroma not. |
| `src/evaluator.py` | 50% | 60+ rule handlers; only the most common ~30 covered. |
| `src/fault_templates.py` | 51% | 10 templates; complex stimulus templates (VSM, phase_jump) lightly covered. |

## Type checking

Configured in `mypy.ini`. Goal: catch **real type bugs** (wrong
return types, missing-attribute access, unreachable branches) without
churning over the legacy untyped surface.

### Strictness ladder

- **Global:** lenient. Missing annotations are not errors. Real type
  bugs ARE errors (`warn_unreachable`, `no_implicit_optional`,
  `strict_equality`).
- **Strict canaries** (must stay clean): `src/constants.py`,
  `src/audit.py`. Adding a strict module is a 1-line `mypy.ini`
  edit -- promote when a module is fully annotated.
- **Ignored** (third-party stub gaps): `tools/hil_tools.py`,
  `tools/xcp_tools.py`, `tools/rag_tools.py`, `nodes/analyze_failure.py`,
  `nodes/plan_tests.py` (LangChain stubs are partial), and the
  HTAF codegen modules.

### Promoting a module to strict

```ini
[mypy-src.your_module]
disallow_untyped_defs = True
disallow_incomplete_defs = True
```

Then `python -m mypy src/your_module.py` until clean.

## CI sketch

```yaml
- name: Quality gate
  run: |
    pip install -r requirements.txt
    pip install pytest-cov mypy types-PyYAML
    python -m pytest --cov  # fails below 60%
    python -m mypy
```

## Inner loop

```bash
# Fast unit tests, no coverage:
python -m pytest tests/ -q

# Just one module's tests:
python -m pytest tests/test_orchestrator.py -v

# Coverage for one file:
python -m pytest --cov=src/graph_orchestrator tests/test_orchestrator.py

# Type-check one file:
python -m mypy src/twin.py
```
