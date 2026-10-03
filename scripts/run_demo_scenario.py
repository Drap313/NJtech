#!/usr/bin/env python
"""Primary end-to-end scenario in one deterministic command.

  .venv/bin/python scripts/run_demo_scenario.py            # incident only
  .venv/bin/python scripts/run_demo_scenario.py --approve  # + dispatcher approval and execution
  .venv/bin/python scripts/run_demo_scenario.py --explain  # + local-model explanation (vLLM)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cli import print_fleet, replay  # noqa: E402
from app.clock import SimClock  # noqa: E402
from app.config import RUNTIME_DIR  # noqa: E402
from app.db import Store  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--fleet", action="store_true", help="print the fleet after the scenario")
    a = ap.parse_args()
    store = Store(RUNTIME_DIR / "demo.db")
    replay(store, "primary", approve=a.approve, explain=a.explain)
    if a.fleet:
        print_fleet(store, SimClock(store))
