from __future__ import annotations

import math
import json
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from statistics import median
from threading import RLock
from time import perf_counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


@dataclass(frozen=True, slots=True)
class LatencyEvent:
    stage: str
    duration_ms: float
    trace_id: str | None = None
    thread_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LatencyStats:
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float


class LatencyRecorder:
    """In-process structured timings with trace context and no user-content logging."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.enabled = enabled
        self._clock = clock
        self._events: list[LatencyEvent] = []
        self._lock = RLock()
        self._context: ContextVar[tuple[str | None, str | None]] = ContextVar(
            f"jarvis_latency_context_{id(self)}", default=(None, None)
        )

    @property
    def current_trace_id(self) -> str | None:
        return self._context.get()[0]

    @property
    def current_thread_id(self) -> str | None:
        return self._context.get()[1]

    @contextmanager
    def trace(self, trace_id: str, *, thread_id: str | None = None) -> Iterator[None]:
        current_trace, current_thread = self._context.get()
        token = self._context.set(
            (trace_id or current_trace, thread_id if thread_id is not None else current_thread)
        )
        try:
            yield
        finally:
            self._context.reset(token)

    @contextmanager
    def measure(self, stage: str, **metadata: Any) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = self._clock()
        try:
            yield
        finally:
            self.record_elapsed(stage, started, **metadata)

    def record_elapsed(self, stage: str, started: float, **metadata: Any) -> None:
        if not self.enabled:
            return
        self.record(stage, (self._clock() - started) * 1000, **metadata)

    def now(self) -> float:
        return self._clock()

    def record(self, stage: str, duration_ms: float, **metadata: Any) -> None:
        if not self.enabled:
            return
        trace_id, thread_id = self._context.get()
        event = LatencyEvent(
            stage=stage,
            duration_ms=max(0.0, duration_ms),
            trace_id=trace_id,
            thread_id=thread_id,
            metadata=metadata,
        )
        with self._lock:
            self._events.append(event)

    def checkpoint(self) -> int:
        with self._lock:
            return len(self._events)

    def events_since(self, checkpoint: int = 0) -> list[LatencyEvent]:
        with self._lock:
            return list(self._events[checkpoint:])

    def statistics(self, events: list[LatencyEvent] | None = None) -> dict[str, LatencyStats]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for event in events if events is not None else self.events_since():
            grouped[event.stage].append(event.duration_ms)
        return {
            stage: LatencyStats(
                count=len(values),
                p50_ms=median(values),
                p95_ms=sorted(values)[math.ceil(len(values) * 0.95) - 1],
                max_ms=max(values),
            )
            for stage, values in grouped.items()
        }

    def format_events(self, events: list[LatencyEvent]) -> str:
        totals: dict[str, float] = defaultdict(float)
        order: list[str] = []
        for event in events:
            if event.stage not in totals:
                order.append(event.stage)
            totals[event.stage] += event.duration_ms
        return " | ".join(f"{stage}={totals[stage]:.0f}ms" for stage in order)

    def format_statistics(self) -> str:
        stats = self.statistics()
        return " | ".join(
            f"{stage}: n={value.count}, p50={value.p50_ms:.0f}ms, "
            f"p95={value.p95_ms:.0f}ms"
            for stage, value in sorted(stats.items())
        )

    def report(self, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a content-free, machine-readable latency report."""
        events = self.events_since()
        statistics = self.statistics(events)
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(metadata or {}),
            "events": [
                {
                    "stage": event.stage,
                    "duration_ms": event.duration_ms,
                    "trace_id": event.trace_id,
                    "thread_id": event.thread_id,
                    "metadata": event.metadata,
                }
                for event in events
            ],
            "statistics": {
                stage: {
                    "count": value.count,
                    "p50_ms": value.p50_ms,
                    "p95_ms": value.p95_ms,
                    "max_ms": value.max_ms,
                }
                for stage, value in sorted(statistics.items())
            },
        }

    def write_report(
        self,
        path: str | Path,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.report(metadata=metadata), indent=2) + "\n",
            encoding="utf-8",
        )
        return destination
