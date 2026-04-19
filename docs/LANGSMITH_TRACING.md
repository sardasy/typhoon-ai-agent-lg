# LangSmith Tracing

THAA's LangGraph runs and Claude API calls are traceable to
[LangSmith](https://smith.langchain.com/) for end-to-end observability:

- Per-node spans (load_model, plan_tests, execute_scenario, analyze_failure,
  apply_fix, advance_scenario, generate_report)
- Nested ChatAnthropic spans with prompt + completion + token counts
- Self-healing retries linked under one parent run via `thaa_run_id`
- RAG queries visible as child spans of load_model

Tracing is **opt-in** — disabled when env vars are unset, no behavior change.

## Setup (3 steps)

### 1. Get a LangSmith API key

Sign in at https://smith.langchain.com/ and create a personal API key
under Settings → API Keys. Free tier ships with 5k traces/month.

### 2. Set env vars (choose one)

**Option A — `.env` file (recommended, auto-loaded by main.py):**

```ini
# Copy from .env.example, paste your key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LANGCHAIN_PROJECT=thaa-dev
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

**Option B — shell env:**

```bash
# Windows PowerShell
$env:LANGCHAIN_TRACING_V2="true"
$env:LANGCHAIN_API_KEY="lsv2_pt_..."
$env:LANGCHAIN_PROJECT="thaa-dev"

# Linux / macOS / git-bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=lsv2_pt_...
export LANGCHAIN_PROJECT=thaa-dev
```

### 3. Run the agent

```bash
python main.py --goal "IEEE 2800 GFM compliance" --config configs/scenarios_vsm_gfm.yaml
```

Browse traces at https://smith.langchain.com/o/<your-org>/projects/p/thaa-dev.

## Span structure

Each top-level run looks like:

```
THAA: <goal>                         <-- root run
├── load_model
│   └── rag.execute (Chroma query)   <-- child if RAG enabled
├── plan_tests
│   └── plan_tests.llm               <-- ChatAnthropic span (when YAML
│                                         scenarios absent)
├── execute_scenario [retry#0]
├── analyze_failure
│   └── analyze_failure.llm          <-- ChatAnthropic span
├── apply_fix
└── execute_scenario [retry#1]
    ...
```

Spans are tagged so you can filter:
- `thaa`, `hil` — every THAA run
- `verify` — production agent runs
- `codegen` — TSE → pytest pipeline runs
- `plan_tests`, `analyze_failure` — node-level filtering
- `claude-sonnet-4` — model filtering

## Metadata available on every run

- `thaa_run_id` — UUID for correlating with backend logs
- `goal` — first 80 chars of the user goal
- `mode` — `verify` or `codegen`
- `config_path` — which scenarios YAML was used
- `node` — set on LLM child spans for easy grouping

## What gets sent to LangSmith

When tracing is enabled, the following data leaves your machine:

| Span | Data |
|------|------|
| Graph nodes | Inputs (state slice), outputs (state delta), errors |
| `plan_tests.llm` | Full system prompt + user message (goal + signals + RAG context) + Claude response |
| `analyze_failure.llm` | Failed scenario JSON + result waveform stats + RAG context + diagnosis JSON |
| RAG queries | Query text + document IDs returned (no embeddings) |

**Privacy notes:**

- Goal text and scenario YAML content are sent as-is.
- Captured waveform data (means, RMS) is included in failure analysis.
- TSE model paths are included in metadata.

For projects with proprietary IP, set `LANGCHAIN_ENDPOINT` to a
self-hosted LangSmith instance, or simply leave tracing disabled.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `403 Forbidden` on multipart ingest | API key wrong / project access missing — check `LANGCHAIN_API_KEY` |
| No traces appear | Confirm `LANGCHAIN_TRACING_V2=true` (must be the literal string `true`) |
| Traces appear but not in expected project | `LANGCHAIN_PROJECT` is created on first write; check the projects list at the top of LangSmith |
| Slow startup | First trace per project triggers project creation — subsequent runs are fast |

## Verifying the wireup without a real key

```bash
LANGCHAIN_TRACING_V2=true \
LANGCHAIN_API_KEY=lsv2_pt_test_dummy \
LANGCHAIN_PROJECT=thaa-wireup-check \
  python main.py --goal "wireup test" --config configs/scenarios_heal_demo.yaml 2>&1 | grep langsmith
```

You should see a `403 Forbidden` warning from `langsmith.client` — that
confirms the integration is firing. Replace the dummy key with a real one
and traces will appear.
