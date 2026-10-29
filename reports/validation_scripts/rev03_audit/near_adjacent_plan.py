"""Read-only adversarial valid-plan check for different interval-union tolerances."""
from pathlib import Path
from dataclasses import replace, asdict
import json,sys
ROOT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path('/workspace/scratch/1b1b52c852ec/grid_planner_ai_rev03_audit')
sys.path.insert(0,str(ROOT/'src'))
from kgp.contracts import *
from kgp.scheduling import build_window_schedule
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import compute_exact_plan_objective,ObjectiveWeights
from kgp.metrics import compute_consumer_interruption_unions
n=GridNetwork('N','v',0,nodes={'SRC':GridNode('SRC','SRC','F',0.,0.,phases=('A',),is_source=True),'N':GridNode('N','N','F',0.,0.,phases=('A',))},branches={'B':GridBranch('B','SRC','N','F',phases=('A',))},switches={'SW':GridSwitch('SW','B',SwitchState.CLOSED,SwitchState.CLOSED)},assets={'AS':GridAsset('AS','N',None,'F','TEST',phases=('A',))},consumers={'C':ConsumerUnit('C','N','AS')},isolation_zones={'Z':IsolationZoneSpec('Z','Z','F',('SW',),prep_duration_hours=0.,recomp_duration_hours=0.)})
s=[ServiceOrder(k,k,ServiceType.PODA,('AS',),True,0,1,1,1.,(k,),prep_hours=0.,recomp_hours=0.) for k in ('S1','S2')]
c=ResourceCatalog(crews={k:CrewResource(k,k,(k,),(1,),prep_hours=0.) for k in ('S1','S2')},planning_days=(1,))
w1,error=build_window_schedule(n,c,'Z',1,'W1',[s[0]],earliest_window_start_hour=9.)
w2,error=build_window_schedule(n,c,'Z',1,'W2',[s[1]],earliest_window_start_hour=w1.end_hour)
def shift(w,delta):
 return replace(w,**{k:getattr(w,k)+delta for k in ('start_hour','work_start_hour','work_end_hour','end_hour')},maneuver_steps=tuple(replace(t,start_hour=t.start_hour+delta,end_hour=t.end_hour+delta) for t in w.maneuver_steps),crew_intervals=tuple(replace(iv,**{k:getattr(iv,k)+delta for k in ('travel_start_hour','prep_start_hour','exec_start_hour','exec_end_hour','recomp_end_hour')}) for iv in w.crew_intervals))
p=ExecutionPlan('P','CUSTOM','S',0,windows=[shift(w1,.000049),shift(w2,.000054)],completed_service_ids=('S1','S2'))
p=plan_from_json(json.dumps(asdict(p),allow_nan=False))
r=verify_plan_feasibility(n,s,c,p)
w=ObjectiveWeights()
j=compute_exact_plan_objective(p,w,s)
crew=sum(iv.recomp_end_hour-iv.prep_start_hour+iv.travel_hours for win in p.windows for iv in win.crew_intervals)
jv=w.window_weight*r.recalculated_windows_count+w.consumer_hour_weight*r.recalculated_consumer_hours+w.repeat_consumer_weight*r.recalculated_repeat_consumers+w.physical_maneuver_weight*r.recalculated_physical_maneuvers+w.crew_hour_weight*crew
result={'strict_roundtrip_passed':True,'feasible':r.is_feasible,'violations':r.violations,'gap_hours':p.windows[1].start_hour-p.windows[0].end_hour,'optimizer_consumer_union':compute_consumer_interruption_unions(p),'verifier_repeat_consumers':r.recalculated_repeat_consumers,'verifier_consumer_hours':r.recalculated_consumer_hours,'optimizer_exact_J':j,'verifier_counters_J':round(jv,4),'objective_difference':round(j-jv,4),'limitation':'Subsecond exported times accepted by contracts; no published 48-plan evidence affected.'}
Path(__file__).with_suffix('.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
