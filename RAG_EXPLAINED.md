# RAG (Retrieval Augmented Generation) - Explained

## What is RAG?

**RAG = Retrieval + Generation**

Instead of asking an LLM to answer from its training data (which might be outdated or hallucinate), you:

1. **Retrieve** relevant documents from your own database
2. **Augment** the LLM prompt with those documents as context
3. **Generate** an answer based on the retrieved facts

---

## Why RAG?

### Without RAG (Pure LLM):
```
User: "Is there a flood warning in Chicago?"
LLM: "I don't have real-time weather data. My training cutoff was 2025..."
```

❌ LLM doesn't know current information

### With RAG (Retrieval + LLM):
```
User: "Is there a flood warning in Chicago?"

Step 1 - RETRIEVE:
  → Vector search for "flood warning Chicago"
  → Find: "Flash Flood Warning for Chicago, IL. Heavy rainfall expected..."

Step 2 - AUGMENT:
  → Pass retrieved documents to LLM as context
  
Step 3 - GENERATE:
  LLM: "Yes, there is currently a Flash Flood Warning for Chicago. 
       Heavy rainfall is expected through tonight. Avoid driving 
       through flooded areas..."
```

✅ LLM answers with **your data**, not its training data

---

## RAG Flow in Our Weather App

```
┌─────────────────────────────────────────────────────────────┐
│                    USER QUERY                                │
│           "Is there flooding in Illinois?"                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: EMBED THE QUERY                                     │
│  ────────────────────────────────────                        │
│  sentence-transformers/all-MiniLM-L6-v2                      │
│  "Is there flooding..." → [0.12, -0.45, 0.78, ...]          │
│                           (384-dim vector)                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: VECTOR SEARCH (Retrieval)                          │
│  ───────────────────────────────────                         │
│  SELECT chunk_text, location, headline                       │
│  FROM weather_embeddings                                     │
│  ORDER BY embedding <=> query_embedding                      │
│  LIMIT 5                                                     │
│                                                              │
│  Results:                                                    │
│  1. "Flash Flood Warning for Chicago..." (similarity: 0.89) │
│  2. "River levels rising in Springfield..." (sim: 0.84)     │
│  3. "Flood Watch continues for..." (sim: 0.81)              │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 3: AUGMENT PROMPT                                      │
│  ────────────────────                                        │
│  System: You are a weather assistant.                        │
│                                                              │
│  Context:                                                    │
│  - [Doc 1] Flash Flood Warning for Chicago...               │
│  - [Doc 2] River levels rising in Springfield...            │
│  - [Doc 3] Flood Watch continues for...                     │
│                                                              │
│  User Question: Is there flooding in Illinois?              │
│                                                              │
│  Instructions: Answer based ONLY on the context above.      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 4: GENERATE (LLM)                                      │
│  ────────────────────────                                    │
│  Claude/GPT processes the augmented prompt                   │
│                                                              │
│  Output:                                                     │
│  "Yes, there are active flood warnings in Illinois:         │
│   - Chicago has a Flash Flood Warning due to heavy rain     │
│   - Springfield is experiencing rising river levels         │
│   - A Flood Watch is in effect for multiple counties        │
│   Stay informed and avoid flooded roads."                   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  RESPONSE TO USER                                            │
│  {                                                           │
│    "query": "Is there flooding in Illinois?",               │
│    "summary": "Yes, there are active flood warnings...",    │
│    "sources": [                                              │
│      {"location": "Chicago", "headline": "Flash Flood..."}  │
│    ]                                                         │
│  }                                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Insight: Grounding

**The LLM never saw your weather data during training!**

But by putting it in the prompt (augmentation), the LLM can:
- Reference specific alerts
- Cite locations and timestamps
- Answer with **your real-time data**

This is called **grounding** - the LLM's response is grounded in retrieved facts.

---

## RAG vs Fine-Tuning

| Approach | When to Use | Cost | Update Frequency |
|----------|-------------|------|------------------|
| **RAG** | Your data changes frequently (weather, news, logs) | Low | Real-time (just add new docs) |
| **Fine-Tuning** | Task-specific behavior (tone, format, domain expertise) | High | Rare (retrain entire model) |

For weather data that updates hourly, **RAG is perfect**.

---

## RAG in the Weather App

### Before RAG (what we have now):
```bash
POST /weather/search
{
  "query": "flooding",
  "top_k": 5
}

Response:
{
  "results": [
    {"chunk_text": "Flash Flood Warning...", "similarity": 0.89},
    {"chunk_text": "River levels rising...", "similarity": 0.84}
  ]
}
```

User sees raw chunks → has to read and interpret themselves

### After RAG (what we're adding):
```bash
GET /weather/search?query=flooding

Response:
{
  "query": "flooding",
  "summary": "Yes, there are active flood warnings in Chicago and Springfield...",
  "sources": [...]
}
```

User gets a **natural language answer** → easier to understand

---

## Implementation Steps

1. **Keep the existing vector search** (already done ✅)
2. **Add LLM client** (OpenAI, Anthropic, or Databricks Foundation Models)
3. **Build augmented prompt** (system + context + user query)
4. **Call LLM** with the augmented prompt
5. **Return summary + sources**

---

## Why This is Called "Basic RAG"

**Basic RAG** (what we're building):
- One-shot retrieval → generate
- No iteration, no self-correction

**Advanced RAG** (beyond this bootcamp):
- **Iterative retrieval**: If first answer is weak, retrieve more docs
- **Reranking**: Use a second model to rerank retrieved docs by relevance
- **Query expansion**: Rephrase query multiple ways, retrieve from each
- **Self-correction**: LLM checks its own answer, retrieves more if uncertain

Basic RAG is enough for 80% of use cases!

---

## Next Steps

1. Add LLM client (we'll use Anthropic Claude API)
2. Update `app.py` with `GET /weather/search` endpoint
3. Build the prompt template
4. Test with real queries

Let's code it! 🚀
