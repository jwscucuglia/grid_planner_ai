import json, sys
from pathlib import Path
from dataclasses import replace
REPO=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[3]/'grid_planner_ai_audit'
sys.path.insert(0,str(REPO/'src'))
from kgp.contracts import *
from kgp.optimizer import run_kgp_optimizer, ObjectiveWeights
from kgp.baselines import run_baseline_b0_individual, run_baseline_b2_topological_greedy
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics
from kgp.scheduling import build_window_schedule

def instance(n=1, days=(1,), duration=.5, maxwindow=1., consumers=True, coordinates=None):
    net=GridNetwork('audit','1',0)
    net.nodes['S']=GridNode('S','source','F',0.,0.,is_source=True)
    for k in range(n):
        i=str(k); x=coordinates[k] if coordinates else 0.
        net.nodes[i]=GridNode(i,i,'F',x,0.)
        net.branches[i]=GridBranch(i,'S',i,'F')
        net.switches[i]=GridSwitch(i,i,SwitchState.CLOSED,SwitchState.CLOSED)
        net.assets[i]=GridAsset(i,i,None,'F','test',x=x)
        net.isolation_zones[i]=IsolationZoneSpec(i,i,'F',(i,),prep_duration_hours=.1,recomp_duration_hours=.1)
        if consumers: net.consumers[i]=ConsumerUnit(i,i,i)
    crew=CrewResource('C','C',('s',),days,speed_kmh=10.,prep_hours=.1)
    cat=ResourceCatalog({'C':crew},{},days,max_window_duration_hours=maxwindow)
    jobs=[ServiceOrder('J'+str(k),'job',ServiceType.OBRA,(str(k%n),),True,0,days[0],days[-1],duration,('s',),compatible_zone_ids=(str(k%n),),allow_simultaneous_in_zone=False,prep_hours=.1,recomp_hours=.1,x=coordinates[k%n] if coordinates else 0.) for k in range(n)]
    return net,cat,jobs

def summarize(net,cat,jobs,plan):
    f=verify_plan_feasibility(net,jobs,cat,plan)
    m=compute_plan_metrics(net,jobs,plan,f)
    actual=90*len(plan.windows)+m.consumer_hours_total+12*m.repeat_interrupted_consumers_count+2.5*m.physical_maneuvers_count+.6*(m.total_crew_work_hours+m.total_crew_travel_hours)
    return dict(status=plan.solver_status,optimal=plan.is_optimal_proven,completed=plan.completed_service_ids,unscheduled=plan.unscheduled_service_ids,windows=[dict(services=w.service_ids,start=w.start_hour,end=w.end_hour,travel=sum(iv.travel_hours for iv in w.crew_intervals)) for w in plan.windows],feasible=f.is_feasible,violations=f.violations,solver_objective=plan.solver_objective,solver_bound=plan.solver_bound,documented_J=round(actual,6),repeat_consumers=m.repeat_interrupted_consumers_count)

out={}
net,cat,jobs=instance()
jobs=[replace(jobs[0],service_id='J'+str(k)) for k in range(3)]
out['repeat_cap_one_day']={method:summarize(net,cat,jobs,fn(net,jobs,cat,'repeat')) for method,fn in [('KGP',run_kgp_optimizer),('B0',run_baseline_b0_individual)]}
net,cat,jobs=instance(3,duration=2.,maxwindow=2.2)
out['shift_domain']={method:summarize(net,cat,jobs,fn(net,jobs,cat,'domain')) for method,fn in [('KGP',run_kgp_optimizer),('B0',run_baseline_b0_individual)]}
net,cat,jobs=instance(2,duration=.5,maxwindow=1.,coordinates=[10.,10.1])
out['reconstruction_travel']={method:summarize(net,cat,jobs,fn(net,jobs,cat,'travel')) for method,fn in [('KGP',run_kgp_optimizer),('B0',run_baseline_b0_individual)]}
net,cat,jobs=instance(1,days=(1,2,3))
jobs=[replace(jobs[0],service_id='J'+str(k),ready_at_day=k,deadline_day=k) for k in (1,2,3)]
out['repeat_objective']=summarize(net,cat,jobs,run_kgp_optimizer(net,jobs,cat,'repeat_obj'))
net,cat,jobs=instance(1,duration=2.,maxwindow=2.2)
cat.crews={'C1':replace(cat.crews['C'],crew_id='C1',skills=('s','special')), 'C2':replace(cat.crews['C'],crew_id='C2')}
jobs=[replace(jobs[0],service_id='A',priority=1),replace(jobs[0],service_id='B',priority=2,required_skills=('special',),allow_simultaneous_in_zone=True)]
jobs[0]=replace(jobs[0],allow_simultaneous_in_zone=True)
goodwin,err=build_window_schedule(net,cat,'0',1,'manually_found',[replace(jobs[0],priority=2),replace(jobs[1],priority=1)])
assert goodwin is not None,err
goodplan=ExecutionPlan('manual','audit','assignment',0,windows=[goodwin],completed_service_ids=('A','B'))
out['greedy_allocation']={'KGP':summarize(net,cat,jobs,run_kgp_optimizer(net,jobs,cat,'assignment')),'alternative':summarize(net,cat,jobs,goodplan)}
Path(__file__).with_name('root_reproduction.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
