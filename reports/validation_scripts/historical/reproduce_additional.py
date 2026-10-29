import sys, json
from pathlib import Path
from dataclasses import replace
ROOT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[3]/'grid_planner_ai_audit'
sys.path.insert(0,str(ROOT/'src'))
from kgp.contracts import *
from kgp.experiment import generate_scenario_instance
from kgp.baselines import run_baseline_b0_individual
from kgp.feasibility import verify_plan_feasibility
from kgp.topology import evaluate_maneuver_step
from kgp.scheduling import compute_travel_hours
sc=generate_scenario_instance(202601,'EVAL')
p=run_baseline_b0_individual(sc.network,sc.services,sc.catalog,sc.scenario_id,sc.decision_day)
assert verify_plan_feasibility(sc.network,sc.services,sc.catalog,p).is_feasible
sid=p.windows[0].service_ids[0]
svc=next(s for s in sc.services if s.service_id==sid)
w=p.windows[0]
iv=next(iv for iv in w.crew_intervals if iv.service_id==sid)
crew=sc.catalog.crews[iv.crew_id]
# Input service coordinates and target registration remain canonical; only crew base is remote.
remote_crew=replace(crew,base_x=350.0,base_y=350.0)
remote_catalog=replace(sc.catalog,crews={iv.crew_id:remote_crew})
spoofed_iv=replace(iv,x=350.0,y=350.0,travel_start_hour=iv.prep_start_hour,travel_hours=0.0)
spoofed_plan=replace(p,windows=[replace(w,crew_intervals=(spoofed_iv,))])
r=verify_plan_feasibility(sc.network,[svc],remote_catalog,spoofed_plan)
travel={'service_id':sid,'actual_service_xy':[svc.x,svc.y],'crew_base_xy':[350,350],'accepted_interval_xy':[spoofed_iv.x,spoofed_iv.y],'expected_hours_from_base':compute_travel_hours(350,350,svc.x,svc.y,crew.speed_kmh),'allocated_travel_hours':spoofed_iv.prep_start_hour-spoofed_iv.travel_start_hour,'feasible':r.is_feasible,'violations':r.violations}
# Existing B0 plan requires SW_A1_1, but a non-maneuverable switch remains accepted.
switch='SW_A1_1'; sc.network.switches[switch]=replace(sc.network.switches[switch],is_maneuverable=False)
r=verify_plan_feasibility(sc.network,sc.services,sc.catalog,p)
immovable={'switch_id':switch,'used_by_windows':[w.window_id for w in p.windows if switch in sc.network.isolation_zones[w.zone_id].open_switches],'feasible':r.is_feasible,'violations':r.violations}
# Per-phase network is radial: A supplies X through S-Y-X; B supplies X through S-X.
# The consumer is A-only, so both A branches carry 1000 kW and overload 100 kW ratings.
net=GridNetwork('phase_transfer','1',0)
net.nodes={k:GridNode(k,k,'F',0,0,phases=ph,is_source=(k=='S')) for k,ph in [('S',('A','B')),('X',('A','B')),('Y',('A',))]}
net.branches={
 'B_SHORTCUT':GridBranch('B_SHORTCUT','S','X','F',phases=('B',),max_capacity_kw=5000),
 'A_FEED':GridBranch('A_FEED','S','Y','F',phases=('A',),max_capacity_kw=100),
 'A_LOAD':GridBranch('A_LOAD','Y','X','F',phases=('A',),max_capacity_kw=100)}
net.switches={'TIE_B':GridSwitch('TIE_B','B_SHORTCUT',SwitchState.OPEN,SwitchState.OPEN)}
net.consumers={'C':ConsumerUnit('C','X',None,phase='A',avg_demand_kw=1000)}
zone=IsolationZoneSpec('Z','Z','F',(),tie_switches_closed=('TIE_B',),requires_transfer_validation=True)
net.isolation_zones={'Z':zone}
step=evaluate_maneuver_step(net,1,ManeuverStage.TRANSFER,9,10,(),('TIE_B',),zone)
phase={'transfer_feasible':step.transfer_feasible,'reported_max_loading_pct':step.max_loading_pct,'required_A_path_loading_pct':1000.0,'energized_nodes':step.energized_nodes,'interrupted_consumers':step.interrupted_consumers,'notes':step.notes}
# No target assets is rejected by eligibility but not by independent verifier.
empty_services=[replace(s,target_assets=()) if s.service_id==sid else s for s in sc.services]
r=verify_plan_feasibility(sc.network,empty_services,sc.catalog,p)
empty={'service_id':sid,'target_assets':[],'feasible':r.is_feasible,'violations':r.violations}
out={'spoofed_travel_coordinates':travel,'immovable_switch':immovable,'phase_blind_transfer':phase,'no_target_assets':empty}
print(json.dumps(out,indent=2,ensure_ascii=False))
Path(__file__).with_name('additional_results.json').write_text(json.dumps(out,indent=2,ensure_ascii=False))
