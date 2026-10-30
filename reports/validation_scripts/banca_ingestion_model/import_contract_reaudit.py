"""Public independent CSV, effective-map and path-diagnostic audit."""
from __future__ import annotations
import argparse
from copy import deepcopy
import csv
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',type=Path,default=ROOT/'src')
parser.add_argument('--output-name',default='import_current')
args=parser.parse_args()
sys.path.insert(0,str(args.source_root.resolve()))
from audit_support import TABLES,write_bundle
from kgp.ingestion import import_bundle
from kgp.contracts import plan_from_json
from kgp.topology import compute_energized_phases_by_node
from kgp.baselines import run_baseline_b0_individual,run_baseline_b1_geographic,run_baseline_b2_topological_greedy
from kgp.optimizer import run_kgp_optimizer
from kgp.feasibility import verify_plan_feasibility

OUT=ROOT/'.audit_cache'/'banca_audit'/'ingestion_model'/args.output_name
OUT.mkdir(parents=True,exist_ok=True)
rows=[];skips=[]
METHODS={'B0':run_baseline_b0_individual,'B1':run_baseline_b1_geographic,
         'B2':run_baseline_b2_topological_greedy,'KGP':run_kgp_optimizer}

def check(name,condition,**evidence):rows.append({'probe':name,'passed':bool(condition),'evidence':evidence})
def snapshot(result):
    out={'is_valid':result.is_valid,'service_ids':[s.service_id for s in result.services],
         'mandatory_ids':[s.service_id for s in result.services if s.mandatory],
         'source_count':result.provenance['counts'].get('services'),
         'source_ids':result.provenance['record_ids'].get('services'),
         'issues':[asdict(i) for i in result.issues]}
    if result.is_valid:
        phases,_,_=compute_energized_phases_by_node(result.network,set(),set())
        out['nodes']={key:{'is_source':n.is_source,'in_service':n.in_service,'x':n.x,'y':n.y}
                      for key,n in result.network.nodes.items()}
        out['phases']={key:sorted(value) for key,value in phases.items()}
    return out

def plans(result,label,expected_count):
    if not result.is_valid:return
    for method,fn in METHODS.items():
        p=fn(result.network,result.services,result.catalog,label)
        p=plan_from_json(json.dumps(asdict(p),allow_nan=False))
        r=verify_plan_feasibility(result.network,result.services,result.catalog,p)
        check(label+'/'+method,not r.is_feasible and r.total_mandatory_count==expected_count,
              feasible=r.is_feasible,completed=r.completed_mandatory_count,total=r.total_mandatory_count,
              violations=r.violations)

# Two records: the second has an explicit but operationally impossible target.
# Its mandatory obligation must remain visible; no claim of two schedulable jobs.
tables=deepcopy(TABLES)
second=dict(tables['services'][0],service_id='S2',title='Second',target_assets=[],
            required_skills=[],compatible_zone_ids=[])
tables['services'].append(second)
path=write_bundle(OUT/'csv',tables)
manifest=json.loads(path.read_text(encoding='utf-8'))
keys=[k for k in tables['services'][0] if k!='title']+['title']
buffer=io.StringIO(newline='')
writer=csv.DictWriter(buffer,fieldnames=keys);writer.writeheader()
writer.writerows({k:v if isinstance(v,str) else json.dumps(v) for k,v in row.items()}
                for row in tables['services'])
clean=buffer.getvalue()
manifest['tables']['services']={'path':'services.csv','format':'csv'}
path.write_text(json.dumps(manifest),encoding='utf-8')
service_path=path.parent/'services.csv';service_path.write_bytes(clean.encode('utf-8'))
control=import_bundle(path)
check('csv/control',control.is_valid and len(control.services)==2 and control.provenance['counts'].get('services')==2,
      **snapshot(control))
plans(control,'csv/control',2)
malformed=clean.replace(',Work\r\n',',"Work\r\n')
assert malformed!=clean
try:
    list(csv.DictReader(io.StringIO(malformed,newline=''),strict=True))
    strict_error=None
except csv.Error as error:strict_error=str(error)
service_path.write_bytes(malformed.encode('utf-8'))
bad=import_bundle(path)
check('csv/unterminated_title',not bad.is_valid and bad.network is None and bad.catalog is None
      and any(i.code=='INVALID_FILE' for i in bad.issues),strict_parser_error=strict_error,
      source_sha256=hashlib.sha256(malformed.encode('utf-8')).hexdigest(),**snapshot(bad))
