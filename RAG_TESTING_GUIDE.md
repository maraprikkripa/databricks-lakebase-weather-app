# RAG Testing Guide

## What We Just Built

We added a **RAG (Retrieval Augmented Generation)** endpoint to the weather app that:

1. **Retrieves** relevant weather documents using vector search
2. **Augments** an LLM prompt with those documents as context
3. **Generates** a natural language answer

This is the "extra credit" stretch goal from the bootcamp instructions!

---

## How It Works (Step by Step)

### Example: User asks "Is there flooding in Illinois?"

#### Step 1: Embed the Query
```python
query = "Is there flooding in Illinois?"
query_embedding = embedding_model.encode(query)
# → [0.12, -0.45, 0.78, ...] (384-dimensional vector)
```

#### Step 2: Vector Search (Retrieval)
```sql
SELECT
    d.location, d.headline, e.chunk_text,
    1 - (e.embedding <=> query_embedding) as similarity
FROM weather_embeddings e
JOIN weather_documents d ON d.id = e.document_id
ORDER BY e.embedding <=> query_embedding
LIMIT 5
```

**Results:**
- Document 1: "Flash Flood Warning for Chicago, IL..." (similarity: 0.89)
- Document 2: "River levels rising in Springfield..." (similarity: 0.84)
- Document 3: "Flood Watch continues for northern Illinois..." (similarity: 0.81)

#### Step 3: Build Augmented Prompt
```
System: You are a weather assistant. Answer based ONLY on the context.

Context:
[Document 1]
Location: Chicago, IL
Type: alert
Headline: Flash Flood Warning
Content: Flash Flood Warning for Chicago due to heavy rainfall...

[Document 2]
Location: Springfield, IL
Type: alert
Headline: River Flood Warning
Content: River levels rising rapidly...

User Question: Is there flooding in Illinois?
```

#### Step 4: Generate with LLM
```python
response = anthropic_client.messages.create(
    model="claude-3-5-sonnet-20241022",
    messages=[{"role": "user", "content": augmented_prompt}]
)
```

**LLM Output:**
```
Yes, there are active flood warnings in Illinois:

- Chicago has a Flash Flood Warning due to heavy rainfall
- Springfield has a River Flood Warning with rapidly rising water levels
- A Flood Watch is in effect for northern Illinois

Stay informed, avoid flooded roads, and follow local emergency guidance.
```

#### Step 5: Return JSON Response
```json
{
  "query": "Is there flooding in Illinois?",
  "summary": "Yes, there are active flood warnings...",
  "sources": [
    {
      "location": "Chicago, IL",
      "headline": "Flash Flood Warning",
      "similarity": 0.89
    }
  ]
}
```

---

## API Comparison

### POST /weather/search (Vector Search Only)
**What it does:** Returns raw chunks ranked by similarity

**Request:**
```bash
curl -X POST http://localhost:8000/weather/search \
  -H "Content-Type: application/json" \
  -d '{"query": "flooding", "top_k": 5}'
```

**Response:**
```json
{
  "query": "flooding",
  "results": [
    {
      "chunk_text": "Flash Flood Warning for Chicago...",
      "similarity": 0.89
    }
  ]
}
```

**User must:** Read and interpret the chunks themselves

---

### GET /weather/search?query=... (RAG with LLM)
**What it does:** Returns a natural language answer + sources

**Request:**
```bash
curl "http://localhost:8000/weather/search?query=Is%20there%20flooding%20in%20Illinois?&top_k=5"
```

**Response:**
```json
{
  "query": "Is there flooding in Illinois?",
  "summary": "Yes, there are active flood warnings in Chicago and Springfield...",
  "sources": [
    {
      "location": "Chicago, IL",
      "headline": "Flash Flood Warning",
      "similarity": 0.89
    }
  ]
}
```

**User gets:** Direct answer in natural language

---

## Setup Instructions

### 1. Get an Anthropic API Key

1. Go to https://console.anthropic.com/
2. Sign up or log in
3. Create a new API key
4. Copy the key (starts with `sk-ant-api03-...`)

### 2. Set Environment Variable

**Option A: Local development (.env file)**
```bash
# Create .env file in project root
echo 'ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE' >> .env
```

