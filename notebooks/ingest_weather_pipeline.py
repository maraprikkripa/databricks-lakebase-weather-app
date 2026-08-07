# Databricks notebook source
# MAGIC %md
# MAGIC # Weather Intelligence Pipeline - Complete ETL
# MAGIC
# MAGIC This notebook handles the complete weather data pipeline:
# MAGIC 1. **Fetch** weather data from National Weather Service (NWS) API
# MAGIC 2. **Store** raw weather documents in `weather_documents` table
# MAGIC 3. **Compute** document-level embeddings (entire documents)
# MAGIC 4. **Store** in `weather_document_embeddings` table
# MAGIC 5. **Chunk** long narrative text (800 chars, 100 overlap)
# MAGIC 6. **Compute** chunk-level embeddings
# MAGIC 7. **Store** in `weather_embeddings` table
# MAGIC
# MAGIC **Run this as one complete pipeline!**
# MAGIC
# MAGIC Similar to the news app's single comprehensive notebook.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Install Dependencies

# COMMAND ----------

# DBTITLE 1,Install required packages
# MAGIC %pip install -q pg8000 sentence-transformers requests

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config

# COMMAND ----------

# Weather data config
dbutils.widgets.text("weather_documents_table", "weather_documents", "Weather documents table")
dbutils.widgets.text("weather_document_embeddings_table", "weather_document_embeddings", "Document embeddings table")
dbutils.widgets.text("weather_embeddings_table", "weather_embeddings", "Chunk embeddings table")
dbutils.widgets.text("locations", "chicago,austin,seattle,miami,denver", "Cities (comma-separated)")
dbutils.widgets.dropdown("include_alerts", "true", ["true", "false"], "Include alerts?")
dbutils.widgets.dropdown("include_forecast", "true", ["true", "false"], "Include forecasts?")

# Embedding config
dbutils.widgets.text("embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model")
dbutils.widgets.text("chunk_size", "800", "Chunk size (chars)")
dbutils.widgets.text("chunk_overlap", "100", "Chunk overlap (chars)")

WEATHER_DOCUMENTS_TABLE = dbutils.widgets.get("weather_documents_table")
WEATHER_DOCUMENT_EMBEDDINGS_TABLE = dbutils.widgets.get("weather_document_embeddings_table")
WEATHER_EMBEDDINGS_TABLE = dbutils.widgets.get("weather_embeddings_table")
LOCATIONS_STR = dbutils.widgets.get("locations")
INCLUDE_ALERTS = dbutils.widgets.get("include_alerts") == "true"
INCLUDE_FORECAST = dbutils.widgets.get("include_forecast") == "true"
EMBEDDING_MODEL_NAME = dbutils.widgets.get("embedding_model")
CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))

# Parse locations
LOCATIONS = [loc.strip() for loc in LOCATIONS_STR.split(",")]

# Embedding dimension
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 outputs 384-dim vectors

print(f"Pipeline Config:")
print(f"  Documents table: {WEATHER_DOCUMENTS_TABLE}")
print(f"  Document embeddings table: {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")
print(f"  Chunk embeddings table: {WEATHER_EMBEDDINGS_TABLE}")
print(f"  Locations: {LOCATIONS}")
print(f"  Include alerts: {INCLUDE_ALERTS}")
print(f"  Include forecasts: {INCLUDE_FORECAST}")
print(f"  Embedding model: {EMBEDDING_MODEL_NAME} ({EMBEDDING_DIM}-dim)")
print(f"  Chunking: {CHUNK_SIZE} chars with {CHUNK_OVERLAP} overlap")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resolve Lakebase Connection

# COMMAND ----------

# DBTITLE 1,Get Lakebase connection info
import base64
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()


def get_lakebase_url() -> str:
    """Fetch and decode the Lakebase URL from secrets."""
    secret = w.secrets.get_secret(scope="database", key="lakebase-url")
    # The secret is double base64-encoded - decode twice
    first_decode = base64.b64decode(secret.value).decode("utf-8")
    second_decode = base64.b64decode(first_decode).decode("utf-8")
    return second_decode


lakebase_url = get_lakebase_url()
parsed = urlparse(lakebase_url)

db_host = parsed.hostname
db_port = parsed.port or 5432
db_name = parsed.path.lstrip('/')
db_user = parsed.username
db_password = parsed.password

