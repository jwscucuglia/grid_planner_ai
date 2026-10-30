from dataclasses import replace, asdict
from pathlib import Path
import json, sys
REPO=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path('/workspace/scratch/1b1b52c852ec/grid_planner_ai_rev03_audit')
sys.path.insert(0,str(REPO/'src'))
from kgp.contracts import *
from kgp.feasibility import verify_plan_feasibility, _independent_energized_phases_and_sources, _independent_phase_transfer_check
from kgp.topology import evaluate_maneuver_step
from kgp.eligibility import check_service_zone_eligibility
from kgp.scheduling import build_window_schedule
from kgp.experiment import roundtrip_plan_via_json
from kgp.validation import validate_problem_and_plan
from kgp.optimizer import run_kgp_optimizer


def finish(net, service):
    cat=ResourceCatalog(crews={'C':CrewResource('C','C',('K',),(1,))},planning_days=(1,))
    win,err=build_window_schedule(net,cat,'Z',1,'W',[service],earliest_window_start_hour=9.)
    assert win,err
    plan=ExecutionPlan('P','CUSTOM','COUNTEREXAMPLE',0,windows=[win],completed_service_ids=(service.service_id,))
    plan=roundtrip_plan_via_json(plan)
    report=verify_plan_feasibility(net,[service],cat,plan)
    return cat, plan, report


