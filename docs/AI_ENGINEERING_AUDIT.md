# AI engineering self-audit

Every row states where the capability lives and what problem it solves. Where
something is present but weaker than it looks, that is said plainly — a checklist
that only ever says "yes" is not an audit.

## Skill audit

| Skill | Implemented? | Exact file / component | Why it is necessary |
| --- | --- | --- | --- |
| **Python** | Yes | `backend/` — 81 modules, ~13,900 lines, typed throughout, `ruff` clean | Async I/O across DB, HTTP and subprocess MCP, in one runtime |
| **FastAPI** | Yes | `app/main.py`, `app/api/routes.py` — 10 endpoints | Async request handling, background analysis tasks, Pydantic validation, OpenAPI |
| **LLM API** | Yes, with an honest offline path | `app/llm/client.py` — `ChatAnthropic` / `ChatOpenAI` via LangChain | Reranking and narration. Without keys, `get_chat_model()` returns `None` and rule-based components run, labelled `offline:*`. **Never exercised against a live model in this environment** — see gaps |
| **Structured Output** | Yes | `with_structured_output(RerankVerdict)` and `(AssessmentNarrative)`; both `extra="forbid"` | Schema binding at provider level is what makes "the model may only emit this shape" a real constraint. `AssessmentNarrative` has *no numeric field* — the model has nowhere to put a price |
| **Embeddings** | Yes | `app/retrieval/embeddings.py` — hosted API, or `OfflineConceptEncoder` | Condition, aspect, position and outlook exist only as prose. Nothing trained or hosted |
| **pgvector** | Yes | `app/db/models.py` (`VECTOR(1536)`, HNSW cosine), `app/retrieval/vector_store.py` | Semantic retrieval in the same transaction as the structured filter |
| **Vector Search** | Yes | `similarity_search()` with source-type partitioning and property-id metadata filtering | Ranks *within* the eligible set rather than searching the whole corpus |
| **Hybrid Search** | Yes | `app/retrieval/hybrid.py` — RRF (k=60) over structural + `ts_rank_cd` + cosine | Each arm is blind to what the others see. Measured: fusion 0.708 nDCG@10 vs best single arm 0.697 vs baseline 0.498 |
| **Reranking** | Yes | `app/retrieval/rerank.py` — `LLMRelevanceGrader` / `DeterministicRelevanceGrader` | Retrieval optimises recall; the final set needs precision. `_verify_no_invention()` strips any verdict introducing an unsupported figure |
| **RAG** | Yes | `app/rag/evidence.py` (4 domains), `app/rag/chains.py` | The explanation may only use retrieved evidence, cited by id, with citations validated afterwards |
| **LangChain** | Yes | `BaseRetriever`, `Embeddings`, `ChatPromptTemplate \| model.with_structured_output()`, `StructuredTool`, `langchain-mcp-adapters` | Retrieval, structured output and tool plumbing — not a wrapper around one call |
| **LangGraph** | Yes | `app/agent/graph.py`, `nodes.py`, `state.py` — 14 nodes, 4 conditional edges, 2 bounded loops, `MemorySaver` | The workflow must react to insufficient evidence by widening the search and re-querying. Verified firing in `test_thin_market_triggers_the_expansion_loop` |
| **MCP** | Yes — real stdio boundary | `app/mcp_servers/{property_data,intelligence}_server.py` (9 tools), `client.py` | Enumerable capability surface; vendor swap without touching agent code; consumable by any MCP client. Verified: `test_mcp_stdio_exposes_every_declared_tool` |
| **Tool Calling** | Yes | `nodes._call_tool()`; `research_more_node` selects tools by which gap exists | Search expansion, semantic sweep and market lookup are chosen from state, and every call is traced |
| **State / Memory** | Yes | `PropertyAnalysisState` + `MemorySaver`; `get_previous_analysis` MCP tool | Loop counters and search parameters are what the conditional edges branch on. Cross-run memory via the analyses table |
| **Human-in-loop** | Yes | `POST .../exclude`, `.../include`, `.../rerun`; `rerank_node` honours exclusions and promotes replacements | Exclusion records the judgement; re-running recomputes range, label, confidence and narrative — never patched in place |
| **Evals** | Yes | `evals/` — retrieval (28 cases, 243 labels, 5 ablations), RAG (5 metrics), agent (5 scenarios) | `python -m evals.run` exits non-zero on regression; CI gates on it |
| **Guardrails** | Yes | `app/guardrails/validators.py` — 5 checks, 9 prohibited-claim patterns | Applied to *all* output, LLM or rule-based. 15 dedicated tests |
| **Observability** | Yes | `app/observability/` — structlog always; LangSmith when credentials exist; `agent_traces` table; `GET /api/analysis/{id}/trace` | "Show your working" must not depend on a subscription. **LangSmith path is unexercised here** — see gaps |
| **PostgreSQL** | Yes | 7 tables, generated tsvector, HNSW + GIN + btree indexes, `psycopg` async, SQLAlchemy 2 | Deterministic eligibility filtering, full-text ranking and vector search in one system |
| **React / Next.js** | Yes | `frontend/` — Next 15 App Router, React 19, TypeScript strict, Tailwind, 18 modules | Dashboard-first result UI; progress from real graph stages; every retrieval score labelled for what it is |
| **Docker** | Yes, **unverified build** | `backend/Dockerfile` (multi-stage, non-root, healthcheck), `frontend/Dockerfile` (standalone output), `docker-compose.yml` | One-command stack with pgvector. **The Docker daemon was not running in this environment, so no image was built** — see gaps |
| **Testing** | Yes | 135 tests in 11.5s — unit / integration / AI behaviour | Includes anti-hallucination, grounding and graceful-degradation assertions |

