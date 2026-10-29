"""Twelve focused adversarial classes; independent small controls and JSON audit.

No implementation writes, no global pytest. Every temporary input/output stays
under the repository's .audit_cache directory. Explicit public fixtures are
local; expected material use, outage unions and optional objective are independent.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from audit_support import TABLES, write_bundle, timed_case, shift_window
from kgp.contracts import *
from kgp.ingestion import import_bundle
from kgp.baselines import run_baseline_b0_individual, run_baseline_b1_geographic, run_baseline_b2_topological_greedy
from kgp.optimizer import ObjectiveWeights, run_kgp_optimizer
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics

OUT = ROOT / '.audit_cache' / 'banca_audit' / 'public_reproduction'
OUT.mkdir(parents=True, exist_ok=True)
METHODS = {'B0': run_baseline_b0_individual, 'B1': run_baseline_b1_geographic,
           'B2': run_baseline_b2_topological_greedy, 'KGP': run_kgp_optimizer}
rows = []
start = time.time()
source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted((ROOT / 'src' / 'kgp').glob('*.py'))}

def check(cls, name, condition, evidence=None):
    rows.append({'class': cls, 'probe': name, 'passed': bool(condition), 'evidence': evidence})

def readback(plan):
    return plan_from_json(json.dumps(asdict(plan), allow_nan=False))

def envelope(network, jobs, cat, plan):
    return verify_plan_feasibility(network, jobs, cat, readback(plan))

def base():
    result = import_bundle(write_bundle(OUT / 'matrix_base'))
    assert result.is_valid, result.issues
    return result.network, list(result.services), result.catalog

def planned(name='B0'):
    n,j,c = base()
    p = METHODS[name](n,j,c,'audit_control', decision_day=0)
    assert envelope(n,j,c,p).is_feasible
    return n,j,c,p

def rejected(cls, name, n,j,c,p):
    r = envelope(n,j,c,p)
    check(cls,name,not r.is_feasible, {'violations':r.violations})

# 01. Positive controls for every engine, independent exact outage accounting.
for name in METHODS:
    n,j,c,p = planned(name)
    r = envelope(n,j,c,p)
    # One phase/consumer, no transfer, one 1h task and .1h on either side.
    expected = Decimal('0.1') + Decimal('1.0') + Decimal('0.1')
    check('01_control_all_engines', name, r.is_feasible and r.completed_mandatory_count==1
          and Decimal(str(r.recalculated_consumer_hours))==expected
          and r.recalculated_physical_maneuvers==2, asdict(r))

# 02. Material conservation: positive demand below an undocumented rounding scale.
for quantity, stock in [(0.00004,0.),(0.33333,0.3333),(1.,1.)]:
    n,j,c = base()
    j = [replace(j[0],required_materials={'M':quantity})]
    c.materials_inventory = {'M':stock}
    for name,fn in METHODS.items():
        p = fn(n,j,c,'material_decimal',decision_day=0)
        r = envelope(n,j,c,p)
        exact_stock_ok = Decimal(str(quantity)) <= Decimal(str(stock))
        check('02_material_conservation',f'{name}: required={quantity}, stock={stock}',
              r.is_feasible == exact_stock_ok,
              {'exact_stock_ok':exact_stock_ok,'feasible':r.is_feasible,
               'recalculated':r.recalculated_materials,'optimal_proven':p.is_optimal_proven,
               'solver_status':p.solver_status,'violations':r.violations})

# 03. Mandatory omission and dishonest completion lists.
n,j,c,p=planned()
p.windows=[]
rejected('03_portfolio_integrity','claim completion after erasing window',n,j,c,p)
p.completed_service_ids=()
p.unscheduled_service_ids=('S1',)
rejected('03_portfolio_integrity','consistent fields still cannot omit mandatory',n,j,c,p)

# 04. Real crew occupation: one-tick execution/preparation/recomposition deficits,
# requirements for a second crew, skill and shift boundary.
for mutation in ['duration_tick','prep_tick','recomp_tick','second_crew','skill','shift']:
    n,j,c,p=planned()
    w=p.windows[0]; iv=w.crew_intervals[0]
    if mutation=='duration_tick': j=[replace(j[0],duration_hours=1.0001)]
    elif mutation=='prep_tick': j=[replace(j[0],prep_hours=.1001)]
    elif mutation=='recomp_tick': j=[replace(j[0],recomp_hours=.1001)]
    elif mutation=='second_crew': j=[replace(j[0],required_crews_count=2)]
    elif mutation=='skill': j=[replace(j[0],required_skills=('UNAVAILABLE',))]
    else: c.crews['E1']=replace(c.crews['E1'],shift_end_hour=iv.recomp_end_hour-.0001)
    rejected('04_crews_in_time',mutation,n,j,c,p)

# 05. Availability/knowledge clocks, all three exclusion dimensions.
for mutation in ['created_after_decision','not_ready','past_deadline','crew_unavailable','day_outside_catalog']:
    n,j,c,p=planned()
    if mutation=='created_after_decision': j=[replace(j[0],created_at_day=1,ready_at_day=1)]
    elif mutation=='not_ready': j=[replace(j[0],ready_at_day=2,deadline_day=2)]
    elif mutation=='past_deadline': j=[replace(j[0],deadline_day=0)]
    elif mutation=='crew_unavailable': c.crews['E1']=replace(c.crews['E1'],available_days=(2,))
    else: c.planning_days=(2,)
    rejected('05_temporal_eligibility',mutation,n,j,c,p)

# 06. Predecessor absent and same-day predecessor after successor.
n,j,c,p=timed_case(.1)
j[1]=replace(j[1],precedence_service_ids=('S1',))
check('06_precedence','ordered control',envelope(n,j,c,p).is_feasible)
j[0]=replace(j[0],precedence_service_ids=('S2',))
rejected('06_precedence','reverse same-day cycle',n,j,c,p)
n,j,c,p=planned()
pred=replace(j[0],service_id='P',mandatory=False)
j=[pred,replace(j[0],precedence_service_ids=('P',))]
p.unscheduled_service_ids=('P',)
rejected('06_precedence','optional predecessor omitted',n,j,c,p)

# 07. Phase/backfeed and conservative branch safety; valid topology-only fixtures.
n,j,c,p=planned()
n.nodes['BACK']=GridNode('BACK','second source','F1',2.,0.,phases=('A',),is_source=True)
n.branches['LIVE']=GridBranch('LIVE','BACK','N1','F1',phases=('A',))
rejected('07_post_maneuver_connectivity','unopened source backfeed',n,j,c,p)
n,j,c,p=planned()
n.assets['A1']=replace(n.assets['A1'],node_id=None,branch_id='B1')
rejected('07_post_maneuver_connectivity','branch asset has live upstream endpoint',n,j,c,p)

# 08. Electrical windows serial and crew conflicts are checked in actual time.
n,j,c,p=timed_case(0.)
check('08_global_time_conflicts','exact adjacency control',envelope(n,j,c,p).is_feasible)
p.windows[1]=shift_window(p.windows[1],-.0001)
rejected('08_global_time_conflicts','one-tick overlap across distinct crews',n,j,c,p)

# 09. Exported impact and switching data are untrusted.
for mutation in ['consumer_set','maneuver_count','phase_evidence','stage_clock']:
    n,j,c,p=planned(); w=p.windows[0]
    if mutation=='consumer_set': w=replace(w,interrupted_consumers=())
    elif mutation=='maneuver_count': w=replace(w,physical_maneuvers_count=1)
    elif mutation=='phase_evidence':
        st=w.maneuver_steps[1]
        w=replace(w,maneuver_steps=(w.maneuver_steps[0],replace(st,energized_nodes=('SRC','N1')))+w.maneuver_steps[2:])
    else:
        st=w.maneuver_steps[1]
        w=replace(w,maneuver_steps=(w.maneuver_steps[0],replace(st,end_hour=st.end_hour-.0001))+w.maneuver_steps[2:])
    p.windows[0]=w
    rejected('09_export_evidence',mutation,n,j,c,p)

# 10. Union intervals: independently enumerate each operational tick for 2h.
for gap in [0.,.0001,.1]:
    n,j,c,p=timed_case(gap)
    r=envelope(n,j,c,p)
    covered=set()
    for w in p.windows:
        for t in range(round(w.start_hour*10000),round(w.end_hour*10000)):
            covered.add(t)
    components=sum(1 for t in covered if t-1 not in covered)
    check('10_exact_consumer_intervals',f'gap={gap}',r.is_feasible
          and r.recalculated_consumer_hours==len(covered)/10000
          and r.recalculated_total_interruptions==components,
          {'oracle_hours':len(covered)/10000,'oracle_components':components,
           'reported_hours':r.recalculated_consumer_hours,'reported_components':r.recalculated_total_interruptions})

# 11. Exhaustively enumerate the only two options of a one-order tiny instance.
# Independent default-weight value: 90*1 + 1.2 CH + 2.5*2 + .6*1.2 HH = 96.92.
for penalty in [0.,50.,96.,97.,250.]:
    n,j,c=base(); j=[replace(j[0],mandatory=False)]
    p=run_kgp_optimizer(n,j,c,'optional_oracle',weights=ObjectiveWeights(optional_unscheduled_penalty=penalty))
    r=envelope(n,j,c,p)
    serving=Decimal('90')+Decimal('1.2')+Decimal('2.5')*2+Decimal('.6')*Decimal('1.2')
    expected=min(Decimal(str(penalty)),serving)
    check('11_solver_certificate_tiny_catalog',f'penalty={penalty}',r.is_feasible
          and Decimal(str(p.solver_objective))==expected and Decimal(str(p.solver_bound))==expected
          and p.is_optimal_proven and p.solver_gap==0,
          {'objective':p.solver_objective,'bound':p.solver_bound,'expected':str(expected),'complete':p.completed_service_ids})

# 12. Import boundary controls preserve identity, mandatory count and dimensions.
for fmt in ['json','csv','geojson']:
    r=import_bundle(write_bundle(OUT/f'import_control_{fmt}',fmt=fmt))
    check('12_ingestion_boundary',fmt,r.is_valid and len(r.services)==1 and r.services[0].mandatory)
for mutation in ['unknown_status','orphan_asset','wrong_phase','voltage_transition','escaped_path','duplicate_id','off_grid_duration']:
    t=deepcopy(TABLES)
    if mutation=='unknown_status':t['services'][0]['scope_status']='CANCELLED_UNKNOWN'
    elif mutation=='orphan_asset':t['services'][0]['target_assets']=['ABSENT']
    elif mutation=='wrong_phase':t['branches'][0]['phases']=['B']
    elif mutation=='voltage_transition':t['nodes'][1]['nominal_voltage_kv']=.22
    elif mutation=='duplicate_id':t['services'].append(deepcopy(t['services'][0]))
    elif mutation=='off_grid_duration':t['services'][0]['duration_hours']=1.00005
    path=write_bundle(OUT/f'import_{mutation}',t)
    if mutation=='escaped_path':
        manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest['tables']['nodes']['path']='../matrix_base/nodes.json'
        path.write_text(json.dumps(manifest),encoding='utf-8')
    r=import_bundle(path)
    check('12_ingestion_boundary',mutation,not r.is_valid and r.network is None and r.catalog is None,
          {'issues':[asdict(x) for x in r.issues]})

try:
    observed_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
except (OSError,subprocess.CalledProcessError):
    observed_head=None  # An exported source snapshot need not include Git metadata.
result={'head':observed_head,
        'source_sha256_at_start':source_hashes,'elapsed_seconds':round(time.time()-start,3),
        'classes':len({x['class'] for x in rows}),'probes':len(rows),
        'passed':sum(x['passed'] for x in rows),'failed':sum(not x['passed'] for x in rows),'results':rows}
(OUT/'adversarial_matrix_results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='results'},indent=2))
print(json.dumps([x for x in rows if not x['passed']],indent=2,ensure_ascii=False))
raise SystemExit(1 if result['failed'] else 0)
