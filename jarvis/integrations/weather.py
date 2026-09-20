from __future__ import annotations

import json
import socket
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


class WeatherRequest(BaseModel):
    location: str = Field(min_length=1)
    target_date: date


class WeatherResult(BaseModel):
    location: str
    target_date: date
    condition: str
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    minimum_temperature_c: float
    maximum_temperature_c: float
    precipitation_probability_percent: int | None = None
    wind_speed_kph: float | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime
    source: str = "Open-Meteo"
    source_url: str = "https://open-meteo.com/"


class WeatherProvider(Protocol):
    def get_weather(self, request: WeatherRequest) -> WeatherResult: ...


class WeatherProviderError(RuntimeError):
    """A weather lookup failed without returning trustworthy forecast data."""


class WeatherLocationNotFound(WeatherProviderError):
    pass


class WeatherTimeoutError(WeatherProviderError):
    pass


class MockWeatherProvider:
    """Deterministic, read-only weather data for tests and offline development."""

    def __init__(
        self,
        results: dict[tuple[str, date], WeatherResult] | None = None,
        *,
        today: date | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or {}
        self.today = today or date.today()
        self.error = error
        self.requests: list[WeatherRequest] = []

    def get_weather(self, request: WeatherRequest) -> WeatherResult:
        self.requests.append(request)
        if self.error:
            raise self.error
        key = (request.location.casefold(), request.target_date)
        if key in self.results:
            return self.results[key]
        is_today = request.target_date == self.today
        return WeatherResult(
            location=request.location,
            target_date=request.target_date,
            condition="partly cloudy",
            temperature_c=21.0 if is_today else None,
            apparent_temperature_c=20.0 if is_today else None,
            minimum_temperature_c=16.0,
            maximum_temperature_c=24.0,
            precipitation_probability_percent=20,
            wind_speed_kph=9.0 if is_today else None,
            observed_at=(
                datetime.combine(request.target_date, datetime.min.time(), tzinfo=timezone.utc)
                if is_today
                else None
            ),
            retrieved_at=datetime.now(timezone.utc),
            source="Mock weather",
            source_url="local://mock-weather",
        )


class OpenMeteoWeatherProvider:
    """Read-only Open-Meteo geocoding and forecast client."""

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Weather timeout must be greater than zero")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_weather(self, request: WeatherRequest) -> WeatherResult:
        location = self._geocode(request.location)
        forecast = self._forecast(
            latitude=location["latitude"],
            longitude=location["longitude"],
            target_date=request.target_date,
        )
        return self._parse_result(request, location, forecast)

    def _geocode(self, query: str) -> dict[str, Any]:
        payload = self._get_json(
            self.GEOCODING_URL,
            {"name": query, "count": 1, "language": "en", "format": "json"},
        )
        results = payload.get("results") or []
        if not results:
            raise WeatherLocationNotFound(f"No weather location matched {query!r}")
        result = results[0]
        if "latitude" not in result or "longitude" not in result:
            raise WeatherProviderError("The geocoding service returned incomplete coordinates")
        return result

    def _forecast(self, *, latitude: float, longitude: float, target_date: date) -> dict[str, Any]:
        return self._get_json(
            self.FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
                ),
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "timezone": "auto",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
        )

    def _get_json(self, base_url: str, parameters: dict[str, Any]) -> dict[str, Any]:
        url = f"{base_url}?{urlencode(parameters)}"
        request = Request(url, headers={"User-Agent": "Jarvis/0.1"})
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise WeatherTimeoutError("The weather service timed out") from exc
        except HTTPError as exc:
            raise WeatherProviderError(f"The weather service returned HTTP {exc.code}") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise WeatherTimeoutError("The weather service timed out") from exc
            raise WeatherProviderError("The weather service could not be reached") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise WeatherProviderError("The weather service returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise WeatherProviderError("The weather service returned an invalid response")
        if payload.get("error"):
            raise WeatherProviderError(str(payload.get("reason") or "Weather lookup failed"))
        return payload

    def _parse_result(
        self,
        request: WeatherRequest,
        location: dict[str, Any],
        forecast: dict[str, Any],
    ) -> WeatherResult:
        daily = forecast.get("daily") or {}
        dates = daily.get("time") or []
        try:
            index = dates.index(request.target_date.isoformat())
            weather_code = int(daily["weather_code"][index])
            minimum = float(daily["temperature_2m_min"][index])
            maximum = float(daily["temperature_2m_max"][index])
            precipitation = daily.get("precipitation_probability_max", [None])[index]
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            raise WeatherProviderError("No forecast was returned for the requested date") from exc

        current = forecast.get("current") or {}
        current_date = str(current.get("time", ""))[:10]
        use_current = current_date == request.target_date.isoformat()
        observed_at = None
        if use_current and current.get("time"):
            try:
                observed_at = datetime.fromisoformat(str(current["time"]))
            except ValueError:
                observed_at = None

        admin = location.get("admin1")
        country = location.get("country")
        label_parts = [str(location.get("name") or request.location)]
        for part in (admin, country):
            if part and str(part) not in label_parts:
                label_parts.append(str(part))
        return WeatherResult(
            location=", ".join(label_parts),
            target_date=request.target_date,
            condition=_weather_code_label(weather_code),
            temperature_c=_optional_float(current.get("temperature_2m")) if use_current else None,
            apparent_temperature_c=(
                _optional_float(current.get("apparent_temperature")) if use_current else None
            ),
            minimum_temperature_c=minimum,
            maximum_temperature_c=maximum,
            precipitation_probability_percent=(
                int(precipitation) if precipitation is not None else None
            ),
            wind_speed_kph=(
                _optional_float(current.get("wind_speed_10m")) if use_current else None
            ),
            observed_at=observed_at,
            retrieved_at=self._clock(),
        )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _weather_code_label(code: int) -> str:
    if code == 0:
        return "clear"
    if code in {1, 2}:
        return "partly cloudy"
    if code == 3:
        return "overcast"
    if code in {45, 48}:
        return "foggy"
    if code in {51, 53, 55, 56, 57}:
        return "drizzly"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rainy"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snowy"
    if code in {95, 96, 99}:
        return "thunderstorms"
    return "mixed conditions"