---

## Gaps — stated rather than papered over

Four capabilities are implemented and wired but **not exercised end to end in this
environment**. In each case the code path exists, is unit-tested where possible,
and degrades to a labelled fallback.

### 1. Live LLM inference

- **Why missing:** no `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the environment.
- **Genuinely needed?** Yes. The deterministic grader cannot weigh "renovated vs
  original condition" against "3 m² smaller" the way a valuer would, and the
  template narrative reports evidence without interpreting it. The evaluation
  shows the cost directly: reranking does not improve nDCG@10 offline.
- **What completes it:** set either key. `get_chat_model()` returns a configured
  model, `LLMRelevanceGrader` and the narration chain activate with no code change,
  and `generated_by` changes from `offline:*` to the model id. Re-run
  `python -m evals.run` to quantify the improvement.

### 2. Live property data (PropTrack / Domain)

- **Why missing:** no vendor credentials. Both adapters are written against
  documented endpoints; **no response shape has been verified against a live API**.
- **Genuinely needed?** Yes — it is the difference between a demonstration and a
  product.
- **What completes it:** set `PROPTRACK_CLIENT_ID`/`_SECRET` (or
  `DOMAIN_API_KEY`) and `PROPERTY_PROVIDER`, then run
  `python scripts/check_property_data.py`. That gate reports which capabilities
  actually work and whether the historical-description corpus supports semantic
  retrieval. Expect the `_PATHS` mapping and `_normalise_*` helpers to need
  adjustment — they are isolated in one file for exactly that reason.

### 3. LangSmith tracing

- **Why missing:** no `LANGCHAIN_API_KEY`.
- **Genuinely needed?** Useful, not essential. The local `agent_traces` table
  already records every node, tool call, retrieval and guardrail decision, and
  serves the trace endpoint. LangSmith adds token/cost accounting and cross-run
  comparison.
- **What completes it:** set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY`.
  `configure_tracing()` wires it at startup; LangChain and LangGraph instrument
  themselves.

### 4. Docker image builds