**Option B: Databricks Secrets (production)**
```python
# In a Databricks notebook:
from databricks.sdk.runtime import *

# Store the API key
dbutils.secrets.put(scope="database", key="anthropic-api-key")
# You'll be prompted to enter the key
```

Then update `app.py` to read from secrets:
```python
# Add this near line 42:
try:
    from databricks.sdk.runtime import dbutils
    anthropic_api_key = dbutils.secrets.get(scope="database", key="anthropic-api-key")
except:
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
```

### 3. Install Dependencies

```bash
pip install anthropic>=0.39.0
```

---

## Testing the RAG Endpoint

### Test 1: Basic Query
```bash
curl "http://localhost:8000/weather/search?query=Is%20there%20bad%20weather%20today?"
```

**Expected:** Natural language summary of current alerts/forecasts

---

### Test 2: Specific Alert Query
```bash
curl "http://localhost:8000/weather/search?query=flooding%20near%20rivers&source_type=alert"
```

**Expected:** Summary focusing only on flood-related alerts

---

### Test 3: Forecast Query
```bash
curl "http://localhost:8000/weather/search?query=sunny%20weather%20this%20weekend&source_type=forecast"
```

**Expected:** Summary of sunny forecast periods

---

### Test 4: Complex Question
```bash
curl "http://localhost:8000/weather/search?query=Should%20I%20cancel%20my%20outdoor%20plans%20in%20Chicago?"
```

**Expected:** LLM synthesizes weather conditions and gives advice based on context

---

### Test 5: No Results
```bash
curl "http://localhost:8000/weather/search?query=earthquakes"
```

**Expected:** "No relevant weather information found for your query."

---

## Key RAG Concepts (What You Learned)

### 1. Grounding
**Definition:** The LLM's response is "grounded" in retrieved facts

**Without Grounding:**
```
User: Is there flooding in Illinois?
LLM: I don't have real-time data. Check weather.gov...
```

**With Grounding (RAG):**
```
User: Is there flooding in Illinois?
LLM: Yes, there are active flood warnings in Chicago... [cites your data]
```

---

### 2. Context Window
**The augmented prompt** (system + context + user question) must fit in the LLM's context window.

**Claude 3.5 Sonnet:** 200K tokens (plenty of room for 5-10 weather documents)

If you retrieved 1000 documents, they wouldn't all fit! That's why we limit `top_k`.

---

### 3. Hallucination Prevention
**RAG reduces hallucinations** by giving the LLM specific facts to work with.

**System prompt enforces this:**
```
Answer based ONLY on the context provided - do not use outside knowledge
```

The LLM can still hallucinate, but it's much less likely when grounded in real data.

---

### 4. Retrieval Quality = Answer Quality
**The vector search is critical!**

If retrieval returns irrelevant documents → LLM generates a bad answer

**Example:**
- Query: "Is there flooding?"
- Retrieval returns: Documents about sunny weather
- LLM: "No flooding found in the context."

This is why **embedding quality** and **chunking strategy** matter!

---

## Debugging RAG

### Problem: "RAG endpoint not available"
**Cause:** `ANTHROPIC_API_KEY` not set

**Fix:**
```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
python app.py
```

---

### Problem: LLM says "I don't have enough context"
**Cause:** Vector search returned irrelevant documents

**Debug:**
1. Test the vector search directly (POST /weather/search)
2. Check if the similarity scores are high (>0.7 is good)
3. If scores are low, your query might be too different from document text
4. Try rephrasing the query

---

### Problem: LLM response is too generic
**Cause:** Not enough specific details in retrieved documents

**Fix:**
1. Increase `top_k` (retrieve more documents)
2. Check if your weather documents have detailed text
3. Improve chunking (smaller chunks = more focused context)

---

### Problem: Slow response time
**Cause:** LLM API call takes time (typically 1-3 seconds)

**Optimization ideas:**
1. Use streaming (not implemented here)
2. Cache common queries
3. Reduce `max_tokens` in LLM call
4. Use a faster model (e.g., Claude Haiku instead of Sonnet)

---

## Advanced RAG Patterns (Beyond This Project)

### 1. Iterative Retrieval
If the first answer is weak, retrieve more documents and try again.

