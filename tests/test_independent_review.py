"""Contraexemplos da revisão independente do núcleo, sem alterar produção."""
from dataclasses import replace
import json

import pytest

from kgp.baselines import run_baseline_b0_individual
from kgp.contracts import ConsumerUnit, GridNode, plan_from_json
from kgp.experiment import generate_scenario_instance
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics


@pytest.fixture
def core_case():
    scenario = generate_scenario_instance(202601, "EVAL")
    plan = run_baseline_b0_individual(scenario.network, scenario.services,
                                      scenario.catalog, scenario.scenario_id,
                                      scenario.decision_day)
    assert verify_plan_feasibility(scenario.network, scenario.services, scenario.catalog, plan).is_feasible
    return scenario, plan


@pytest.mark.parametrize("mutation", ["null_window", "wrong_decision_type", "null_service"])
def test_invalid_root_structure_returns_rejection_without_crash(core_case, mutation):
    s, p = core_case
    if mutation == "null_window":
        p.windows = [None]
    elif mutation == "wrong_decision_type":
        p.decision_day = "0"
    else:
        s.services = [None]
    report = verify_plan_feasibility(s.network, s.services, s.catalog, p)
    assert not report.is_feasible
    assert not report.isolation_verified
    assert report.violations


@pytest.mark.parametrize("field", ["network_switch_state", "exported_stage"])
def test_string_enum_in_direct_object_never_crashes_audit(core_case, field):
    s, p = core_case
    if field == "network_switch_state":
        switch = next(iter(s.network.switches.values()))
        s.network.switches[switch.switch_id] = replace(switch, current_state=switch.current_state.value)
    else:
        w = p.windows[0]
        bad = replace(w.maneuver_steps[0], stage="EXECUTION")
        p.windows[0] = replace(w, maneuver_steps=(bad,) + w.maneuver_steps[1:])
    report = verify_plan_feasibility(s.network, s.services, s.catalog, p)
    assert not report.is_feasible
    assert report.violations


def test_unrepresentable_float_integer_is_rejected_by_json_contract():
    with pytest.raises(ValueError):
        plan_from_json(json.dumps({"plan_id": "P", "method_name": "X", "scenario_id": "S",
                                  "decision_day": 0, "solver_time_sec": 10 ** 400}))


def test_inconsistent_solver_claim_does_not_become_metric_certificate(core_case):
    s, p = core_case
    p = replace(p, solver_status="OPTIMAL_ON_DISCRETE_COLUMNS", solver_objective=100.,
                solver_bound=0., solver_gap=0., is_optimal_proven=False)
    report = verify_plan_feasibility(s.network, s.services, s.catalog, p)
    metrics = compute_plan_metrics(s.network, s.services, p, report)
    assert not metrics.is_optimal_proven


def test_initially_unsupplied_consumer_is_explicitly_outside_programmed_outage_scope(core_case):
    s, _ = core_case
    s.network.nodes["INITIALLY_DEAD"] = GridNode("INITIALLY_DEAD", "isolated", "F", 0., 0.)
    s.network.consumers["INITIALLY_DEAD_C"] = ConsumerUnit("INITIALLY_DEAD_C", "INITIALLY_DEAD", None)
    p = run_baseline_b0_individual(s.network, s.services, s.catalog, s.scenario_id, s.decision_day)
    report = verify_plan_feasibility(s.network, s.services, s.catalog, p)
    assert not report.is_feasible
    assert not report.isolation_verified
    assert any("escopo" in v.lower() for v in report.violations)
