# Weather App Bootcamp Feedback

**Score: 97 / 100**

---

## Breakdown

### Harvest (25/25) ✅
- Excellent, well-factored client module (weather_client.py)
- Rate limiting, stable IDs, helpful narrative fields
- Normalized document schema
- POST /weather/sync with filters
- Good error handling

### Vectorize (28/30) ⭐ 
**Strengths:**
- Reads documents from Lakebase via pg8000
- Reasonable chunking (800/100 sliding window) with justification
- sentence-transformers/all-MiniLM-L6-v2 (384-dim) consistently
- Direct Postgres driver (pg8000), not Spark JDBC ✅
- Tables with vector(384) and HNSW indexes

**Improvement Needed:**
- Currently inserts cast to `double precision[]` then require manual UPDATE to `vector`
- **Recommendation:** Insert directly as vector using `%s::vector` or `to_vector(%s::float8[])`
- Consider batching inserts (e.g., execute_values) for throughput

### Retrieve (30/30) ✅
- POST /weather/search implemented
- Correct cosine similarity using pgvector's `<=>`
- Model loaded once at startup
- Edge cases covered

### Documentation (14/15) ⭐
**Strengths:**
- Clear rationale for NWS
- Schema decisions well explained
- Excellent end-to-end instructions

**Minor Improvement:**
- Could explicitly mention why direct driver vs Spark JDBC (cohort constraint)

---

## Critical Checks

✅ Spark JDBC used for embedding writes? **NO**  
✅ Vector search is real semantic search? **YES**  
✅ Model loaded once, not per-request? **YES**

---

## Action Items for Next Project

1. **Fix embedding inserts** - Insert directly as `vector` type, avoid manual UPDATE
2. **Batch inserts** - Use execute_values or similar for better throughput
3. **Logger bug** - Move logger setup above try/except in app.py
4. **Explicit Spark note** - Mention cohort constraint in docs

---

## Instructor Comments

> "Outstanding submission: you implemented a clean NWS client, robust ingestion, correct pgvector retrieval, and a thoughtful doc+chunk embedding strategy with indexes. The app exposes both sync and semantic search endpoints (plus a RAG GET endpoint) and handles edge cases well."

---

**Key Takeaway:** Production-ready submission with minor polish needed for direct vector inserts and batching.
