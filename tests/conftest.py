import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clock import SimClock  # noqa: E402
from app.db import Store  # noqa: E402
from app.events.processor import Engine  # noqa: E402
from app.seed import seed  # noqa: E402
from app.state import Fleet  # noqa: E402

T0 = datetime.fromisoformat("2026-10-06T06:40:00-04:00")


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "t.db")
    clock = SimClock(store)
    start = seed(store)
    clock.set(start, paused=True)
    engine = Engine(store, clock)
    engine.sweep()
    clock.set(T0, paused=True)
    return store, clock, engine


@pytest.fixture
def fleet(env):
    return Fleet.load(env[0])


def at(hhmm: str, day: int = 6) -> datetime:
    return datetime.fromisoformat(f"2026-10-{day:02d}T{hhmm}:00-04:00")


def plus(dt: datetime, minutes: int) -> datetime:
    return dt + timedelta(minutes=minutes)
