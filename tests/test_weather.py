from __future__ import annotations

import json
import socket
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from jarvis import Jarvis, SessionCoordinator
from jarvis.config import Settings
from jarvis.core.latency import LatencyRecorder
from jarvis.core.router import Route
from jarvis.integrations.weather import (
    MockWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherLocationNotFound,
    WeatherRequest,
    WeatherTimeoutError,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_open_meteo_geocodes_and_returns_typed_forecast() -> None:
    calls: list[tuple[str, float]] = []

    def opener(request, *, timeout):
        calls.append((request.full_url, timeout))
        if "geocoding-api" in request.full_url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "Seoul",
                            "admin1": "Seoul",
                            "country": "South Korea",
                            "latitude": 37.566,
                            "longitude": 126.9784,
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "current": {
                    "time": "2026-09-07T15:00",
                    "temperature_2m": 27.5,
                    "apparent_temperature": 29.0,
                    "weather_code": 2,
                    "wind_speed_10m": 8.4,
                },
                "daily": {
                    "time": ["2026-09-07"],
                    "weather_code": [61],
                    "temperature_2m_max": [29.2],
                    "temperature_2m_min": [21.1],
                    "precipitation_probability_max": [70],
                },
            }
        )

    provider = OpenMeteoWeatherProvider(
        timeout_seconds=3.5,
        opener=opener,
        clock=lambda: datetime(2026, 9, 7, 6, tzinfo=timezone.utc),
    )
    result = provider.get_weather(
        WeatherRequest(location="Seoul", target_date=date(2026, 9, 7))
    )

    assert result.location == "Seoul, South Korea"
    assert result.condition == "rainy"
    assert result.temperature_c == 27.5
    assert result.minimum_temperature_c == 21.1
    assert result.maximum_temperature_c == 29.2
    assert result.precipitation_probability_percent == 70
    assert result.source == "Open-Meteo"
    assert all(timeout == 3.5 for _, timeout in calls)
    assert parse_qs(urlparse(calls[0][0]).query) == {
        "name": ["Seoul"],
        "count": ["1"],
        "language": ["en"],
        "format": ["json"],
    }
    forecast_query = parse_qs(urlparse(calls[1][0]).query)
    assert forecast_query["timezone"] == ["auto"]
    assert forecast_query["start_date"] == ["2026-09-07"]
    assert forecast_query["end_date"] == ["2026-09-07"]


def test_open_meteo_reports_missing_location_and_timeout() -> None:
    missing = OpenMeteoWeatherProvider(
        opener=lambda request, timeout: FakeResponse({"results": []})
    )
    try:
        missing.get_weather(WeatherRequest(location="Atlantis", target_date=date.today()))
        raise AssertionError("Expected a missing-location error")
    except WeatherLocationNotFound:
        pass

    def timeout(request, *, timeout):
        raise socket.timeout()

    unavailable = OpenMeteoWeatherProvider(opener=timeout)
    try:
        unavailable.get_weather(
            WeatherRequest(location="Seoul", target_date=date.today())
        )
        raise AssertionError("Expected a timeout error")
    except WeatherTimeoutError:
        pass


def test_weather_uses_configured_default_and_records_read_latency() -> None:
    recorder = LatencyRecorder(enabled=True)
    weather = MockWeatherProvider(today=date.today())
    jarvis = Jarvis(
        settings=Settings(default_location="Seoul"),
        weather=weather,
        latency_recorder=recorder,
    )

    result = SessionCoordinator(jarvis).turn(
        "How is the weather today?", thread_id="default-weather"
    )

    assert result.execution_tier == 2
    assert "Seoul" in result.response
    assert "Source: Mock weather" in result.response
    assert weather.requests[0].location == "Seoul"
    assert any(event.stage == "tool.weather.read" for event in recorder.events_since())
    jarvis.close()


def test_weather_asks_once_for_location_and_uses_the_answer() -> None:
    weather = MockWeatherProvider(today=date.today())
    jarvis = Jarvis(settings=Settings(), weather=weather)
    session = SessionCoordinator(jarvis)

    question = session.turn("What's the forecast tomorrow?", thread_id="weather-location")
    result = session.turn("Busan, South Korea", thread_id="weather-location")

    assert question.needs_input
    assert question.interrupt_kind == "clarification"
    assert "location" in question.prompt.lower()
    assert not result.needs_input
    assert "Busan, South Korea" in result.response
    assert weather.requests == [
        WeatherRequest(location="Busan, South Korea", target_date=date.today() + timedelta(days=1))
    ]
    jarvis.close()


def test_weather_temporal_followup_reuses_location_and_stays_in_tier_two() -> None:
    weather = MockWeatherProvider(today=date.today())
    jarvis = Jarvis(settings=Settings(), weather=weather)
    session = SessionCoordinator(jarvis)

    first = session.turn("Weather in Seoul today", thread_id="weather-followup")
    second = session.turn("What about tomorrow?", thread_id="weather-followup")

    assert first.execution_tier == 2
    assert second.execution_tier == 2
    assert second.tier_reason == "weather context continuation"
    assert weather.requests[-1] == WeatherRequest(
        location="Seoul", target_date=date.today() + timedelta(days=1)
    )
    jarvis.close()


def test_rain_question_routes_to_weather_and_extracts_location() -> None:
    weather = MockWeatherProvider(today=date.today())
    jarvis = Jarvis(settings=Settings(), weather=weather)

    result = SessionCoordinator(jarvis).turn(
        "Will it rain in Seoul tomorrow?", thread_id="weather-rain"
    )

    assert result.execution_tier == 2
    assert weather.requests == [
        WeatherRequest(location="Seoul", target_date=date.today() + timedelta(days=1))
    ]
    assert "Source: Mock weather" in result.response
    jarvis.close()


def test_unsupported_fresh_data_is_not_sent_to_conversation_model() -> None:
    jarvis = Jarvis(settings=Settings())

    assert jarvis.select_route("What is the latest Bitcoin price?") is Route.UNSUPPORTED_FRESH_DATA
    result = SessionCoordinator(jarvis).turn(
        "What is the latest Bitcoin price?", thread_id="fresh-data"
    )

    assert result.execution_tier == 2
    assert "verified live source" in result.response
    assert "won't guess" in result.response
    jarvis.close()
