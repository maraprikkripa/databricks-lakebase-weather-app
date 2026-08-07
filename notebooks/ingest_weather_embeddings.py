# Databricks notebook source
# MAGIC %md
# MAGIC # Ingest Weather Documents -> Vector Embeddings (Lakebase)
# MAGIC
# MAGIC This notebook:
# MAGIC 1. Reads unembedded weather documents from the `weather_documents` table in Lakebase
# MAGIC 2. Computes **document-level embeddings** (entire document as one vector)
# MAGIC 3. Chunks long narrative text (sliding window: 800 chars, 100 overlap)
# MAGIC 4. Computes **chunk-level embeddings** using sentence-transformers/all-MiniLM-L6-v2 (384-dim)
# MAGIC 5. Writes both to `weather_document_embeddings` and `weather_embeddings` tables
# MAGIC
# MAGIC **Key differences from news pipeline:**
# MAGIC - Uses pg8000 instead of psycopg2 (pure Python, no C extensions)
# MAGIC - Handles double base64-encoded secrets
# MAGIC - Weather text is shorter than news articles (less chunking needed)
# MAGIC - Generates BOTH document-level AND chunk-level embeddings (like news app)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Install Dependencies

# COMMAND ----------

# DBTITLE 1,Install required packages
# MAGIC %pip install -q pg8000 sentence-transformers

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config

# COMMAND ----------

dbutils.widgets.text("weather_documents_table", "weather_documents", "Source table (weather docs)")
dbutils.widgets.text("weather_document_embeddings_table", "weather_document_embeddings", "Document-level embeddings")
dbutils.widgets.text("weather_embeddings_table", "weather_embeddings", "Chunk-level embeddings")
dbutils.widgets.text("embedding_model", "sentence-transformers/all-MiniLM-L6-v2", "Embedding model")
dbutils.widgets.text("chunk_size", "800", "Chunk size (chars)")
dbutils.widgets.text("chunk_overlap", "100", "Chunk overlap (chars)")

WEATHER_DOCUMENTS_TABLE = dbutils.widgets.get("weather_documents_table")
WEATHER_DOCUMENT_EMBEDDINGS_TABLE = dbutils.widgets.get("weather_document_embeddings_table")
WEATHER_EMBEDDINGS_TABLE = dbutils.widgets.get("weather_embeddings_table")
EMBEDDING_MODEL_NAME = dbutils.widgets.get("embedding_model")
CHUNK_SIZE = int(dbutils.widgets.get("chunk_size"))
CHUNK_OVERLAP = int(dbutils.widgets.get("chunk_overlap"))

# Embedding dimension for the model
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 outputs 384-dim vectors

print(f"Using model {EMBEDDING_MODEL_NAME!r} -> {EMBEDDING_DIM}-dim vectors")
print(f"Chunking: {CHUNK_SIZE} chars with {CHUNK_OVERLAP} overlap")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resolve Lakebase Connection

# COMMAND ----------

# DBTITLE 1,Parse Lakebase Connection Info
import base64
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()


def get_lakebase_url() -> str:
    """Fetch and decode the Lakebase URL from secrets."""
    secret = w.secrets.get_secret(scope="database", key="lakebase-url")
    # The secret is double base64-encoded - decode twice to get the actual PostgreSQL URL
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

# DBTITLE 1,Test pg8000 Connection
import pg8000

print(f"Testing connection to {db_host}:{db_port}/{db_name}")
print(f"Using password authentication as user: {db_user}\n")

try:
    conn = pg8000.connect(
        host=db_host,
        port=db_port,
        database=db_name,
        user=db_user,
        password=db_password,
        ssl_context=True
    )
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_DOCUMENTS_TABLE}")
    count = cursor.fetchone()[0]
    print(f"✅ Connection successful! Found {count} rows in {WEATHER_DOCUMENTS_TABLE}")

    cursor.execute(f"SELECT * FROM {WEATHER_DOCUMENTS_TABLE} LIMIT 5")
    rows = cursor.fetchall()
    print(f"\nSample rows: {len(rows)}")
    for row in rows[:2]:
        print(f"  - {row[2]}: {row[3][:60]}...")  # source_type, headline

    cursor.close()
    conn.close()
    print("\n✅ pg8000 with password authentication working correctly!")
