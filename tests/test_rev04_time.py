"""Operational time resolution and exact outage adjacency (REV04/A5)."""
from dataclasses import asdict, replace
import json

import pytest

from kgp.contracts import (
    ConsumerUnit, CrewResource, ExecutionPlan, GridAsset, GridBranch,
    GridNetwork, GridNode, GridSwitch, IsolationZoneSpec, ResourceCatalog,
    ServiceOrder, ServiceType, SwitchState, _decode_typed, plan_from_json,
)
from kgp.experiment import DEV_SEEDS, EVAL_SEEDS, build_reference_network, generate_scenario_instance
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_consumer_interruption_unions, merge_intervals
from kgp.optimizer import ObjectiveWeights, compute_exact_plan_objective
from kgp.scheduling import build_window_schedule


def timed_case(gap=0.0):
    """One consumer, two one-hour jobs, zero preparation/recomposition."""
    net = GridNetwork(
        'time', 'test', 0,
        nodes={'SRC': GridNode('SRC', 'source', 'F', 0., 0., phases=('A',), is_source=True),
               'N': GridNode('N', 'load', 'F', 0., 0., phases=('A',))},
        branches={'B': GridBranch('B', 'SRC', 'N', 'F', phases=('A',))},
        switches={'SW': GridSwitch('SW', 'B', SwitchState.CLOSED, SwitchState.CLOSED)},
        assets={'AS': GridAsset('AS', 'N', None, 'F', 'TEST', phases=('A',))},
        consumers={'C': ConsumerUnit('C', 'N', 'AS')},
        isolation_zones={'Z': IsolationZoneSpec('Z', 'zone', 'F', ('SW',),
                            prep_duration_hours=0., recomp_duration_hours=0.)},
    )
    jobs = [ServiceOrder(k, k, ServiceType.PODA, ('AS',), True, 0, 1, 1, 1.,
                         (k,), prep_hours=0., recomp_hours=0.) for k in ('S1', 'S2')]
    cat = ResourceCatalog(crews={k: CrewResource(k, k, (k,), (1,), prep_hours=0.)
                                for k in ('S1', 'S2')}, planning_days=(1,))
    first, err = build_window_schedule(net, cat, 'Z', 1, 'W1', jobs[:1], earliest_window_start_hour=9.)
    assert first, err
    second, err = build_window_schedule(net, cat, 'Z', 1, 'W2', jobs[1:],
                                       earliest_window_start_hour=first.end_hour + gap)
    assert second, err
    return net, jobs, cat, ExecutionPlan('time', 'CUSTOM', 'time', 0,
                                        windows=[first, second], completed_service_ids=('S1', 'S2'))


def shift_window(win, delta):
    return replace(
        win,
        **{name: getattr(win, name) + delta for name in
           ('start_hour', 'work_start_hour', 'work_end_hour', 'end_hour')},
        maneuver_steps=tuple(replace(step, start_hour=step.start_hour + delta,
                                     end_hour=step.end_hour + delta) for step in win.maneuver_steps),
        crew_intervals=tuple(replace(iv, **{name: getattr(iv, name) + delta for name in
                         ('travel_start_hour', 'prep_start_hour', 'exec_start_hour',
                          'exec_end_hour', 'recomp_end_hour')}) for iv in win.crew_intervals),
    )


def test_subtick_export_cannot_enter_verifier_or_strict_json_reader():
    net, jobs, cat, plan = timed_case()
    # Original audit witness: two individually valid-looking windows, 18 ms apart.
    plan.windows = [shift_window(plan.windows[0], .000049), shift_window(plan.windows[1], .000054)]
    raw = json.dumps(asdict(plan), allow_nan=False)
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible
    assert any('0.0001' in error for error in report.violations)
    with pytest.raises(ValueError, match='0.0001'):
        plan_from_json(raw)
    assert json.dumps(asdict(plan), allow_nan=False) == raw  # no silent normalization


@pytest.mark.parametrize('target', ['window', 'step', 'crew'])
def test_nested_exported_operational_times_are_checked(target):
    net, jobs, cat, plan = timed_case()
    win = plan.windows[0]
    if target == 'window':
        bad = replace(win, end_hour=win.end_hour + .000005)
    elif target == 'step':
        step = win.maneuver_steps[1]
        bad = replace(win, maneuver_steps=(win.maneuver_steps[0],
                       replace(step, end_hour=step.end_hour + .000005)) + win.maneuver_steps[2:])
    else:
        iv = win.crew_intervals[0]
        bad = replace(win, crew_intervals=(replace(iv, travel_hours=.000005),))
    plan.windows[0] = bad
    assert not verify_plan_feasibility(net, jobs, cat, plan).is_feasible
    with pytest.raises(ValueError, match='0.0001'):
        plan_from_json(json.dumps(asdict(plan), allow_nan=False))


@pytest.mark.parametrize('target', ['switch', 'zone', 'service', 'crew', 'catalog'])
def test_input_operational_durations_and_bounds_outside_grid_fail_closed(target):
    net, jobs, cat, plan = timed_case()
    if target == 'switch':
        net.switches['SW'] = replace(net.switches['SW'], maneuver_duration_hours=.150005)
    elif target == 'zone':
        net.isolation_zones['Z'] = replace(net.isolation_zones['Z'], prep_duration_hours=.000005)
    elif target == 'service':
        jobs[0] = replace(jobs[0], duration_hours=1.000005)
    elif target == 'crew':
        cat.crews['S1'] = replace(cat.crews['S1'], shift_start_hour=8.000005)
    else:
        cat.window_start_hour = 8.000005
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible
    assert any('0.0001' in error for error in report.violations)


