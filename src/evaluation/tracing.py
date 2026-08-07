"""单步骤 Trace 上下文。"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional


TraceSink = Callable[[dict[str, Any]], Optional[Awaitable[None]]]


class TraceRecorder:
    def __init__(self, run_id: str = "", sink: Optional[TraceSink] = None):
        self.run_id = run_id
        self.sink = sink
        self.events: list[dict[str, Any]] = []

    async def record(self, kind: str, name: str, **payload: Any) -> dict[str, Any]:
        event = {
            "trace_id": uuid.uuid4().hex,
            "run_id": self.run_id,
            "kind": kind,
            "name": name,
            "timestamp": time.time(),
            **payload,
        }
        self.events.append(event)
        if self.sink:
            pending = self.sink(event)
            if pending is not None:
                await pending
        return event

    @asynccontextmanager
    async def span(self, kind: str, name: str, **payload: Any) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        event = await self.record(kind, name, phase="start", **payload)
        try:
            yield event
        except Exception as exc:
            await self.record(
                kind,
                name,
                phase="error",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        else:
            await self.record(
                kind,
                name,
                phase="end",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )

    def as_json(self) -> str:
        return json.dumps(self.events, ensure_ascii=False)