except Exception as e:
    import traceback
    print(f"❌ Connection failed: {e}")
    print(f"\nFull traceback:")
    traceback.print_exc()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load Weather Documents

# COMMAND ----------

# DBTITLE 1,Read weather documents from Lakebase
import pandas as pd
import pg8000

conn = pg8000.connect(
    host=db_host,
    port=db_port,
    database=db_name,
    user=db_user,
    password=db_password,
    ssl_context=True
)

try:
    # Query documents with their narrative text
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
    print(f"Loaded {len(weather_df)} weather documents from {WEATHER_DOCUMENTS_TABLE}")
    print(f"\nSource types: {weather_df['source_type'].value_counts().to_dict()}")
    display(weather_df.head(5))
finally:
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compute Document-Level Embeddings (Entire Documents)

# COMMAND ----------

# DBTITLE 1,Load embedding model and compute document-level vectors
import os
from sentence_transformers import SentenceTransformer

# Set up HuggingFace cache
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["TRANSFORMERS_CACHE"] = "/tmp/.cache/huggingface"
os.environ["HF_HUB_CACHE"] = "/tmp/.cache/huggingface"

print(f"Loading embedding model {EMBEDDING_MODEL_NAME}...")
model = SentenceTransformer(EMBEDDING_MODEL_NAME, cache_folder="/tmp/.cache/huggingface")

# Compute document-level embeddings (entire narrative text as one vector)
print(f"\nComputing document-level embeddings for {len(weather_df)} documents...")
batch_size = 32
doc_embeddings = []

for i in range(0, len(weather_df), batch_size):
    batch = weather_df.iloc[i:i+batch_size]
    vectors = model.encode(batch["narrative_text"].tolist(), show_progress_bar=False)
    doc_embeddings.extend(vectors.tolist())

    if (i + batch_size) % 128 == 0:
        print(f"  Processed {min(i + batch_size, len(weather_df))}/{len(weather_df)} documents")

# Create document embeddings DataFrame
document_embeddings_df = pd.DataFrame({
    "id": weather_df["id"] + "_doc",
    "document_id": weather_df["id"],
    "embedding": doc_embeddings,
    "model_name": EMBEDDING_MODEL_NAME
})