print(f"Connection details:")
print(f"  Host: {db_host}:{db_port}")
print(f"  Database: {db_name}")
print(f"  User: {db_user}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Fetch Weather Data from NWS API

# COMMAND ----------

# DBTITLE 1,Define Weather Client
import hashlib
import json
import time
from typing import List, Tuple, Optional

import requests


class WeatherClient:
    """Client for fetching weather data from National Weather Service API."""

    BASE_URL = "https://api.weather.gov"

    # Predefined locations (lat, lon)
    LOCATIONS = {
        "chicago": (41.8781, -87.6298),
        "austin": (30.2672, -97.7431),
        "seattle": (47.6062, -122.3321),
        "miami": (25.7617, -80.1918),
        "denver": (39.7392, -104.9903)
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "(Databricks Weather App, contact@example.com)",
            "Accept": "application/geo+json"
        })

    def resolve_location(self, location: str) -> Optional[Tuple[float, float]]:
        """Resolve location name to (lat, lon)."""
        if location.lower() in self.LOCATIONS:
            return self.LOCATIONS[location.lower()]
        return None

    def get_active_alerts(self, state: Optional[str] = None, limit: int = 50) -> List[dict]:
        """Fetch active weather alerts."""
        url = f"{self.BASE_URL}/alerts/active"
        params = {}
        if state:
            params["area"] = state
        if limit:
            params["limit"] = limit

        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            documents = []
            for feature in data.get("features", []):
                props = feature.get("properties", {})

                alert_id = props.get("id") or hashlib.md5(
                    f"{props.get('event')}_{props.get('areaDesc')}_{props.get('effective')}".encode()
                ).hexdigest()

                document = {
                    "id": alert_id,
                    "location": props.get("areaDesc", "Unknown"),
                    "source_type": "alert",
                    "headline": props.get("headline", props.get("event", "Alert")),
                    "event": props.get("event"),
                    "narrative_text": props.get("description", ""),
                    "severity": props.get("severity"),
                    "urgency": props.get("urgency"),
                    "issued_at": props.get("sent"),
                    "effective_at": props.get("effective"),
                    "expires_at": props.get("expires"),
                    "payload": feature
                }
                documents.append(document)

            print(f"  Fetched {len(documents)} active alerts")
            return documents

        except Exception as e:
            print(f"  ⚠️  Error fetching alerts: {e}")
            return []

    def get_forecast(self, lat: float, lon: float) -> List[dict]:
        """Fetch weather forecast for a location."""
        try:
            # Step 1: Get grid point
            points_url = f"{self.BASE_URL}/points/{lat:.4f},{lon:.4f}"
            response = self.session.get(points_url, timeout=10)
            response.raise_for_status()
            points_data = response.json()

            # Step 2: Get forecast
            forecast_url = points_data["properties"]["forecast"]
            time.sleep(0.5)  # Rate limiting

            response = self.session.get(forecast_url, timeout=10)
            response.raise_for_status()
            forecast_data = response.json()

            documents = []
            for period in forecast_data["properties"]["periods"]:
                forecast_id = hashlib.md5(
                    f"{lat}_{lon}_{period['number']}_{period['startTime']}".encode()
                ).hexdigest()

                document = {
                    "id": forecast_id,
                    "location": f"{lat:.4f}, {lon:.4f}",
                    "source_type": "forecast",
                    "headline": period["name"],
                    "narrative_text": period["detailedForecast"],
                    "temperature": period["temperature"],
                    "temperature_unit": period["temperatureUnit"],
                    "wind_speed": period["windSpeed"],
                    "wind_direction": period["windDirection"],
                    "issued_at": period["startTime"],
                    "expires_at": period["endTime"],
                    "payload": period
                }
                documents.append(document)

            print(f"  Fetched {len(documents)} forecast periods for {lat:.4f}, {lon:.4f}")
            return documents

        except Exception as e:
            print(f"  ⚠️  Error fetching forecast for {lat}, {lon}: {e}")
            return []


weather_client = WeatherClient()

# COMMAND ----------

# DBTITLE 1,Fetch weather data
print("=" * 80)
print("STEP 1: FETCHING WEATHER DATA FROM NWS API")
print("=" * 80)

all_documents = []

# Fetch alerts
if INCLUDE_ALERTS:
    print("\n📡 Fetching active alerts...")
    alerts = weather_client.get_active_alerts(limit=50)
    all_documents.extend(alerts)
    time.sleep(1)

# Fetch forecasts
if INCLUDE_FORECAST:
    print("\n📡 Fetching forecasts...")
    for location_name in LOCATIONS:
        coords = weather_client.resolve_location(location_name)
        if coords:
            lat, lon = coords
            print(f"  → {location_name} ({lat}, {lon})...")
            forecast = weather_client.get_forecast(lat, lon)
            all_documents.extend(forecast)
            time.sleep(1)

