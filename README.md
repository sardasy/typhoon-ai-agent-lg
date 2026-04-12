# Typhoon HIL AI Agent — LangGraph Edition

AI-powered HIL test automation built on **LangGraph StateGraph**.

## Why LangGraph?

The previous version used a hand-rolled Orchestrator class with nested async loops.
LangGraph replaces that with a **declarative state machine**:

| Aspect | v1 (hand-rolled) | v2 (LangGraph) |
|--------|-------------------|----------------|
| Control flow | Nested `for`/`if` in Orchestrator | Nodes + conditional edges |
| State | Scattered across class attributes | Single `AgentState` TypedDict |
| Retry loop | Manual counter + `while` | `apply_fix` → `execute_scenario` edge |
| Streaming | Custom SSE event yield | `app.astream()` built-in |
| Visualization | None | `get_graph().draw_mermaid()` |
| Checkpointing | None | LangGraph MemorySaver (opt-in) |
| Human-in-loop | Not supported | Breakpoints on any node |

## Graph topology

```
START → load_model → plan_tests → execute_scenario
                                        │
                                  [route_after_exec]
                                   │      │      │
                                 fail   next    done
                                   │      │      │
                          analyze_failure │  generate_report → END
                                   │      │
                           [route_after_analysis]    advance_scenario
                              │         │                 │
                           retry    escalate        [route_has_more]
                              │         │              │       │
                          apply_fix     └──→ advance  yes      no
                              │                        │       │
                              └──→ execute_scenario    │  generate_report
                                                       │
                                              execute_scenario
```

## Quick start

```bash
pip install -r requirements.txt
cp configs/model.yaml.example configs/model.yaml

# CLI
python main.py --goal "BMS overvoltage protection test, 4.2V, 100ms"

# Web dashboard
python main.py --server

# Tests
pytest tests/ -v
```

## Project structure

```
typhoon_ai_agent_lg/
├── configs/
│   ├── model.yaml              # HIL + safety + AI config
│   └── scenarios.yaml          # Predefined scenario library
├── prompts/
│   ├── planner.md              # Planner agent system prompt
│   └── analyzer.md             # Analyzer agent system prompt
├── src/
│   ├── state.py                # AgentState TypedDict + Pydantic models
│   ├── graph.py                # ★ StateGraph definition + conditional edges
│   ├── nodes/
│   │   ├── load_model.py       # Load HIL model + RAG context
│   │   ├── plan_tests.py       # Claude Planner → test plan JSON
│   │   ├── execute_scenario.py # Run scenario on HIL
│   │   ├── analyze_failure.py  # Claude Analyzer → diagnosis JSON
│   │   ├── apply_fix.py        # XCP calibration write
│   │   ├── advance_scenario.py # Move to next scenario
│   │   └── generate_report.py  # HTML report generation
│   ├── tools/
│   │   ├── hil_tools.py        # Typhoon HIL API wrappers
│   │   ├── xcp_tools.py        # pyXCP wrappers
│   │   └── rag_tools.py        # Qdrant RAG search
│   ├── validator.py            # Safety guard
│   └── reporter.py             # Jinja2 report generator
├── templates/report.html
├── tests/test_graph.py         # 29 tests (routing, structure, tools)
├── main.py                     # CLI + FastAPI + SSE streaming
└── requirements.txt
```

## Key design: conditional edges

The routing logic is pure functions that inspect state and return a string:

```python
def route_after_exec(state) -> "fail" | "next" | "done":
    last = state["results"][-1]
    if last["status"] == "fail" and state["heal_retry_count"] < 3:
        return "fail"       # → analyze_failure
    if more_scenarios:
        return "next"       # → advance_scenario
    return "done"           # → generate_report
```

LangGraph uses this return value to pick the next node. No `if/else` spaghetti.

## Extending

**Add a new node** (e.g., `human_review`):

```python
# 1. Create src/nodes/human_review.py
async def human_review(state):
    # interrupt and wait for human input
    return {"human_approved": True, "events": [...]}

# 2. Wire it in graph.py
graph.add_node("human_review", human_review)
graph.add_conditional_edges("analyze_failure", route, {
    "retry": "apply_fix",
    "review": "human_review",   # new route
    "escalate": "advance_scenario",
})
```

**Add checkpointing** (resume after crash):

```python
from langgraph.checkpoint.memory import MemorySaver
app = build_graph().compile(checkpointer=MemorySaver())
```
