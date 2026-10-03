"""
Configurable cost model.

Every rate below is a placeholder default, not a sourced industry figure --
tune them in config/cost_model.py (or load from YAML later) before trusting
any dollar output. They exist so the SOLVER has a consistent, swappable cost
surface to rank options against; the ranking logic doesn't change when the
numbers do.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    cost_per_loaded_mile: float = 1.10
    cost_per_deadhead_mile: float = 1.10
    driver_hourly_cost: float = 32.0
    overtime_multiplier: float = 1.5
    fuel_cost_per_mile: float = 0.55
    relay_handling_cost: float = 150.0
    disruption_penalty: float = 40.0        # fixed friction cost of any plan change
    minimum_target_margin_pct: float = 0.12


DEFAULT_COST_MODEL = CostModel()


def incremental_cost(
    *,
    deadhead_miles: float = 0.0,
    additional_driver_minutes: float = 0.0,
    overtime_minutes: float = 0.0,
    additional_fuel_miles: float = 0.0,
    includes_relay: bool = False,
    expected_late_penalty: float = 0.0,
    model: CostModel = DEFAULT_COST_MODEL,
) -> float:
    """
    incremental cost =
        deadhead cost
      + additional driver cost
      + overtime
      + additional fuel
      + relay cost
      + expected late penalty
      + operational disruption penalty
    """
    deadhead_cost = deadhead_miles * model.cost_per_deadhead_mile
    additional_driver_cost = (additional_driver_minutes / 60) * model.driver_hourly_cost
    overtime_cost = (overtime_minutes / 60) * model.driver_hourly_cost * model.overtime_multiplier
    additional_fuel_cost = additional_fuel_miles * model.fuel_cost_per_mile
    relay_cost = model.relay_handling_cost if includes_relay else 0.0

    return round(
        deadhead_cost
        + additional_driver_cost
        + overtime_cost
        + additional_fuel_cost
        + relay_cost
        + expected_late_penalty
        + model.disruption_penalty,
        2,
    )


def estimated_load_margin(load_revenue: float, total_cost: float) -> float:
    return round(load_revenue - total_cost, 2)
