"""
Databricks Weather App:
- Serves a Flask REST API
- Fetches weather data from NWS API via weather_client.py
- Stores data in Lakebase (Databricks-managed Postgres with pgvector)
- Provides semantic search over weather documents using vector embeddings

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import json
import logging
import os

from flask import Flask, jsonify, request, render_template
from sentence_transformers import SentenceTransformer

import lakebase
from weather_client import WeatherClient

# Try to import Databricks SDK for Foundation Models
try:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
    DATABRICKS_AVAILABLE = True
except ImportError:
    DATABRICKS_AVAILABLE = False
    logger.warning("databricks-sdk not available - Databricks Foundation Models disabled")

# Try to import Anthropic as fallback
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-app")

app = Flask(__name__)

# Table names
WEATHER_DOCUMENTS_TABLE = os.environ.get("WEATHER_DOCUMENTS_TABLE", "weather_documents")
WEATHER_DOCUMENT_EMBEDDINGS_TABLE = os.environ.get("WEATHER_DOCUMENT_EMBEDDINGS_TABLE", "weather_document_embeddings")
WEATHER_EMBEDDINGS_TABLE = os.environ.get("WEATHER_EMBEDDINGS_TABLE", "weather_embeddings")

# Load embedding model once at startup (not per-request)
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
logger.info("Embedding model loaded successfully")

# Initialize LLM clients for RAG (priority: Databricks > Anthropic)
llm_client = None
llm_type = None

# Try Databricks Foundation Models first (recommended for Databricks deployments)
if DATABRICKS_AVAILABLE:
    try:
        workspace_client = WorkspaceClient()
        # Test if we can access the workspace
        workspace_client.current_user.me()
        llm_client = workspace_client
        llm_type = "databricks"
        logger.info("Databricks Foundation Models initialized for RAG (using meta-llama-3.1-70b-instruct)")
    except Exception as e:
        logger.warning(f"Databricks Foundation Models not available: {e}")

# Fall back to Anthropic if Databricks not available
if not llm_client and ANTHROPIC_AVAILABLE:
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_api_key:
        llm_client = Anthropic(api_key=anthropic_api_key)
        llm_type = "anthropic"
        logger.info("Anthropic client initialized for RAG (fallback)")

if not llm_client:
    logger.warning("No LLM client available - RAG endpoint will not be available")

# Pre-defined locations (lat, lon) - can be expanded
DEFAULT_LOCATIONS = {
    "chicago": (41.8781, -87.6298),
    "austin": (30.2672, -97.7431),
    "seattle": (47.6062, -122.3321),
    "miami": (25.7617, -80.1918),
    "denver": (39.7392, -104.9903)
}


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Call the LLM (Databricks or Anthropic) with the given prompts.

    Args:
        system_prompt: System instructions
        user_prompt: User query with context

    Returns:
        str: Generated response text

    Raises:
        Exception: If LLM call fails
    """
    if not llm_client:
        raise Exception("No LLM client available")

    if llm_type == "databricks":
        # Databricks Foundation Models API
        # Available models: meta-llama-3.1-70b-instruct, databricks-meta-llama-3.1-70b-instruct
        try:
            response = llm_client.serving_endpoints.query(
                name="databricks-meta-llama-3-1-70b-instruct",  # Foundation Model endpoint
                messages=[
                    ChatMessage(role=ChatMessageRole.SYSTEM, content=system_prompt),
                    ChatMessage(role=ChatMessageRole.USER, content=user_prompt)
                ],
                max_tokens=500,
                temperature=0.3  # Lower temp for factual weather responses
            )

            # Extract text from response
            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content
            else:
                raise Exception("Empty response from Databricks Foundation Model")

        except Exception as e:
            logger.exception("Databricks Foundation Model call failed")
            raise Exception(f"Databricks LLM error: {str(e)}")

    elif llm_type == "anthropic":
        # Anthropic Claude API
        try:
            message = llm_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=500,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            return message.content[0].text

        except Exception as e:
            logger.exception("Anthropic API call failed")
            raise Exception(f"Anthropic LLM error: {str(e)}")

    else:
        raise Exception(f"Unknown LLM type: {llm_type}")


