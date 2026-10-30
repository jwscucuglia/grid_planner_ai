"""Regressões da reauditoria D/E e referência enumerativa de uma instância pequena."""

from dataclasses import replace

import pytest

from kgp.contracts import (
    ConsumerUnit, CrewResource, GridAsset, GridBranch, GridNetwork, GridNode,
    GridSwitch, IsolationZoneSpec, ResourceCatalog, ServiceOrder, ServiceType,
    SwitchState,
)
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import (
    ObjectiveWeights, compute_exact_plan_objective, run_kgp_optimizer,
)
from kgp.scheduling import build_window_schedule
import kgp.optimizer as optimizer_module


def small_instance(mandatory=False):
    net = GridNetwork("enumeration", "1", 0)
    net.nodes = {
        "S": GridNode("S", "source", "F", 0., 0., is_source=True),
        "N": GridNode("N", "load", "F", 0., 0.),
    }
    net.branches = {"B": GridBranch("B", "S", "N", "F")}
    net.switches = {"SW": GridSwitch("SW", "B", SwitchState.CLOSED, SwitchState.CLOSED)}
    net.assets = {"A": GridAsset("A", "N", None, "F", "test")}
    net.consumers = {"C": ConsumerUnit("C", "N", "A")}
    net.isolation_zones = {
        "Z": IsolationZoneSpec("Z", "zone", "F", ("SW",),
                               prep_duration_hours=.1, recomp_duration_hours=.1),
    }
    cat = ResourceCatalog(
        {"T": CrewResource("T", "team", ("skill",), (1,), prep_hours=.1)},
        {}, (1,), max_window_duration_hours=1.,
    )
    job = ServiceOrder(
        "J", "job", ServiceType.OBRA, ("A",), mandatory, 0, 1, 1, .5,
        ("skill",), compatible_zone_ids=("Z",), prep_hours=.1, recomp_hours=.1,
    )
    return net, cat, [job]


def test_optional_omission_objective_bound_and_status_describe_same_problem():
    net, cat, jobs = small_instance()
    plan = run_kgp_optimizer(net, jobs, cat, "optional_omission")
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.unscheduled_service_ids == ("J",)
    assert plan.solver_objective == pytest.approx(50.)
    assert plan.solver_bound == pytest.approx(50.)
    assert plan.solver_gap == pytest.approx(0.)
    assert plan.solver_status == "OPTIMAL_ON_DISCRETE_COLUMNS"
    assert plan.is_optimal_proven
    assert plan.metadata["omitted_optional_service_ids"] == ["J"]
    assert plan.metadata["missing_mandatory_service_ids"] == []
    assert compute_exact_plan_objective(plan, ObjectiveWeights(), jobs) == pytest.approx(50.)


@pytest.mark.parametrize("optional_penalty", [0., 50., 250.])
def test_tiny_optional_problem_matches_exhaustive_serve_or_omit_reference(optional_penalty):
    net, cat, jobs = small_instance()
    weights = ObjectiveWeights(optional_unscheduled_penalty=optional_penalty)
    window, errors = build_window_schedule(net, cat, "Z", 1, "reference", jobs)
    assert window is not None, errors
    # Exhaustive alternatives: the single job is served in its only zone/day or omitted.
    # One consumer, one interruption; each positive stage is consecutive, so the
    # union is measured directly by its first/last interruption boundaries.
    interrupted = [s for s in window.maneuver_steps if "C" in s.interrupted_consumers]
    consumer_hours = max(s.end_hour for s in interrupted) - min(s.start_hour for s in interrupted)
    crew_hours = sum(iv.recomp_end_hour - iv.prep_start_hour + iv.travel_hours
                     for iv in window.crew_intervals)
    service_cost = (weights.window_weight + weights.consumer_hour_weight * consumer_hours
                    + weights.physical_maneuver_weight * 2
                    + weights.crew_hour_weight * crew_hours)
    expected = min(optional_penalty, service_cost)
    plan = run_kgp_optimizer(net, jobs, cat, "enumerated", weights=weights)
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.solver_objective == pytest.approx(expected, abs=1e-4)
    assert plan.solver_bound == pytest.approx(expected, abs=1e-4)
    assert plan.solver_gap == 0.
    assert plan.is_optimal_proven
    assert set(plan.completed_service_ids) == ({"J"} if service_cost < optional_penalty else set())


