"""Public underflow audit: source bytes + Decimal oracle, no test dependency.

This extends ingestion class 12. Unsupported nonzero-to-zero conversions must
block; true zero and supported canonical subnormal values remain usable.
"""
from __future__ import annotations
from copy import deepcopy
import csv
from dataclasses import asdict
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
from audit_support import TABLES,write_bundle
from kgp.contracts import strict_json_loads,plan_from_json
from kgp.ingestion import import_bundle
from kgp.baselines import run_baseline_b0_individual,run_baseline_b1_geographic,run_baseline_b2_topological_greedy
from kgp.optimizer import run_kgp_optimizer
from kgp.feasibility import verify_plan_feasibility

OUT=ROOT/'.audit_cache'/'banca_audit'/'public_reproduction'/'numeric_underflow'
OUT.mkdir(parents=True,exist_ok=True)
METHODS={'B0':run_baseline_b0_individual,'B1':run_baseline_b1_geographic,
         'B2':run_baseline_b2_topological_greedy,'KGP':run_kgp_optimizer}
rows=[]
started=time.time()
source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT/'src/kgp').glob('*.py'))}

def record(name,condition,**evidence):
    rows.append({'probe':name,'passed':bool(condition),'evidence':evidence})

def source_quantity(path,fmt):
    raw=path.read_bytes()
    if fmt=='csv':
        row=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline='')))[0]
        return Decimal(json.loads(row['required_materials'],parse_float=Decimal)['M'])
    parsed=json.loads(raw.decode('utf-8-sig'),parse_float=Decimal)
    record=parsed[0] if fmt=='json' else parsed['features'][0]['properties']
    return Decimal(record['required_materials']['M'])

def build_literal(fmt,literal,stock=0.):
    tables=deepcopy(TABLES)
    tables['services'][0]['required_materials']={'M':'__SOURCE_NUMBER__'}
    folder=OUT/(fmt+'_'+literal.replace('-','minus').replace('+','plus')+'_'+str(stock))
    manifest=write_bundle(folder,tables,fmt=fmt)
    path=folder/f'services.{fmt}'
    raw=path.read_bytes()
    marker=b'"__SOURCE_NUMBER__"' if fmt!='csv' else b'""__SOURCE_NUMBER__""'
    assert raw.count(marker)==1
    raw=raw.replace(marker,literal.encode('ascii'))
    path.write_bytes(raw)
    data=json.loads(manifest.read_text(encoding='utf-8'))
    data['catalog']['materials_inventory']={'M':stock}
    manifest.write_text(json.dumps(data),encoding='utf-8')
    return manifest,path

def run_methods(name,loaded,should_complete):
    for method,fn in METHODS.items():
        p=fn(loaded.network,loaded.services,loaded.catalog,name)
        p=plan_from_json(json.dumps(asdict(p),allow_nan=False))
        r=verify_plan_feasibility(loaded.network,loaded.services,loaded.catalog,p)
        record(f'{name}/{method}',r.is_feasible==should_complete
               and bool(p.completed_service_ids)==should_complete
               and (should_complete or not p.is_optimal_proven),
               feasible=r.is_feasible,complete=list(p.completed_service_ids),optimal=p.is_optimal_proven,
               solver_status=p.solver_status,materials_exact=r.recalculated_materials_exact,
               violations=r.violations)

# Strict JSON controls, including true-zero mantissas with extreme exponents.
for literal,allowed in [('0',True),('0e-400',True),('-0.000e-400',True),('5e-324',True),
                         ('1e-400',False),('-1e-400',False),('1e-324',False),('1e400',False)]:
    source=('{"M":'+literal+'}').encode('ascii')
    oracle=Decimal(json.loads(source,parse_float=Decimal)['M'])
    error=None
    try:
        parsed=strict_json_loads(source.decode('ascii'))['M']
        accepted=True
    except ValueError as exc:
        parsed=None;accepted=False;error=str(exc)
    record('strict_json/'+literal,accepted==allowed,oracle=str(oracle),accepted=accepted,
           parsed=parsed,error=error)

for fmt in ('json','csv','geojson'):
    for literal,allowed,stock,complete in [
        ('0',True,0.,True),('0e-400',True,0.,True),('-0.000e-400',True,0.,True),
        ('1e-400',False,0.,False),('-1e-400',False,0.,False),('1e400',False,0.,False),
        ('5e-324',True,0.,False),('5e-324',True,5e-324,True),('0.1',True,.1,True),
    ]:
        manifest,path=build_literal(fmt,literal,stock)
        oracle=source_quantity(path,fmt)
        loaded=import_bundle(manifest)
        name=f'{fmt}/literal={literal}/stock={stock}'
        record(name+'/boundary',loaded.is_valid==allowed
               and (allowed or (loaded.network is None and loaded.catalog is None)),
               source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),oracle=str(oracle),
               import_valid=loaded.is_valid,issues=[asdict(x) for x in loaded.issues],
               imported=(loaded.services[0].required_materials['M'] if loaded.is_valid else None))
        if loaded.is_valid:
            # Canonical float decimal equals the intended control. Common .1
            # need not have an exact binary representation; it must stay nonzero.
            quantity=loaded.services[0].required_materials['M']
            record(name+'/sign_preserved',(oracle==0)==(quantity==0),oracle=str(oracle),quantity=quantity)
            run_methods(name,loaded,complete)

# Both independent raw literals and unit-converted physical values need a gate.
# Quantities below are representable *before* applying the declared factor.
for fmt in ('json','csv','geojson'):
    for dimension,table,index,field,unit,factor in [
        ('coordinates','nodes',1,'x','m',Decimal('.001')),
        ('power','consumers',0,'avg_demand_kw','W',Decimal('.001')),
    ]:
        for quantity,allowed in [(5e-324,False),(0.,True),(1.,True)]:
            tables=deepcopy(TABLES)
            tables[table][index][field]=quantity
            folder=OUT/f'unit_{fmt}_{dimension}_{quantity}'
            manifest=write_bundle(folder,tables,fmt=fmt)
            data=json.loads(manifest.read_text(encoding='utf-8'))
            data['units'][dimension]=unit
            manifest.write_text(json.dumps(data),encoding='utf-8')
            loaded=import_bundle(manifest)
            exact=Decimal(str(quantity))*factor
            record(f'unit/{fmt}/{dimension}/{quantity}',loaded.is_valid==allowed
                   and (allowed or (loaded.network is None and loaded.catalog is None)),
                   source_quantity=str(quantity),factor=str(factor),oracle_converted=str(exact),
                   import_valid=loaded.is_valid,issues=[asdict(x) for x in loaded.issues])

result={'scope':'Extension of ingestion class 12: A-CODE-02 source and unit underflow',
        'source_sha256':source_hashes,'elapsed_seconds':round(time.time()-started,3),
        'probes':len(rows),'passed':sum(x['passed'] for x in rows),
        'failed':sum(not x['passed'] for x in rows),'results':rows}
(OUT/'numeric_underflow_results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='results'},indent=2))
print(json.dumps([x for x in rows if not x['passed']],indent=2,ensure_ascii=False))
raise SystemExit(1 if result['failed'] else 0)