print(f"\n✅ Computed {len(document_embeddings_df)} document-level embeddings")
print(f"   Vector dimensions: {len(doc_embeddings[0])}")
display(document_embeddings_df.head(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chunk Long Narrative Text

# COMMAND ----------

# DBTITLE 1,Chunk narrative text with sliding window
def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
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
print(f"Created {len(chunks_df)} chunks from {len(weather_df)} documents")
print(f"Average chunks per document: {len(chunks_df) / len(weather_df):.1f}")

display(chunks_df.head(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compute Chunk-Level Embeddings

# COMMAND ----------

# DBTITLE 1,Compute chunk-level embeddings (model already loaded)
# Compute chunk embeddings in batches
print(f"Computing chunk-level embeddings for {len(chunks_df)} chunks...")
batch_size = 32
all_embeddings = []

for i in range(0, len(chunks_df), batch_size):
    batch = chunks_df.iloc[i:i+batch_size]
    vectors = model.encode(batch["chunk_text"].tolist(), show_progress_bar=False)
    all_embeddings.extend(vectors.tolist())

    if (i + batch_size) % 128 == 0:
        print(f"  Processed {min(i + batch_size, len(chunks_df))}/{len(chunks_df)} chunks")

# Create embeddings DataFrame
embeddings_df = pd.DataFrame({
    "id": chunks_df["document_id"] + "_" + chunks_df["chunk_index"].astype(str),
    "document_id": chunks_df["document_id"],
    "chunk_index": chunks_df["chunk_index"],
    "chunk_text": chunks_df["chunk_text"],
    "embedding": all_embeddings,
    "model_name": EMBEDDING_MODEL_NAME
})

print(f"\n✅ Computed {len(embeddings_df)} embeddings using {EMBEDDING_MODEL_NAME}")
print(f"   Vector dimensions: {len(all_embeddings[0])}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write Document-Level Embeddings to Lakebase

# COMMAND ----------

# DBTITLE 1,Insert document-level embeddings using pg8000
import pg8000
from datetime import datetime

print(f"Inserting {len(document_embeddings_df)} document-level embeddings into {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}...")

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

    # Prepare data for batch insert
    embedded_at = datetime.now()
    insert_count = 0

    for idx, row in document_embeddings_df.iterrows():
        # Format embedding as PostgreSQL array literal
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

        if insert_count % 50 == 0:
            print(f"  Inserted {insert_count}/{len(document_embeddings_df)} document embeddings...")

    conn.commit()
    print(f"\n✅ Successfully inserted {insert_count} document-level embeddings")
    print(f"   (Duplicates were updated via ON CONFLICT)")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write Chunk-Level Embeddings to Lakebase

# COMMAND ----------

# DBTITLE 1,Insert embeddings using pg8000
import pg8000
from datetime import datetime

print(f"Inserting {len(embeddings_df)} embeddings into {WEATHER_EMBEDDINGS_TABLE}...")

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

    # Prepare data for batch insert
    embedded_at = datetime.now()
    insert_count = 0

    for idx, row in embeddings_df.iterrows():
        # Format embedding as PostgreSQL array literal
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
    print(f"\n✅ Successfully inserted {insert_count} embeddings")
    print(f"   (Duplicates were updated via ON CONFLICT)")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Embeddings

# COMMAND ----------

# DBTITLE 1,Check embeddings were written correctly
import pg8000

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

    # Check count
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_EMBEDDINGS_TABLE}")
    count = cursor.fetchone()[0]
    print(f"✅ Total embeddings in table: {count}")

    # Sample a few
    cursor.execute(f"""
        SELECT
            document_id,
            chunk_index,
            LEFT(chunk_text, 80) as chunk_preview,
            model_name
        FROM {WEATHER_EMBEDDINGS_TABLE}
        ORDER BY embedded_at DESC
        LIMIT 5
    """)

    print(f"\nSample embeddings:")
    for row in cursor.fetchall():
        print(f"  {row[0]} chunk {row[1]}: {row[2]}...")

    cursor.close()

finally:
    conn.close()

# COMMAND ----------

# DBTITLE 1,Verify document-level embeddings
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

    # Check count
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")
    doc_count = cursor.fetchone()[0]
    print(f"✅ Total document-level embeddings in table: {doc_count}")

    # Sample a few
    cursor.execute(f"""
        SELECT
            document_id,
            model_name
        FROM {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}
        ORDER BY embedded_at DESC
        LIMIT 5
    """)

    print(f"\nSample document embeddings:")
    for row in cursor.fetchall():
        print(f"  {row[0]} (model: {row[1]})")

    cursor.close()

finally:
    conn.close()

# COMMAND ----------

print(f"\n🎉 Embedding pipeline complete!")
print(f"   Documents processed: {len(weather_df)}")
print(f"   Document-level embeddings stored: {len(document_embeddings_df)}")
print(f"   Chunks created: {len(chunks_df)}")
print(f"   Chunk-level embeddings stored: {len(embeddings_df)}")
print(f"\n✅ Ready for semantic search!")
print(f"   - Document-level: Search via {WEATHER_DOCUMENT_EMBEDDINGS_TABLE}")
print(f"   - Chunk-level: Search via {WEATHER_EMBEDDINGS_TABLE}")
