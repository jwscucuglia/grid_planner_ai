"""Contraprovas elétricas A1/A2/A4 da auditoria independente de f21690e."""
from dataclasses import replace
import pytest
from kgp.contracts import *
from kgp.topology import simulate_zone_intervention, evaluate_maneuver_step
from kgp.scheduling import build_window_schedule
from kgp.eligibility import check_service_zone_eligibility
from kgp.feasibility import verify_plan_feasibility, _independent_phase_transfer_check, _independent_energized_phases_and_sources
from kgp.experiment import roundtrip_plan_via_json
from kgp.optimizer import run_kgp_optimizer


def phase_case(load=180.):
    nodes = {i: GridNode(i, i, 'F', 0., 0., ph, is_source=source, load_kw=demand)
             for i, ph, source, demand in [('S', ('A','B'), True, 0.), ('T', ('A',), True, 0.),
                                            ('L', ('A','B'), False, load), ('X', ('A','B'), False, 0.)]}
    branches = {i: GridBranch(i, a, b, 'F', ph, max_capacity_kw=cap) for i,a,b,ph,cap in
                [('A','S','L',('A',),100.), ('B','S','L',('B',),100.),
                 ('TIE','T','L',('A',),100.), ('CUT','S','X',('A','B'),100.)]}
    switches = {i: GridSwitch(i,b,st,st) for i,b,st in [('SA','A',SwitchState.CLOSED),
                ('ST','TIE',SwitchState.OPEN), ('SC','CUT',SwitchState.CLOSED)]}
    n = GridNetwork('PHASE','test',0,nodes=nodes,branches=branches,switches=switches,
        assets={'AX':GridAsset('AX','X',None,'F','TEST',phases=('A','B'))},
        consumers={'U':ConsumerUnit('U','L',None,phase='B',avg_demand_kw=0.)},
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SC','SA'),('ST',),requires_transfer_validation=True)})
    svc = ServiceOrder('WORK','WORK',ServiceType.PODA,('AX',),True,0,1,1,1.,('K',),compatible_zone_ids=('Z',))
    cat = ResourceCatalog(crews={'C':CrewResource('C','C',('K',),(1,))},planning_days=(1,))
    return n,svc,cat


def exported_window(n,svc,cat):
    # Construct the historical unsafe export independently of the fixed scheduler.
    steps, physical = simulate_zone_intervention(n,'Z',9.25,10.25)
    iv = CrewScheduleInterval('C',svc.service_id,'W',1,9.,9.05,9.25,10.25,10.4,0.,0.,0.)
    win = PlannedInterruptionWindow('W',1,'Z',(svc.service_id,),9.,10.5,9.25,10.25,
            steps,(iv,),{},physical,tuple(sorted({c for s in steps for c in s.interrupted_consumers})))
    return roundtrip_plan_via_json(ExecutionPlan('P','CUSTOM','A1',0,windows=[win],completed_service_ids=(svc.service_id,)))


def test_positive_isolation_overload_is_rejected_by_verifier_and_planners():
    n,svc,c = phase_case()
    p = exported_window(n,svc,c)
    assert p.windows[0].maneuver_steps[0].max_loading_pct == 180.
    assert not p.windows[0].maneuver_steps[0].transfer_feasible
    r = verify_plan_feasibility(n,[svc],c,p)
    assert not r.is_feasible
    assert not r.isolation_verified
    assert not check_service_zone_eligibility(n,svc,'Z')[0]
    assert build_window_schedule(n,c,'Z',1,'W',[svc])[0] is None


def test_admissible_phase_transfer_control_is_preserved():
    n,svc,c = phase_case(90.)
    p = exported_window(n,svc,c)
    assert verify_plan_feasibility(n,[svc],c,p).is_feasible
    assert check_service_zone_eligibility(n,svc,'Z')[0]
    assert build_window_schedule(n,c,'Z',1,'W',[svc])[0] is not None


def fallback_case(required=False):
    nodes={i:GridNode(i,i,'F',0.,0.,('A',),is_source=i in ('S','T'),load_kw=100. if i=='X' else 0.) for i in ('S','T','X')}
    n=GridNetwork('FALLBACK','test',0,nodes=nodes,
        branches={'CUT':GridBranch('CUT','S','X','F',('A',)), 'TIE':GridBranch('TIE','T','X','F',('A',),max_capacity_kw=10.)},
        switches={'SC':GridSwitch('SC','CUT',SwitchState.CLOSED,SwitchState.CLOSED), 'ST':GridSwitch('ST','TIE',SwitchState.OPEN,SwitchState.OPEN)},
        assets={'AX':GridAsset('AX','X',None,'F','TEST',phases=('A',))}, consumers={'U':ConsumerUnit('U','X','AX',phase='A',avg_demand_kw=0.)},
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SC',),('ST',),requires_transfer_validation=required)})
    svc=ServiceOrder('WORK','WORK',ServiceType.PODA,('AX',),True,0,1,1,1.,('K',),compatible_zone_ids=('Z',))
    cat=ResourceCatalog(crews={'C':CrewResource('C','C',('K',),(1,))},planning_days=(1,))
    return n,svc,cat


def test_abandoned_optional_transfer_verifies_actual_open_tie_state():
    n,svc,c=fallback_case()
    w,error=build_window_schedule(n,c,'Z',1,'W',[svc])
    assert w is not None,error
    assert all(not st.closed_switches for st in w.maneuver_steps)
    p=ExecutionPlan('P','CUSTOM','A4',0,windows=[w],completed_service_ids=('WORK',))
    assert verify_plan_feasibility(n,[svc],c,roundtrip_plan_via_json(p)).is_feasible
    opt=run_kgp_optimizer(n,[svc],c,'A4')
    assert verify_plan_feasibility(n,[svc],c,opt).is_feasible


def test_failed_required_transfer_never_silently_falls_back():
    n,svc,c=fallback_case(True)
    assert not check_service_zone_eligibility(n,svc,'Z')[0]
    assert build_window_schedule(n,c,'Z',1,'W',[svc])[0] is None
    p=run_kgp_optimizer(n,[svc],c,'A4_REQUIRED')
    assert not p.is_optimal_proven
    assert not verify_plan_feasibility(n,[svc],c,p).is_feasible


def test_direct_transfer_check_rejects_unsupported_dg_islanding():
    n,svc,c=fallback_case()
    n.allow_dg_islanding=True
    p=ExecutionPlan('P','CUSTOM','DG',0,unscheduled_service_ids=('WORK',))
    assert not verify_plan_feasibility(n,[svc],c,p).is_feasible
    step=evaluate_maneuver_step(n,1,ManeuverStage.EXECUTION,9.,10.,('SC',),('ST',),n.isolation_zones['Z'])
    assert not step.transfer_feasible
    assert 'suport' in step.notes.lower() or 'ilhamento' in step.notes.lower()
    assert build_window_schedule(n,c,'Z',1,'W',[svc])[0] is None
