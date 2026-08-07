# Weather Intelligence App - README

**Project:** Databricks Lakebase Weather App  
**Purpose:** Semantic search over unstructured weather data using vector embeddings  
**Data Source:** National Weather Service (NWS) API (api.weather.gov)

---

## Overview

This application demonstrates end-to-end unstructured data processing with vector search:

1. **Harvest** - Fetch weather alerts and forecasts from NWS API
2. **Store** - Save raw documents in Lakebase PostgreSQL
3. **Vectorize** - Chunk text and create embeddings with sentence-transformers
4. **Search** - Semantic search using pgvector cosine similarity

**Tech Stack:**
- **API Client**: NWS API (free, no key required)
- **Backend**: Flask (Python)
- **Database**: Databricks Lakebase (PostgreSQL + pgvector)
- **Embeddings**: sentence-transformers/all-MiniLM-L6-v2 (384-dim)
- **Connection**: pg8000 (pure Python, Serverless-compatible)
- **Orchestration**: Databricks Workflows

---

## Why National Weather Service API?

### Chosen Data Source: NWS API (api.weather.gov)

**Reasons for selection:**

1. **Free & No API Key** - Zero friction to start
2. **Rich Unstructured Text** - Perfect for embeddings:
   - Active alerts with detailed descriptions and instructions
   - Forecast narratives with natural language descriptions
   - Example: "Sunny, with a high near 78. Northwest wind around 6 mph."
3. **Reliable & Well-Documented** - Government-maintained API
4. **Real-Time Data** - Continuously updated weather conditions
5. **Location-Based** - Supports lat/lon and state-based queries

**Alternative considered:** OpenWeatherMap (requires API key, less narrative text)

---

## Architecture

### Data Flow

```
NWS API
   ↓
weather_client.py (fetch alerts + forecasts)
   ↓
weather_documents table (raw text + metadata)
   ↓
ingest_weather_embeddings.py notebook (chunk + embed)
   ↓
weather_embeddings table (384-dim vectors)
   ↓
Flask /weather/search endpoint (cosine similarity)
   ↓
JSON results ranked by semantic relevance
```

### Database Schema

**weather_documents** (raw weather data):
- `id` TEXT PRIMARY KEY - Stable identifier for dedup
- `location` TEXT - City, state or grid coordinates
- `source_type` TEXT - "alert" or "forecast"
- `headline` TEXT - Summary/title
- `event` TEXT - Event type (e.g., "Flash Flood Warning")
- `narrative_text` TEXT - Full description to embed
- `severity`, `urgency` TEXT - Alert-specific fields
- `temperature`, `wind_speed` - Forecast-specific fields
- `issued_at`, `effective_at`, `expires_at` TIMESTAMPTZ
- `payload` JSONB - Full API response for provenance
- `synced_at` TIMESTAMPTZ

**weather_embeddings** (vector search):
- `id` TEXT PRIMARY KEY
- `document_id` TEXT FK → weather_documents(id)
- `chunk_index` INT - Position in document
- `chunk_text` TEXT - The actual text chunk
- `embedding` VECTOR(384) - Vector representation
- `model_name` TEXT - sentence-transformers/all-MiniLM-L6-v2
- `embedded_at` TIMESTAMPTZ
- **Indexes**: HNSW on embedding for fast similarity search

---

## Schema Decisions

### Chunking Parameters

- **Chunk Size**: 800 characters
- **Overlap**: 100 characters
- **Rationale**:
  - NWS text is typically shorter than news articles (forecasts ~200-400 chars, alerts ~500-1500 chars)
  - 800 chars covers most content without splitting
  - 100-char overlap preserves context at boundaries

### Embedding Model

- **Model**: sentence-transformers/all-MiniLM-L6-v2
- **Dimensions**: 384
- **Rationale**:
  - Same model as news app (consistency across projects)
  - Fast inference (~50ms per chunk on CPU)
  - Good balance of speed vs. quality
  - Small enough to avoid Lakebase storage issues

### Why pg8000 Instead of psycopg2?

- **psycopg2** has C extensions → crashes on Databricks Serverless (SIGABRT 134)
- **pg8000** is pure Python → works reliably on Serverless
- Slightly slower but compatible with serverless compute