- **Why missing:** the Docker daemon was not running in this environment.
- **Genuinely needed?** Yes for deployment.
- **What completes it:** `docker compose up --build`. The Dockerfiles and compose
  file are written and reviewed but **have not been executed**; treat them as
  untested until they are. CI builds both images on every push.

### Smaller honest notes

- **Offline embeddings are weaker than a real model.** `OfflineConceptEncoder`
  generalises only to paraphrases its hand-written lexicon anticipates. Reported on
  every analysis and in the feasibility report.
- **Golden labels are synthetic.** Valid for the demo corpus only; real evaluation
  needs human-labelled comparables.
- **Fusion's margin over keyword-only is thin** (0.708 vs 0.697) on this corpus in
  this configuration. Real and reproducible, but not a large effect, and stated as
  such in `EVALUATION.md`.

---

## Prohibited-stack audit

Confirmed **absent** from this project:

| Prohibited | Status | Evidence |
| --- | --- | --- |
| ML model training | **Not present** | No `fit()`, no training loop, no optimiser, no dataset splits for learning. No scikit-learn, PyTorch, TensorFlow, XGBoost or LightGBM in `requirements.lock.txt` |
| Fine-tuning | **Not present** | No fine-tuning API calls, no adapters, no checkpoints |
| Predictive statistical valuation models | **Not present** | No regression, no hedonic pricing, no time-series forecast. `app/assessment/pricing.py` computes median, quartiles, differences and percentages from observed sale prices only |
| Feature engineering for ML | **Not present** | No feature pipeline feeds any predictor. The concept lexicon is a hand-written retrieval vocabulary, never fitted to data and never consumed by a model |
| Model serving | **Not present** | No inference server, no model artefacts, no GPU dependency. Embeddings and chat come from hosted APIs; the offline encoder is deterministic hashing |
| MLOps pipelines | **Not present** | No experiment tracking, model registry, feature store or retraining schedule. CI runs lint, tests and evaluations only |

### On the two components that could be mistaken for ML

**`OfflineConceptEncoder`** maps text to a vector, which superficially resembles an
embedding model. It is not one: there are no learned parameters, nothing is fitted
to data, and it is fully determined by a hand-written lexicon plus a hash function.
It is a deterministic text encoding — the same category as TF-IDF hashing — used so
the pgvector infrastructure is exercisable without an API key.

**`scripts/generate_fixtures.py`** synthesises plausible sale prices from suburb
tier, size and concept premiums. That is **data synthesis for fixtures**, run once
offline to create test data. It is not used as a model: no product code path
predicts a price, and the evidence range derives exclusively from observed sale
prices in the corpus. Its output is labelled synthetic everywhere it appears.

---

## Engineering-quality summary

| Dimension | Evidence |
| --- | --- |
| Clean architecture | Provider / retrieval / RAG / agent / API layers with one-directional dependencies |
| Type safety | Pydantic v2 throughout, SQLAlchemy 2 `Mapped[...]`, TypeScript `strict` + `noUncheckedIndexedAccess` |
| Modular provider abstraction | One ABC, three adapters, capability declaration, factory selection |
| Error states | Nodes never raise; per-candidate and per-narration fallbacks; MCP degradation; app boots even with no database so `/health` can explain why |
| Evidence transparency | Every retrieval score persisted and surfaced with a caption saying what it means; full trace endpoint |
| Testability | 135 tests in 11.5 s; integration tests skip with actionable messages instead of failing obscurely |
| Retrieval quality | Measured with ablations against a no-AI baseline, on 243 relevance labels |
| Realistic failure handling | Bounded loops; `insufficient_evidence` is a first-class answer |
| Maintainability | `ruff` clean and formatted; docstrings explain *why*, not *what*; a single `_PATHS` map per vendor |
| UX | Dashboard-first; real workflow stages; verdict beside confidence; unknowns given equal weight; zero horizontal overflow verified at 390/768/1440 px |
