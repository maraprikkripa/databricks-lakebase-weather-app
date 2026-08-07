# Databricks Foundation Models RAG Guide

## Overview

The weather app now uses **Databricks Foundation Models** for RAG by default!

**Priority:**
1. **Databricks Foundation Models** (free/included in workspace) ✅ PREFERRED
2. **Anthropic Claude** (paid API, fallback)

---

## What Are Databricks Foundation Models?

Databricks provides pre-deployed LLM endpoints in your workspace:

- **Meta Llama 3.1 70B Instruct** (what we're using)
- **Mistral Large**
- **DBRX Instruct**

These are:
- ✅ **GPU-accelerated** (fast inference)
- ✅ **Pay-per-token** (or free in some Databricks tiers)
- ✅ **Already authenticated** (uses your workspace credentials)
- ✅ **No external API keys needed**

---

## How It Works

### Automatic Fallback Chain

```python
if DATABRICKS_AVAILABLE:
    try:
        llm_client = WorkspaceClient()
        llm_type = "databricks"
        # Uses: databricks-meta-llama-3-1-70b-instruct
    except:
        # Fall back to Anthropic
        if ANTHROPIC_API_KEY:
            llm_client = Anthropic(api_key=...)
            llm_type = "anthropic"
```

**What this means:**
- If you deploy in Databricks → Uses Foundation Models (no API key needed!)
- If you run locally → Falls back to Anthropic (if ANTHROPIC_API_KEY set)

---

## Models Available

### databricks-meta-llama-3-1-70b-instruct (Default)
- **Size:** 70 billion parameters
- **Quality:** Very good for factual Q&A
- **Speed:** ~2-3 seconds per response
- **Cost:** Pay-per-token (typically $0.001 per 1K tokens)

### Alternative Models (edit app.py to switch)

```python
# In call_llm() function, change this line:
name="databricks-meta-llama-3-1-70b-instruct"

# To one of these:
name="databricks-dbrx-instruct"           # Databricks' own model
name="databricks-mixtral-8x7b-instruct"   # Faster, smaller
```

---

## Testing the RAG Endpoint

### Test 1: Check Which LLM is Being Used

```bash
# Start the app
python app.py

# Check logs - you should see:
# "Databricks Foundation Models initialized for RAG"
```

### Test 2: Make a RAG Request

```bash
curl "http://localhost:8000/weather/search?query=Is%20there%20flooding?"
```

**Response will include:**
```json
{
  "query": "Is there flooding?",
  "summary": "Yes, there are active flood warnings...",
  "sources": [...],
  "llm_type": "databricks"  // ← Shows which LLM was used
}
```

---

## Deployment Scenarios

### Scenario 1: Deployed in Databricks (Production)

**What happens:**
- ✅ Databricks Foundation Models automatically available
- ✅ Uses workspace authentication
- ✅ No API keys needed
- ✅ Cost billed to your Databricks account

**Setup required:** NONE (it just works!)

---

### Scenario 2: Running Locally (Development)

**What happens:**
- ⚠️ Databricks Foundation Models NOT available (requires workspace context)
- 🔄 Falls back to Anthropic (if ANTHROPIC_API_KEY set)
- ❌ If no Anthropic key → RAG endpoint returns 503

**Setup required:**
```bash
# Option A: Use Anthropic for local testing
export ANTHROPIC_API_KEY=sk-ant-api03-...

# Option B: Test without RAG (just use POST /weather/search)
# The vector search endpoint works without any LLM
```

---

### Scenario 3: CI/CD Pipeline

**What happens:**
- ❌ Databricks Foundation Models NOT available (no workspace)
- 🔄 Falls back to Anthropic
- ⚠️ Tests requiring RAG need ANTHROPIC_API_KEY

**Recommendation:**
Skip RAG endpoint tests in CI, or set ANTHROPIC_API_KEY as a secret.

---

## Comparing Databricks vs Anthropic

| Aspect | Databricks | Anthropic |
|--------|------------|-----------|
| **Cost** | $0.001/1K tokens | $0.003/1K tokens |
| **Speed** | 2-3 seconds | 1-2 seconds |
| **Quality** | Very good | Excellent |
| **Setup** | None (in Databricks) | API key required |
| **Local Dev** | ❌ Not available | ✅ Works everywhere |
| **Production** | ✅ Recommended | ✅ Works (costs more) |

**For your bootcamp:** Use Databricks in production, Anthropic for local testing.

---

## Troubleshooting

### Error: "No LLM client available"

**Cause:** Neither Databricks nor Anthropic is available.

**Fix:**
1. Check logs to see why Databricks failed
2. Set `ANTHROPIC_API_KEY` as fallback

---

### Error: "Endpoint 'databricks-meta-llama-3-1-70b-instruct' not found"

**Cause:** Foundation Models endpoint name might be different in your workspace.

**Fix:**
```python
# List available endpoints in a Databricks notebook:
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
endpoints = w.serving_endpoints.list()

for ep in endpoints:
    if "llama" in ep.name.lower() or "instruct" in ep.name.lower():
        print(f"Found: {ep.name}")

# Use the correct name in app.py
```

---

### Error: "Authentication failed"

**Cause:** Databricks SDK can't find workspace credentials.

**Fix:**
```bash
# Make sure you're authenticated
databricks auth login --host https://dbc-401e4b5b-2949.cloud.databricks.com

# Or set environment variables:
export DATABRICKS_HOST=https://dbc-401e4b5b-2949.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...
```

---

### Slow Response Times

**Cause:** Cold start - first request wakes up the endpoint.

**Fix:**
- First request: ~5-10 seconds (cold start)
- Subsequent requests: ~2-3 seconds (warm)

This is normal for serverless endpoints!

---

## Cost Estimation

### Databricks Foundation Models

**Typical RAG request:**
- Input tokens: ~1000 (system prompt + 5 documents + query)
- Output tokens: ~200 (summary)
- Cost: ~$0.0012 per request

**For 1000 queries/day:**
- Daily: $1.20
- Monthly: ~$36

**60% cheaper than Anthropic!**

---

### Anthropic (Fallback)

**Typical RAG request:**
- Input tokens: ~1000
- Output tokens: ~200
- Cost: ~$0.006 per request

**For 1000 queries/day:**
- Daily: $6
- Monthly: ~$180

---

## Best Practices

### 1. Use Databricks in Production
```yaml
# In databricks.yml or app.yaml
# No need to set ANTHROPIC_API_KEY
# Databricks Foundation Models work automatically
```

### 2. Use Anthropic for Local Dev
```bash
# In .env (local development)
ANTHROPIC_API_KEY=sk-ant-api03-...
```

### 3. Monitor Token Usage
```python
# Add to app.py after LLM call:
logger.info(f"LLM tokens - input: {input_tokens}, output: {output_tokens}")
```

### 4. Set Temperature Low for Facts
```python
# For weather (factual data):
temperature=0.3  # More deterministic

# For creative tasks:
temperature=0.7  # More creative
```

---

## Advanced: Using Other Databricks Models

### Switch to DBRX (Databricks' own model)

```python
# In call_llm() function:
response = llm_client.serving_endpoints.query(
    name="databricks-dbrx-instruct",  # ← Changed
    messages=[...],
    max_tokens=500
)
```

### Switch to Mixtral (Faster, smaller)

```python
response = llm_client.serving_endpoints.query(
    name="databricks-mixtral-8x7b-instruct",  # ← Changed
    messages=[...],
    max_tokens=500
)
```

**Recommendation:** Start with Llama 3.1 70B (what we're using). It has the best balance of quality and speed.

---

## Example RAG Flow with Databricks

```
1. User Query: "Is there flooding?"
   ↓

2. Vector Search (Local)
   → Find 5 relevant weather documents
   ↓

3. Build Augmented Prompt
   System: You are a weather assistant...
   Context: [5 documents]
   User: Is there flooding?
   ↓

4. Call Databricks Foundation Model
   → POST to databricks-meta-llama-3-1-70b-instruct
   → Llama 3.1 processes prompt on GPU
   ↓

5. Generate Summary
   → "Yes, there are active flood warnings in Chicago..."
   ↓

6. Return JSON Response
   {
     "summary": "Yes, there are...",
     "llm_type": "databricks"
   }
```

---

## Checking Your Setup

### In a Databricks Notebook

```python
# Test if Foundation Models are available
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

# List available endpoints
endpoints = w.serving_endpoints.list()
print("Available Foundation Models:")
for ep in endpoints:
    print(f"  - {ep.name}")

# Test the Llama endpoint
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

response = w.serving_endpoints.query(
    name="databricks-meta-llama-3-1-70b-instruct",
    messages=[
        ChatMessage(role=ChatMessageRole.USER, content="Say hello!")
    ],
    max_tokens=50
)

print(response.choices[0].message.content)
# Expected: "Hello! How can I assist you today?"
```

---

## Summary

✅ **Default:** Databricks Foundation Models (Llama 3.1 70B)  
🔄 **Fallback:** Anthropic Claude (if ANTHROPIC_API_KEY set)  
💰 **Cost:** 60% cheaper than Anthropic  
🚀 **Setup:** Zero config in Databricks!  

**Your app is now production-ready with free/cheap LLM access!** 🎉

---

## Next Steps

1. Deploy the app to Databricks (it will auto-detect Foundation Models)
2. Test the RAG endpoint: `GET /weather/search?query=...`
3. Check the response for `"llm_type": "databricks"`
4. If testing locally, set `ANTHROPIC_API_KEY` as fallback

**You're ready to earn that extra credit!** 🏆