def test_mandatory_completion_cannot_be_exchanged_for_finite_penalty():
    net, cat, jobs = small_instance(mandatory=True)
    weights = ObjectiveWeights(window_weight=2_000_000.)
    plan = run_kgp_optimizer(net, jobs, cat, "expensive_required", weights=weights)
    assert plan.completed_service_ids == ("J",)
    assert plan.unscheduled_service_ids == ()
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.solver_objective > weights.unscheduled_penalty
    assert plan.solver_bound == plan.solver_objective
    assert plan.is_optimal_proven


def test_infeasible_mandatory_portfolio_has_no_feasible_objective_or_certificate():
    net, cat, jobs = small_instance(mandatory=True)
    jobs = [replace(jobs[0], required_skills=("unavailable",))]
    plan = run_kgp_optimizer(net, iter(jobs), cat, "required_infeasible")
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible
    assert not report.mandatory_portfolio_complete
    assert plan.unscheduled_service_ids == ("J",)
    assert plan.solver_status == "INFEASIBLE_ON_DISCRETE_COLUMNS"
    assert not plan.is_optimal_proven
    assert plan.solver_objective is None
    assert plan.solver_bound is None
    assert plan.solver_gap is None


def test_recount_requires_canonical_orders_when_omissions_exist():
    net, cat, jobs = small_instance()
    plan = run_kgp_optimizer(net, jobs, cat, "canonical_recount")
    with pytest.raises(ValueError, match="services"):
        compute_exact_plan_objective(plan, ObjectiveWeights())


def test_optional_predecessor_cannot_be_omitted_when_mandatory_successor_is_executed():
    net, cat, jobs = small_instance()
    predecessor = replace(jobs[0], service_id="P")
    successor = replace(jobs[0], service_id="S", mandatory=True,
                        precedence_service_ids=("P",))
    jobs = [predecessor, successor]
    plan = run_kgp_optimizer(net, jobs, cat, "required_optional_predecessor")
    assert set(plan.completed_service_ids) == {"P", "S"}
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.is_optimal_proven


def test_time_limit_preserves_incumbent_and_zero_dual_bound(monkeypatch):
    net, cat, jobs = small_instance(mandatory=True)
    real_milp = optimizer_module.milp

    def stopped_milp(**kwargs):
        result = real_milp(**kwargs)
        assert result.x is not None
        result.status = 1
        result.success = False
        result.mip_dual_bound = 0.
        result.mip_gap = 1.
        return result

    monkeypatch.setattr(optimizer_module, "milp", stopped_milp)
    plan = run_kgp_optimizer(net, jobs, cat, "bounded_incumbent")
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.solver_status == "FEASIBLE_BOUNDED"
    assert plan.solver_objective > 0.
    assert plan.solver_bound == 0.
    assert plan.solver_gap == 1.
    assert not plan.is_optimal_proven
    assert 'Ótimo global comprovado' not in plan.metadata['optimality_scope']


def test_time_limit_without_incumbent_preserves_feasible_fallback_and_known_bound(monkeypatch):
    from types import SimpleNamespace
    net, cat, jobs = small_instance(mandatory=True)
    monkeypatch.setattr(optimizer_module, 'milp', lambda **kwargs:
                        SimpleNamespace(x=None, status=1, mip_dual_bound=0., mip_gap=None, fun=None))
    plan = run_kgp_optimizer(net, jobs, cat, 'fallback_known_bound')
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.solver_objective == compute_exact_plan_objective(plan, ObjectiveWeights(), jobs)
    assert plan.solver_bound == 0.
    assert plan.solver_gap == 1.
    assert plan.solver_status == 'FEASIBLE_FALLBACK_BOUNDED'
    assert 'Inviabilidade' not in plan.metadata['optimality_scope']
    assert not plan.is_optimal_proven


def test_kgp_seeds_use_the_same_b1_radius_as_evaluated_comparator(monkeypatch):
    import kgp.experiment as experiment
    net, cat, jobs = small_instance(mandatory=True)
    scenario = experiment.ScenarioInstance('RADIUS', 'EVAL', 1, 'radius', 0, net, jobs, cat)
    real_b1 = optimizer_module.run_baseline_b1_geographic
    observed = []
    def spy(*args, **kwargs):
        observed.append(kwargs.get('radius_km', 1.25))
        return real_b1(*args, **kwargs)
    monkeypatch.setattr(experiment, 'run_baseline_b1_geographic', spy)
    monkeypatch.setattr(optimizer_module, 'run_baseline_b1_geographic', spy)
    experiment.evaluate_scenario_all_methods(scenario, b1_radius_km=0.75)
    assert observed == [0.75, 0.75]
