"""Physical admission must not choose an arbitrary tree in an energized mesh."""
from dataclasses import asdict, replace
import json

import pytest

from kgp.baselines import (run_baseline_b0_individual, run_baseline_b1_geographic,
                           run_baseline_b2_topological_greedy)
from kgp.contracts import (ConsumerUnit, CrewResource, ExecutionPlan, GridAsset,
    GridBranch, GridNetwork, GridNode, GridSwitch, IsolationZoneSpec,
    ResourceCatalog, ServiceOrder, ServiceType, SwitchState, plan_from_json)
from kgp.feasibility import (verify_plan_feasibility,
    _independent_energized_phases_and_sources, _independent_phase_transfer_check)
from kgp.optimizer import run_kgp_optimizer
from kgp.scheduling import build_window_schedule
from kgp.topology import (compute_energized_phases_by_node,
    verify_transfer_electrical_feasibility, evaluate_maneuver_step)
from kgp.contracts import ManeuverStage


METHODS = [run_baseline_b0_individual, run_baseline_b1_geographic,
           run_baseline_b2_topological_greedy, run_kgp_optimizer]


def radial_case(shape='parallel', reverse=False, load=100.):
    nodes = {name: GridNode(name, name, 'F', 0., 0., ('A',),
                            is_source=name == 'SRC', load_kw=load if name == 'L' else 0.)
             for name in ('SRC', 'W', 'L')}
    high = GridBranch('HIGH', 'SRC', 'L', 'F', ('A',), r_ohm=1., x_ohm=0., max_capacity_kw=1000.)
    low = replace(high, branch_id='LOW', r_ohm=.01, max_capacity_kw=1.)
    extras = [high]
    if shape == 'parallel':
        extras += [low]
    elif shape == 'triangle':
        nodes['M'] = GridNode('M', 'M', 'F', 0., 0., ('A',))
        extras += [replace(high, branch_id='MID1', to_node='M'),
                   replace(high, branch_id='MID2', from_node='M')]
    elif shape == 'self_loop':
        extras += [replace(high, branch_id='LOOP', from_node='L')]
    elif shape == 'multiple_sources':
        nodes['L'] = replace(nodes['L'], is_source=True)
    elif shape == 'dark_cycle':
        for name in ('D1', 'D2'):
            nodes[name] = GridNode(name, name, 'F', 0., 0., ('A',))
        extras += [replace(high, branch_id=name, from_node='D1', to_node='D2')
                   for name in ('DARK1', 'DARK2')]
    elif shape == 'separate_sources':
        nodes['T'] = GridNode('T', 'T', 'F', 0., 0., ('A',), is_source=True)
    elif shape == 'separate_phases':
        nodes['SRC'] = replace(nodes['SRC'], phases=('A', 'B'))
        nodes['L'] = replace(nodes['L'], phases=('A', 'B'))
        extras += [replace(low, phases=('B',), max_capacity_kw=1000.)]
    elif shape == 'low_radial':
        extras = [low]
    if reverse:
        extras.reverse()
    branches = {'CUT': GridBranch('CUT', 'SRC', 'W', 'F', ('A',), max_capacity_kw=1000.)}
    branches.update({branch.branch_id: branch for branch in extras})
    network = GridNetwork('RADIAL_DOMAIN', 'test', 0, nodes=nodes, branches=branches,
        switches={'SW': GridSwitch('SW', 'CUT', SwitchState.CLOSED, SwitchState.CLOSED)},
        assets={'AW': GridAsset('AW', 'W', None, 'F', 'TEST', phases=('A',))},
        consumers={'CW': ConsumerUnit('CW', 'W', 'AW', phase='A', avg_demand_kw=0.)},
        isolation_zones={'Z': IsolationZoneSpec('Z', 'zone', 'F', ('SW',),
                            prep_duration_hours=.25, recomp_duration_hours=.25)})
    service = ServiceOrder('WORK', 'work', ServiceType.PODA, ('AW',), True, 0, 1, 1,
                           1., ('K',), compatible_zone_ids=('Z',))
    catalog = ResourceCatalog(crews={'C': CrewResource('C', 'C', ('K',), (1,))}, planning_days=(1,))
    return network, [service], catalog


def electrical_checks(network, opened=('SW',), closed=()):
    phases, sources, _ = compute_energized_phases_by_node(network, set(opened), set(closed))
    own_phases, own_sources, own_branches = _independent_energized_phases_and_sources(network, set(opened), set(closed))
    zone = network.isolation_zones['Z']
    return (verify_transfer_electrical_feasibility(network, zone, set(opened), set(closed), phases, sources),
            _independent_phase_transfer_check(network, zone, set(opened), set(closed), own_phases, own_sources, own_branches))


