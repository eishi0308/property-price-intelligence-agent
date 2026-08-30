# Property Price Intelligence Agent

**A buyer-side evidence intelligence agent for Australian residential property.**

It answers one question:

> Is this property's asking price actually reasonable compared with genuinely similar recent sold properties?

It is **not** a property search engine, a price predictor, an automated valuation
model, or a chatbot. It retrieves real comparable-sales evidence, shows exactly
which sales support its conclusion, and is explicit about what it could not
establish.

```
ASKING PRICE      $950,000
EVIDENCE RANGE    $881,250 – $961,250   (median $915,000)
ASSESSMENT        Reasonable
CONFIDENCE        Medium
```

> This is an evidence-based comparable-sales analysis, not a professional
> property valuation or financial advice.

---

## The problem

A buyer looking at a listing has almost no way to answer "is this price fair?".
The agent will not tell them. Portal "estimates" are opaque automated valuations
with error bands wide enough to swallow a deposit. Recent sales data exists, but
turning it into an answer means finding the handful of sales that are genuinely
comparable — same type, nearby, recent, similar size *and* similar in the
qualitative ways that move price: renovated or original, quiet street or main
road, parking or none, north-facing or dark.

That last part is why this is an interesting retrieval problem rather than a SQL
query. "Renovated, north-facing, with secure parking" and "reimagined throughout,
bathed in morning sun, with lock-up garaging" describe the same property and
share no keywords.

## What it does instead of guessing

1. Resolves a pasted listing URL or address to a known property.
2. Narrows recent nearby sales with deterministic SQL — type, distance, recency,
   bedrooms, size.
3. Ranks the survivors three ways (structural, lexical, semantic) and fuses them.
4. Grades the shortlist down to the strongest 5–10 comparables.
5. Computes an evidence range from those sale prices by plain arithmetic.
6. Retrieves supporting evidence and writes an explanation that may only cite it.
7. Runs guardrails, then shows you everything — including the gaps.

If the evidence is thin, it widens the search. If it is still thin, it says
**insufficient evidence** rather than producing a confident number.

---

## Architecture

```
                        User
                          │
                    Next.js / React / TypeScript
                          │  REST
                    FastAPI  (Pydantic schemas everywhere)
                          │
                    LangGraph  ── stateful workflow, loops, retries
                       │   │
        ┌──────────────┘   └──────────────┐
        │                                 │
   MCP tool boundary                LangChain components
   (stdio, typed tools)             retrievers · structured output · RAG chain
        │                                 │
   ┌────┴─────┐                           │
property-data  property-intelligence      │
 (PropTrack /   (semantic search,         │
  Domain /       market evidence,         │
  Demo)          prior analyses)          │
        │                                 │
        └──────────────┬──────────────────┘
                       │
                  PostgreSQL
                       │
        ┌──────────────┼───────────────────────┐
        │              │                       │
  SQL hard filter   full-text (tsvector)   pgvector (HNSW, cosine)
   Stage A            Stage C                Stage B
        └──────────────┴───────────┬───────────┘
                                   │
                      Reciprocal Rank Fusion   Stage D
                                   │
                          Reranker (LLM or rules)   Stage E
                                   │
                   Deterministic price arithmetic
                                   │
                    Evidence-grounded narration (RAG)
                                   │
                              Guardrails
                                   │
                             Final response
```

### The retrieval pipeline in numbers

A real run against the demo corpus (`12/45 Redmyre Road, Strathfield`):

```
174 sold properties in the search window
→  92 of a compatible property type          Stage A: SQL
→  64 sold within 12 months                  Stage A: SQL
→  55 within 3 km                            Stage A: SQL
→  54 of comparable size and configuration   Stage A: SQL
→  24 ranked by fusion of three arms         Stages B–D
→   8 selected as defensible comparables     Stage E
```

---

## Why each piece exists

Every technology below earns its place by solving a problem the others cannot.
Where something is weaker than it looks, that is stated.

### PostgreSQL — deterministic eligibility
Comparability has hard rules before it has soft ones. A 4-bedroom house is never
a comparable for a 2-bedroom apartment, however similarly the listings read.
Encoding that as SQL makes it exact, fast, auditable and impossible for a model
to override. It also cuts the candidate set by ~70% before any expensive stage
runs. → [`app/retrieval/sql_filter.py`](backend/app/retrieval/sql_filter.py)

