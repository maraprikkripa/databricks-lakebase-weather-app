-- Setup script for weather_documents table
-- Run this manually in your Lakebase Postgres database before running the Flask app or notebook

-- Create the weather documents table
CREATE TABLE IF NOT EXISTS weather_documents (
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
);

-- Create index for source_type lookups (alert vs forecast)
CREATE INDEX IF NOT EXISTS idx_weather_documents_source_type
ON weather_documents (source_type);

-- Create index for location lookups
CREATE INDEX IF NOT EXISTS idx_weather_documents_location
ON weather_documents (location);

-- Create index for time-based queries
CREATE INDEX IF NOT EXISTS idx_weather_documents_synced_at
ON weather_documents (synced_at DESC);

-- Verify the table was created
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'weather_documents'
ORDER BY ordinal_position;