```python
# Pseudo-code
answer = generate(retrieve(query, top_k=5))
if confidence_score < threshold:
    answer = generate(retrieve(query, top_k=10))
```

---

### 2. Query Expansion
Rephrase the query multiple ways, retrieve from each, merge results.

```python
queries = [
    "flooding in Illinois",
    "flood warnings Illinois",
    "Illinois river levels high"
]
all_results = [retrieve(q) for q in queries]
merged = dedupe(all_results)
answer = generate(merged)
```

---

### 3. Reranking
Use a second model to rerank retrieved documents by relevance.

```python
results = retrieve(query, top_k=20)
reranked = reranker.rank(query, results)
top5 = reranked[:5]
answer = generate(top5)
```

Popular rerankers: Cohere Rerank, Cross-Encoder models

---

### 4. Self-Correction
Let the LLM check its own answer and retrieve more if uncertain.

```python
answer = generate(retrieve(query))
confidence = llm.evaluate_confidence(answer)
if confidence < threshold:
    refined_query = llm.generate_refined_query(query, answer)
    more_docs = retrieve(refined_query)
    answer = generate(more_docs)
```

---

### 5. Hybrid Search
Combine vector search (semantic) with keyword search (exact match).

```sql
-- Vector search
SELECT * FROM docs ORDER BY embedding <=> query LIMIT 10

UNION

-- Keyword search
SELECT * FROM docs WHERE text ILIKE '%flood warning%' LIMIT 10
```

Best of both worlds: semantic similarity + exact keyword matches

---

## Cost Considerations

### Anthropic Claude Pricing (as of 2026)
- **Claude 3.5 Sonnet**: ~$3 per million input tokens, ~$15 per million output tokens
- **Claude Haiku**: ~$0.25 per million input tokens, ~$1.25 per million output tokens

### Typical RAG Request Cost:
- **Input tokens**: ~1000 (system prompt + 5 documents + query)
- **Output tokens**: ~200 (summary)
- **Cost per request**: ~$0.006 (less than 1 cent)

**For 1000 queries/day:**
- Daily: $6
- Monthly: ~$180

**Optimization tip:** Use Claude Haiku for simple queries, Sonnet for complex ones.

---

## Comparison: RAG vs Fine-Tuning

| Aspect | RAG | Fine-Tuning |
|--------|-----|-------------|
| **Setup Cost** | Low (just API key) | High (train model) |
| **Update Frequency** | Real-time (add new docs) | Rare (retrain) |
| **Use Case** | Dynamic data (weather, news) | Task-specific behavior |
| **Latency** | Higher (retrieval + LLM) | Lower (just LLM) |
| **Explainability** | High (cite sources) | Low (black box) |

**For weather data:** RAG is the clear winner (data changes constantly)

---

## Success Criteria

✅ You should be able to:

1. Ask a natural language question via GET /weather/search?query=...
2. Get a human-readable answer (not raw chunks)
3. See the sources that were used to generate the answer
4. Compare RAG results vs non-RAG results

---

## Example Queries to Try

### Severe Weather
```
Is there dangerous weather right now?
Should I worry about tornadoes?
Any flood warnings active?
```

### Planning
```
Is it safe to drive today?
Should I cancel outdoor plans?
Do I need an umbrella?
```

### Specific Locations
```
What's the weather like in Chicago?
Any alerts for Austin, Texas?
Is Seattle getting rain?
```

### Comparisons
```
Which city has the worst weather?
Where is it sunny?
Compare weather in Chicago vs Austin
```

---

## Congratulations! 🎉

You've just built a **production-ready RAG system**!

You now understand:
- ✅ What RAG is (Retrieval + Augmentation + Generation)
- ✅ Why RAG is useful (grounding LLMs in your data)
- ✅ How to implement RAG (vector search + prompt engineering + LLM)
- ✅ When to use RAG vs alternatives (fine-tuning, pure LLM)
- ✅ How to debug RAG systems (retrieval quality, prompt design)

This pattern applies to **any domain**:
- Customer support (RAG over tickets)
- Documentation search (RAG over docs)
- Legal (RAG over contracts)
- Medical (RAG over patient records)

You've earned the extra credit! 🏆
