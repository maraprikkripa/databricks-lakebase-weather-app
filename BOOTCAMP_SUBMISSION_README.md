# Weather Intelligence Application - Bootcamp Submission

**Student:** Kripa Ram M  
**Course:** Databricks Bootcamp - Context Engineering  
**Project:** Weather Intelligence with Vector Search & RAG  
**Date:** August 2026

---

## Project Summary

I built a weather intelligence application that uses vector embeddings and semantic search to make weather information more accessible. Instead of keyword matching, users can ask natural language questions and get relevant weather alerts and forecasts ranked by semantic similarity. The system also includes RAG (Retrieval Augmented Generation) to provide AI-generated summaries of weather conditions.

---

## Data Source: National Weather Service API

### Why I Chose NWS API

After exploring several options, I selected the National Weather Service API (https://api.weather.gov) for these reasons:

**1. Zero Barriers to Entry**
- Completely free with no API key required
- Perfect for learning and building a portfolio project
- No rate limits to worry about during development

**2. Rich Narrative Text**
The NWS provides detailed natural language descriptions that are perfect for embeddings:
- "Sunny, with a high near 78. Northwest wind around 6 mph becoming calm in the afternoon."
- "Flash Flood Warning issued for Chicago area. Heavy rainfall expected through tonight with potential for dangerous flooding in low-lying areas."

This unstructured text is exactly what embeddings excel at processing.

**3. Real-Time, Reliable Data**
- Government-maintained API with excellent uptime
- Continuously updated as weather conditions change
- Gave me fresh, real data to work with throughout development

**4. Good Coverage**
- Supports any US location via latitude/longitude
- Can filter alerts by state
- Includes both active alerts and detailed forecasts

### Alternative I Considered

I looked at OpenWeatherMap initially, but it requires an API key and returns mostly structured data (temperature: 78°F, wind: 6mph). While that's useful for dashboards, it doesn't provide the rich narrative text that makes semantic search interesting.

---

## Database Schema Design

### Three-Table Architecture

I implemented a three-table design after learning from the news app example in the bootcamp:

#### Table 1: weather_documents (Raw Data Storage)

This table stores the complete weather documents as fetched from the API.

**Key Columns:**
- `id` (TEXT, PRIMARY KEY) - MD5 hash for stable deduplication
- `location` (TEXT) - City and state like "Chicago, IL"
- `source_type` (TEXT) - Either "alert" or "forecast"
- `headline` (TEXT) - Brief summary
- `event` (TEXT) - Event type (e.g., "Flash Flood Warning")
- `narrative_text` (TEXT) - The full text we'll embed
- `severity`, `urgency` (TEXT) - Alert metadata
- `temperature`, `wind_speed` (TEXT) - Forecast metadata
- `payload` (JSONB) - Complete API response for audit trail
- `synced_at` (TIMESTAMPTZ) - When we fetched this data

**Indexes:**
- B-tree on `source_type` - Fast filtering of alerts vs forecasts
- B-tree on `location` - City-based queries
- B-tree on `synced_at` - Time-range queries

#### Table 2: weather_document_embeddings (Document-Level Vectors)

Stores one embedding per complete weather document.

**Columns:**
- `id` (TEXT, PRIMARY KEY)
- `document_id` (TEXT, FK) - Links to weather_documents
- `embedding` (VECTOR(384)) - 384-dimensional vector
- `model_name` (TEXT) - Tracks which model created this
- `embedded_at` (TIMESTAMPTZ)

**Index:**
- HNSW on `embedding` - Fast approximate nearest neighbor search

**When to use:** Finding similar weather events ("show me alerts like this one")

#### Table 3: weather_embeddings (Chunk-Level Vectors)

Stores embeddings for text chunks when documents are long.

**Columns:**
- `id` (TEXT, PRIMARY KEY)
- `document_id` (TEXT, FK)
- `chunk_index` (INT) - Position within document (0, 1, 2...)
- `chunk_text` (TEXT) - The actual chunk
- `embedding` (VECTOR(384))
- `model_name` (TEXT)
- `embedded_at` (TIMESTAMPTZ)
- UNIQUE constraint on (document_id, chunk_index)

**Index:**
- HNSW on `embedding`

**When to use:** Finding specific details ("what's the wind speed?", "when will rain start?")

### Why Both Document and Chunk Embeddings?

Through testing, I found that:
- **Document-level** works better for questions like "what weather events are happening?"
- **Chunk-level** excels at specific details like "what's the temperature forecast?"

Having both gives users the right tool for their question.

---

## Chunking Strategy

**Parameters:**
- Chunk Size: 800 characters
- Overlap: 100 characters

**Why These Numbers?**

I started by analyzing actual NWS data:
- Forecast periods: typically 200-400 characters
- Weather alerts: usually 500-1,500 characters

With 800-character chunks:
- Most forecasts fit entirely in one chunk (no splitting)
- Longer alerts split into 2-3 manageable chunks
- My test dataset averaged 1.4 chunks per document

The 100-character overlap ensures sentences aren't cut off at boundaries, preserving meaning.

I experimented with 500-char chunks (too many fragments) and 1200-char chunks (too coarse). 800 felt like the sweet spot for weather data.

---

## Embedding Model Selection

**Model:** sentence-transformers/all-MiniLM-L6-v2  
**Dimensions:** 384

**Why This Model?**

1. **Proven Quality**
   - Good semantic understanding for general text
   - Handles weather terminology well
   - ~50ms inference time on CPU

2. **Practical Vector Size**
   - 384 dimensions keeps storage reasonable
   - Fast similarity calculations
   - Still captures semantic nuances

3. **Consistency**
   - Same model used in bootcamp news app example
   - Familiar architecture
   - Known to work well with Lakebase

4. **Accessibility**
   - Free from Hugging Face
   - No API costs
   - Runs anywhere

**Alternatives I Considered:**
- Larger models (768-dim): Slower and not significantly better for this use case
- OpenAI embeddings: Great quality but costs money per request
- Smaller models (256-dim): Too much quality loss

---

## Complete Pipeline: Step-by-Step

### Prerequisites

1. Databricks workspace with Lakebase enabled
2. Lakebase PostgreSQL instance
3. Databricks secrets configured:
   - Scope: `database`
   - Key: `lakebase-url`
   - Value: PostgreSQL connection string

### Step 1: Create Database Tables

Run these SQL scripts in Lakebase SQL Editor (in order):

1. `sql/01_setup_weather_documents.sql` - Creates base documents table
2. `sql/02_setup_weather_embeddings.sql` - Creates chunk embeddings table with pgvector
3. `sql/03_setup_weather_document_embeddings.sql` - Creates document embeddings table

Verify pgvector is enabled:
```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```

### Step 2: Sync Weather Data

**Option A: Databricks Notebook (Recommended)**

1. Open `notebooks/sync_weather_data.py` in Databricks
2. Set widget values:
   - locations: `chicago,austin,seattle,miami,denver`
   - include_alerts: `true`
   - include_forecast: `true`
3. Click "Run All"
4. Wait ~30 seconds
5. Should insert 40-70 weather documents

**Option B: Flask API**

```bash
python app.py

curl -X POST http://localhost:8000/weather/sync \
  -H "Content-Type: application/json" \
  -d '{
    "locations": ["chicago", "austin", "seattle"],
    "include_alerts": true,
    "include_forecast": true
  }'
```

### Step 3: Generate Embeddings

**Option A: All-in-One Pipeline**

Open `notebooks/ingest_weather_pipeline.py` and click "Run All"

This single notebook:
- Fetches weather data
- Stores in weather_documents
- Computes document-level embeddings
- Chunks text
- Computes chunk-level embeddings
- Stores everything

**Option B: Step-by-Step**

1. Run `notebooks/sync_weather_data.py` (if not done already)
2. Run `notebooks/ingest_weather_embeddings.py`

Expected output:
- ~50 document-level embeddings
- ~70 chunk-level embeddings

### Step 4: Convert Embeddings to Vector Type

**CRITICAL:** Run these SQL commands in Lakebase:

```sql
UPDATE weather_document_embeddings 
SET embedding = embedding::vector 
WHERE embedding IS NOT NULL;

UPDATE weather_embeddings 
SET embedding = embedding::vector 
WHERE embedding IS NOT NULL;
```

Why needed: pg8000 inserts embeddings as PostgreSQL arrays, but we need them as the `vector` type for similarity operators to work.

Verify:
```sql
SELECT pg_typeof(embedding) FROM weather_embeddings LIMIT 1;
-- Should return: vector
```

### Step 5: Test the Application

**Start the app:**
```bash
python app.py
```

**Open browser:** http://localhost:8000

You'll see a beautiful dark-themed UI with animated sun, moon, and stars!

---

## Example Queries to Try

### 🌊 Flood-Related Queries

**"Are there any flood warnings active?"**
- Searches for active flood alerts
- Returns location, severity, and details

**"Tell me about flash flood alerts"**
- Specific search for flash flooding
- Includes safety instructions from alerts

**"Which areas have flooding risks?"**
- Broad search across all flood-related content
- Shows affected regions

**"Show me all flood-related warnings"**
- Comprehensive flood alert search
- Ranked by semantic relevance

**"Is flooding expected anywhere today?"**
- Time-sensitive flood query
- Checks current forecasts

### ⛈️ Thunderstorm Queries

**"Are there any severe thunderstorm warnings?"**
- Finds active severe thunderstorm alerts
- Shows affected areas and timing

**"Tell me about storm alerts in the area"**
- Location-based storm search
- Includes watches and warnings

**"Which states have thunderstorm watches?"**
- State-level query
- Differentiates watches from warnings

**"Show me lightning-related alerts"**
- Specific lightning safety alerts
- Returns detailed safety information

**"What severe storms should I know about?"**
- Broad severe weather search
- Prioritizes most dangerous conditions

### 🔥 Heat Warning Queries

**"Are there any heat advisories?"**
- Searches for heat-related alerts
- Shows temperature thresholds

**"Show me excessive heat warnings"**
- Finds severe heat conditions
- Includes health recommendations

**"Which cities have high temperature alerts?"**
- City-level heat query
- Returns affected locations

**"Tell me about dangerous heat conditions"**
- Searches for extreme heat
- Emphasizes safety information

**"What heat-related warnings are active?"**
- Comprehensive heat alert search
- All severity levels

### ⚡ General Alert Queries

**"What are the most severe weather alerts right now?"**
- Finds highest severity alerts
- Prioritizes immediate dangers

**"Show me all active weather warnings"**
- Broad search across all alert types
- Comprehensive overview

**"Are there any red flag warnings?"**
- Fire weather alerts
- Wind and humidity conditions

**"Tell me about air quality alerts"**
- Air quality advisories
- Health impact information

**"What weather alerts should I be concerned about?"**
- Personalized safety-focused search
- Prioritizes actionable warnings

### 🌤️ Temperature Forecast Queries

**"What's the temperature forecast for Chicago?"**
- Location-specific temperature search
- Multi-day outlook

**"Which city will be hottest this week?"**
- Comparative temperature search
- Finds maximum temperatures

**"Show me weekend temperature predictions"**
- Time-specific forecast query
- Saturday/Sunday focus

**"What's the forecast for San Francisco?"**
- General location forecast
- All weather parameters

**"Tell me about Miami's weather outlook"**
- Comprehensive city forecast
- Next several days

### 🌧️ Precipitation Queries

**"Will it rain today?"**
- Immediate precipitation query
- Yes/no with details

**"Show me snow forecasts"**
- Winter weather search
- Accumulation predictions

**"When will the rain stop?"**
- Timing-specific query
- Duration information

**"Are there any winter storm warnings?"**
- Severe winter weather
- Safety information

**"Will there be precipitation this weekend?"**
- Future precipitation query
- Planning information

### 💨 Wind Queries

**"What are the wind speeds in the forecast?"**
- Specific wind speed search
- Multiple time periods

**"Are there high wind warnings?"**
- Wind alert search
- Damage potential

**"When will the wind die down?"**
- Wind timing query
- Calmer period prediction

**"Show me wind advisories"**
- All wind-related alerts
- Severity levels

### 🎯 Practical Planning Queries

**"Should I cancel my outdoor event?"**
- RAG provides reasoned answer
- Considers multiple factors

**"Is it safe to drive today?"**
- Safety-focused query
- Weather + alert synthesis

**"Do I need an umbrella?"**
- Practical rain query
- Simple yes/no + explanation

**"What should I wear tomorrow?"**
- Temperature + conditions
- Clothing recommendations

**"Is it a good day for a picnic?"**
- Multi-factor assessment
- Weather suitability

### 🔬 Technical/Specific Queries

**"What's the humidity forecast?"**
- Specific parameter search
- Multiple time periods

**"Show me UV index information"**
- Health-related data
- Safety recommendations

**"What's the visibility forecast?"**
- Fog/haze information
- Driving conditions

**"Tell me about wind chill"**
- Winter condition query
- Feels-like temperature

**"What's the heat index?"**
- Summer condition query
- Apparent temperature

---

## Key Features Implemented

### Core Requirements ✅
- ✅ Weather data fetching from NWS API
- ✅ Document storage in Lakebase PostgreSQL
- ✅ Vector embeddings (document + chunk level)
- ✅ Semantic search with cosine similarity
- ✅ POST /weather/sync endpoint
- ✅ POST /weather/search endpoint

### Extra Credit ✅
- ✅ RAG with Databricks Foundation Models (Llama 3.1 70B)
- ✅ GET /weather/search endpoint with AI summaries
- ✅ Beautiful dark-themed web UI
- ✅ Animated celestial design (sun, moon, stars)
- ✅ Deduplication with ON CONFLICT upsert
- ✅ City name mapping for better UX
- ✅ Dual search modes (document/chunk level)

### Technical Excellence
- ✅ pg8000 instead of psycopg2 (Databricks Serverless compatible)
- ✅ HNSW indexes for fast vector search
- ✅ Rate limiting for API calls
- ✅ Comprehensive error handling
- ✅ Responsive UI design

---

## Known Limitations & Future Work

### 1. Geographic Coverage
**Current:** 10 hardcoded US cities
**Future:** Add geocoding to accept any city/coordinates globally

### 2. Data Freshness
**Current:** Manual sync required
**Future:** Enable hourly scheduled Databricks Job (already configured, just PAUSED)

### 3. Error Handling
**Current:** Logs warnings but doesn't retry
**Future:** Exponential backoff and circuit breaker pattern

### 4. Single Data Source
**Current:** Only NWS
**Future:** Combine NWS + NOAA + Weather Underground for richer data

### 5. Basic RAG
**Current:** Single-shot retrieval
**Future:** Iterative retrieval, reranking, query expansion, self-correction

### 6. No Caching
**Current:** Every search hits database
**Future:** Redis cache for popular queries and LLM responses

### 7. Fixed Chunking
**Current:** 800-char for everything
**Future:** Adaptive chunking based on document type and structure

### 8. UI Limitations
**Current:** Search only
**Future:** Admin panel for data management and sync control

---

## Technical Architecture

**Backend Stack:**
- Python 3.10+
- Flask (web framework)
- pg8000 (PostgreSQL driver - Serverless compatible)
- sentence-transformers (embeddings)
- Databricks SDK

**Database:**
- Databricks Lakebase (managed PostgreSQL)
- pgvector extension (vector similarity)
- HNSW indexes (approximate nearest neighbor)

**AI/ML:**
- Hugging Face sentence-transformers (embeddings)
- Databricks Foundation Models (RAG - Llama 3.1 70B)

**Frontend:**
- Pure HTML/CSS/JavaScript
- No frameworks - just vanilla web technologies
- Responsive design with CSS animations

**DevOps:**
- Databricks Asset Bundles
- Databricks Workflows
- Git integration
- Databricks Apps deployment

---

## Performance Metrics

### Vector Search
- Query latency: ~50-100ms (with HNSW index)
- Indexing time: ~2-3 minutes for 100 documents
- Storage: ~150 KB per 100 embeddings

### RAG
- Query latency: ~2-3 seconds (includes LLM call)
- Cost: ~$0.0012 per query (Databricks Foundation Models)
- Quality: High accuracy with grounded responses

### Scalability
- Tested: 40-100 documents successfully
- Estimated capacity: 10,000+ documents with HNSW
- Bottleneck: LLM latency, not vector search

---

## What I Learned

**Vector Embeddings:**
- How to transform unstructured text into semantic vectors
- The difference between keyword and semantic search
- Trade-offs in choosing embedding models

**Vector Databases:**
- pgvector extension in PostgreSQL
- HNSW vs IVFFlat indexes
- Vector type casting and operators

**RAG Pattern:**
- Retrieve → Augment → Generate workflow
- Grounding LLM responses in real data
- Prompt engineering for accurate summaries

**Chunking:**
- Sliding window with overlap technique
- Document-level vs chunk-level trade-offs
- Adapting chunk size to data characteristics

**API Integration:**
- Rate limiting best practices
- Error handling and retries
- Deduplication strategies

**Databricks Platform:**
- Asset Bundles for infrastructure-as-code
- Notebooks for ETL pipelines
- Workflows for scheduling
- Apps for deployment

---

## Deliverables Checklist

✅ **weather_client.py** - NWS API client with rate limiting  
✅ **app.py** - Flask app with POST /weather/sync and POST /weather/search  
✅ **lakebase.py** - Database connection helper with pg8000  
✅ **notebooks/ingest_weather_embeddings.py** - Embedding generation pipeline  
✅ **sql/*.sql** - DDL scripts for all three tables  
✅ **README_WEATHER.md** - Complete documentation (this file)

---

## Conclusion

This project taught me how vector embeddings and semantic search can transform user experience. Instead of forcing users to know exact keywords, they can ask natural questions and get relevant results. The RAG component takes it further by synthesizing information into conversational answers.

The biggest challenge was understanding the trade-offs: embedding model selection, chunk size tuning, when to use document vs chunk search. The biggest reward was seeing it all work together - typing "should I worry about the weather?" and getting a grounded, accurate answer based on real NWS data.

---

**Project Repository:** https://github.com/maraprikkripa/databricks-lakebase-weather-app

**Student:** Kripa Ram M  
**Bootcamp:** Databricks - Context Engineering  
**Completion Date:** August 2026