def test_binary_float_noise_is_accepted_and_nonoperational_numbers_keep_precision():
    net, jobs, cat, plan = timed_case()
    # These values arise from decimal arithmetic; accepting their ULP noise
    # must not license a physical epsilon that closes a positive supply gap.
    plan = replace(plan, windows=[shift_window(w, .1 + .2) for w in plan.windows],
                   solver_time_sec=.123456789, solver_gap=.000000123456,
                   solver_objective=193.123456789, solver_bound=193.123456788,
                   metadata={'unrounded_measurement': .000005})
    net.nodes['N'] = replace(net.nodes['N'], x=.123456789, y=.000005)
    raw = json.dumps(asdict(plan), allow_nan=False)
    restored = plan_from_json(raw)
    assert restored.windows[0].start_hour == plan.windows[0].start_hour
    assert restored.solver_time_sec == .123456789
    assert restored.solver_gap == .000000123456
    assert restored.metadata['unrounded_measurement'] == .000005
    assert verify_plan_feasibility(net, jobs, cat, restored).is_feasible


def test_interval_union_rejects_off_grid_endpoints_instead_of_erasing_short_gaps():
    with pytest.raises(ValueError, match='0.0001'):
        merge_intervals([(9., 10.), (10.000005, 11.000005)])


def test_interval_union_retains_one_tick_gap_and_exactly_adjacent_tiny_intervals():
    assert merge_intervals([(9., 10.), (10.0001, 11.)]) == [(9., 10.), (10.0001, 11.)]
    assert merge_intervals([(9., 10.), (10., 10.0001), (10.0001, 11.)]) == [(9., 11.)]
    assert merge_intervals([(9., 9.), (9.0001, 9.0002)]) == [(9.0001, 9.0002)]


def test_one_tick_electrical_evidence_tampering_is_rejected():
    net, jobs, cat, plan = timed_case()
    win = plan.windows[0]
    step = win.maneuver_steps[1]
    plan.windows[0] = replace(win, maneuver_steps=(win.maneuver_steps[0],
                               replace(step, end_hour=step.end_hour - .0001)) + win.maneuver_steps[2:])
    # A grid-valid forged endpoint introduces one tick of supply before W2.
    # Accepting it would restore the 12-point frequency/objective mismatch.
    assert len(compute_consumer_interruption_unions(plan)['C']) == 2
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible
    assert not report.isolation_verified


@pytest.mark.parametrize('field,value', [('duration_hours', 1.0001), ('prep_hours', .0001),
                                        ('recomp_hours', .0001)])
def test_one_tick_short_operational_service_requirement_is_not_completed(field, value):
    net, jobs, cat, plan = timed_case()
    jobs[0] = replace(jobs[0], **{field: value})
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible


def test_positive_subnormal_is_not_a_positive_operational_service_duration():
    net, jobs, cat, plan = timed_case()
    jobs[0] = replace(jobs[0], duration_hours=5e-324)
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert not report.is_feasible


@pytest.mark.parametrize('gap,count,objective', [(0., 1, 193.2), (.0001, 2, 205.2)])
def test_zero_prep_windows_recount_same_ticks_in_objective_and_independent_audit(gap, count, objective):
    net, jobs, cat, plan = timed_case(gap)
    plan = plan_from_json(json.dumps(asdict(plan), allow_nan=False))
    report = verify_plan_feasibility(net, jobs, cat, plan)
    assert report.is_feasible, report.violations
    assert report.recalculated_total_interruptions == count
    assert report.recalculated_repeat_consumers == count - 1
    assert report.recalculated_consumer_hours == 2.
    assert len(compute_consumer_interruption_unions(plan)['C']) == count
    assert compute_exact_plan_objective(plan, ObjectiveWeights(), jobs) == objective


def test_default_scenarios_with_duration_variations_stay_on_operational_grid():
    for split, seeds in [('DEV', DEV_SEEDS), ('EVAL', EVAL_SEEDS)]:
        for seed in seeds:
            scenario = generate_scenario_instance(seed, split)
            _decode_typed(scenario.network, GridNetwork, 'network')
            _decode_typed(scenario.catalog, ResourceCatalog, 'catalog')
            for factor in (.85, 1., 1.15):
                for job in scenario.services:
                    varied = replace(job, duration_hours=job.duration_hours * factor)
                    _decode_typed(varied, ServiceOrder, 'service')


def test_odd_tick_transfer_preparation_has_a_declared_integer_midpoint():
    net = build_reference_network()
    zone_id = 'Z_F1_TRUNK_TRANSFER'
    net.isolation_zones[zone_id] = replace(net.isolation_zones[zone_id], prep_duration_hours=.0001)
    job = ServiceOrder('S', 'S', ServiceType.PODA, ('AST_A1_2A',), True, 0, 1, 1,
                       1., ('PODA',), prep_hours=0., recomp_hours=0.)
    cat = ResourceCatalog(crews={'C': CrewResource('C', 'C', ('PODA',), (1,), prep_hours=0.)},
                          planning_days=(1,))
    win, error = build_window_schedule(net, cat, zone_id, 1, 'W', [job],
                                      earliest_window_start_hour=8.9999)
    assert win, error
    # floor(preparation_ticks / 2) assigns the odd tick to transfer, with
    # no sub-tick state boundary and no loss of the total preparation time.
    assert (win.maneuver_steps[0].start_hour, win.maneuver_steps[0].end_hour) == (8.9999, 8.9999)
    assert (win.maneuver_steps[1].start_hour, win.maneuver_steps[1].end_hour) == (8.9999, 9.)
    plan = ExecutionPlan('odd', 'CUSTOM', 'odd', 0, windows=[win], completed_service_ids=('S',))
    exported = plan_from_json(json.dumps(asdict(plan), allow_nan=False))
    report = verify_plan_feasibility(net, [job], cat, exported)
    assert report.is_feasible, report.violations
