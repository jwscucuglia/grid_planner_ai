"""Replanejamento a partir da decisão e publicação auditada do plano KGP."""

from dataclasses import replace

import pytest

from kgp.baselines import (
    run_baseline_b0_individual, run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy,
)
from kgp.contracts import (
    ConsumerUnit, CrewResource, GridAsset, GridBranch, GridNetwork, GridNode,
    GridSwitch, IsolationZoneSpec, ResourceCatalog, ServiceOrder, ServiceType,
    SwitchState,
)
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import run_kgp_optimizer
import kgp.optimizer as optimizer_module


METHODS = (
    run_baseline_b0_individual, run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy, run_kgp_optimizer,
)


def replanning_instance(crew_days=(1, 2)):
    net = GridNetwork("replanning", "1", 0)
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
        {"T": CrewResource("T", "team", ("s",), crew_days, prep_hours=.1)},
        {}, (1, 2), max_window_duration_hours=2.,
    )
    job = ServiceOrder(
        "J", "job", ServiceType.OBRA, ("A",), True, 0, 1, 2, .5,
        ("s",), compatible_zone_ids=("Z",), prep_hours=.1, recomp_hours=.1,
    )
    return net, cat, [job]


@pytest.mark.parametrize("method", METHODS)
def test_unsupported_dg_islanding_is_blocked_before_planning(method):
    net, cat, jobs = replanning_instance()
    net.allow_dg_islanding = True
    plan = method(net, jobs, cat, "unsupported_island", decision_day=2)
    assert plan.solver_status == "UNSUPPORTED_INPUT"
    assert plan.windows == []
    assert plan.completed_service_ids == ()
    assert plan.unscheduled_service_ids == ("J",)
    assert not plan.is_optimal_proven
    assert plan.solver_objective is None
    assert plan.solver_bound is None
    assert plan.solver_gap is None
    assert plan.metadata["unsupported_option"] == "allow_dg_islanding"
    assert "reason" in plan.metadata
    assert not verify_plan_feasibility(net, jobs, cat, plan).is_feasible


@pytest.mark.parametrize("method", METHODS)
def test_replanning_never_schedules_catalog_days_before_decision(method):
    net, cat, jobs = replanning_instance()
    plan = method(net, jobs, cat, "future", decision_day=2)
    assert [(w.day, w.service_ids) for w in plan.windows] == [(2, ("J",))]
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible


@pytest.mark.parametrize("method", METHODS)
def test_past_only_crews_cannot_complete_future_mandatory_order(method):
    net, cat, jobs = replanning_instance(crew_days=(1,))
    plan = method(net, jobs, cat, "past_crews", decision_day=2)
    assert plan.windows == []
    assert plan.completed_service_ids == ()
    assert plan.unscheduled_service_ids == ("J",)
    assert not verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert not plan.is_optimal_proven
    assert plan.solver_objective is None
    assert plan.solver_bound is None
    assert plan.solver_gap is None


@pytest.mark.parametrize("method", METHODS)
def test_expired_optional_order_is_omitted_instead_of_past_bundling(method):
    net, cat, jobs = replanning_instance()
    jobs.append(replace(jobs[0], service_id="O", mandatory=False, deadline_day=1))
    plan = method(net, jobs, cat, "expired_optional", decision_day=2)
    assert [(w.day, w.service_ids) for w in plan.windows] == [(2, ("J",))]
    assert plan.completed_service_ids == ("J",)
    assert plan.unscheduled_service_ids == ("O",)
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    if method is run_kgp_optimizer:
        # 90/window + .7 consumer-hour + 2*2.5 maneuvers + .7*.6 crew + 50 omission.
        assert plan.solver_objective == pytest.approx(146.12)
        assert plan.solver_bound == pytest.approx(146.12)
        assert plan.solver_gap == 0.
        assert plan.is_optimal_proven


@pytest.mark.parametrize("empty_portfolio", [False, True])
def test_raw_optimizer_rejects_initially_unsupplied_network_before_certification(empty_portfolio):
    net, cat, jobs = replanning_instance()
    net.nodes["DEAD"] = GridNode("DEAD", "isolated", "F", 0., 0.)
    net.consumers["DEAD_C"] = ConsumerUnit("DEAD_C", "DEAD", None)
    plan = run_kgp_optimizer(net, [] if empty_portfolio else jobs, cat, "invalid_network")
    assert not verify_plan_feasibility(net, [] if empty_portfolio else jobs, cat, plan).is_feasible
    assert plan.solver_status == "INVALID_PLAN_REJECTED"
    assert not plan.is_optimal_proven
    assert plan.solver_objective is None
    assert plan.solver_bound is None
    assert plan.solver_gap is None
    audit = plan.metadata["independent_verification"]
    assert audit["json_roundtrip_succeeded"]
    assert not audit["exported_is_feasible"]
    assert any("escopo" in issue.lower() for issue in audit["violations"])


def test_invalid_solver_candidate_prefers_verified_fallback_without_catalog_certificate(monkeypatch):
    net, cat, jobs = replanning_instance()
    real_generate = optimizer_module._generate_day_schedule_columns

    def corrupt_columns(*args, **kwargs):
        # Boundary fault injection: keep the real solver/model/schedules, but
        # corrupt the exported physical maneuver tally in each candidate.
        columns = real_generate(*args, **kwargs)
        for col in columns:
            col.windows = tuple(replace(w, physical_maneuvers_count=0) for w in col.windows)
        return columns

    monkeypatch.setattr(optimizer_module, "_generate_day_schedule_columns", corrupt_columns)
    plan = run_kgp_optimizer(net, jobs, cat, "corrupt_candidate", decision_day=2)
    assert verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    assert plan.solver_status == "FEASIBLE_FALLBACK_NO_BOUND"
    assert plan.solver_objective == pytest.approx(96.12)
    assert plan.solver_bound is None
    assert plan.solver_gap is None
    assert not plan.is_optimal_proven
    assert plan.metadata["rejected_candidate_verification"]["violations"]


def test_failed_json_export_cannot_publish_raw_optimality_claim(monkeypatch):
    import kgp.contracts as contracts_module
    net, cat, jobs = replanning_instance()

    def reject_export(raw):
        # Output-boundary fault injection; the raw independent checker remains real.
        raise ValueError("strict JSON export rejected")

    monkeypatch.setattr(contracts_module, "plan_from_json", reject_export)
    plan = run_kgp_optimizer(net, jobs, cat, "rejected_export", decision_day=2)
    assert plan.solver_status == "INVALID_PLAN_REJECTED"
    assert plan.solver_objective is None
    assert plan.solver_bound is None
    assert plan.solver_gap is None
    assert not plan.is_optimal_proven
    audit = plan.metadata["independent_verification"]
    assert audit["raw_is_feasible"]
    assert not audit["json_roundtrip_succeeded"]
    assert any("JSON" in issue for issue in audit["violations"])