if bad.is_valid:plans(bad,'csv/hidden_mandatory',2)
# A real multiline quoted title remains a valid record rather than physical-line counting.
tables['services'][0]['title']='Work\nwith "quoted" text'
buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=keys);writer.writeheader()
writer.writerows({k:v if isinstance(v,str) else json.dumps(v) for k,v in row.items()} for row in tables['services'])
service_path.write_bytes(buffer.getvalue().encode('utf-8'))
multiline=import_bundle(path)
check('csv/legitimate_multiline',multiline.is_valid and len(multiline.services)==2
      and multiline.services[0].title==tables['services'][0]['title'],**snapshot(multiline))

# Explicit maps are checked together with identity fallbacks.
for name,field_map,delete,rename in [
    ('control',{},None,None),
    ('implicit_state',{'in_service':'is_source'},'in_service',None),
    ('explicit_state',{'in_service':'is_source','is_source':'is_source'},'in_service',None),
    ('implicit_coordinate',{'x':'y'},'x',None),
    ('legitimate_rename',{'in_service':'STATE'},None,('in_service','STATE')),
    ('explicit_axis_swap',{'x':'y','y':'x'},None,None),
]:
    tables=deepcopy(TABLES)
    for node in tables['nodes']:
        if delete:node.pop(delete)
        if rename:node[rename[1]]=node.pop(rename[0])
    path=write_bundle(OUT/name,tables)
    data=json.loads(path.read_text(encoding='utf-8'));data['tables']['nodes']['field_map']=field_map
    path.write_text(json.dumps(data),encoding='utf-8')
    result=import_bundle(path)
    allowed=name in ['control','legitimate_rename','explicit_axis_swap']
    check('mapping/'+name,result.is_valid==allowed
          and (allowed or any(i.code=='INVALID_FIELD_MAP' for i in result.issues)),**snapshot(result))

# Native symlink construction can require privileges on Windows. Record the
# environmental limitation, and separately exercise RuntimeError at resolve().
path=write_bundle(OUT/'symlink_native')
loop=path.parent/'loop.json'
try:
    loop.symlink_to('loop.json')
except OSError as error:
    skips.append({'probe':'symlink/native_loop','reason':'Native symlink creation unavailable in this environment',
                  'error_type':type(error).__name__,'winerror':getattr(error,'winerror',None),'platform':sys.platform})
else:
    data=json.loads(path.read_text(encoding='utf-8'));data['tables']['nodes']['path']='loop.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    try:
        result=import_bundle(path)
        check('symlink/native_loop',not result.is_valid and result.network is None and result.catalog is None,
              **snapshot(result))
    except RuntimeError as error:check('symlink/native_loop',False,uncaught_error=str(error))
    finally:loop.unlink()

for mode in ['manifest','nodes']:
    path=write_bundle(OUT/('symlink_runtime_'+mode))
    original_resolve=Path.resolve
    def fail_on_target(self,*a,**kw):
        if self.name==('manifest.json' if mode=='manifest' else 'nodes.json'):
            raise RuntimeError('Synthetic native Path.resolve symlink-cycle condition')
        return original_resolve(self,*a,**kw)
    with patch.object(Path,'resolve',fail_on_target):
        try:
            result=import_bundle(path)
            check('symlink/resolve_runtime_'+mode,not result.is_valid and result.network is None
                  and result.catalog is None and any(i.code=='SYMLINK_LOOP' for i in result.issues),**snapshot(result))
        except RuntimeError as error:check('symlink/resolve_runtime_'+mode,False,uncaught_error=str(error))

result={'source_sha256':{str(p.relative_to(args.source_root.resolve())):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((args.source_root.resolve()/'kgp').glob('*.py'))},
        'probes':len(rows),'passed':sum(r['passed'] for r in rows),'failed':sum(not r['passed'] for r in rows),
        'skipped':skips,'results':rows}
(OUT/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='results'},indent=2))
print(json.dumps([r for r in rows if not r['passed']],indent=2,ensure_ascii=False))
raise SystemExit(1 if result['failed'] else 0)
