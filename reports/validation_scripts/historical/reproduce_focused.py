from dataclasses import replace, asdict
import sys
from pathlib import Path
REPO=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path('/workspace/scratch/1b1b52c852ec/grid_planner_ai_rev02_audit')
sys.path.insert(0,str(REPO/'src'))
import json
from unittest.mock import patch
from kgp.experiment import generate_scenario_instance
from kgp.baselines import run_baseline_b0_individual
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics
from kgp.scheduling import build_window_schedule
from kgp.topology import simulate_zone_intervention, evaluate_maneuver_step
from kgp.contracts import *

out={}
s=generate_scenario_instance(202601,'EVAL')
p=run_baseline_b0_individual(s.network,s.services,s.catalog,s.scenario_id,s.decision_day)
r=verify_plan_feasibility(s.network,s.services,s.catalog,p)
out['baseline']={'feasible':r.is_feasible,'consumer_hours':r.recalculated_consumer_hours}
# Tamper fields not compared by integrity validation.
mut=[]
for w in p.windows:
 st=tuple(replace(t, energized_nodes=(), deenergized_nodes=('NONEXISTENT',), deenergized_assets=('NONEXISTENT',), active_sources=(), transfer_feasible=False, max_loading_pct=999999.0, estimated_voltage_drop_pct=99999.0, step_order=-999) for t in w.maneuver_steps)
 mut.append(replace(w,maneuver_steps=st))
r=verify_plan_feasibility(s.network,s.services,s.catalog,replace(p,windows=mut))
out['tamper_exported_electrical_fields']={'feasible':r.is_feasible,'isolation_verified':r.isolation_verified,'violations':r.violations}
# Shared simulator loses all outages: verifier independently checks isolation but not consumer impacts.
def bad_sim(*args,**kwargs):
 st,phys=simulate_zone_intervention(*args,**kwargs)
 return tuple(replace(t,interrupted_consumers=()) for t in st),phys
mut=[replace(w,maneuver_steps=tuple(replace(t,interrupted_consumers=()) for t in w.maneuver_steps),interrupted_consumers=()) for w in p.windows]
with patch('kgp.feasibility.simulate_zone_intervention',bad_sim):
 r=verify_plan_feasibility(s.network,s.services,s.catalog,replace(p,windows=mut))
out['shared_simulator_outage_fault']={'feasible':r.is_feasible,'isolation_verified':r.isolation_verified,'consumer_hours':r.recalculated_consumer_hours,'violations':r.violations}
# Joint chronology interaction with disjoint zones/switches/deenergized assets.
nodes={n:GridNode(n,n,'F',0,0,is_source=n.startswith('SRC')) for n in ('SRC1','SRC2','N1','N3')}
branches={k:GridBranch(k,a,b,'F') for k,a,b in [('B1','SRC1','N1'),('B2','SRC2','N3'),('B3','SRC2','N1')]}
switches={k:GridSwitch(k,b,st,st) for k,b,st in [('SW1','B1',SwitchState.CLOSED),('SW2','B2',SwitchState.CLOSED),('SW3','B3',SwitchState.OPEN)]}
assets={k:GridAsset(k,n,None,'F','TEST') for k,n in [('A1','N1'),('A3','N3')]}
zones={'Z1':IsolationZoneSpec('Z1','Z1','F',('SW1',)),'Z2':IsolationZoneSpec('Z2','Z2','F',('SW2',),('SW3',),requires_transfer_validation=True)}
net=GridNetwork('joint','test',0,nodes=nodes,branches=branches,switches=switches,assets=assets,isolation_zones=zones,consumers={'C1':ConsumerUnit('C1','N1','A1'),'C3':ConsumerUnit('C3','N3','A3')})
services=[ServiceOrder(k,k,ServiceType.PODA,(a,),True,0,1,1,1,(skill,),compatible_zone_ids=(z,)) for k,a,skill,z in [('S1','A1','ONE','Z1'),('S3','A3','THREE','Z2')]]
crews={k:CrewResource(k,k,(skill,),(1,)) for k,skill in [('C1','ONE'),('C3','THREE')]}
cat=ResourceCatalog(crews=crews,planning_days=(1,))
w1,e1=build_window_schedule(net,cat,'Z1',1,'W1',services[:1],earliest_window_start_hour=9.0)
w2,e2=build_window_schedule(net,cat,'Z2',1,'W2',services[1:],earliest_window_start_hour=9.0)
assert w1 and w2,(e1,e2)
plan=ExecutionPlan('joint','CUSTOM','joint',0,windows=[w1,w2])
r=verify_plan_feasibility(net,services,cat,plan)
actual=evaluate_maneuver_step(net,1,ManeuverStage.EXECUTION,max(w1.work_start_hour,w2.work_start_hour),min(w1.work_end_hour,w2.work_end_hour),('SW1','SW2'),('SW3',))
out['joint_switch_state_interaction']={'feasible':r.is_feasible,'violations':r.violations,'isolation_verified':r.isolation_verified,'window_bounds':[[w.start_hour,w.end_hour] for w in plan.windows],'individual_dead_assets':[[t.deenergized_assets for t in w.maneuver_steps if t.stage==ManeuverStage.EXECUTION] for w in plan.windows],'combined_deenergized_assets':actual.deenergized_assets,'combined_energized_nodes':actual.energized_nodes,'unsafe_service':'S1 targets A1 at energized N1 during overlap'}
# Shared isolation defect is now caught by independent execution-state BFS.
wrong_svc=replace(services[0],target_assets=('A3',))
def bad_isolation_sim(*args,**kwargs):
 st,phys=simulate_zone_intervention(*args,**kwargs)
 return tuple(replace(t,deenergized_assets=tuple(net.assets)) for t in st),phys
with patch('kgp.feasibility.simulate_zone_intervention',bad_isolation_sim):
 r=verify_plan_feasibility(net,[wrong_svc],cat,replace(plan,windows=[w1]))
out['shared_simulator_isolation_fault_detected']={'feasible':r.is_feasible,'violations':r.violations}
# Malformed duration NaN can evade ordered comparisons (finite input contract absent).
q=replace(p,windows=[replace(w,crew_intervals=tuple(replace(iv,exec_start_hour=float('nan'),exec_end_hour=float('nan')) for iv in w.crew_intervals)) for w in p.windows])
r=verify_plan_feasibility(s.network,s.services,s.catalog,q)
out['nan_execution_bounds']={'feasible':r.is_feasible,'violations':r.violations}
Path(__file__).with_name('root_results.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps(out,indent=2,ensure_ascii=False))