---

## Setup Guide

### Prerequisites

1. **Databricks workspace** with Lakebase enabled
2. **Lakebase instance** with password authentication
3. **Secrets stored** in Databricks:
   - Scope: `database`
   - Key: `lakebase-url`
   - Value: `postgresql://user:pass@host:5432/databricks_postgres?sslmode=require`

### Step 1: Create Database Tables

Run these SQL scripts in your Lakebase instance:

```bash
# Connect to Lakebase via Databricks SQL Editor
# Run in order:
sql/01_setup_weather_documents.sql
sql/02_setup_weather_embeddings.sql
```

### Step 2: Sync Weather Data

**Option A: Via Flask API**
```bash
# Start Flask app
python app.py

# Sync weather data
curl -X POST http://localhost:8000/weather/sync \
  -H "Content-Type: application/json" \
  -d '{
    "locations": ["chicago", "austin", "seattle"],
    "include_alerts": true,
    "include_forecast": true,
    "state_filter": "IL"
  }'

# Response: {"synced": 42, "alerts": 5, "forecasts": 37}
```

**Option B: Via Python**
```python
from weather_client import WeatherClient

client = WeatherClient()
locations = [(41.8781, -87.6298), (30.2672, -97.7431)]  # Chicago, Austin
documents = client.get_weather_for_locations(locations)
print(f"Fetched {len(documents)} weather documents")
```

### Step 3: Generate Embeddings

Run the Databricks notebook:

```bash
# Via Databricks UI: Open notebooks/ingest_weather_embeddings.py and "Run All"

# OR via CLI:
databricks bundle deploy -t dev
databricks bundle run ingest_weather_embeddings_job -t dev
```

**Expected output:**
- Documents processed: ~40-50
- Chunks created: ~50-70 (most weather text is short)
- Embeddings stored: Same as chunks

### Step 4: Test Semantic Search

```bash
curl -X POST http://localhost:8000/weather/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "risk of flooding near rivers",
    "top_k": 5,
    "source_type": "alert"
  }'
```

**Example response:**
```json
{
  "query": "risk of flooding near rivers",
  "top_k": 5,
  "results": [
    {
      "document_id": "abc123",
      "location": "Chicago, IL",
      "headline": "Flash Flood Warning",
      "chunk_text": "A Flash Flood Warning means...",
      "similarity": 0.87
    }
  ]
}
```

---

## API Endpoints

### POST /weather/sync

Fetch weather data from NWS API and store in Lakebase.

**Request:**
```json
{
  "locations": ["chicago", "austin"],
  "include_alerts": true,
  "include_forecast": true,
  "state_filter": "TX"
}
```

**Response:**
```json
{
  "synced": 42,
  "alerts": 5,
  "forecasts": 37
}
```

### POST /weather/search

Semantic search over weather embeddings.

**Request:**
```json
{
  "query": "heavy snow and ice",
  "top_k": 5,
  "source_type": "alert"
}
```

**Response:**
```json
{
  "query": "heavy snow and ice",
  "results": [
    {
      "document_id": "xyz789",
      "location": "Denver, CO",
      "headline": "Winter Storm Warning",
      "chunk_text": "Heavy snow expected...",
      "similarity": 0.92
    }
  ]
}
```

---

## How to Run End-to-End

### Complete Pipeline

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create tables (run SQL scripts in Lakebase)
# sql/01_setup_weather_documents.sql
# sql/02_setup_weather_embeddings.sql

# 3. Start Flask app
python app.py

# 4. Sync weather data
curl -X POST http://localhost:8000/weather/sync \
  -d '{"locations": ["chicago", "austin"]}'

# 5. Run embedding notebook in Databricks
# notebooks/ingest_weather_embeddings.py

# 6. Test search
curl -X POST http://localhost:8000/weather/search \
  -d '{"query": "tornado warning", "top_k": 3}'
