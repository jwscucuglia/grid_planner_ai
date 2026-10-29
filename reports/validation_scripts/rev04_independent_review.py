"""Independent REV04 review: small cases only; no production/test mutation."""
from __future__ import annotations

import ast
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / 'src'))
from kgp.contracts import *
from kgp.baselines import run_baseline_b0_individual, run_baseline_b1_geographic, run_baseline_b2_topological_greedy
from kgp.eligibility import check_service_zone_eligibility
from kgp.experiment import roundtrip_plan_via_json
from kgp.feasibility import verify_plan_feasibility, merge_intervals as independent_union
from kgp.metrics import merge_intervals as planner_union, compute_consumer_interruption_unions
from kgp.optimizer import run_kgp_optimizer, compute_exact_plan_objective, ObjectiveWeights
from kgp.scheduling import build_window_schedule
from kgp.topology import simulate_zone_intervention, TopologyValidationError, evaluate_maneuver_step
from kgp.validation import validate_problem_and_plan

checks = []
details = {}
def check(name, actual, expected):
    checks.append({'name': name, 'actual': actual, 'expected': expected, 'passed': actual == expected})

# Preserve the exact original counterexample inputs, but do not execute its
# historical assertions that a defect is present, nor its unsafe finish().
original = ROOT / 'reports/validation_scripts/rev03_audit/reproduce_remaining.py'
tree = ast.parse(original.read_text(encoding='utf-8'))
def original_input(name):
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    body = []
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets = {n.id for target in statement.targets for n in ast.walk(target) if isinstance(n, ast.Name)}
            if targets & {'cat', 'eligible'}:
                break
        body.append(statement)
    node.body = body + [ast.parse('return net, svc').body[0]]
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = dict(globals())
    exec(compile(module, str(original), 'exec'), namespace)
    return namespace[name]

phase_input = original_input('stage_thermal_case')
fallback_input = original_input('optional_transfer_fallback_case')
dg_input = original_input('dg_case')
def cat():
    return ResourceCatalog(crews={'C': CrewResource('C','C',('K',),(1,))}, planning_days=(1,))

def export_at_fixed_times(net, svc, resources):
    steps, physical = simulate_zone_intervention(net, 'Z', 9.25, 10.25)
    z = net.isolation_zones['Z']
    iv = CrewScheduleInterval('C',svc.service_id,'W',1,9.05,9.05,9.25,10.25,10.4,0.,0.,0.)
    win = PlannedInterruptionWindow('W',1,'Z',(svc.service_id,),9.25-z.prep_duration_hours,
        10.25+z.recomp_duration_hours,9.25,10.25,steps,(iv,),{},physical,
        tuple(sorted({c for s in steps for c in s.interrupted_consumers})))
    plan = roundtrip_plan_via_json(ExecutionPlan('P','REVIEW','REVIEW',0,windows=[win],completed_service_ids=(svc.service_id,)))
    return plan, verify_plan_feasibility(net,[svc],resources,plan)

# A1: overload and voltage-only failures in a positive isolation stage;
# controls verify that zero-duration failed stages do not block a valid plan.
for load, prep, expected in [(180.,.25,False),(90.,.25,True),(180.,0.,True),
                             (180.,.0001,True),(180.,.0002,False)]:
    net, svc = phase_input()
    net.nodes['L'] = replace(net.nodes['L'],load_kw=load)
    net.isolation_zones['Z'] = replace(net.isolation_zones['Z'],prep_duration_hours=prep)
    resources = cat()
    plan, report = export_at_fixed_times(net,svc,resources)
    label = f'A1_load_{load:g}_prep_{prep:g}'
    eligible = check_service_zone_eligibility(net,svc,'Z')[0]
    win, error = build_window_schedule(net,resources,'Z',1,'BUILD',[svc])
    check(label+'_structural_errors',validate_problem_and_plan(net,[svc],resources,plan),[])
    check(label+'_audit',report.is_feasible,expected)
    check(label+'_eligible',eligible,expected)
    check(label+'_scheduled',win is not None,expected)
    details[label] = {'stages':[{'stage':s.stage.value,'duration':s.end_hour-s.start_hour,
                                  'feasible':s.transfer_feasible,'loading':s.max_loading_pct} for s in plan.windows[0].maneuver_steps],
                      'violations':report.violations,'scheduler_error':error}

net,svc=phase_input()
for key, branch in list(net.branches.items()):
    net.branches[key]=replace(branch,max_capacity_kw=1000.,r_ohm=80. if key=='B' else 0.,x_ohm=0.)
plan, report = export_at_fixed_times(net,svc,cat())
check('A1_voltage_only_rejected',report.is_feasible,False)
check('A1_voltage_failure_is_isolation',
      [s.stage.value for s in plan.windows[0].maneuver_steps if not s.transfer_feasible],['ISOLATION'])
details['A1_voltage']={'stages':[{'stage':s.stage.value,'loading':s.max_loading_pct,'drop':s.estimated_voltage_drop_pct,
                                 'feasible':s.transfer_feasible} for s in plan.windows[0].maneuver_steps],
                       'violations':report.violations}