def ensure_weather_documents_table():
    """Create the weather_documents table if it doesn't exist."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {WEATHER_DOCUMENTS_TABLE} (
            id TEXT PRIMARY KEY,
            location TEXT NOT NULL,
            source_type TEXT NOT NULL,
            headline TEXT NOT NULL,
            event TEXT,
            narrative_text TEXT NOT NULL,
            severity TEXT,
            urgency TEXT,
            temperature NUMERIC,
            temperature_unit TEXT,
            wind_speed TEXT,
            wind_direction TEXT,
            issued_at TIMESTAMPTZ,
            effective_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            payload JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Create index for source_type and location lookups
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_source_type "
        f"ON {WEATHER_DOCUMENTS_TABLE} (source_type)"
    )
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_location "
        f"ON {WEATHER_DOCUMENTS_TABLE} (location)"
    )


@app.route("/healthz")
def healthz():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "weather-app"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not HTML)."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Serve the beautiful dark-themed UI."""
    return render_template("index.html")


@app.route("/api")
def api_info():
    """API info page."""
    return jsonify({
        "name": "Databricks Weather App",
        "version": "1.0",
        "endpoints": {
            "health": "GET /healthz",
            "sync": "POST /weather/sync",
            "search": "POST /weather/search (vector search only)",
            "search_rag": "GET /weather/search?query=... (RAG with LLM summary)"
        }
    })


@app.route("/weather/sync", methods=["POST"])
def sync_weather():
    """
    Fetch weather data from NWS API and store in Lakebase.

    Body (optional):
    {
        "locations": ["chicago", "austin"],  // or ["41.8781,-87.6298", "30.2672,-97.7431"]
        "include_alerts": true,
        "include_forecast": true,
        "state_filter": "IL",  // for alerts only
        "limit": 50
    }

    Returns:
    {
        "synced": 42,
        "alerts": 5,
        "forecasts": 37
    }
    """
    ensure_weather_documents_table()

    # Parse request body
    body = request.json if request.is_json else {}
    location_names = body.get("locations", list(DEFAULT_LOCATIONS.keys()))
    include_alerts = body.get("include_alerts", True)
    include_forecast = body.get("include_forecast", True)
    state_filter = body.get("state_filter")

    # Resolve location names to (lat, lon) tuples
    locations = []
    for loc in location_names:
        if "," in str(loc):
            # Already lat,lon format
            try:
                lat, lon = map(float, str(loc).split(","))
                locations.append((lat, lon))
            except ValueError:
                logger.warning(f"Invalid lat,lon format: {loc}")
        elif loc.lower() in DEFAULT_LOCATIONS:
            # Known city name
            locations.append(DEFAULT_LOCATIONS[loc.lower()])
        else:
            logger.warning(f"Unknown location: {loc}")

    if not locations and not include_alerts:
        return jsonify({"error": "No valid locations provided and alerts disabled"}), 400

    # Fetch weather data
    client = WeatherClient()
    documents = client.get_weather_for_locations(
        locations=locations,
        include_alerts=include_alerts,
        include_forecast=include_forecast,
        state_filter=state_filter
    )

    if not documents:
        return jsonify({"synced": 0, "message": "No weather data available"})

    # Upsert into Lakebase
    alert_count = 0
    forecast_count = 0
    synced_count = 0

    with lakebase.get_connection() as conn:
        cursor = conn.cursor()
        for doc in documents:
            cursor.execute(
                f"""
                INSERT INTO {WEATHER_DOCUMENTS_TABLE} (
                    id, location, source_type, headline, event, narrative_text,
                    severity, urgency, temperature, temperature_unit,
                    wind_speed, wind_direction, issued_at, effective_at,
                    expires_at, payload, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE
                    SET location = EXCLUDED.location,
                        headline = EXCLUDED.headline,
                        narrative_text = EXCLUDED.narrative_text,
                        synced_at = EXCLUDED.synced_at
                """,
                [
                    doc["id"],
                    doc["location"],
                    doc["source_type"],
                    doc["headline"],
                    doc.get("event"),
                    doc["narrative_text"],
                    doc.get("severity"),
                    doc.get("urgency"),
                    doc.get("temperature"),
                    doc.get("temperature_unit"),
                    doc.get("wind_speed"),
                    doc.get("wind_direction"),
                    doc.get("issued_at"),
                    doc.get("effective_at"),
                    doc.get("expires_at"),
                    json.dumps(doc["payload"])
                ]
            )
            synced_count += 1
            if doc["source_type"] == "alert":
                alert_count += 1
            else:
                forecast_count += 1

        conn.commit()

    logger.info(f"Synced {synced_count} weather documents ({alert_count} alerts, {forecast_count} forecasts)")

    return jsonify({
        "synced": synced_count,
        "alerts": alert_count,
        "forecasts": forecast_count
    })


