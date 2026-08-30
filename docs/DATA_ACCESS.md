# Data access

## The legal boundary

**This project does not scrape realestate.com.au or domain.com.au.**

A user may paste a URL from either site. That URL is treated purely as an
*identifier*: the slug is parsed locally
([`app/ingest/url_parser.py`](../backend/app/ingest/url_parser.py)) to recover the
suburb, state and listing id the user is referring to, and a licensed property
data provider is then asked about that property. No HTTP request is ever made to
those sites, by any code path.

## The provider abstraction

Everything downstream — SQL filtering, embedding, retrieval, the agent, the API —
depends only on
[`PropertyDataProvider`](../backend/app/providers/base.py) and the canonical types
in [`app/schemas/property.py`](../backend/app/schemas/property.py). Swapping
vendors is a configuration change.

```python
class PropertyDataProvider(ABC):
    async def resolve_property(query) -> PropertyResolution
    async def get_property(external_id) -> PropertyRecord | None
    async def get_current_listing(external_id) -> ListingRecord | None
    async def get_historical_listing(external_id) -> ListingRecord | None
    async def search_sold_transactions(...) -> list[SoldTransaction]
    async def get_market_context(...) -> MarketContext | None
```

### Capability declaration

Each adapter declares a `ProviderCapabilities`. This is not decoration: the flag
`historical_descriptions` decides whether semantic comparable retrieval is
meaningful at all. Without historical listing *text*, embedding property records
would just re-encode the structured attributes SQL already handles exactly, adding
cost and no signal.

A capability flag is a **hypothesis**. `scripts/check_property_data.py` is the
experiment.

## Verification status — read this before trusting anything

| Provider | Status | Notes |
| --- | --- | --- |
| **Demo** | Fully verified | Synthetic fixtures. Everything works end to end; proves the pipeline, proves nothing about a vendor. |
| **PropTrack** | **Unverified** | Written against documented endpoints. No credentials were available, so **no response shape has been checked against a live API**. |
| **Domain** | **Unverified** | Same. `historical_descriptions` is declared `False` — the conservative default — until proven otherwise. |

This is stated in the module docstrings, in the capability `notes`, and here,
because a plausible-looking adapter that has never seen a real response is a
liability if mistaken for a working one.

### How the unverified adapters are written to fail safely

1. **No field is assumed.** Every read goes through `_first(payload, *paths)`,
   which tries several documented paths and returns `None` rather than raising or
   inventing. A renamed upstream field becomes a visible "not recorded".
2. **Incomplete rows are dropped, never back-filled.** A sale with no price or no
   date is discarded rather than defaulted.
3. **Missing credentials fail loudly.** `PropTrackProvider()` and
   `DomainProvider()` raise `ProviderNotConfiguredError` on construction. A
   misconfigured live provider **never** silently falls back to fixtures —
   quietly serving demo data as live data is the single worst failure this
   product could have.

## The Phase 1 gate

```bash
make feasibility          # or: python scripts/check_property_data.py
```

It probes the configured provider and reports what actually works:

```
DATA FEASIBILITY REPORT
==============================================================

Provider                    demo  (DEMO FIXTURES)
Embedding backend           offline

Target property             PASS
Property attributes         PASS
Current listing             PASS
Asking / guide price        PASS
Sold transactions           PASS
Sold property identifiers   PASS
Historical sale prices      PASS
Historical descriptions     PASS
Market context              PASS

--------------------------------------------------------------
VECTOR RETRIEVAL VIABLE     YES
--------------------------------------------------------------
```

If the corpus cannot support semantic retrieval it says so, with the reason:

```
VECTOR RETRIEVAL VIABLE     LIMITED
Reason: Insufficient qualitative historical text: only 6 of 40 inspected sales
(15%) have a usable description. Semantic retrieval will fall back to
structured-attribute text, which cannot distinguish 'renovated, north-facing,
quiet' from its opposite.
```

The script exits non-zero when a check fails, so CI blocks on it. It is also
served live at `GET /api/data-feasibility`.

## The demo corpus

Generated deterministically by
[`scripts/generate_fixtures.py`](../backend/scripts/generate_fixtures.py)
(seed `20260830`), committed, and regenerable byte-identically — CI fails if the
committed file and a fresh generation disagree.

- 177 properties across 8 real inner-west Sydney suburbs (synthetic addresses)
- 174 sold listings spanning ~24 months, $530k–$3.0m
- 3 on-market targets: an apartment, a townhouse and a house
- 20 suburb/type market-context rows, 8 location profiles

### The design decision that makes the eval meaningful

Every qualitative concept is expressed in one of two registers, chosen
deterministically, roughly 50/50 across the corpus:

| Register | Example | Which retriever finds it |
| --- | --- | --- |
| explicit | "north-facing living areas" | PostgreSQL full-text |
| implicit | "living areas bathed in morning sun year round" | dense/semantic only |

Because the split is even, **neither the keyword arm nor the vector arm can
recover the full comparable set alone**. The hybrid-fusion evaluation therefore
measures something real instead of confirming a foregone conclusion.

### Labelling

Every fixture record carries `is_demo_data=True`, propagated through the database,
the API and into the UI as a persistent banner. The fixture file itself carries a
warning in its `_meta` block. Demo data is never presented as live market data.

## Offline mode for the AI components

Separate from property data, two more credentials are optional:

**Embeddings.** Without `OPENAI_API_KEY`, `OfflineConceptEncoder` produces vectors
deterministically: concept dimensions fire when a phrase from the hand-written
lexicon in [`concepts.py`](../backend/app/retrieval/concepts.py) appears, and the
rest is a signed hashed bag-of-words. Nothing is trained or fitted. It generalises
only to paraphrases the lexicon anticipates — a real embedding model generalises
to paraphrases nobody wrote down. Measured cosine on the canonical example:

| Comparison | Similarity |
| --- | --- |
| explicit restatement | 0.82 |
| paraphrase, no shared keywords | 0.44 |
| the opposite property | 0.17 |
| unrelated text | 0.06 |

Directionally correct, materially weaker than a real embedding model, and
reported as such on every analysis.

**Chat model.** Without an LLM key, `get_chat_model()` returns `None` and each
LLM-backed component switches to a rule-based implementation labelled
`offline:deterministic-*`. No component ever fabricates model output.
