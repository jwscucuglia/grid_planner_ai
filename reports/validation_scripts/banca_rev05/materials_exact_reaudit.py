"""Independent post-fix material audit, using a 2,000-digit Decimal oracle."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from audit_support import TABLES,write_bundle,timed_case
from kgp.ingestion import import_bundle
from kgp.contracts import plan_from_json
from kgp.baselines import run_baseline_b0_individual,run_baseline_b1_geographic,run_baseline_b2_topological_greedy
from kgp.optimizer import run_kgp_optimizer,ObjectiveWeights
from kgp.feasibility import verify_plan_feasibility

OUT=ROOT/'.audit_cache'/'banca_audit'/'public_reproduction'
OUT.mkdir(parents=True,exist_ok=True)
METHODS={'B0':run_baseline_b0_individual,'B1':run_baseline_b1_geographic,
         'B2':run_baseline_b2_topological_greedy,'KGP':run_kgp_optimizer}
results=[]
started=time.time()
source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT/'src/kgp').glob('*.py'))}

def decimal_sum(quantities):
    with localcontext() as ctx:
        ctx.prec=2000
        return sum((Decimal(str(q)) for q in quantities),Decimal(0))

def rational_decimal(text):
    with localcontext() as ctx:
        ctx.prec=2000
        if '/' not in text:return Decimal(text)
        a,b=text.split('/')
        return Decimal(a)/Decimal(b)

def back(plan):return plan_from_json(json.dumps(asdict(plan),allow_nan=False))
def record(name,condition,**evidence):
    results.append({'probe':name,'passed':bool(condition),'evidence':evidence})

def case(quantities,stock,layout):
    loaded=import_bundle(write_bundle(OUT/'exact_material_base'))
    assert loaded.is_valid
    n,c=loaded.network,loaded.catalog
    if layout=='days':
        c.planning_days=tuple(range(1,len(quantities)+1))
        c.crews['E1']=replace(c.crews['E1'],available_days=c.planning_days)
    if layout=='windows':
        n.isolation_zones['Z2']=replace(n.isolation_zones['Z1'],zone_id='Z2')
    c.materials_inventory={'M':stock}
    jobs=[replace(loaded.services[0],service_id=f'S{i}',required_materials={'M':q},
                  ready_at_day=i if layout=='days' else 0,
                  deadline_day=i if layout=='days' else 1,
                  compatible_zone_ids=('Z2',) if layout=='windows' and i>1 else ('Z1',))
          for i,q in enumerate(quantities,1)]
    return n,jobs,c

cases=[
    ('large_plus_tiny',[1.,1e-28],1.),
    ('decimal_sum',[.1,.2],.3),
    ('subnormal_sum',[5e-324,5e-324],5e-324),
    ('subnormal_exact',[5e-324],5e-324),
    ('thirds_fit',[.33333]*3,.99999),
    ('thirds_deficit',[.33333]*3,.99998),
    ('overflow',[1e308,1e308],1.7e308),
]
for title,quantities,stock in cases:
    for layout in ['package','windows','days']:
        for name,fn in METHODS.items():
            n,j,c=case(quantities,stock,layout)
            exact=decimal_sum(quantities)
            enough=exact<=Decimal(str(stock))
            p=back(fn(n,j,c,f'{title}_{layout}'))
            r=verify_plan_feasibility(n,j,c,p)
            # Demand is the oracle; exported display totals are never debited.
            used=decimal_sum(s.required_materials['M'] for s in j if s.service_id in p.completed_service_ids)
            record(f'{title}/{layout}/{name}',r.is_feasible==enough and used<=Decimal(str(stock))
                   and (enough or not p.is_optimal_proven),
                   exact_required=str(exact),stock=str(stock),exact_used=str(used),
                   feasible=r.is_feasible,complete=list(p.completed_service_ids),
                   optimal=p.is_optimal_proven,status=p.solver_status,violations=r.violations)

# Forge complete, physically overstocked plans with valid timing and exact echoes.
for quantities,stock in [([1.,1e-28],1.),([5e-324,5e-324],5e-324),([1e308,1e308],1.7e308)]:
    for separate_days in [False,True]:
        n,j,c,p=timed_case(.1)
        j=[replace(s,required_materials={'M':q}) for s,q in zip(j,quantities)]
        c.materials_inventory={'M':stock}
        p.windows=[replace(w,materials_used={'M':q}) for w,q in zip(p.windows,quantities)]
        if separate_days:
            c.planning_days=(1,2)
            c.crews={k:replace(crew,available_days=(1,2)) for k,crew in c.crews.items()}
            j=[replace(s,deadline_day=2) for s in j]
            p.windows[1]=replace(p.windows[1],day=2,
                                crew_intervals=tuple(replace(iv,day=2) for iv in p.windows[1].crew_intervals))
        r=verify_plan_feasibility(n,j,c,back(p))
        json.dumps(asdict(r),allow_nan=False)
        exact=decimal_sum(quantities)
        reported=r.recalculated_materials_exact.get('M')
        record(f'forged_stock/{quantities}/days={separate_days}',not r.is_feasible
               and reported is not None and rational_decimal(reported)==exact
               and any('estoque' in v.lower() for v in r.violations),
               expected_total=str(exact),reported_exact=reported,reported_float=r.recalculated_materials,
               violations=r.violations)

# Echo tampering is rejected even when the difference is one binary ULP.
for quantities,stock,bad_echo in [([.33333],.33333,.3333),([5e-324],5e-324,0.),
                                  ([.1,.2],.3,.30000000000000004)]:
    n,j,c=case(quantities,stock,'package')
    for name,fn in METHODS.items():
        p=back(fn(n,j,c,'bad_echo'))
        assert verify_plan_feasibility(n,j,c,p).is_feasible
        # For B0 the first order alone has .1; still an invalid echo for that window.
        p.windows[0]=replace(p.windows[0],materials_used={'M':bad_echo})
        r=verify_plan_feasibility(n,j,c,back(p))
        record(f'forged_echo/{quantities}/{name}',not r.is_feasible,
               bad_echo=bad_echo,violations=r.violations)

# Finite inputs whose aggregate cannot be echoed as a finite float fail closed.
n,j,c,p=timed_case(.1)
j=[replace(s,required_materials={'M':1e308}) for s in j]
c.materials_inventory={'M':1.7e308}
p.windows[0]=replace(p.windows[0],service_ids=('S1','S2'),materials_used={'M':1.7e308},
                     crew_intervals=p.windows[0].crew_intervals+p.windows[1].crew_intervals)
p.windows=p.windows[:1]
r=verify_plan_feasibility(n,j,c,back(p))
record('overflow_echo_same_window/no_crash',not r.is_feasible and any('represent' in v.lower() for v in r.violations),violations=r.violations)
json.dumps(asdict(r),allow_nan=False)

# Exercise the final audit when a double MILP cannot distinguish a tiny parcel
# alongside stock fully consumed by a mandatory order (including ratio underflow).
for stock,tiny in [(1.,1e-28),(1e308,5e-324)]:
    n,j,c=case([stock,tiny],stock,'days')
    j[1]=replace(j[1],mandatory=False)
    p=back(run_kgp_optimizer(n,j,c,'milp_extreme_ratio',weights=ObjectiveWeights(optional_unscheduled_penalty=1000.)))
    r=verify_plan_feasibility(n,j,c,p)
    record(f'milp_exact_final_gate/{stock}/{tiny}',r.is_feasible and p.completed_service_ids==('S1',),
           complete=list(p.completed_service_ids),optimal=p.is_optimal_proven,status=p.solver_status,
           objective=p.solver_objective,bound=p.solver_bound,violations=r.violations)

output={'scope':'Extension of material conservation class 02 only',
        'source_sha256':source_hashes,'elapsed_seconds':round(time.time()-started,3),
        'probes':len(results),'passed':sum(x['passed'] for x in results),
        'failed':sum(not x['passed'] for x in results),'results':results}
(OUT/'materials_exact_reaudit_results.json').write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in output.items() if k!='results'},indent=2))
print(json.dumps([x for x in results if not x['passed']],indent=2,ensure_ascii=False))
print(json.dumps([x for x in results if x['probe'].startswith('milp_exact')],indent=2,ensure_ascii=False))
raise SystemExit(1 if output['failed'] else 0)
