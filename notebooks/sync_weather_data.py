# Databricks notebook source
# MAGIC %md
# MAGIC # Sync Weather Data from NWS API
# MAGIC
# MAGIC This notebook:
# MAGIC 1. Fetches weather data from the National Weather Service (NWS) API
# MAGIC 2. Stores raw weather documents in the `weather_documents` table in Lakebase
# MAGIC 3. Prepares data for embedding generation
# MAGIC
# MAGIC **Run this BEFORE running the embedding notebook!**

# COMMAND ----------

# MAGIC %md
# MAGIC ## Install Dependencies

# COMMAND ----------

# DBTITLE 1,Install required packages
# MAGIC %pip install -q pg8000 requests

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Config

# COMMAND ----------

dbutils.widgets.text("weather_documents_table", "weather_documents", "Weather documents table")
dbutils.widgets.text("locations", "chicago,austin,seattle,miami,denver", "Cities to sync (comma-separated)")
dbutils.widgets.dropdown("include_alerts", "true", ["true", "false"], "Include active alerts?")
dbutils.widgets.dropdown("include_forecast", "true", ["true", "false"], "Include forecasts?")

WEATHER_DOCUMENTS_TABLE = dbutils.widgets.get("weather_documents_table")
LOCATIONS_STR = dbutils.widgets.get("locations")
INCLUDE_ALERTS = dbutils.widgets.get("include_alerts") == "true"
INCLUDE_FORECAST = dbutils.widgets.get("include_forecast") == "true"

# Parse locations
LOCATIONS = [loc.strip() for loc in LOCATIONS_STR.split(",")]

print(f"Target table: {WEATHER_DOCUMENTS_TABLE}")
print(f"Locations: {LOCATIONS}")
print(f"Include alerts: {INCLUDE_ALERTS}")
print(f"Include forecasts: {INCLUDE_FORECAST}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resolve Lakebase Connection

# COMMAND ----------

# DBTITLE 1,Get Lakebase Connection Info
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
# MAGIC ## Weather Client (NWS API)

# COMMAND ----------

# DBTITLE 1,Define Weather Client
import hashlib
import json
import time
from datetime import datetime
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

                # Generate stable ID
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

            print(f"Fetched {len(documents)} active alerts")
            return documents

        except Exception as e:
            print(f"Error fetching alerts: {e}")
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
                # Generate stable ID
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

            print(f"Fetched {len(documents)} forecast periods for {lat:.4f}, {lon:.4f}")
            return documents

        except Exception as e:
            print(f"Error fetching forecast for {lat}, {lon}: {e}")
            return []


# Initialize client
weather_client = WeatherClient()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fetch Weather Data

# COMMAND ----------

# DBTITLE 1,Fetch weather data from NWS API
all_documents = []

# Fetch alerts
if INCLUDE_ALERTS:
    print("Fetching active alerts...")
    alerts = weather_client.get_active_alerts(limit=50)
    all_documents.extend(alerts)
    time.sleep(1)  # Rate limiting

# Fetch forecasts for each location
if INCLUDE_FORECAST:
    print("\nFetching forecasts...")
    for location_name in LOCATIONS:
        coords = weather_client.resolve_location(location_name)
        if coords:
            lat, lon = coords
            print(f"  Fetching forecast for {location_name} ({lat}, {lon})...")
            forecast = weather_client.get_forecast(lat, lon)
            all_documents.extend(forecast)
            time.sleep(1)  # Rate limiting

print(f"\n✅ Total weather documents fetched: {len(all_documents)}")
print(f"   Alerts: {sum(1 for d in all_documents if d['source_type'] == 'alert')}")
print(f"   Forecasts: {sum(1 for d in all_documents if d['source_type'] == 'forecast')}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write to Lakebase

# COMMAND ----------

# DBTITLE 1,Insert weather documents into Lakebase
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

        if insert_count % 20 == 0:
            print(f"  Inserted {insert_count}/{len(all_documents)} documents...")

    conn.commit()
    print(f"\n✅ Successfully inserted {insert_count} weather documents into {WEATHER_DOCUMENTS_TABLE}")
    print(f"   (Duplicates were updated via ON CONFLICT)")

finally:
    cursor.close()
    conn.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Data

# COMMAND ----------

# DBTITLE 1,Verify weather documents in Lakebase
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

    # Count total
    cursor.execute(f"SELECT COUNT(*) FROM {WEATHER_DOCUMENTS_TABLE}")
    total_count = cursor.fetchone()[0]
    print(f"✅ Total weather documents in table: {total_count}")

    # Count by source type
    cursor.execute(f"""
        SELECT source_type, COUNT(*) as count
        FROM {WEATHER_DOCUMENTS_TABLE}
        GROUP BY source_type
    """)
    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]}")

    # Sample documents
    cursor.execute(f"""
        SELECT source_type, headline, location, LEFT(narrative_text, 80) as preview
        FROM {WEATHER_DOCUMENTS_TABLE}
        ORDER BY synced_at DESC
        LIMIT 5
    """)

    print(f"\nSample weather documents:")
    for row in cursor.fetchall():
        print(f"  [{row[0]}] {row[1]}")
        print(f"    Location: {row[2]}")
        print(f"    Preview: {row[3]}...")
        print()

    cursor.close()

finally:
    conn.close()

# COMMAND ----------

print("🎉 Weather data sync complete!")
print(f"   Total documents: {len(all_documents)}")
print(f"   Stored in: {WEATHER_DOCUMENTS_TABLE}")
print(f"\n✅ Next step: Run the embedding notebook (ingest_weather_embeddings.py)")