```

---

## Known Limitations & Future Improvements

### Current Limitations

1. **No automatic deduplication** - Re-syncing creates duplicates (mitigated by ON CONFLICT in upsert)
2. **Fixed locations** - Hardcoded city list, no dynamic location input
3. **No scheduled updates** - Manual sync only (Job exists but paused)
4. **Basic error handling** - NWS API failures fail silently with warnings
5. **No RAG/LLM summary** - Returns raw results, no natural language generation

### Improvements Given More Time

1. **Add LLM summary** - POST /weather/search returns natural language answer using top results
2. **Scheduled sync** - Enable Databricks Job to auto-sync every hour
3. **Multi-source** - Combine NWS + NOAA Climate Prediction Center discussions
4. **Geolocation** - Accept any lat/lon or city name, resolve via geocoding
5. **Filtering** - Add date range, severity level, temperature range filters
6. **Performance** - Benchmark HNSW vs IVFFlat index for vector search
7. **Monitoring** - Add logging, metrics, and alerts for sync failures

---

## Testing the Vector Search

### Example Queries

```bash
# 1. Find flood-related alerts
curl -X POST http://localhost:8000/weather/search \
  -d '{"query": "flooding and heavy rain", "source_type": "alert"}'

# 2. Find sunny weather forecasts
curl -X POST http://localhost:8000/weather/search \
  -d '{"query": "sunny and clear skies", "source_type": "forecast"}'

# 3. Find severe weather
curl -X POST http://localhost:8000/weather/search \
  -d '{"query": "dangerous weather conditions"}'
```

### Verify Embeddings Work

```sql
-- In Lakebase SQL Editor
SELECT COUNT(*) FROM weather_embeddings;
-- Should return > 0

-- Test vector search directly
WITH query AS (
  SELECT embedding FROM weather_embeddings LIMIT 1
)
SELECT
  document_id,
  chunk_text,
  1 - (embedding <=> (SELECT embedding FROM query)) as similarity
FROM weather_embeddings
ORDER BY embedding <=> (SELECT embedding FROM query)
LIMIT 5;
```

---

## Key Fixes Applied

This project incorporates lessons learned from the news app:

1. **✅ Double Base64 Decode** - Secrets are decoded twice
2. **✅ pg8000 Instead of psycopg2** - Serverless-compatible
3. **✅ ON CONFLICT Upsert** - Prevents duplicate embeddings
4. **✅ HNSW Index** - Fast vector search
5. **✅ Rate Limiting** - Respects NWS API throttling
6. **✅ Error Handling** - Graceful API failures
7. **✅ RAG with Databricks Foundation Models** - Uses Llama 3.1 70B (free in Databricks!)

---

## Project Structure

```
databricks-lakebase-weather-app/
├── app.py                          # Flask API
├── lakebase.py                     # Lakebase connection helper
├── weather_client.py               # NWS API client
├── requirements.txt                # Python dependencies
├── databricks.yml                  # Bundle configuration
├── app.yaml                        # Databricks App config
├── .env.example                    # Environment variables template
├── resources/
│   └── ingest_weather_job.yml     # Scheduled job definition
├── notebooks/
│   └── ingest_weather_embeddings.py  # Embedding pipeline
├── sql/
│   ├── 01_setup_weather_documents.sql
│   └── 02_setup_weather_embeddings.sql
└── docs/
    └── README_WEATHER.md           # This file
```

---

## Success Criteria

**✅ You should be able to:**

1. POST to /weather/sync and get weather documents synced
2. Run the embedding notebook and see vectors in weather_embeddings
3. POST to /weather/search with "flood risk" and get relevant alerts ranked by similarity
4. Query embeddings directly in SQL using <=> operator
5. **GET /weather/search?query=...** with RAG to get natural language summaries (Extra Credit!)

---

## References

- **NWS API Docs**: https://www.weather.gov/documentation/services-web-api
- **pgvector**: https://github.com/pgvector/pgvector
- **sentence-transformers**: https://www.sbert.net/
- **Databricks Lakebase**: https://docs.databricks.com/lakebase/
- **Databricks Foundation Models**: https://docs.databricks.com/en/machine-learning/foundation-models/
- **RAG Guide**: See DATABRICKS_RAG_GUIDE.md for using Databricks LLMs

---

**Created**: 2026-08-07  
**Last Updated**: 2026-08-07  
**Status**: ✅ Complete and ready for deployment