### Embeddings — the qualitative dimension SQL cannot hold
Condition, aspect, outlook, position and noise are the difference between two
otherwise identical units, and they exist only as prose. Embedding the listing
description captures them. No model is trained or hosted; a hosted embedding API
is called through LangChain's `Embeddings` interface.
→ [`app/retrieval/embeddings.py`](backend/app/retrieval/embeddings.py),
[`app/retrieval/semantic_text.py`](backend/app/retrieval/semantic_text.py)

### pgvector — semantic retrieval in the same database
The structured filter and the semantic search operate on the same rows. Keeping
both in PostgreSQL means the candidate set can be narrowed by SQL and searched by
cosine similarity in one query, one transaction, one consistency model. A separate
vector service would add a distributed join and an eventual-consistency problem
for no benefit at this scale. HNSW index, cosine distance, metadata filtering.
→ [`app/retrieval/vector_store.py`](backend/app/retrieval/vector_store.py)

### Hybrid search — because each arm is blind to something
Dense retrieval is defeated by literal requirements ("lock-up garage", a street
name). Lexical retrieval is defeated by paraphrase. Structural scoring is blind to
everything qualitative. Running all three and fusing their *rankings* — not their
incomparable scores — is the only way to get both. Reciprocal Rank Fusion, k=60,
scale-free and untuned.
→ [`app/retrieval/hybrid.py`](backend/app/retrieval/hybrid.py)

### Reranking — recall and precision are different objectives
Retrieval casts a wide net; the final comparable set must be precise, because
every property in it moves the range a buyer will negotiate against. An LLM grader
with structured output judges comparability the way a valuer would. It may only
*judge* the fact sheet it is given — a filter rejects any verdict introducing a
figure that was not in the input.
→ [`app/retrieval/rerank.py`](backend/app/retrieval/rerank.py)

### RAG — so the explanation cannot drift from the evidence
Four retrieval domains (the target listing, the selected comparables, suburb
market context, location context), each queried differently and each carrying a
citation id. The narrator receives evidence and nothing else, and the guardrail
layer rejects any citation that was not retrieved.
→ [`app/rag/evidence.py`](backend/app/rag/evidence.py),
[`app/rag/chains.py`](backend/app/rag/chains.py)

### LangChain — retrieval, structured output and tool plumbing
`BaseRetriever` for the pgvector retriever, `Embeddings` for both embedding
backends, `ChatPromptTemplate | model.with_structured_output(Schema)` for the
narration chain, `StructuredTool` for in-process tools, and the MCP adapters that
turn MCP tools into LangChain tools. Structured output is the load-bearing part:
binding a Pydantic schema at the provider level is what makes "the model may only
emit `RerankVerdict`" a real constraint rather than a hopeful instruction.

### LangGraph — because the workflow has to change its mind
A straight pipeline can only report what it found. This workflow has to react to
finding too little: widen the radius and lookback, re-query, re-filter, re-rank,
re-evaluate. That needs persistent state (`search_radius_km`, `expansions_used`,
`evidence_quality`) and conditional edges that branch on it. Two bounded loops,
explicit failure routing, and a checkpointer.
→ [`app/agent/graph.py`](backend/app/agent/graph.py),
[`app/agent/state.py`](backend/app/agent/state.py)

### MCP — a real boundary, not a wrapper
Two stdio MCP servers expose nine typed tools. Everything the agent touches
outside its own process goes through them, which means the agent's capabilities
are enumerable in one place, the property vendor can be swapped without touching
agent code, and the same tools are consumable by any other MCP client.
Keeping the vendor server separate from the internal retrieval server lets an
operator grant semantic search without granting metered API access.
→ [`app/mcp_servers/`](backend/app/mcp_servers/)

### Tool calling — the agent chooses what it needs
`search_sold_properties` with a wider radius when comparables are too few;
`search_similar_properties` when the set is qualitatively mismatched;
`retrieve_market_evidence` when a price gap is unexplained. Every call is recorded
with its arguments, duration and outcome.

