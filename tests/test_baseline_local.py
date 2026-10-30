from dataclasses import replace

from kgp.baselines import run_baseline_b0_individual
from kgp.experiment import generate_scenario_instance
from kgp.contracts import ServiceOrder, ServiceType, ExecutionPlan
from kgp.scheduling import build_window_schedule
from kgp.feasibility import verify_plan_feasibility


def test_b2_local_zone_change_reduces_impact_and_preserves_portfolio():
    from kgp.baselines import _improve_b2_zones
    s = generate_scenario_instance(202601, 'EVAL')
    svc = ServiceOrder('LOCAL', 'Local', ServiceType.PODA, ('AST_A1_5A',), True, 0, 1, 1,
                       1.0, ('PODA',))
    # Deliberately broad isolation; local neighbor is the distal-only zone.
    zones = list(s.network.isolation_zones)
    viable = []
    for zid in zones:
        win, _ = build_window_schedule(s.network, s.catalog, zid, 1, 'W', [svc])
        if win:
            p = ExecutionPlan('P', 'B2', 'LOCAL', 0, windows=[win], completed_service_ids=('LOCAL',))
            f = verify_plan_feasibility(s.network, [svc], s.catalog, p)
            if f.is_feasible:
                viable.append((f.recalculated_consumer_hours, p))
    assert len(viable) >= 2
    before, plan = max(viable, key=lambda pair: pair[0])
    after = _improve_b2_zones(s.network, [svc], s.catalog, plan)
    report = verify_plan_feasibility(s.network, [svc], s.catalog, after)
    assert report.is_feasible
    assert after.completed_service_ids == plan.completed_service_ids
    assert report.recalculated_consumer_hours < before
    assert after.metadata['local_zone_search']['accepted']
