# Evaluation

```bash
make evals                          # or: python -m evals.run
python -m evals.run --only retrieval
python -m evals.run --json report.json
```

Exits non-zero when a quality gate fails (any unsupported claim, or an agent
scenario failing), so CI blocks on it.

---

## 1. Retrieval evaluation

### Method

Five configurations are ranked over **identical Stage-A candidate sets**, so the
only variable is the ranking method:

| Configuration | What it is |
| --- | --- |
| `structural_only` | Deterministic closeness — the no-AI baseline |
| `keyword_only` | PostgreSQL `ts_rank_cd` |
| `semantic_only` | pgvector cosine |
| `hybrid_rrf` | Reciprocal rank fusion of all three |
| `hybrid_rerank` | Fusion, then the grader, truncated to the final set |

The baseline is the point of the exercise. If fusion cannot beat plain structural
scoring, the embedding infrastructure is decoration and should be removed.

### Avoiding a circular evaluation

Ground truth comes from the fixture **generative process** — the concept tags
assigned when each synthetic listing was created — not from anything the retrieval
system produces. The pipeline never sees those tags; it reads only the rendered
listing prose, in which each concept appears in one of two unpredictable registers.

A sold property is a "known good" comparable for a target when it is the same
type, within 2.5 km, sold within 12 months, within ±1 bedroom and ±25% floor area,
**and** shares at least half the target's qualitative concepts. That last clause is
what makes the set discriminating: without it every structural near-match would
qualify and a distance-sorted SQL query would score perfectly while telling us
nothing.

### Sample size

Three on-market targets is far too small to draw conclusions from, so the golden
set also uses **25 held-out sold properties as pseudo-targets**. A sold property is
a legitimate stand-in: same attributes, same listing prose, and it is excluded from
its own candidate set.

**28 cases (27 gradeable), 243 relevance labels.**

### Current results — fully offline configuration

```
configuration         P@5   P@10    R@5   R@10    MRR  nDCG@10
--------------------------------------------------------------
structural_only      0.430  0.433  0.264  0.528  0.570   0.498
keyword_only         0.667  0.529  0.443  0.652  0.844   0.699
semantic_only        0.474  0.474  0.309  0.560  0.740   0.577
hybrid_rrf           0.637  0.544  0.414  0.665  0.898   0.718
hybrid_rerank        0.496  0.522  0.295  0.645  0.710   0.622
```

These figures are reproducible. All three retrieval arms break ties on a stable
key (`properties.external_id` for the SQL and keyword arms, `source_id` for the
vector arm). Without that, `ts_rank_cd` and cosine distance produce enough exact
ties for the ranking — and therefore every fused score — to drift between
identical runs, which was observed and fixed rather than tolerated.

### What this actually shows

**Fusion beats the no-AI baseline decisively** — nDCG@10 0.718 against 0.498, a
44% relative improvement. The retrieval stack earns its place against doing
nothing clever at all.

**Fusion also beats the best single arm** — 0.718 against keyword-only's 0.699,
with the clearest gains where they matter most for a shortlist: MRR 0.898 vs
0.844 (the first genuinely comparable sale appears higher) and R@10 0.665 vs
0.652. That clears the bar for running three retrievers instead of one.

The margin is real but modest, and worth reading precisely: **the lexical arm is
doing most of the work on this corpus**, and fusion's contribution is robustness
rather than a large precision gain. Keyword retrieval collapses on a listing
written entirely in paraphrase — this corpus contains those by construction — and
fusion is what stops a single arm's blind spot becoming the system's blind spot.

**The semantic arm is the weak one, and that is expected here.** It runs on the
offline concept encoder, which only generalises to paraphrases the hand-written
lexicon anticipates. This is the single number most likely to improve with a real
embedding model, and the honest position is that this configuration *understates*
what the semantic arm contributes in production.

