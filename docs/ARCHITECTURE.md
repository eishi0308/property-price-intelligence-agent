# Architecture

## Responsibility split

These four are constantly conflated in agent codebases. Here they are kept strictly apart:

| Layer | Responsibility | Does **not** |
| --- | --- | --- |
| **MCP** | The capability boundary — typed tools reaching external data and internal retrieval | Decide anything about workflow |
| **LangGraph** | Orchestration — what runs, in what order, what happens when evidence is insufficient | Talk to vendors directly |
| **LangChain** | LLM, retriever, prompt and structured-output components | Own control flow |
| **RAG** | Evidence retrieval and grounding of the explanation | Compute any number |
| **pgvector** | Semantic retrieval substrate | Decide eligibility |

## Request lifecycle

```
POST /api/analysis
  → analyses row created (status=pending), background task started, 202 returned
  → LangGraph run streams state snapshots
      → each snapshot persists `stages` so the client sees real progress
  → final state persisted: assessment, comparables (with all retrieval scores),
    evidence, trace
GET /api/analysis/{id}   ← client polls; sees stages appear as they complete
```

The analysis runs in the background rather than in the request because it touches
an external API, a vector index and possibly an LLM. The UI wants to show progress
anyway, so holding a request open would be a bad trade.

## The agent graph

```
START
  ↓
resolve_property ──────── error ──→ failure
  ↓
fetch_target_data
  ↓
search_sold_candidates ← ─────────────────┐  (MCP tool call)
  ↓                    ── error ──→ failure│
hard_filter                                │
  ↓                                        │
hybrid_retrieval                           │
  ↓                                        │
rerank                                     │
  ↓                                        │
evaluate_evidence                          │
  ↓                                        │
[enough strong comparables?] ── no ──→ expand_search
  │ yes
  ↓
retrieve_supporting_evidence ← ────────────┐
  ↓                                        │
[evidence sufficient?] ─────── no ────→ research_more
  │ yes
  ↓
assess_price ──── error ──→ failure
  ↓
guardrail_check
  ↓
finalise → END
```

### Why this is a graph and not a function

The two loops are the whole point.

**Outer loop — expansion.** When too few comparables survive grading,
`expand_search` widens the radius and lookback and routes back to
`search_sold_candidates`, so the wider window actually pulls *new* sales rather
than re-filtering the same rows. Observed on the Concord house: 3 comparables at
3 km/12 months → widen to 5 km/18 months → 6 comparables, evidence quality
`strong`.

**Inner loop — research.** When the evidence bundle cannot support an
explanation, `research_more` performs targeted extra retrieval chosen by *which*
gap exists, then returns to `retrieve_supporting_evidence`.

### Termination

Both loops are bounded by counters checked in the routing functions
(`max_search_expansions`, `max_evidence_retries`), with `recursion_limit=60` as a
backstop. Routing also refuses to spend a retry widening a window already at its
maximum.

When a budget is exhausted the graph proceeds to assessment rather than failing:
`evidence_quality` is carried forward honestly, the label becomes
`insufficient_evidence`, and the user is told what was missing. For a buyer that
is a far more useful outcome than an error.

## Data model

```
properties ──1:N── listings ─────────┐
     │                               │ search_vector (STORED tsvector, GIN)
     │                               │ → the lexical retrieval arm
     ├──1:N── evidence_chunks ───────┤
     │           embedding VECTOR(1536), HNSW cosine
     │           → the semantic retrieval arm
     │
analyses ──1:N── comparable_results  (every stage's score retained)
     ├───1:N── analysis_evidence      (what the narrator actually saw)
     └───1:N── agent_traces           (node, tool, retrieval, guardrail events)
```

Three deliberate choices:

- **`listings.search_vector` is a generated STORED column.** PostgreSQL maintains
  it, so the keyword arm needs no application-side indexing and can never drift
  from the description.
- **`comparable_results` keeps every arm's score, not just the winner's.** This is
  what makes the retrieval evaluation and the UI's transparency panel possible.
- **`agent_traces` exists even though LangSmith integration does.** The
  "show your working" guarantee must not depend on a third-party subscription.

## The retrieval pipeline