print(f"\n✅ Total weather documents fetched: {len(all_documents)}")
print(f"   Alerts: {sum(1 for d in all_documents if d['source_type'] == 'alert')}")
print(f"   Forecasts: {sum(1 for d in all_documents if d['source_type'] == 'forecast')}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Store Weather Documents in Lakebase

# COMMAND ----------

# DBTITLE 1,Insert weather documents
import pg8000

print("=" * 80)
print("STEP 2: STORING WEATHER DOCUMENTS IN LAKEBASE")
print("=" * 80)

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    cursor = conn.cursor()
    insert_count = 0

    for doc in all_documents:
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
        insert_count += 1

    conn.commit()
    print(f"\n✅ Successfully stored {insert_count} documents in {WEATHER_DOCUMENTS_TABLE}")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Load Weather Documents with Pandas

# COMMAND ----------

# DBTITLE 1,Load documents from Lakebase
import pandas as pd

print("=" * 80)
print("STEP 3: LOADING WEATHER DOCUMENTS")
print("=" * 80)

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    query = f"""
        SELECT
            id,
            location,
            source_type,
            headline,
            narrative_text
        FROM {WEATHER_DOCUMENTS_TABLE}
        WHERE narrative_text IS NOT NULL
          AND TRIM(narrative_text) != ''
    """

    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    weather_df = pd.DataFrame(rows, columns=columns)
    print(f"\n✅ Loaded {len(weather_df)} documents from {WEATHER_DOCUMENTS_TABLE}")
    print(f"   Source types: {weather_df['source_type'].value_counts().to_dict()}")

finally:
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Compute Document-Level Embeddings

# COMMAND ----------

# DBTITLE 1,Load embedding model and compute document embeddings
import os
from sentence_transformers import SentenceTransformer

print("=" * 80)
print("STEP 4: COMPUTING DOCUMENT-LEVEL EMBEDDINGS")
print("=" * 80)

# Set up HuggingFace cache
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["TRANSFORMERS_CACHE"] = "/tmp/.cache/huggingface"
os.environ["HF_HUB_CACHE"] = "/tmp/.cache/huggingface"

print(f"\n🤖 Loading embedding model: {EMBEDDING_MODEL_NAME}...")
model = SentenceTransformer(EMBEDDING_MODEL_NAME, cache_folder="/tmp/.cache/huggingface")
print("✅ Model loaded successfully")

# Compute document-level embeddings
print(f"\n📊 Computing document-level embeddings for {len(weather_df)} documents...")
batch_size = 32
doc_embeddings = []

for i in range(0, len(weather_df), batch_size):
    batch = weather_df.iloc[i:i+batch_size]
    vectors = model.encode(batch["narrative_text"].tolist(), show_progress_bar=False)
    doc_embeddings.extend(vectors.tolist())

    if (i + batch_size) % 128 == 0:
        print(f"  Processed {min(i + batch_size, len(weather_df))}/{len(weather_df)} documents...")

# Create document embeddings DataFrame
document_embeddings_df = pd.DataFrame({
    "id": weather_df["id"] + "_doc",
    "document_id": weather_df["id"],
    "embedding": doc_embeddings,
    "model_name": EMBEDDING_MODEL_NAME
})

print(f"\n✅ Computed {len(document_embeddings_df)} document-level embeddings")
print(f"   Vector dimensions: {len(doc_embeddings[0])}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Store Document-Level Embeddings

# COMMAND ----------

# DBTITLE 1,Insert document embeddings into Lakebase
from datetime import datetime

print("=" * 80)
print("STEP 5: STORING DOCUMENT-LEVEL EMBEDDINGS")
print("=" * 80)

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    cursor = conn.cursor()
    embedded_at = datetime.now()
    insert_count = 0

    for idx, row in document_embeddings_df.iterrows():
        embedding_str = '{' + ','.join(str(float(x)) for x in row['embedding']) + '}'

        cursor.execute(
            f"""
            INSERT INTO {WEATHER_DOCUMENT_EMBEDDINGS_TABLE} (
                id, document_id, embedding, model_name, embedded_at
            )
            VALUES (%s, %s, %s::double precision[], %s, %s)
            ON CONFLICT (document_id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    embedded_at = EXCLUDED.embedded_at
            """,
            [
                row['id'],
                row['document_id'],
                embedding_str,
                row['model_name'],
                embedded_at
            ]
        )
        insert_count += 1

    conn.commit()
    print(f"\n✅ Successfully stored {insert_count} document embeddings in {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Chunk Narrative Text

# COMMAND ----------

# DBTITLE 1,Chunk text with sliding window
print("=" * 80)
print("STEP 6: CHUNKING NARRATIVE TEXT")
print("=" * 80)


def chunk_text(text: str, chunk_size: int, overlap: int) -> list:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += (chunk_size - overlap)

    return chunks


# Chunk all documents
chunked_data = []
for idx, row in weather_df.iterrows():
    doc_id = row['id']
    narrative = row['narrative_text']

    chunks = chunk_text(narrative, CHUNK_SIZE, CHUNK_OVERLAP)

    for chunk_idx, chunk_text in enumerate(chunks):
        chunked_data.append({
            'document_id': doc_id,
            'chunk_index': chunk_idx,
            'chunk_text': chunk_text
        })

chunks_df = pd.DataFrame(chunked_data)
print(f"\n✅ Created {len(chunks_df)} chunks from {len(weather_df)} documents")
print(f"   Average chunks per document: {len(chunks_df) / len(weather_df):.1f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7: Compute Chunk-Level Embeddings

# COMMAND ----------

# DBTITLE 1,Compute chunk embeddings
print("=" * 80)
print("STEP 7: COMPUTING CHUNK-LEVEL EMBEDDINGS")
print("=" * 80)

print(f"\n📊 Computing chunk embeddings for {len(chunks_df)} chunks...")
batch_size = 32
all_embeddings = []

for i in range(0, len(chunks_df), batch_size):
    batch = chunks_df.iloc[i:i+batch_size]
    vectors = model.encode(batch["chunk_text"].tolist(), show_progress_bar=False)
    all_embeddings.extend(vectors.tolist())

    if (i + batch_size) % 128 == 0:
        print(f"  Processed {min(i + batch_size, len(chunks_df))}/{len(chunks_df)} chunks...")

# Create embeddings DataFrame
embeddings_df = pd.DataFrame({
    "id": chunks_df["document_id"] + "_" + chunks_df["chunk_index"].astype(str),
    "document_id": chunks_df["document_id"],
    "chunk_index": chunks_df["chunk_index"],
    "chunk_text": chunks_df["chunk_text"],
    "embedding": all_embeddings,
    "model_name": EMBEDDING_MODEL_NAME
})

print(f"\n✅ Computed {len(embeddings_df)} chunk embeddings")
print(f"   Vector dimensions: {len(all_embeddings[0])}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 8: Store Chunk-Level Embeddings

# COMMAND ----------

# DBTITLE 1,Insert chunk embeddings into Lakebase
print("=" * 80)
print("STEP 8: STORING CHUNK-LEVEL EMBEDDINGS")
print("=" * 80)

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    cursor = conn.cursor()
    embedded_at = datetime.now()
    insert_count = 0

    for idx, row in embeddings_df.iterrows():
        embedding_str = '{' + ','.join(str(float(x)) for x in row['embedding']) + '}'

        cursor.execute(
            f"""
            INSERT INTO {WEATHER_EMBEDDINGS_TABLE} (
                id, document_id, chunk_index, chunk_text, embedding, model_name, embedded_at
            )
            VALUES (%s, %s, %s, %s, %s::double precision[], %s, %s)
            ON CONFLICT (document_id, chunk_index) DO UPDATE
                SET chunk_text = EXCLUDED.chunk_text,
                    embedding = EXCLUDED.embedding,
                    embedded_at = EXCLUDED.embedded_at
            """,
            [
                row['id'],
                row['document_id'],
                int(row['chunk_index']),
                row['chunk_text'],
                embedding_str,
                row['model_name'],
                embedded_at
            ]
        )
        insert_count += 1

        if insert_count % 100 == 0:
            print(f"  Inserted {insert_count}/{len(embeddings_df)} embeddings...")

    conn.commit()
    print(f"\n✅ Successfully stored {insert_count} chunk embeddings in {WEATHER_EMBEDDINGS_TABLE}")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Pipeline Results

# COMMAND ----------

# DBTITLE 1,Verify all tables
print("=" * 80)
print("PIPELINE VERIFICATION")
print("=" * 80)

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    cursor = conn.cursor()

    # Check documents
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_DOCUMENTS_TABLE}")
    doc_count = cursor.fetchone()[0]
    print(f"\n✅ weather_documents: {doc_count} documents")

    # Check document embeddings
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")
    doc_emb_count = cursor.fetchone()[0]
    print(f"✅ weather_document_embeddings: {doc_emb_count} embeddings")

    # Check chunk embeddings
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_EMBEDDINGS_TABLE}")
    chunk_emb_count = cursor.fetchone()[0]
    print(f"✅ weather_embeddings: {chunk_emb_count} embeddings")

    cursor.close()

finally:
    conn.close()

# COMMAND ----------

print("\n" + "=" * 80)
print("🎉 WEATHER INTELLIGENCE PIPELINE COMPLETE!")
print("=" * 80)
print(f"\nSummary:")
print(f"  📡 Weather documents fetched: {len(all_documents)}")
print(f"  💾 Documents stored: {len(weather_df)}")
print(f"  📊 Document-level embeddings: {len(document_embeddings_df)}")
print(f"  ✂️  Chunks created: {len(chunks_df)}")
print(f"  📊 Chunk-level embeddings: {len(embeddings_df)}")
print(f"\n✅ Ready for semantic search!")
print(f"   - Document-level: {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")
print(f"   - Chunk-level: {WEATHER_EMBEDDINGS_TABLE}")
print(f"\n🌐 Test the beautiful UI at: http://localhost:8000")