for load,expected in [(180.,False),(90.,True)]:
    net,svc=phase_input()
    net.nodes['L']=replace(net.nodes['L'],load_kw=load)
    net.isolation_zones['Z']=replace(net.isolation_zones['Z'],tie_switches_closed=(),requires_transfer_validation=False)
    plan,report=export_at_fixed_times(net,svc,cat())
    check(f'A1_no_tie_load_{load:g}_audit',report.is_feasible,expected)
    check(f'A1_no_tie_load_{load:g}_schedule',build_window_schedule(net,cat(),'Z',1,'BUILD',[svc])[0] is not None,expected)

# A4: optional transfer failures are audited with the unused tie kept open;
# a mandatory failed transfer is rejected rather than silently abandoned.
for failure in ('overload','permission','missing_parameters'):
    for required in (False,True):
        net,svc=fallback_input(required)
        if failure!='overload':
            net.branches['TIE']=replace(net.branches['TIE'],max_capacity_kw=1000.)
        if failure=='permission':
            net.switches['ST']=replace(net.switches['ST'],is_maneuverable=False)
        if failure=='missing_parameters':
            net.isolation_zones['Z']=replace(net.isolation_zones['Z'],transfer_params_complete=False)
        resources=cat()
        label=f'A4_{failure}_required_{required}'
        eligible,reason=check_service_zone_eligibility(net,svc,'Z')
        win,error=build_window_schedule(net,resources,'Z',1,'W',[svc])
        check(label+'_eligible',eligible,not required)
        check(label+'_scheduled',win is not None,not required)
        if win is not None:
            plan=roundtrip_plan_via_json(ExecutionPlan('P','REVIEW','A4',0,windows=[win],completed_service_ids=('WORK',)))
            report=verify_plan_feasibility(net,[svc],resources,plan)
            check(label+'_verified',report.is_feasible,True)
            check(label+'_actual_closed_ties',[list(s.closed_switches) for s in win.maneuver_steps],[[],[],[]])
            check(label+'_maneuvers',win.physical_maneuvers_count,2)
        else:
            try:
                simulate_zone_intervention(net,'Z',9.25,10.25)
            except TopologyValidationError:
                rejected=True
            else:
                rejected=False
            check(label+'_direct_simulation_rejected',rejected,True)
        details[label]={'eligibility_reason':reason,'scheduler_error':error}

# A2 is a scope gate, not a demand for implementation of a DG dispatch model.
net,svc=dg_input()
resources=cat()
for method in (run_baseline_b0_individual,run_baseline_b1_geographic,run_baseline_b2_topological_greedy,run_kgp_optimizer):
    plan=method(net,[svc],resources,'DG_REVIEW')
    check('A2_'+method.__name__+'_status',plan.solver_status,'UNSUPPORTED_INPUT')
    check('A2_'+method.__name__+'_audit',verify_plan_feasibility(net,[svc],resources,plan).is_feasible,False)
    check('A2_'+method.__name__+'_proof',plan.is_optimal_proven,False)

