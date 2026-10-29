"""Regressões adversariais da reauditoria REV02, sem depender de seus scripts."""
from dataclasses import replace
from unittest.mock import patch
import json

import pytest

from kgp.baselines import run_baseline_b0_individual, _get_conflicting_zones_map
from kgp.contracts import *
from kgp.experiment import generate_scenario_instance, roundtrip_plan_via_json
from kgp.feasibility import verify_plan_feasibility
from kgp.scheduling import build_window_schedule


@pytest.fixture
def case():
    s = generate_scenario_instance(202601, 'EVAL')
    p = run_baseline_b0_individual(s.network, s.services, s.catalog, s.scenario_id, s.decision_day)
    assert verify_plan_feasibility(s.network, s.services, s.catalog, p).is_feasible
    return s, p


@pytest.mark.parametrize('field,value', [
    ('energized_nodes', ()), ('deenergized_nodes', ('NONEXISTENT',)),
    ('deenergized_assets', ('NONEXISTENT',)), ('active_sources', ()),
    ('transfer_feasible', False), ('max_loading_pct', 999999.0),
    ('estimated_voltage_drop_pct', 99999.0), ('step_order', -999),
])
def test_every_exported_electrical_evidence_field_is_checked(case, field, value):
    s, p = case
    w = p.windows[0]
    bad = replace(w.maneuver_steps[0], **{field: value})
    q = replace(p, windows=[replace(w, maneuver_steps=(bad,) + w.maneuver_steps[1:])] + p.windows[1:])
    r = verify_plan_feasibility(s.network, s.services, s.catalog, roundtrip_plan_via_json(q))
    assert not r.is_feasible, field
    assert not r.isolation_verified


def test_impact_reference_survives_common_simulator_fault(case):
    s, p = case
    q = replace(p, windows=[replace(w, interrupted_consumers=(), maneuver_steps=tuple(
        replace(t, interrupted_consumers=()) for t in w.maneuver_steps)) for w in p.windows])
    with patch('kgp.feasibility.simulate_zone_intervention', return_value=((), 0), create=True), \
         patch('kgp.topology.simulate_zone_intervention', return_value=((), 0)):
        r = verify_plan_feasibility(s.network, s.services, s.catalog, q)
    assert not r.is_feasible
    assert r.recalculated_consumer_hours > 0


def joint_case():
    nodes = {n: GridNode(n, n, 'F', 0, 0, is_source=n.startswith('SRC'))
             for n in ('SRC1', 'SRC2', 'N1', 'N3')}
    branches = {k: GridBranch(k, a, b, 'F') for k, a, b in
                [('B1', 'SRC1', 'N1'), ('B2', 'SRC2', 'N3'), ('B3', 'SRC2', 'N1')]}
    switches = {k: GridSwitch(k, b, st, st) for k, b, st in
                [('SW1', 'B1', SwitchState.CLOSED), ('SW2', 'B2', SwitchState.CLOSED),
                 ('SW3', 'B3', SwitchState.OPEN)]}
    assets = {k: GridAsset(k, n, None, 'F', 'TEST') for k, n in [('A1', 'N1'), ('A3', 'N3')]}
    # Break the normal source path before transferring N1 to SRC2. Each
    # individual window stays in the single-source radial domain; overlapping
    # Z2 still reenergizes A1 while S1 is working, which is the test's oracle.
    zones = {'Z1': IsolationZoneSpec('Z1', 'Z1', 'F', ('SW1',)),
             'Z2': IsolationZoneSpec('Z2', 'Z2', 'F', ('SW2', 'SW1'), ('SW3',), requires_transfer_validation=True)}
    net = GridNetwork('joint', 'test', 0, nodes=nodes, branches=branches, switches=switches,
                      assets=assets, isolation_zones=zones,
                      consumers={'U1': ConsumerUnit('U1', 'N1', 'A1'), 'U3': ConsumerUnit('U3', 'N3', 'A3')})
    services = [ServiceOrder(k, k, ServiceType.PODA, (a,), True, 0, 1, 1, 1, (skill,),
                            compatible_zone_ids=(z,)) for k, a, skill, z in
                [('S1', 'A1', 'ONE', 'Z1'), ('S3', 'A3', 'THREE', 'Z2')]]
    cat = ResourceCatalog(crews={k: CrewResource(k, k, (skill,), (1,))
                                for k, skill in [('C1', 'ONE'), ('C3', 'THREE')]}, planning_days=(1,))
    wins = [build_window_schedule(net, cat, z, 1, 'W' + str(i), [services[i]],
                                 earliest_window_start_hour=9.0)[0] for i, z in enumerate(zones)]
    assert all(wins)
    p = ExecutionPlan('joint', 'CUSTOM', 'joint', 0, windows=wins,
                      completed_service_ids=tuple(s.service_id for s in services))
    return net, services, cat, p


