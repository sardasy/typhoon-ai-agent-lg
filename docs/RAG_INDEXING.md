# RAG Knowledge Base Indexing

THAA's RAG layer is now backed by an embedded **ChromaDB** index under
`chroma_db/`. The Planner (`plan_tests`) and Analyzer (`analyze_failure`)
nodes both read RAG context — better RAG → better plans + diagnoses.

## Quick start

```bash
# Build / rebuild the index
python scripts/index_knowledge.py

# Index only one source
python scripts/index_knowledge.py --source standards

# Append instead of wipe
python scripts/index_knowledge.py --no-clean
```

After indexing, the agent picks up Chroma automatically — no code changes.

## Collections (4)

| Collection | Source | Typical doc count |
|-----------|--------|-------------------|
| `thaa_standards` | IEEE 1547/2800 reference markdown (skill) + IEC/UL summaries | ~25 |
| `thaa_scenarios` | All `configs/scenarios*.yaml` files (one doc per scenario) | ~65 |
| `thaa_api_docs` | Module + function docstrings in `src/tools/*.py` | ~30 |
| `thaa_test_history` | HTML reports under `reports/` (text-extracted) | grows over time |

Total today: **133 docs**.

## Domain namespacing (Phase 4-G)

Every indexed document carries a `metadata.domain` tag — one of
`bms`, `pcs`, `grid`, `general`. The indexer fills it via
`src.domain_classifier.infer_doc_domain` (heuristic on `standard`
metadata + text vote); explicit `metadata["domain"]` from the
collector wins. Re-run `python scripts/index_knowledge.py` to
backfill an older index.

Query-time, callers pass an optional `domain` argument:

```python
rag.execute("rag_query", {
    "query": "OVP threshold response time",
    "sources": ["standards", "test_history"],
    "domain": "bms",      # filters to docs tagged bms OR general
})
```

The filter always includes `general` as a catch-all so shared API
references / common safety notes remain visible to every agent.

`load_model` now populates `state["rag_context_by_domain"]` with one
entry per domain alongside the global `state["rag_context"]`.
`analyze_failure` reads the entry matching the failed scenario's
domain and falls back to the global pull when the namespace is empty.

Indexer log line shows the per-domain breakdown:

```
[standards] indexed 25 documents (collection=thaa_standards) | bms=8, grid=12, pcs=3, general=2
```

## How the pipeline uses RAG

```
load_model
   └─▶ rag.execute({'query': goal, 'sources': ['standards', 'test_history']})
          └─▶ ChromaDB query → top-5 docs joined with newlines
              → state.rag_context (~3-5KB of bullet-point context)

plan_tests
   └─▶ user_msg += f"## Standards / knowledge context\n{rag_context}\n"
       → Claude Planner sees relevant standards before generating scenarios

analyze_failure
   └─▶ user_msg += f"## Past test history / standards\n{rag_context}\n"
       → Claude Analyzer sees past similar failures + standards thresholds
       → produces more accurate corrective_value (e.g. J=0.3 exactly at
         the IEEE 2800 inertia threshold instead of guessing)
```

## Backend priority (RAGToolExecutor)

1. **ChromaDB** (this) — embedded, no server, repo-local at `chroma_db/`
2. Qdrant — when `QDRANT_URL` is set and the client is initialised
3. Mock KB — falls back automatically when neither is available

## Extending the index

### Add a new source type

1. Add a `collect_<name>()` function in `scripts/index_knowledge.py`
   returning `[{id, text, metadata}]`.
2. Register in the `SOURCES` dict at the bottom.
3. Update the source enum in `src/tools/rag_tools.py::RAG_TOOLS`.
4. Re-run `python scripts/index_knowledge.py --source <name>`.

### Add new standards inline

Edit `collect_standards()` in `scripts/index_knowledge.py` and append
`{id, text, metadata}` dicts. Re-run the indexer.

### Add new IEEE reference markdown

Drop the `.md` files anywhere under
`~/AppData/Roaming/Claude/local-agent-mode-sessions/**/ieee-standards-guide/references/`.
The indexer auto-discovers them.

## Performance notes

- ChromaDB uses its **default sentence-transformer-mini** (all-MiniLM-L6-v2)
  for embeddings. ~80 MB on disk per ~1000 documents.
- Query latency on the current 133-doc index: **~30 ms**.
- For larger corpora (>10k docs), consider switching to BGE-M3 via
  `chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction`.

## Verification

```bash
python -c "
import asyncio; from src.tools.rag_tools import RAGToolExecutor
async def t():
    r = await RAGToolExecutor().execute('rag_query', {
        'query': 'IEEE 2800 GFM virtual inertia tuning',
        'sources': ['standards'], 'top_k': 3})
    for h in r['results']: print(h['score'], h['id'])
asyncio.run(t())
"
```

Expected output:
```
0.428 ieee2800_virtual_inertia
0.123 ieee2800_phase_jump
0.000 ieee2800_voltage_source
```

## Roadmap

- [ ] Track per-document vector hashes so the indexer can incrementally
      update only changed YAML / report files.
- [ ] Add a CLI flag to filter `test_history` by date range.
- [ ] Hybrid search (BM25 keyword + vector) for ID lookups.
- [ ] Auto-reindex hook on `git commit` of `configs/` or `prompts/`.