def timed_input(gap=0., duration=1., coupled=False):
    net=GridNetwork('TIME','v',0,nodes={'S':GridNode('S','S','F',0.,0.,phases=('A',),is_source=True),
        'N':GridNode('N','N','F',0.,0.,phases=('A',))},branches={'B':GridBranch('B','S','N','F',phases=('A',))},
        switches={'SW':GridSwitch('SW','B',SwitchState.CLOSED,SwitchState.CLOSED)},
        assets={'A':GridAsset('A','N',None,'F','TEST',phases=('A',))},consumers={'U':ConsumerUnit('U','N','A')},
        isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SW',),prep_duration_hours=0.,recomp_duration_hours=0.)})
    jobs=[ServiceOrder(k,k,ServiceType.PODA,('A',),True,0,1,1,duration,(k,),prep_hours=0.,recomp_hours=0.,
                       precedence_service_ids=('J1',) if coupled and k=='J2' else ()) for k in ('J1','J2')]
    resources=ResourceCatalog(crews={k:CrewResource(k,k,(k,),(1,),prep_hours=0.) for k in ('J1','J2')},planning_days=(1,))
    if coupled:
        win,error=build_window_schedule(net,resources,'Z',1,'W',jobs,earliest_window_start_hour=9.)
        if win is None: raise RuntimeError(error)
        wins=[win]
    else:
        wins=[]
        for idx,job in enumerate(jobs):
            win,error=build_window_schedule(net,resources,'Z',1,f'W{idx}',[job],earliest_window_start_hour=9.+idx*(duration+gap))
            if win is None: raise RuntimeError(error)
            wins.append(win)
    return net,jobs,resources,ExecutionPlan('T','REVIEW','TIME',0,windows=wins,completed_service_ids=('J1','J2'))

def shift_window(win,delta):
    return replace(win,**{k:getattr(win,k)+delta for k in ('start_hour','work_start_hour','work_end_hour','end_hour')},
        maneuver_steps=tuple(replace(s,start_hour=s.start_hour+delta,end_hour=s.end_hour+delta) for s in win.maneuver_steps),
        crew_intervals=tuple(replace(iv,**{k:getattr(iv,k)+delta for k in ('travel_start_hour','prep_start_hour','exec_start_hour','exec_end_hour','recomp_end_hour')}) for iv in win.crew_intervals))

net,jobs,resources,plan=timed_input()
plan.windows=[shift_window(plan.windows[0],.000049),shift_window(plan.windows[1],.000054)]
before=asdict(plan)
check('A5_original_subtick_audit',verify_plan_feasibility(net,jobs,resources,plan).is_feasible,False)
try:
    roundtrip_plan_via_json(plan)
except ValueError:
    rejected=True
else:
    rejected=False
check('A5_original_subtick_JSON_rejected',rejected,True)
check('A5_no_silent_input_normalization',asdict(plan),before)
for gap,count,objective in [(0.,1,193.2),(.0001,2,205.2)]:
    net,jobs,resources,plan=timed_input(gap)
    plan=roundtrip_plan_via_json(plan)
    report=verify_plan_feasibility(net,jobs,resources,plan)
    check(f'A5_gap_{gap}_audit',report.is_feasible,True)
    check(f'A5_gap_{gap}_interruptions',report.recalculated_total_interruptions,count)
    check(f'A5_gap_{gap}_union_count',len(compute_consumer_interruption_unions(plan)['U']),count)
    check(f'A5_gap_{gap}_CH',report.recalculated_consumer_hours,2.)
    check(f'A5_gap_{gap}_objective',compute_exact_plan_objective(plan,ObjectiveWeights(),jobs),objective)

# The shortest positive admissible services and their exactly adjacent windows
# must survive every union and audit (no epsilon may discard them).
net,jobs,resources,plan=timed_input(duration=.0001)
report=verify_plan_feasibility(net,jobs,resources,roundtrip_plan_via_json(plan))
check('A5_one_tick_jobs_feasible',report.is_feasible,True)
check('A5_one_tick_jobs_CH',report.recalculated_consumer_hours,.0002)
check('A5_one_tick_jobs_contiguous',report.recalculated_total_interruptions,1)

for boundary in ('catalog_start','catalog_end','max_duration','crew_start','crew_end'):
    net,jobs,resources,plan=timed_input()
    if boundary=='catalog_start': resources.window_start_hour=9.0001
    elif boundary=='catalog_end': resources.window_end_hour=10.9999
    elif boundary=='max_duration': resources.max_window_duration_hours=.9999
    elif boundary=='crew_start': resources.crews['J1']=replace(resources.crews['J1'],shift_start_hour=9.0001)
    else: resources.crews['J2']=replace(resources.crews['J2'],shift_end_hour=10.9999)
    check('A5_one_tick_boundary_'+boundary,verify_plan_feasibility(net,jobs,resources,plan).is_feasible,False)

net,jobs,resources,plan=timed_input(coupled=True)
win=plan.windows[0]
iv=next(iv for iv in win.crew_intervals if iv.service_id=='J2')
bad=replace(iv,travel_start_hour=iv.travel_start_hour-.0001,prep_start_hour=iv.prep_start_hour-.0001,
            exec_start_hour=iv.exec_start_hour-.0001,exec_end_hour=iv.exec_end_hour-.0001,
            recomp_end_hour=iv.recomp_end_hour-.0001)
plan.windows=[replace(win,crew_intervals=tuple(bad if v.service_id=='J2' else v for v in win.crew_intervals))]
report=verify_plan_feasibility(net,jobs,resources,plan)
check('A5_precedence_one_tick_violation_rejected',report.is_feasible,False)
check('A5_precedence_is_reported',any('preced' in v.lower() for v in report.violations),True)

# Third reference: brute occupancy of integer tick cells, independent of either
# interval-merging algorithm. It catches dropped positive gaps and adjacency.
rng=random.Random(20261029)
for index in range(1000):
    raw=[tuple(sorted(rng.sample(range(17),2))) for _ in range(rng.randrange(10))]
    occupied={tick for start,end in raw for tick in range(start,end)}
    runs=[]
    for tick in sorted(occupied):
        if runs and tick==runs[-1][1]: runs[-1]=(runs[-1][0],tick+1)
        else: runs.append((tick,tick+1))
    expected=[(tick/10000,end/10000) for tick,end in runs]
    hours=[(start/10000,end/10000) for start,end in raw]
    check(f'A5_brute_planner_union_{index}',planner_union(hours),expected)
    check(f'A5_brute_independent_union_{index}',independent_union(hours),expected)

paths=['src/kgp/topology.py','src/kgp/eligibility.py','src/kgp/scheduling.py','src/kgp/feasibility.py',
       'src/kgp/contracts.py','src/kgp/metrics.py','src/kgp/validation.py']
result={'review_kind':'new_independent_computational_review','checks_count':len(checks),
        'passed':sum(c['passed'] for c in checks),'failed':[c for c in checks if not c['passed']],
        'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        'original_fixture_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'details':details}
Path(__file__).with_name('results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ('source_sha256','details')},indent=2,ensure_ascii=False))
sys.exit(bool(result['failed']))
