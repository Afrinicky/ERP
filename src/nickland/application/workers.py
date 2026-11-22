"""The service's background work, and when each piece last ran. PRD §9.4.

Scheduled reports and scheduled backups run inside the service. Each records a heartbeat
after every pass, so the health report can say not only that the work exists but that it is
running, and when it last succeeded or failed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Heartbeat:
    name: str
    interval_seconds: int
    last_pass: datetime | None = None
    last_error: str | None = None
    passes: int = 0


_LOCK = threading.Lock()
_WORKERS: dict[str, Heartbeat] = {}


def register(name: str, interval_seconds: int) -> None:
    with _LOCK:
        _WORKERS.setdefault(name, Heartbeat(name, interval_seconds))


def beat(name: str, at: datetime, *, error: str | None = None) -> None:
    with _LOCK:
        worker = _WORKERS.setdefault(name, Heartbeat(name, 60))
        worker.last_pass = at
        worker.last_error = error
        worker.passes += 1


def workers() -> list[Heartbeat]:
    with _LOCK:
        return [
            Heartbeat(**{slot: getattr(item, slot) for slot in Heartbeat.__slots__})
            for item in _WORKERS.values()
        ]


def reset() -> None:
    """For tests."""
    with _LOCK:
        _WORKERS.clear()


__all__ = ["Heartbeat", "beat", "register", "reset", "workers"]