def test_disjoint_targets_that_reenergize_other_work_are_blocked():
    n, s, c, p = joint_case()
    r = verify_plan_feasibility(n, s, c, p)
    assert not r.is_feasible
    assert not r.isolation_verified
    assert any('sobrepos' in v.lower() for v in r.violations)
    assert _get_conflicting_zones_map(n)['Z1'] == {'Z1', 'Z2'}


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_rejected_at_construction_and_after_mutation(case, value):
    s, p = case
    with pytest.raises(ValueError):
        replace(p.windows[0].crew_intervals[0], exec_start_hour=value)
    iv = p.windows[0].crew_intervals[0]
    object.__setattr__(iv, 'exec_start_hour', value)
    r = verify_plan_feasibility(s.network, s.services, s.catalog, p)
    assert not r.is_feasible
    with pytest.raises(ValueError):
        roundtrip_plan_via_json(p)


@pytest.mark.parametrize('mutation', ['reversed', 'unknown_interval', 'window_id', 'duplicate_window', 'materials', 'completed'])
def test_structural_integrity(case, mutation):
    s, p = case
    w = p.windows[0]
    iv = w.crew_intervals[0]
    if mutation == 'reversed':
        q = replace(p, windows=[replace(w, crew_intervals=(replace(iv, prep_start_hour=iv.exec_start_hour + 2),) + w.crew_intervals[1:])] + p.windows[1:])
    elif mutation == 'unknown_interval':
        q = replace(p, windows=[replace(w, crew_intervals=w.crew_intervals + (replace(iv, service_id='UNKNOWN'),))] + p.windows[1:])
    elif mutation == 'window_id':
        q = replace(p, windows=[replace(w, crew_intervals=(replace(iv, window_id='WRONG'),) + w.crew_intervals[1:])] + p.windows[1:])
    elif mutation == 'duplicate_window':
        q = replace(p, windows=[replace(w, window_id=p.windows[1].window_id)] + p.windows[1:])
    elif mutation == 'materials':
        q = replace(p, windows=[replace(w, materials_used={'FAKE': 100})] + p.windows[1:])
    else:
        q = replace(p, completed_service_ids=())
    assert not verify_plan_feasibility(s.network, s.services, s.catalog, q).is_feasible


def test_unknown_network_reference_rejected_without_exception(case):
    s, p = case
    consumer = next(iter(s.network.consumers.values()))
    s.network.consumers[consumer.consumer_id] = replace(consumer, node_id='UNKNOWN')
    assert not verify_plan_feasibility(s.network, s.services, s.catalog, p).is_feasible


@pytest.mark.parametrize('raw', ['{"decision_day": NaN}', '{"decision_day": Infinity}',
                              '{"decision_day": 1e999}', '{"decision_day": 1, "decision_day": 2}'])
def test_strict_json_rejects_nonfinite_and_duplicate_keys(raw):
    from kgp.contracts import plan_from_json
    with pytest.raises(ValueError):
        plan_from_json(raw)


@pytest.mark.parametrize('field,value', [('decision_day', 0.5), ('decision_day', True),
                                       ('is_optimal_proven', 'false'), ('windows', {})])
def test_json_reader_does_not_coerce_types(case, field, value):
    from dataclasses import asdict
    from kgp.contracts import plan_from_json
    _, p = case
    data = asdict(p)
    data[field] = value
    with pytest.raises(ValueError):
        plan_from_json(json.dumps(data, allow_nan=False))


def test_global_timeline_checks_transfer_and_recomposition_boundaries():
    from kgp.feasibility import _global_reference_timeline
    n, s, c, p = joint_case()
    _, violations = _global_reference_timeline(n, p, {svc.service_id: svc for svc in s})
    assert any('A1' in v and 'global' in v for v in violations)
    # Restoration before the next isolation at the same timestamp is safe.
    first = p.windows[0]
    second, err = build_window_schedule(n, c, 'Z2', 1, 'W1', [s[1]],
                                       earliest_window_start_hour=first.end_hour)
    assert second, err
    serial = replace(p, windows=[first, second])
    assert verify_plan_feasibility(n, s, c, serial).is_feasible


@pytest.mark.parametrize('field', ['solver_gap', 'solver_time_sec', 'solver_objective'])
def test_negative_solver_values_are_rejected(case, field):
    s, p = case
    q = replace(p, **{field: -1.0})
    assert not verify_plan_feasibility(s.network, s.services, s.catalog, q).is_feasible