### Stage A — deterministic SQL filtering
Eligibility is a rules question, not a similarity question. Property type,
distance (haversine in SQL, with an index-friendly bounding box first), recency,
bedrooms, bathrooms, floor and land area. Unknown attributes are **kept**, not
dropped — an unknown is not a mismatch, and the reranker can see the gap and
discount it explicitly. Funnel counts are computed in a single pass with
conditional aggregation and shown to the user.

### Stage B — pgvector semantic retrieval
Cosine similarity over listing-description embeddings, restricted to the Stage-A
candidate ids. We never search the whole corpus: Stage A has already decided what
is *eligible*, so this stage only ranks what is *similar*, which is what
embeddings are actually good at.

### Stage C — PostgreSQL full-text
`ts_rank_cd` over the stored tsvector, with a tsquery built from the concepts
detected in the target's own listing text.

### Stage D — reciprocal rank fusion
The three arms produce incomparable scales: `ts_rank_cd` is unbounded and
corpus-dependent, cosine sits in [-1,1] with a distribution that shifts with the
embedding model, and the structural score is a bounded hand-built composite.
Normalising them would require weights we have no principled basis for and would
need re-tuning whenever the embedding backend changes. RRF discards magnitudes and
keeps ordering:

```
score(d) = Σ_arms  weight_arm / (k + rank_arm(d))       k = 60
```

Scale-free, untuned, and it degrades gracefully when an arm returns nothing.

### Stage E — reranking
Retrieval optimises recall; the final set needs precision. An LLM grader with
structured output, or the deterministic grader offline. Both emit `RerankVerdict`,
so nothing downstream knows which ran.

## Price assessment

No model. The evidence range is the **interquartile range** of the selected
comparables' sale prices when there are five or more, and the full observed span
below that (with a note saying so).

Min–max is the obvious choice and the wrong one: one deceased estate or one
unusually large unit stretches it until it says nothing. The IQR needs two
atypical sales pointing the same way before it moves, and the full span is
reported alongside so nothing is hidden.

Labels come from explicit thresholds (±3% of the range is "reasonable", up to 10%
above is "slightly high"). Confidence starts at the ceiling the evidence quality
allows and only ever falls — every mechanism that lowers it corresponds to a known
weakness the user cannot see for themselves.

## Guardrails

Applied to every assessment, LLM-generated or not — rule-based templates can also
overclaim, and a user cannot tell which produced a sentence.

1. Prohibited claims (guaranteed value, appreciation, yield, "professional
   valuation", financial or legal advice, point valuation) → sentence removed.
2. Citation integrity → citations to unretrieved evidence stripped.
3. No invented comparables → the answer's comparables are replaced with records
   re-read from the database, so a hallucinated price cannot survive.
4. Unsupported figures → dollar amounts must trace to evidence or to our own
   deterministic arithmetic.
5. Confidence coherence → capped by evidence quality; `insufficient_evidence`
   forces `low`.

## Failure handling

- Nodes never raise; they record `error`/`failed_stage` and route to `failure`,
  which still produces a well-formed `insufficient_evidence` answer explaining
  where it stopped.
- A failed grade for one candidate falls back to the deterministic grader for that
  candidate only, and says so.
- A failed narration falls back to the deterministic template and says so.
- MCP stdio unavailable → in-process tools, with `degraded_reason` surfaced in
  `/api/health` and in the run metadata.
- Database unreachable at startup → the app still boots, so `/api/health` can
  report why it is unusable.

## Performance note

MCP tools were originally loaded via `MultiServerMCPClient.get_tools()`, which
opens a fresh stdio session — and therefore spawns a fresh Python subprocess — on
**every tool call**: ~1.5 s of pure overhead each, dominating the whole analysis.

Sessions are now held open by a single dedicated owner task
([`_PersistentSessions`](../backend/app/mcp_servers/client.py)). It cannot simply
be an `AsyncExitStack` on a module global, because anyio cancel scopes must be
entered and exited by the same task while the toolset is shared across request
tasks. One task enters the stack, publishes the tools, and parks until shutdown;
other tasks only send and receive on the session's memory streams, which is safe
across tasks.

Measured: **1566 ms → 7–22 ms per tool call.** A full analysis runs in ~300 ms.
