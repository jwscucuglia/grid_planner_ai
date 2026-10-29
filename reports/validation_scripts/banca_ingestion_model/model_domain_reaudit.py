"""Independent per-phase multigraph oracle and physical-domain regression.

No implementation edits. Runtime files stay in .audit_cache. --source-root
permits reusing a read-only Git source snapshot to preserve a RED baseline.
"""
from __future__ import annotations
import argparse
from collections import deque
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,default=ROOT/'src')
parser.add_argument('--output-name',default='model_current')
args=parser.parse_args()
sys.path.insert(0,str(args.source_root.resolve()))
from audit_support import TABLES,write_bundle
from kgp.ingestion import import_bundle
from kgp.contracts import plan_from_json
from kgp.topology import compute_energized_phases_by_node,verify_transfer_electrical_feasibility
from kgp.feasibility import _independent_energized_phases_and_sources,_independent_phase_transfer_check,verify_plan_feasibility
from kgp.baselines import run_baseline_b0_individual,run_baseline_b1_geographic,run_baseline_b2_topological_greedy
from kgp.optimizer import run_kgp_optimizer

OUT=ROOT/'.audit_cache'/'banca_audit'/'ingestion_model'/args.output_name
OUT.mkdir(parents=True,exist_ok=True)
METHODS={'B0':run_baseline_b0_individual,'B1':run_baseline_b1_geographic,
         'B2':run_baseline_b2_topological_greedy,'KGP':run_kgp_optimizer}
rows=[]
started=time.time()

def oracle(tables,opened=(),closed=()):
    """Walk raw records, counting edge IDs; no product graph helper is used."""
    nodes={n['node_id']:n for n in tables['nodes']}
    by_branch={}
    for sw in tables['switches']:by_branch.setdefault(sw['branch_id'],[]).append(sw)
    active=[]
    for branch in tables['branches']:
        u,v=branch['from_node'],branch['to_node']
        if not branch['in_service'] or not nodes[u]['in_service'] or not nodes[v]['in_service']:continue
        conducting=True
        for sw in by_branch.get(branch['branch_id'],[]):
            state=sw['current_state']
            if sw['is_maneuverable']:
                if sw['switch_id'] in opened:state='OPEN'
                elif sw['switch_id'] in closed:state='CLOSED'
            conducting &= sw['in_service'] and state=='CLOSED'
        if conducting:active.append(branch)
    components=[]
    for phase in 'ABC':
        adj={nid:[] for nid,n in nodes.items() if n['in_service'] and phase in n['phases']}
        for branch in active:
            u,v=branch['from_node'],branch['to_node']
            if phase in branch['phases'] and u in adj and v in adj:
                adj[u].append((v,branch['branch_id']))
                adj[v].append((u,branch['branch_id']))
        seen=set()
        for start in adj:
            if start in seen:continue
            pending=deque([start]);vertices=set();edges=set()
            while pending:
                here=pending.popleft()
                if here in vertices:continue
                vertices.add(here);seen.add(here)
                for neighbor,bid in adj[here]:
                    edges.add(bid)
                    if neighbor not in vertices:pending.append(neighbor)
            sources=sorted(nid for nid in vertices if nodes[nid]['is_source'])
            rank=len(edges)-len(vertices)+1
            components.append({'phase':phase,'nodes':len(vertices),'edges':len(edges),
                               'sources':sources,'cycle_rank':rank,
                               'unsupported':bool(sources) and (rank!=0 or len(sources)!=1)})
    return {'supported':not any(c['unsupported'] for c in components),'components':components}

def check(name,condition,**evidence):rows.append({'probe':name,'passed':bool(condition),'evidence':evidence})

def live_node(tables,nid='LIVE',phases=('A',),source=False,load=100.):
    n=deepcopy(tables['nodes'][1]);n.update(node_id=nid,name=nid,phases=list(phases),is_source=source,load_kw=load)
    tables['nodes'].append(n)

def branch(tables,bid,u,v,phases=('A',),capacity=1000.,resistance=.1,active=True):
    b=deepcopy(tables['branches'][0]);b.update(branch_id=bid,from_node=u,to_node=v,phases=list(phases),
        max_capacity_kw=capacity,r_ohm=resistance,x_ohm=0.,in_service=active)
    tables['branches'].append(b)