def stage_thermal_case():
    # Each energized phase component is radial; no parallel source on any phase.
    nodes={i:GridNode(i,i,'F',0.,0.,ph,is_source=source,load_kw=load)
           for i,ph,source,load in [('S',('A','B'),True,0.),('T',('A',),True,0.),
                                  ('L',('A','B'),False,180.),('X',('A','B'),False,0.)]}
    branches={i:GridBranch(i,a,b,'F',ph,max_capacity_kw=cap)
              for i,a,b,ph,cap in [('A','S','L',('A',),100.),('B','S','L',('B',),100.),
                                   ('TIE','T','L',('A',),100.),('CUT','S','X',('A','B'),100.)]}
    switches={i:GridSwitch(i,b,st,st) for i,b,st in [('SA','A',SwitchState.CLOSED),
                ('ST','TIE',SwitchState.OPEN),('SC','CUT',SwitchState.CLOSED)]}
    net=GridNetwork('PHASE','test',0,nodes=nodes,branches=branches,switches=switches,
        assets={'AX':GridAsset('AX','X',None,'F','TEST',phases=('A','B'))},
        consumers={'U':ConsumerUnit('U','L',None,phase='B',avg_demand_kw=0.)},
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SC','SA'),('ST',),requires_transfer_validation=True)})
    svc=ServiceOrder('WORK','WORK',ServiceType.PODA,('AX',),True,0,1,1,1.,('K',),compatible_zone_ids=('Z',))
    cat,plan,report=finish(net,svc)
    init=evaluate_maneuver_step(net,0,ManeuverStage.EXECUTION,8.,9.,(),(),net.isolation_zones['Z'])
    return {'structural_errors':validate_problem_and_plan(net,[svc],cat,plan),
        'initial_loading_pct':init.max_loading_pct,'initial_transfer_feasible':init.transfer_feasible,
        'plan_stages':[{'stage':s.stage.value,'start':s.start_hour,'end':s.end_hour,'transfer_feasible':s.transfer_feasible,
                        'loading_pct':s.max_loading_pct,'notes':s.notes} for s in plan.windows[0].maneuver_steps],
        'verification':asdict(report)}


def dg_case():
    nodes={i:GridNode(i,i,'F',0.,0.,('A',),is_source=(i=='S'),distributed_gen_kw=(1000. if i=='DG' else 0.),load_kw=(1000. if i=='L' else 0.))
        for i in ('S','X','DG','L')}
    net=GridNetwork('DG','test',0,nodes=nodes,
        branches={'CUT':GridBranch('CUT','S','X','F',('A',),max_capacity_kw=10000.),
                  'CUT2':GridBranch('CUT2','X','L','F',('A',),max_capacity_kw=10000.),
                  'LIMIT':GridBranch('LIMIT','DG','L','F',('A',),max_capacity_kw=10.)},
        switches={'SC':GridSwitch('SC','CUT',SwitchState.CLOSED,SwitchState.CLOSED),
                  'SC2':GridSwitch('SC2','CUT2',SwitchState.CLOSED,SwitchState.CLOSED),
                  'ST':GridSwitch('ST','LIMIT',SwitchState.OPEN,SwitchState.OPEN)},
        assets={'AX':GridAsset('AX','X',None,'F','TEST',phases=('A',))},
        consumers={'U':ConsumerUnit('U','L',None,phase='A',avg_demand_kw=0.)},allow_dg_islanding=True,
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SC','SC2'),('ST',),requires_transfer_validation=True)})
    svc=ServiceOrder('WORK','WORK',ServiceType.PODA,('AX',),True,0,1,1,1.,('K',),compatible_zone_ids=('Z',))
    cat,plan,report=finish(net,svc)
    init=evaluate_maneuver_step(net,0,ManeuverStage.EXECUTION,8.,9.,(),(),net.isolation_zones['Z'])
    init_ph,init_sources,init_branches=_independent_energized_phases_and_sources(net,set(),set())
    init_check=_independent_phase_transfer_check(net,net.isolation_zones['Z'],set(),set(),init_ph,init_sources,init_branches)
    exec_ph,exec_sources,exec_branches=_independent_energized_phases_and_sources(net,{'SC','SC2'},{'ST'})
    return {'structural_errors':validate_problem_and_plan(net,[svc],cat,plan),
        'initial_normal_source_path':'S -> X -> L, capacity10000kW; DG -> L10kW tie initially OPEN',
        'initial_planner_max_loading_pct':init.max_loading_pct,'initial_independent_electrical_check':init_check,
        'initial_tie_state':net.switches['ST'].current_state.value,
        'initial_sources_at_load':sorted(init_sources['A']['L']),
        'execution_sources_at_load':sorted(exec_sources['A']['L']),
        'execution_closed_switches':[s.closed_switches for s in plan.windows[0].maneuver_steps if s.stage==ManeuverStage.EXECUTION],
        'branch_capacity_kw':10.,'downstream_load_kw':1000.,'generation_kw':1000.,
        'physical_min_loading_pct':10000.,
        'plan_stages':[{'stage':s.stage.value,'active_sources':s.active_sources,'transfer_feasible':s.transfer_feasible,'loading_pct':s.max_loading_pct} for s in plan.windows[0].maneuver_steps],
        'verification':asdict(report)}


def optional_transfer_fallback_case(required=False):
    # Transfer would restore work target itself and is invalid; no-transfer fallback isolates work.
    nodes={i:GridNode(i,i,'F',0.,0.,('A',),is_source=(i in ('S','T')),load_kw=(100. if i=='X' else 0.)) for i in ('S','T','X')}
    net=GridNetwork('OPTIONAL_TRANSFER','test',0,nodes=nodes,
        branches={'CUT':GridBranch('CUT','S','X','F',('A',)), 'TIE':GridBranch('TIE','T','X','F',('A',),max_capacity_kw=10.)},
        switches={'SC':GridSwitch('SC','CUT',SwitchState.CLOSED,SwitchState.CLOSED),
                  'ST':GridSwitch('ST','TIE',SwitchState.OPEN,SwitchState.OPEN)},
        assets={'AX':GridAsset('AX','X',None,'F','TEST',phases=('A',))},
        consumers={'U':ConsumerUnit('U','X','AX',phase='A',avg_demand_kw=0.)},
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SC',),('ST',),requires_transfer_validation=required)})
    svc=ServiceOrder('WORK','WORK',ServiceType.PODA,('AX',),True,0,1,1,1.,('K',),compatible_zone_ids=('Z',))
    eligible=check_service_zone_eligibility(net,svc,'Z')
    cat,plan,report=finish(net,svc)
    kgp=roundtrip_plan_via_json(run_kgp_optimizer(net,[svc],cat,'FALLBACK',0,time_limit_sec=2.))
    kgp_report=verify_plan_feasibility(net,[svc],cat,kgp)
    return {'eligible':eligible,'planned_execution_closed_switches':[s.closed_switches for s in plan.windows[0].maneuver_steps if s.stage==ManeuverStage.EXECUTION],
        'structural_errors':validate_problem_and_plan(net,[svc],cat,plan),'verification':asdict(report),
        'kgp_raw_status':kgp.solver_status,'kgp_raw_is_optimal_proven':kgp.is_optimal_proven,'kgp_raw_windows':len(kgp.windows),
        'kgp_verification':asdict(kgp_report)}


out={name:f() for name,f in [('nonexecution_thermal_violation_accepted',stage_thermal_case),('dg_branch_loading_ignored',dg_case),('optional_transfer_fallback_inconsistent',optional_transfer_fallback_case),('required_transfer_fallback_inconsistent',lambda:optional_transfer_fallback_case(True))]}
assert out['nonexecution_thermal_violation_accepted']['verification']['is_feasible']
assert out['nonexecution_thermal_violation_accepted']['initial_loading_pct']==90.
assert out['nonexecution_thermal_violation_accepted']['plan_stages'][0]['loading_pct']==180.
assert not out['nonexecution_thermal_violation_accepted']['plan_stages'][0]['transfer_feasible']
assert out['nonexecution_thermal_violation_accepted']['plan_stages'][0]['end']>out['nonexecution_thermal_violation_accepted']['plan_stages'][0]['start']
assert out['dg_branch_loading_ignored']['verification']['is_feasible']
assert out['dg_branch_loading_ignored']['initial_planner_max_loading_pct']==10.
assert out['dg_branch_loading_ignored']['initial_independent_electrical_check'][0:2]==(True,10.)
assert out['dg_branch_loading_ignored']['initial_tie_state']=='OPEN'
assert out['dg_branch_loading_ignored']['initial_sources_at_load']==['S']
assert out['dg_branch_loading_ignored']['execution_sources_at_load']==['DG']
assert out['dg_branch_loading_ignored']['execution_closed_switches']==[('ST',)]
assert all(s['loading_pct']==0. for s in out['dg_branch_loading_ignored']['plan_stages'])
assert not out['optional_transfer_fallback_inconsistent']['verification']['is_feasible']
assert not out['required_transfer_fallback_inconsistent']['verification']['is_feasible']
assert all(out[key]['kgp_raw_status']=='OPTIMAL_ON_DISCRETE_COLUMNS' and out[key]['kgp_raw_is_optimal_proven']
           and not out[key]['kgp_verification']['is_feasible'] for key in ('optional_transfer_fallback_inconsistent','required_transfer_fallback_inconsistent'))
Path(__file__).with_name('remaining_results.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps(out,indent=2,ensure_ascii=False))