@pytest.mark.parametrize('shape', ['parallel', 'triangle', 'self_loop', 'multiple_sources'])
@pytest.mark.parametrize('reverse', [False, True])
def test_both_physical_checks_reject_energized_nonradial_domain(shape, reverse):
    network, _, _ = radial_case(shape, reverse)
    for result in electrical_checks(network):
        assert result[0] is False
        assert 'radial' in result[3].lower()


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('reverse', [False, True])
def test_all_methods_reject_initial_mesh_without_erasing_mandatory_order(method, reverse):
    network, services, catalog = radial_case('parallel', reverse)
    plan = plan_from_json(json.dumps(asdict(method(network, services, catalog, 'mesh')), allow_nan=False))
    assert plan.solver_status == 'UNSUPPORTED_INPUT'
    assert plan.completed_service_ids == () and plan.windows == []
    assert plan.unscheduled_service_ids == ('WORK',)
    assert not plan.is_optimal_proven and plan.solver_bound is None and plan.solver_gap is None
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert not report.is_feasible
    assert any('radial' in message.lower() for message in report.violations)


def test_independent_verifier_rejects_forged_complete_plan_on_initial_mesh():
    network, services, catalog = radial_case('radial')
    plan = run_baseline_b0_individual(network, services, catalog, 'base')
    # Add a parallel conducting path while preserving the exported target/work.
    network.branches['OTHER'] = replace(network.branches['HIGH'], branch_id='OTHER')
    report = verify_plan_feasibility(network, services, catalog,
                                     plan_from_json(json.dumps(asdict(plan), allow_nan=False)))
    assert not report.is_feasible
    assert any('radial' in message.lower() for message in report.violations)


@pytest.mark.parametrize('shape', ['radial', 'dark_cycle', 'separate_sources', 'separate_phases'])
def test_radial_forests_and_dark_cycles_remain_admissible(shape):
    network, services, catalog = radial_case(shape)
    assert all(result[0] for result in electrical_checks(network))
    plan = run_baseline_b0_individual(network, services, catalog, shape)
    report = verify_plan_feasibility(network, services, catalog,
                                     plan_from_json(json.dumps(asdict(plan), allow_nan=False)))
    assert report.is_feasible, report.violations


@pytest.mark.parametrize('shape', ['parallel', 'multiple_sources'])
def test_zero_demand_does_not_make_unsupported_physical_domain_supported(shape):
    network, _, _ = radial_case(shape, load=0.)
    for result in electrical_checks(network):
        assert not result[0] and 'radial' in result[3].lower()


def test_radial_thermal_failure_is_still_rejected_for_physical_reason():
    network, _, _ = radial_case('low_radial')
    for result in electrical_checks(network):
        assert not result[0] and result[1] == 10000.
        assert 'radial' not in result[3].lower()


@pytest.mark.parametrize('mode', ['branch_out', 'switch_open', 'endpoint_out'])
def test_only_conducting_paths_on_live_endpoints_enter_domain_check(mode):
    network, _, _ = radial_case('parallel')
    if mode == 'branch_out':
        network.branches['LOW'] = replace(network.branches['LOW'], in_service=False)
    elif mode == 'switch_open':
        network.switches['LOW_SW'] = GridSwitch('LOW_SW', 'LOW', SwitchState.OPEN, SwitchState.OPEN)
    else:
        # The entire load component becomes dark; the work component is radial.
        network.nodes['L'] = replace(network.nodes['L'], in_service=False)
    assert all(result[0] for result in electrical_checks(network))


def test_switch_closure_is_checked_in_the_effective_stage_not_only_initially():
    network, services, catalog = radial_case('parallel')
    network.switches['TIE'] = GridSwitch('TIE', 'LOW', SwitchState.OPEN, SwitchState.OPEN)
    assert all(result[0] for result in electrical_checks(network))
    assert all(not result[0] and 'radial' in result[3].lower()
               for result in electrical_checks(network, closed=('TIE',)))
    step = evaluate_maneuver_step(network, 1, ManeuverStage.EXECUTION, 9., 10.,
                                  ('SW',), ('TIE',), network.isolation_zones['Z'])
    assert not step.transfer_feasible
    assert 'radial' in step.notes.lower()


def test_stage_without_zone_does_not_claim_physical_support_for_a_mesh():
    network, _, _ = radial_case('parallel')
    step = evaluate_maneuver_step(network, 1, ManeuverStage.EXECUTION, 9., 10.,
                                  ('SW',), ())
    assert not step.transfer_feasible
    assert 'radial' in step.notes.lower()


@pytest.mark.parametrize('required', [False, True])
def test_nonradial_transfer_is_never_executed_even_when_optional(required):
    network, services, catalog = radial_case('parallel')
    network.switches['TIE'] = GridSwitch('TIE', 'LOW', SwitchState.OPEN, SwitchState.OPEN)
    network.isolation_zones['Z'] = replace(network.isolation_zones['Z'],
        tie_switches_closed=('TIE',), requires_transfer_validation=required,
        transfer_params_complete=True)
    window, error = build_window_schedule(network, catalog, 'Z', 1, 'W', services)
    if required:
        assert window is None
    else:
        assert window is not None, error
        assert all(not stage.closed_switches for stage in window.maneuver_steps)
        plan = ExecutionPlan('P', 'CUSTOM', 'fallback', 0, windows=[window], completed_service_ids=('WORK',))
        report = verify_plan_feasibility(network, services, catalog,
                                         plan_from_json(json.dumps(asdict(plan), allow_nan=False)))
        assert report.is_feasible, report.violations
