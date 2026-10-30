"""Read-only review harness. Run: PYTHONDONTWRITEBYTECODE=1 python reproduce_adversarial.py.
No installation; original package files are never written. Outputs live here.
"""
import ast,copy,json,pathlib,sys
from dataclasses import replace
ROOT=pathlib.Path(__file__).resolve().parent
PACKAGE=pathlib.Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT.parent.parent/'grid_planner_ai_audit'
sys.path.insert(0,str(PACKAGE/'src'))
from kgp.experiment import generate_scenario_instance,evaluate_scenario_all_methods
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics
from kgp.topology import evaluate_maneuver_step
from kgp.scheduling import build_window_schedule
from kgp.eligibility import find_eligible_zones_for_service
from kgp.contracts import GridAsset,ManeuverStage,ResourceCatalog,ExecutionPlan

sc=generate_scenario_instance(202605,'EVAL')
p=copy.deepcopy(evaluate_scenario_all_methods(sc,b1_radius_km=.75)['B0'][0])
for win in p.windows:
    win.maneuver_steps=();win.interrupted_consumers=();win.physical_maneuvers_count=0
v=verify_plan_feasibility(sc.network,sc.services,sc.catalog,p)
m=compute_plan_metrics(sc.network,sc.services,p,v)
out={'empty_exported_impact':{'accepted':v.is_feasible,'violations':v.violations,
    'verifier_customer_hours':v.recalculated_consumer_hours,
    'metric_customer_hours':m.consumer_hours_total,'metric_switch_operations':m.physical_maneuvers_count}}
net=sc.network
net.assets['REVIEW_BRANCH']=GridAsset('REVIEW_BRANCH',None,'BR_A1_12','F1','VAO',('A','B','C'))
st=evaluate_maneuver_step(net,1,ManeuverStage.EXECUTION,9,10,('SW_A1_1',),())
out['branch_one_live_endpoint']={'asset_marked_deenergized':'REVIEW_BRANCH' in st.deenergized_assets,
    'upstream_energized':'N_A1_1' in st.energized_nodes,
    'downstream_deenergized':'N_A1_2' in st.deenergized_nodes}
sc=generate_scenario_instance(202601,'EVAL');net=sc.network;cat=sc.catalog
a=replace(sc.services[0],service_id='REVIEW_A',duration_hours=2,required_skills=('MANUT_GERAL',),
    required_crews_count=1,precedence_service_ids=(),ready_at_day=1,deadline_day=5,required_materials={})
b=replace(a,service_id='REVIEW_B');zid=find_eligible_zones_for_service(net,a)[0]
crews=list(cat.crews.values())[:2];windows=[]
for j,(svc,crew) in enumerate(zip([a,b],crews)):
    sub=ResourceCatalog(crews={crew.crew_id:crew},materials_inventory=cat.materials_inventory,
        planning_days=cat.planning_days,window_start_hour=8,window_end_hour=17,max_window_duration_hours=8)
    win,err=build_window_schedule(net,sub,zid,3,'REVIEW_W'+str(j),[svc],(),earliest_window_start_hour=9+j)
    assert win is not None,err
    windows.append(win)
p=ExecutionPlan('REVIEW','REVIEW','REVIEW',0,windows=windows)
v=verify_plan_feasibility(net,[a,b],cat,p)
out['same_zone_overlapping_windows']={'accepted':v.is_feasible,'violations':v.violations,
    'windows':[[w.start_hour,w.work_start_hour,w.work_end_hour,w.end_hour] for w in windows]}
svc=replace(a,required_crews_count=2);win=copy.deepcopy(windows[0]);iv1=win.crew_intervals[0]
iv2=replace(iv1,crew_id=crews[1].crew_id,travel_start_hour=iv1.travel_start_hour+2.5,
    prep_start_hour=iv1.prep_start_hour+2.5,exec_start_hour=iv1.exec_start_hour+2.5,
    exec_end_hour=iv1.exec_end_hour+2.5,recomp_end_hour=iv1.recomp_end_hour+2.5)
win.crew_intervals=(iv1,iv2);win.work_end_hour=iv2.exec_end_hour
win.end_hour=win.work_end_hour+net.isolation_zones[zid].recomp_duration_hours
v=verify_plan_feasibility(net,[svc],cat,ExecutionPlan('REVIEW2','REVIEW','REVIEW',0,windows=[win]))
out['two_crews_disjoint']={'accepted':v.is_feasible,'violations':v.violations,
    'execution_intervals':[[iv1.exec_start_hour,iv1.exec_end_hour],[iv2.exec_start_hour,iv2.exec_end_hour]]}
(ROOT/'adversarial_reproduced.json').write_text(json.dumps(out,indent=2))
assert out['empty_exported_impact']['accepted'] and out['empty_exported_impact']['metric_customer_hours']==0
assert out['branch_one_live_endpoint']['asset_marked_deenergized']
assert out['same_zone_overlapping_windows']['accepted']
assert out['two_crews_disjoint']['accepted']
print('Four reported false positives reproduced. Original package unchanged.')
