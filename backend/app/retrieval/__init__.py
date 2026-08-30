"""Multi-stage comparable retrieval.

Stage A  sql_filter    deterministic PostgreSQL filtering  (SQL only)
Stage B  vector_store  pgvector cosine similarity          (semantic)
Stage C  keyword       PostgreSQL full-text ranking        (lexical)
Stage D  hybrid        reciprocal rank fusion              (combine)
Stage E  rerank        LLM / deterministic grader          (select 5-10)
"""
