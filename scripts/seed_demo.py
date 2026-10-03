#!/usr/bin/env python
"""Reset the live database to the synthetic fleet (the API server uses this DB)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clock import SimClock  # noqa: E402
from app.config import DB_PATH  # noqa: E402
from app.db import Store  # noqa: E402
from app.events.processor import Engine  # noqa: E402
from app.seed import seed  # noqa: E402

if __name__ == "__main__":
    store = Store(DB_PATH)
    start = seed(store)
    clock = SimClock(store)
    clock.set(start, speed=1.0, paused=False)
    Engine(store, clock).sweep("SEED")
    print(f"Seeded {DB_PATH} at {start.isoformat()}; open incidents: {[i['id'] for i in store.list_incidents('OPEN')]}")
