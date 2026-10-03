"""Quick sanity check: run this after cloning to confirm the engine works
end to end before building anything on top of it."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.fleet_state import load_fleet_state
from app.feasibility.assignment import evaluate_assignment
from app.optimization.alternatives import find_costed_alternatives

fs = load_fleet_state()
print(f"Loaded fleet: {len(fs.drivers)} drivers, {len(fs.loads)} loads, "
      f"{len(fs.assignments)} active assignments\n")

d, ld = fs.driver("ramirez"), fs.load("4417")
v, t = fs.vehicle("V-01"), fs.trailer("TR-01")
result = evaluate_assignment(d, ld, v, t, proposed_start=fs.now, now=fs.now)
print(f"Ramirez on load 4417: legal={result['legally_compliant']} "
      f"feasible={result['operationally_feasible']}")
print(f"  reason: {result['reason']}\n")

plans = find_costed_alternatives("A-4417", fs)
print(f"Recovery options for load 4417 ({len(plans)}):")
for p in plans:
    tag = " <-- RECOMMENDED" if p.get("recommended") else ""
    print(f"  [{p['type']}] cost=${p['added_cost']} delay={p['delivery_delay_minutes']}min "
          f"risk={p['schedule_risk']} score={p['score']}{tag}")

print("\nSmoke test passed.")
