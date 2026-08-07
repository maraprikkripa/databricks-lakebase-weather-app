"""
Client for the National Weather Service (NWS) API.

Free, no API key required, returns rich unstructured narrative text
perfect for embedding and semantic search.
"""

import hashlib
import time
from typing import Any
from datetime import datetime

import requests


class WeatherClient:
    """Thin wrapper around the NWS API with rate-limiting."""

    BASE_URL = "https://api.weather.gov"
    DEFAULT_TIMEOUT = 30

    def __init__(self, rate_limit_delay: float = 0.5):
        """
        Initialize weather client.

        Args:
            rate_limit_delay: Seconds to wait between requests (NWS asks for reasonable throttling)
        """
        self.rate_limit_delay = rate_limit_delay
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "(Databricks Weather App, learning project)",
            "Accept": "application/json"
        })
        self._last_request_time = 0

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a rate-limited GET request."""
        self._rate_limit()
        resp = self._session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=self.DEFAULT_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()

    def resolve_location(self, lat: float, lon: float) -> dict:
        """
        Resolve lat/lon to NWS grid coordinates.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Dict with gridId, gridX, gridY, and forecast URLs
        """
        data = self._get(f"/points/{lat},{lon}")
        properties = data.get("properties", {})
        return {
            "gridId": properties.get("gridId"),
            "gridX": properties.get("gridX"),
            "gridY": properties.get("gridY"),
            "forecast_url": properties.get("forecast"),
            "forecast_hourly_url": properties.get("forecastHourly"),
            "city": properties.get("relativeLocation", {}).get("properties", {}).get("city"),
            "state": properties.get("relativeLocation", {}).get("properties", {}).get("state")
        }

    def get_active_alerts(self, state: str | None = None, limit: int = 50) -> list[dict]:
        """
        Fetch active weather alerts.

        Args:
            state: Two-letter state code (e.g., "TX", "IL") or None for all
            limit: Maximum alerts to return

        Returns:
            List of alert documents
        """
        params = {"limit": limit}
        if state:
            params["area"] = state.upper()

        data = self._get("/alerts/active", params=params)
        features = data.get("features", [])

        alerts = []
        for feature in features[:limit]:
            props = feature.get("properties", {})

            # Generate stable ID from alert ID or content hash
            alert_id = props.get("id")
            if not alert_id:
                content = f"{props.get('event')}_{props.get('areaDesc')}_{props.get('onset')}"
                alert_id = hashlib.md5(content.encode()).hexdigest()

            # Combine description and instruction for narrative text
            narrative_parts = []
            if props.get("description"):
                narrative_parts.append(props["description"])
            if props.get("instruction"):
                narrative_parts.append(f"Instructions: {props['instruction']}")
            narrative_text = " ".join(narrative_parts)

            alerts.append({
                "id": alert_id,
                "location": props.get("areaDesc", "Unknown"),
                "source_type": "alert",
                "headline": props.get("headline", props.get("event", "Weather Alert")),
                "event": props.get("event"),
                "severity": props.get("severity"),
                "urgency": props.get("urgency"),
                "narrative_text": narrative_text,
                "issued_at": props.get("sent"),
                "effective_at": props.get("effective"),
                "expires_at": props.get("expires"),
                "payload": feature
            })

        return alerts

    def get_forecast(self, grid_id: str, grid_x: int, grid_y: int) -> list[dict]:
        """
        Fetch detailed forecast for a grid location.

        Args:
            grid_id: NWS grid office ID (e.g., "TOP")
            grid_x: Grid X coordinate
            grid_y: Grid Y coordinate

        Returns:
            List of forecast period documents
        """
        data = self._get(f"/gridpoints/{grid_id}/{grid_x},{grid_y}/forecast")
        properties = data.get("properties", {})
        periods = properties.get("periods", [])

        location = f"{grid_id} Grid ({grid_x},{grid_y})"

        forecasts = []
        for period in periods:
            # Generate stable ID from location + period
            forecast_id = hashlib.md5(
                f"{grid_id}_{grid_x}_{grid_y}_{period.get('number')}_{period.get('startTime')}".encode()
            ).hexdigest()

            # Use detailedForecast as narrative text
            narrative_text = period.get("detailedForecast", "")

            forecasts.append({
                "id": forecast_id,
                "location": location,
                "source_type": "forecast",
                "headline": period.get("name", "Forecast"),
                "event": "forecast",
                "narrative_text": narrative_text,
                "temperature": period.get("temperature"),
                "temperature_unit": period.get("temperatureUnit"),
                "wind_speed": period.get("windSpeed"),
                "wind_direction": period.get("windDirection"),
                "issued_at": properties.get("updated"),
                "effective_at": period.get("startTime"),
                "expires_at": period.get("endTime"),
                "payload": period
            })

        return forecasts

    def get_weather_for_locations(
        self,
        locations: list[tuple[float, float]],
        include_alerts: bool = True,
        include_forecast: bool = True,
        state_filter: str | None = None
    ) -> list[dict]:
        """
        Fetch weather data for multiple locations.

        Args:
            locations: List of (lat, lon) tuples
            include_alerts: Whether to fetch alerts
            include_forecast: Whether to fetch forecasts
            state_filter: Optional state code for alerts

        Returns:
            Combined list of weather documents
        """
        documents = []

        # Fetch alerts if requested
        if include_alerts:
            try:
                alerts = self.get_active_alerts(state=state_filter)
                documents.extend(alerts)
            except Exception as e:
                print(f"Warning: Failed to fetch alerts: {e}")

        # Fetch forecasts for each location
        if include_forecast:
            for lat, lon in locations:
                try:
                    grid_info = self.resolve_location(lat, lon)
                    forecasts = self.get_forecast(
                        grid_info["gridId"],
                        grid_info["gridX"],
                        grid_info["gridY"]
                    )
                    # Add city/state to location
                    for f in forecasts:
                        if grid_info.get("city") and grid_info.get("state"):
                            f["location"] = f"{grid_info['city']}, {grid_info['state']}"
                    documents.extend(forecasts)
                except Exception as e:
                    print(f"Warning: Failed to fetch forecast for {lat},{lon}: {e}")

        return documents