**Reranking does not improve nDCG@10 offline (0.622 vs 0.718) — and that is
reported rather than hidden.** The deterministic grader re-weights signals the
fusion already used; it adds no qualitative judgement, so it cannot add ranking
information. It does still improve MRR over the structural baseline, and it
enforces a defensible *inclusion* decision rather than blindly taking the top N.
An LLM grader is the component that changes this, and re-running this suite with
`ANTHROPIC_API_KEY` set is how you would find out by how much.

**One case is skipped and reported, not dropped.** The Concord house has no
comparable in the corpus meeting the golden criteria, so precision and recall are
undefined for it. Silently excluding it would flatter the averages.

### What would change these numbers

A real embedding model, which is the one change most likely to move the semantic
arm. The evaluation harness is the instrument for finding out: set
`OPENAI_API_KEY`, rebuild the index, and re-run.

Note what has deliberately *not* been done: the fusion weights are configurable
(`hybrid_retrieve(weights=...)`) and could easily be tuned until hybrid wins by a
comfortable margin. Tuning them against a 27-case synthetic golden set would be
overfitting to the evaluation, and the resulting number would mean nothing. The
weights stay equal and untuned.

### Limits of these numbers

They are measured on a synthetic corpus with programmatic labels. They demonstrate
that the pipeline works, that each stage is measurable, and that the ablation
methodology is sound. They are **not** evidence of production retrieval quality
against real listings, which would need human-labelled comparables.

---

## 2. RAG evaluation

Measured mechanically rather than with an LLM judge — a judge would itself need
validating and would not run at all in the offline configuration.

| Metric | Score | Meaning |
| --- | --- | --- |
| `retrieval_relevance` | 1.000 | Evidence tied to the target, its suburb, or a selected comparable |
| `groundedness` | 1.000 | Dollar figures trace to evidence or to deterministic computation |
| `citation_correctness` | 1.000 | Every cited id exists in what was retrieved |
| `faithfulness` | 1.000 | No prohibited claims, no invented comparables |
| `unsupported_claim_rate` | **0.000** | Share of answers containing *any* unsupported statement |

`unsupported_claim_rate` is the headline number. For a product people make
six-figure decisions on, "did it ever say something the evidence does not support"
matters more than any ranking metric.

These are perfect scores in a configuration where the narrative is
template-generated, which makes them a floor rather than a triumph: they prove the
grounding *checks* work and the deterministic path is clean. The same suite run
with an LLM configured is where they become interesting.

---

## 3. Agent evaluation

Scenario-driven, checked against the **recorded trace** rather than the final
answer — an agent can reach a plausible answer through incorrect behaviour, and
that is exactly the failure that bites later.

**Pass rate 100% (5/5), mean latency 219 ms.**

| Scenario | What it proves |
| --- | --- |
| `resolve_from_rea_url` | A pasted REA URL resolves and completes without widening |
| `resolve_from_address` | A plain street address resolves and completes |
| `thin_market_expansion` | Too few comparables → the agent widens the search and re-queries, rather than answering on weak evidence |
| `unresolvable_input` | Garbage input degrades to `insufficient_evidence`, and **no data tools are called** |
| `unsupported_site_url` | An unsupported property site is rejected before any data call |

Per scenario the suite checks: required tools called, forbidden tools not called,
workflow completed, assessment as expected, search expansion correct, no duplicate
tool calls, and graceful failure. Latency, tool-call count and LLM-call count are
recorded as the cost signal — with no LLM configured, LLM calls are zero and the
deterministic components run instead.

---

## 4. What is deliberately not measured

- **Valuation accuracy.** There is no ground-truth "correct" price, and inventing
  one would recreate the automated-valuation model this project deliberately is
  not.
- **LLM-judged answer quality.** Would need its own validation and cannot run
  offline. The mechanical grounding checks above are stricter about the thing that
  matters (unsupported claims) than a judge would be.
- **Real-world retrieval quality.** Requires human-labelled comparables against a
  live provider. The harness is ready; the labels are not.
