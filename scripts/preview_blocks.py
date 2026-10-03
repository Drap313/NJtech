"""Smoke test without Slack: render the demo incident's Block Kit JSON.

Run: python scripts/preview_blocks.py
Paste the output into https://app.slack.com/block-kit-builder to preview.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.slack import renderer  # noqa: E402
from app.core.client import FakeCore  # noqa: E402

core = FakeCore()
incident = core.open_demo_incident()
blocks = renderer.incident_blocks(incident)
print(json.dumps({"blocks": blocks}, indent=2, default=str))

trace = core.get_decision_trace(incident.incident_id)
print("\n--- decision trace ---", file=sys.stderr)
for step in trace:
    print(f"  {step}", file=sys.stderr)