@app.route("/weather/search", methods=["POST"])
def search_weather():
    """
    Semantic search over weather embeddings using vector similarity.

    Body:
    {
        "query": "risk of flooding near rivers",
        "top_k": 5,
        "source_type": "alert",  // optional filter: "alert" or "forecast"
        "search_level": "chunk"   // optional: "document" or "chunk" (default: "chunk")
    }

    Returns:
    {
        "query": "risk of flooding near rivers",
        "search_level": "chunk",
        "results": [
            {
                "document_id": "abc123",
                "location": "Chicago, IL",
                "headline": "Flash Flood Warning",
                "chunk_text": "...",  // only for chunk-level search
                "similarity": 0.87
            },
            ...
        ]
    }
    """
    # Parse request
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON"}), 400

    query_text = request.json.get("query")
    if not query_text:
        return jsonify({"error": "Missing 'query' field"}), 400

    top_k = request.json.get("top_k", 5)
    top_k = max(1, min(top_k, 20))  # Clamp to [1, 20]

    source_type_filter = request.json.get("source_type")  # optional: "alert" or "forecast"
    search_level = request.json.get("search_level", "chunk")  # "document" or "chunk"

    if search_level not in ("document", "chunk"):
        return jsonify({"error": "search_level must be 'document' or 'chunk'"}), 400

    # Determine which embeddings table to use
    embeddings_table = WEATHER_DOCUMENT_EMBEDDINGS_TABLE if search_level == "document" else WEATHER_EMBEDDINGS_TABLE

    # Check if embeddings table exists and has data
    try:
        count_result = lakebase.run_query(
            f"SELECT COUNT(*) as count FROM {embeddings_table}"
        )
        if count_result[0]["count"] == 0:
            return jsonify({
                "query": query_text,
                "results": [],
                "message": f"No {search_level}-level embeddings found. Run /weather/sync first, then run the embedding notebook."
            })
    except Exception as e:
        return jsonify({
            "error": f"Embeddings table '{embeddings_table}' not found or not accessible: {str(e)}",
            "message": f"Run the SQL DDL script to create {embeddings_table} table"
        }), 500

    # Embed the query
    query_embedding = embedding_model.encode(query_text).tolist()

    # Build the search query based on search_level
    if search_level == "document":
        # Document-level search (entire documents)
        search_sql = f"""
            SELECT
                d.id as document_id,
                d.location,
                d.source_type,
                d.headline,
                d.event,
                d.narrative_text,
                1 - (e.embedding <=> %s::vector) as similarity
            FROM {WEATHER_DOCUMENT_EMBEDDINGS_TABLE} e
            JOIN {WEATHER_DOCUMENTS_TABLE} d ON d.id = e.document_id
        """
    else:
        # Chunk-level search (text chunks)
        search_sql = f"""
            SELECT
                d.id as document_id,
                d.location,
                d.source_type,
                d.headline,
                d.event,
                e.chunk_text,
                e.chunk_index,
                1 - (e.embedding <=> %s::vector) as similarity
            FROM {WEATHER_EMBEDDINGS_TABLE} e
            JOIN {WEATHER_DOCUMENTS_TABLE} d ON d.id = e.document_id
        """

    params = [json.dumps(query_embedding)]

    # Add source_type filter if requested
    if source_type_filter in ("alert", "forecast"):
        search_sql += " WHERE d.source_type = %s"
        params.append(source_type_filter)

    search_sql += f"""
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    params.append(json.dumps(query_embedding))
    params.append(top_k)

    # Execute search
    try:
        results = lakebase.run_query(search_sql, params)
    except Exception as e:
        logger.exception("Vector search failed")
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

    # Format results
    formatted_results = []
    for row in results:
        result = {
            "document_id": row["document_id"],
            "location": row["location"],
            "source_type": row["source_type"],
            "headline": row["headline"],
            "event": row.get("event"),
            "similarity": float(row["similarity"])
        }

        # Add chunk-specific or document-specific fields
        if search_level == "chunk":
            result["chunk_text"] = row["chunk_text"]
            result["chunk_index"] = row.get("chunk_index", 0)
        else:
            result["narrative_text"] = row["narrative_text"]

        formatted_results.append(result)

    return jsonify({
        "query": query_text,
        "top_k": top_k,
        "search_level": search_level,
        "source_type_filter": source_type_filter,
        "results": formatted_results
    })


@app.route("/weather/search", methods=["GET"])
def search_weather_rag():
    """
    RAG (Retrieval Augmented Generation) endpoint:
    - Retrieves relevant weather documents via vector search
    - Augments an LLM prompt with the retrieved context
    - Generates a natural language summary

    Query Parameters:
        query (required): Natural language question (e.g., "Is there flooding in Illinois?")
        top_k (optional): Number of documents to retrieve (default: 5, max: 10)
        source_type (optional): Filter by "alert" or "forecast"
        search_level (optional): "document" or "chunk" (default: "document" for RAG)

    Example:
        GET /weather/search?query=Is%20there%20flooding%20in%20Illinois?&top_k=5&search_level=document

    Returns:
        {
            "query": "Is there flooding in Illinois?",
            "summary": "Yes, there are active flood warnings in Chicago...",
            "sources": [
                {"location": "Chicago", "headline": "Flash Flood Warning", "similarity": 0.89},
                ...
            ]
        }
    """
    # Check if any LLM client is available
    if not llm_client:
        return jsonify({
            "error": "RAG endpoint not available",
            "message": "No LLM available (tried Databricks Foundation Models, then Anthropic)"
        }), 503

    logger.info(f"Using LLM type: {llm_type}")

    # Parse query parameters
    query_text = request.args.get("query")
    if not query_text:
        return jsonify({"error": "Missing 'query' parameter"}), 400

    top_k = request.args.get("top_k", 5, type=int)
    top_k = max(1, min(top_k, 10))  # Clamp to [1, 10] for RAG

    source_type_filter = request.args.get("source_type")
    search_level = request.args.get("search_level", "document")  # Default to document-level for RAG

    if search_level not in ("document", "chunk"):
        return jsonify({"error": "search_level must be 'document' or 'chunk'"}), 400

    # STEP 1: RETRIEVE - Use vector search to find relevant documents
    logger.info(f"RAG Step 1: Retrieving {search_level}-level documents for query: {query_text}")

    # Determine which embeddings table to use
    embeddings_table = WEATHER_DOCUMENT_EMBEDDINGS_TABLE if search_level == "document" else WEATHER_EMBEDDINGS_TABLE

    # Check if embeddings exist
    try:
        count_result = lakebase.run_query(
            f"SELECT COUNT(*) as count FROM {embeddings_table}"
        )
        if count_result[0]["count"] == 0:
            return jsonify({
                "error": "No embeddings found",
                "message": f"Run /weather/sync first, then run the embedding notebook to generate {search_level}-level embeddings."
            }), 404
    except Exception as e:
        return jsonify({
            "error": f"Embeddings table '{embeddings_table}' not accessible: {str(e)}"
        }), 500

    # Embed the query
    query_embedding = embedding_model.encode(query_text).tolist()

    # Build search query based on search_level
    if search_level == "document":
        # Document-level search
        search_sql = f"""
            SELECT
                d.id as document_id,
                d.location,
                d.source_type,
                d.headline,
                d.event,
                d.narrative_text,
                1 - (e.embedding <=> %s::vector) as similarity
            FROM {WEATHER_DOCUMENT_EMBEDDINGS_TABLE} e
            JOIN {WEATHER_DOCUMENTS_TABLE} d ON d.id = e.document_id
        """
    else:
        # Chunk-level search
        search_sql = f"""
            SELECT
                d.id as document_id,
                d.location,
                d.source_type,
                d.headline,
                d.event,
                d.narrative_text,
                e.chunk_text,
                e.chunk_index,
                1 - (e.embedding <=> %s::vector) as similarity
            FROM {WEATHER_EMBEDDINGS_TABLE} e
            JOIN {WEATHER_DOCUMENTS_TABLE} d ON d.id = e.document_id
        """

    params = [json.dumps(query_embedding)]

    if source_type_filter in ("alert", "forecast"):
        search_sql += " WHERE d.source_type = %s"
        params.append(source_type_filter)

    search_sql += f"""
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    params.append(json.dumps(query_embedding))
    params.append(top_k)

    try:
        results = lakebase.run_query(search_sql, params)
    except Exception as e:
        logger.exception("Vector search failed")
        return jsonify({"error": f"Search failed: {str(e)}"}), 500

    if not results:
        return jsonify({
            "query": query_text,
            "summary": "No relevant weather information found for your query.",
            "sources": []
        })

    # STEP 2: AUGMENT - Build context from retrieved documents
    logger.info(f"RAG Step 2: Augmenting prompt with {len(results)} retrieved {search_level}-level documents")

    context_parts = []
    for i, doc in enumerate(results, 1):
        # Use chunk_text for chunk-level, narrative_text for document-level
        content = doc.get('chunk_text') if search_level == "chunk" else doc['narrative_text']

        context_parts.append(
            f"[Document {i}]\n"
            f"Location: {doc['location']}\n"
            f"Type: {doc['source_type']}\n"
            f"Headline: {doc['headline']}\n"
            f"Content: {content}\n"
        )

    context = "\n".join(context_parts)

    # Build the augmented prompt
    system_prompt = """You are a helpful weather assistant. Your job is to answer questions about weather conditions based ONLY on the provided context.

Rules:
1. Answer based ONLY on the context provided - do not use outside knowledge
2. If the context doesn't contain enough information, say so
3. Be concise but informative
4. Cite specific locations and details from the context
5. If there are severe weather alerts, emphasize safety
6. Do not make up information or speculate"""

    user_prompt = f"""Context (retrieved weather documents):

{context}

User Question: {query_text}

Please provide a helpful answer based on the context above."""

    # STEP 3: GENERATE - Call LLM to generate summary
    logger.info(f"RAG Step 3: Generating LLM summary using {llm_type}")

    try:
        summary = call_llm(system_prompt, user_prompt)
        logger.info(f"RAG summary generated successfully via {llm_type}")

    except Exception as e:
        logger.exception("LLM generation failed")
        return jsonify({
            "error": f"Failed to generate summary: {str(e)}",
            "message": f"Retrieved documents successfully but {llm_type} LLM call failed",
            "llm_type": llm_type
        }), 500

    # Format sources for response
    sources = []
    for doc in results:
        sources.append({
            "document_id": doc["document_id"],
            "location": doc["location"],
            "source_type": doc["source_type"],
            "headline": doc["headline"],
            "event": doc.get("event"),
            "similarity": float(doc["similarity"])
        })

    return jsonify({
        "query": query_text,
        "summary": summary,
        "sources": sources,
        "search_level": search_level,
        "retrieval_count": len(results),
        "llm_type": llm_type  # Show which LLM was used
    })


if __name__ == '__main__':
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_RUN_PORT', 8000))
    logger.info(f"Starting Weather App on {host}:{port}")
    app.run(debug=True, host=host, port=port)