def fixture(name):
    t=deepcopy(TABLES)
    if name in ['parallel_high_first','parallel_low_first','radial_high','radial_low','parallel_disabled',
                'optional_transfer_mesh','required_transfer_mesh']:
        live_node(t)
        a=dict(bid='HIGH',u='SRC',v='LIVE',capacity=1000.,resistance=1.)
        b=dict(bid='LOW',u='SRC',v='LIVE',capacity=1.,resistance=.01,active=name!='parallel_disabled')
        order=[b,a] if name=='parallel_low_first' else [a,b]
        if name=='radial_high':order=[a]
        if name=='radial_low':order=[b]
        for item in order:branch(t,**item)
        if name in ['optional_transfer_mesh','required_transfer_mesh']:
            sw=deepcopy(t['switches'][0]);sw.update(switch_id='TIE',branch_id='LOW',normal_state='OPEN',current_state='OPEN')
            t['switches'].append(sw)
            t['isolation_zones'][0].update(tie_switches_closed=['TIE'],
                requires_transfer_validation=name=='required_transfer_mesh',transfer_params_complete=True,
                max_transfer_voltage_drop_pct=5.)
    elif name=='triangle_live':
        live_node(t,'X');live_node(t,'Y',load=0.)
        for bid,u,v in [('SX','SRC','X'),('XY','X','Y'),('YS','Y','SRC')]:branch(t,bid,u,v)
    elif name=='triangle_dead':
        for nid in ('X','Y','Z'):live_node(t,nid,load=0.)
        for bid,u,v in [('XY','X','Y'),('YZ','Y','Z'),('ZX','Z','X')]:branch(t,bid,u,v)
    elif name=='two_sources_tree':
        live_node(t,'SRC2',source=True,load=0.);live_node(t)
        branch(t,'S1L','SRC','LIVE');branch(t,'S2L','SRC2','LIVE')
    elif name=='two_sources_disconnected':
        live_node(t,'SRC2',source=True,load=0.);live_node(t)
        branch(t,'S2L','SRC2','LIVE')
    elif name=='two_sources_different_phases':
        live_node(t,'SRC2',phases=('B',),source=True,load=0.);live_node(t,phases=('A','B'))
        branch(t,'S1L','SRC','LIVE',phases=('A',));branch(t,'S2L','SRC2','LIVE',phases=('B',))
    elif name=='parallel_different_phases':
        t['nodes'][0]['phases']=['A','B'];live_node(t,phases=('A','B'))
        branch(t,'AL','SRC','LIVE',phases=('A',));branch(t,'BL','SRC','LIVE',phases=('B',))
    elif name=='source_selfloop':branch(t,'LOOP','SRC','SRC')
    elif name=='switch_two_hours':t['switches'][0]['maneuver_duration_hours']=2.
    elif name=='switch_and_zone_two_hours':
        t['switches'][0]['maneuver_duration_hours']=2.
        t['isolation_zones'][0].update(prep_duration_hours=2.,recomp_duration_hours=2.)
    return t

def run_case(name):
    tables=fixture(name)
    raw_oracle=oracle(tables)
    closed=set(tables['isolation_zones'][0].get('tie_switches_closed',()))
    state_oracle=oracle(tables,opened=('SW1',),closed=closed)
    electrical_expected=state_oracle['supported'] and name!='radial_low'
    expected=raw_oracle['supported'] and (electrical_expected or name=='optional_transfer_mesh')
    path=write_bundle(OUT/name,tables)
    loaded=import_bundle(path)
    evidence={'import_valid':loaded.is_valid,'issues':[asdict(i) for i in loaded.issues],
              'initial_oracle':raw_oracle,'work_oracle':state_oracle}
    if not loaded.is_valid:
        check(name+'/import',name=='source_selfloop',**evidence)
        return
    n,z=loaded.network,loaded.network.isolation_zones['Z1']
    phases,sources,_=compute_energized_phases_by_node(n,{'SW1'},closed)
    independent,isources,branches=_independent_energized_phases_and_sources(n,{'SW1'},closed)
    planner=verify_transfer_electrical_feasibility(n,z,{'SW1'},closed,phases,sources)
    verifier=_independent_phase_transfer_check(n,z,{'SW1'},closed,independent,isources,branches)
    check(name+'/electrical',planner[0]==electrical_expected and verifier[0]==electrical_expected,
          planner=list(planner),verifier=list(verifier),**evidence)
    for method,fn in METHODS.items():
        p=fn(n,loaded.services,loaded.catalog,name)
        p=plan_from_json(json.dumps(asdict(p),allow_nan=False))
        r=verify_plan_feasibility(n,loaded.services,loaded.catalog,p)
        summary={'feasible':r.is_feasible,'completed':list(p.completed_service_ids),
                 'optimal':p.is_optimal_proven,'status':p.solver_status,'violations':r.violations,
                 'windows':[{'start':w.start_hour,'work_start':w.work_start_hour,'work_end':w.work_end_hour,
                             'end':w.end_hour,'maneuvers':w.physical_maneuvers_count} for w in p.windows]}
        no_mesh_transfer=(name!='optional_transfer_mesh' or
                          all(not stage.closed_switches for w in p.windows for stage in w.maneuver_steps))
        check(name+'/'+method,r.is_feasible==expected and (expected or not p.is_optimal_proven)
              and no_mesh_transfer,**summary)
        (OUT/name/f'{method}_plan.json').write_text(json.dumps(asdict(p),indent=2,allow_nan=False),encoding='utf-8')

cases=['control','parallel_high_first','parallel_low_first','radial_high','radial_low','parallel_disabled',
       'triangle_live','triangle_dead','two_sources_tree','two_sources_disconnected',
       'two_sources_different_phases','parallel_different_phases','source_selfloop',
       'switch_two_hours','switch_and_zone_two_hours','optional_transfer_mesh','required_transfer_mesh']
for name in cases:run_case(name)

public=ROOT/'data/gis_reference/eflul_2025_ctmt002/bundle'
manifest=json.loads((public/'manifest.json').read_text(encoding='utf-8-sig'))
tables={table:json.loads((public/d['path']).read_text(encoding='utf-8-sig')) for table,d in manifest['tables'].items()}
reference={'nodes':len(tables['nodes']),'branches':len(tables['branches']),'oracle':oracle(tables),
           'file_sha256':{name:hashlib.sha256((public/name).read_bytes()).hexdigest()
                          for name in ['nodes.json','branches.json','switches.json']}}
result={'source_sha256':{str(p.relative_to(args.source_root.resolve())):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((args.source_root.resolve()/'kgp').glob('*.py'))},
        'elapsed_seconds':round(time.time()-started,3),'cases':len(cases),'probes':len(rows),
        'passed':sum(r['passed'] for r in rows),'failed':sum(not r['passed'] for r in rows),
        'public_topology_reference':reference,'results':rows}
(OUT/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['results','public_topology_reference']},indent=2))
print(json.dumps([r for r in rows if not r['passed']],indent=2,ensure_ascii=False))
raise SystemExit(1 if result['failed'] else 0)
