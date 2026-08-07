-- Setup script for weather_document_embeddings table
-- Run this manually in your Lakebase Postgres database
-- This table stores document-level embeddings (entire document as one vector)
-- Different from weather_embeddings which stores chunk-level embeddings

-- Create the document-level embeddings table
-- Using dimension 384 for sentence-transformers/all-MiniLM-L6-v2
CREATE TABLE IF NOT EXISTS weather_document_embeddings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES weather_documents(id) ON DELETE CASCADE,
    embedding VECTOR(384) NOT NULL,
    model_name TEXT NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id)
);

-- Create HNSW index for fast cosine similarity search
-- This index makes vector search ~100x faster on large datasets
CREATE INDEX IF NOT EXISTS idx_weather_document_embeddings_embedding
ON weather_document_embeddings
USING hnsw (embedding vector_cosine_ops);

-- Create index for document_id lookups
CREATE INDEX IF NOT EXISTS idx_weather_document_embeddings_document_id
ON weather_document_embeddings (document_id);

-- Verify the table was created
SELECT
    table_name,
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_name = 'weather_document_embeddings'
ORDER BY ordinal_position;

-- Check row count (will be 0 until you run the embedding notebook)
SELECT COUNT(*) as document_embedding_count FROM weather_document_embeddings;