### Guardrails — this is a six-figure decision
No guaranteed value, no appreciation or yield forecast, no "professional
valuation", no financial or legal advice, no point valuation. Every citation must
exist; every comparable is re-read from the database rather than trusted from the
narrative; every dollar figure must trace to evidence or to our own arithmetic;
confidence can never exceed what the evidence quality supports.
→ [`app/guardrails/validators.py`](backend/app/guardrails/validators.py)

### Evaluations — so "it works" is a measurement
Retrieval is measured against a golden dataset (28 cases, 243 labels) with an
ablation per arm; fusion scores nDCG@10 0.718 against 0.699 for the best single
arm and 0.498 for a no-AI baseline. RAG is measured for groundedness, citation correctness
and unsupported-claim rate (currently 0.000). The agent is measured on behaviour:
correct tool choice, correct search expansion, graceful failure.
`python -m evals.run` exits non-zero when a quality gate fails.
→ [`backend/evals/`](backend/evals/), [`docs/EVALUATION.md`](docs/EVALUATION.md)

### Observability — always on, never dependent on a subscription
LangSmith when credentials exist. A local `agent_traces` table always, which is
what `GET /api/analysis/{id}/trace` serves and what the UI's "How this answer was
produced" panel renders.

---

## Running it

Nothing here requires a paid API key. With no credentials at all the stack runs
end to end on a synthetic corpus with deterministic components, and says so on
every screen.

### Docker (one command)

```bash
docker compose up --build
# frontend  http://localhost:3000
# API docs  http://localhost:8000/docs
```

### Locally

```bash
make setup       # venv + backend deps + frontend deps
make bootstrap   # create the database, seed the demo corpus, build the index
make feasibility # PHASE 1 GATE — prove the provider can supply what we need
make api         # http://localhost:8000
make web         # http://localhost:3000
```

Requires Python 3.11+, Node 20+, and PostgreSQL 14+ with the `vector` extension.

### Verify it

```bash
make test    # 135 tests
make evals   # retrieval / RAG / agent evaluation report
make lint
```

---

## Configuration

Copy `.env.example` to `.env`. Everything is optional; each absent credential
degrades one capability *visibly* rather than breaking the app.

| Variable | Effect when absent |
| --- | --- |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Reranking and narration use deterministic rules; `generated_by` says so and confidence is capped |
| `OPENAI_API_KEY` (embeddings) | Semantic search uses the offline concept encoder; every response reports reduced recall |
| `PROPTRACK_*` / `DOMAIN_API_KEY` | The demo fixture provider is used, labelled as synthetic everywhere |
| `LANGCHAIN_API_KEY` | Local trace table only |

**Degraded modes are never silent.** `GET /api/health` enumerates them, every
analysis carries the backends that produced it, and the UI shows both.

---

## API

```
POST /api/analysis                                       start an analysis
GET  /api/analysis                                       recent analyses
GET  /api/analysis/{id}                                   status, result, stages
GET  /api/analysis/{id}/comparables                       the comparable set
POST /api/analysis/{id}/comparables/{cid}/exclude         human-in-the-loop
POST /api/analysis/{id}/comparables/{cid}/include         undo
POST /api/analysis/{id}/rerun                             recompute honouring exclusions
GET  /api/analysis/{id}/trace                             full run trace
GET  /api/health                                          capabilities and degradations
GET  /api/data-feasibility                                the Phase 1 gate, live
```

Excluding a comparable records the decision but deliberately does **not** patch
the range — you must re-run, so the conclusion and its explanation stay
consistent with each other.

---

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Component responsibilities, the agent graph, data model, request lifecycle |
| [`docs/DATA_ACCESS.md`](docs/DATA_ACCESS.md) | The provider abstraction, what is verified and what is not, the legal boundary |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | Methodology, current results, and what they do and do not prove |
| [`docs/AI_ENGINEERING_AUDIT.md`](docs/AI_ENGINEERING_AUDIT.md) | Skill-by-skill audit and the prohibited-stack audit |

---

## Scope

**Deliberately not built:** contract or strata acquisition and analysis, building
inspection, mortgage or loan recommendation, investment-return or future-price
prediction, automated offers or negotiation, any custom ML valuation model, and
any scraping of realestate.com.au or domain.com.au.

**Extension points kept open:** saved properties and watchlists, price-change
monitoring, user preference embeddings, and document RAG over uploaded contracts
and strata reports — the `evidence_chunks` table and the provider abstraction
already accommodate these without redesign.
