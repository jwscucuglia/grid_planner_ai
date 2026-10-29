"""Explicit public audit fixtures; no dependency on pytest or test modules.

The fixture describes a synthetic one-phase reference and work portfolio. It
does not import external/corporate data. Runtime writes are confined to the
directory passed to write_bundle by each audit script.
"""
from copy import deepcopy
from dataclasses import replace
import csv
import json
from pathlib import Path

from kgp.contracts import (
    ConsumerUnit, CrewResource, ExecutionPlan, GridAsset, GridBranch,
    GridNetwork, GridNode, GridSwitch, IsolationZoneSpec, ResourceCatalog,
    ServiceOrder, ServiceType, SwitchState,
)
from kgp.scheduling import build_window_schedule

TABLES = {
    'nodes': [
        dict(node_id='SRC', name='Source', feeder_id='F1', x=0., y=0., phases=['A'],
             is_source=True, in_service=True, nominal_voltage_kv=13.8, load_kw=0., distributed_gen_kw=0.),
        dict(node_id='N1', name='Load', feeder_id='F1', x=1., y=0., phases=['A'],
             is_source=False, in_service=True, nominal_voltage_kv=13.8, load_kw=2., distributed_gen_kw=0.),
    ],
    'branches': [dict(branch_id='B1', from_node='SRC', to_node='N1', feeder_id='F1', phases=['A'],
                      in_service=True, r_ohm=.1, x_ohm=.1, max_capacity_kw=100., length_km=1.)],
    'switches': [dict(switch_id='SW1', branch_id='B1', normal_state='CLOSED', current_state='CLOSED',
                      is_maneuverable=True, maneuver_duration_hours=.1, in_service=True)],
    'assets': [dict(asset_id='A1', node_id='N1', branch_id=None, feeder_id='F1', asset_type='POLE',
                    phases=['A'], in_service=True, x=1., y=0.)],
    'consumers': [dict(consumer_id='C1', node_id='N1', asset_id='A1', phase='A', avg_demand_kw=2.)],
    'isolation_zones': [dict(zone_id='Z1', name='Zone', feeder_id='F1', open_switches=['SW1'],
                            prep_duration_hours=.1, recomp_duration_hours=.1)],
    'services': [dict(service_id='S1', title='Work', service_type='PREVENTIVA', target_assets=['A1'],
                      mandatory=True, created_at_day=0, ready_at_day=0, deadline_day=1, duration_hours=1.,
                      required_skills=['ELEC'], prep_hours=.1, recomp_hours=.1,
                      required_crews_count=1, required_materials={}, precedence_service_ids=[],
                      requires_deenergized=True, allow_simultaneous_in_zone=True, scope_status='ACTIVE',
                      compatible_zone_ids=['Z1'], x=1., y=0.)],
    'crews': [dict(crew_id='E1', name='Crew', skills=['ELEC'], available_days=[1], shift_start_hour=8.,
                   shift_end_hour=17., base_x=1., base_y=0., speed_kmh=30., prep_hours=.1)],
}


def write_bundle(root: Path, tables=None, fmt='json') -> Path:
    root.mkdir(parents=True, exist_ok=True)
    tables = deepcopy(TABLES if tables is None else tables)
    manifest = dict(
        schema_version='kgp.ingest.v1', decision_day=0,
        network=dict(network_id='GIS_TEST', version='1', valid_from_day=0,
                     coordinate_system='CARTESIAN_KM', origin='Test reference', license='CC0'),
        catalog=dict(materials_inventory={}, planning_days=[1], window_start_hour=8.,
                     window_end_hour=17., max_window_duration_hours=8.),
        units=dict(coordinates='km', length='km', power='kW', voltage='kV', impedance='ohm', duration='h'),
        provenance=dict(source='public synthetic audit fixture', synthetic_overlay='load and work assumptions'),
        tables={})
    for name, records in tables.items():
        target = root / f'{name}.{fmt}'
        if fmt == 'csv':
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]) if records else ['id'])
                writer.writeheader()
                writer.writerows({k: json.dumps(v) if not isinstance(v, str) else v for k,v in row.items()}
                                 for row in records)
        else:
            body = records if fmt == 'json' else dict(type='FeatureCollection', features=[
                dict(type='Feature', geometry=None, properties=row) for row in records])
            target.write_text(json.dumps(body, allow_nan=False), encoding='utf-8')
        manifest['tables'][name] = dict(path=target.name, format=fmt)
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def timed_case(gap=0.0):
    net = GridNetwork('time','test',0,
        nodes={'SRC':GridNode('SRC','source','F',0.,0.,phases=('A',),is_source=True),
               'N':GridNode('N','load','F',0.,0.,phases=('A',))},
        branches={'B':GridBranch('B','SRC','N','F',phases=('A',))},
        switches={'SW':GridSwitch('SW','B',SwitchState.CLOSED,SwitchState.CLOSED)},
        assets={'AS':GridAsset('AS','N',None,'F','TEST',phases=('A',))},
        consumers={'C':ConsumerUnit('C','N','AS')},
        isolation_zones={'Z':IsolationZoneSpec('Z','zone','F',('SW',),prep_duration_hours=0.,recomp_duration_hours=0.)})
    jobs=[ServiceOrder(k,k,ServiceType.PODA,('AS',),True,0,1,1,1.,(k,),prep_hours=0.,recomp_hours=0.)
          for k in ('S1','S2')]
    cat=ResourceCatalog(crews={k:CrewResource(k,k,(k,),(1,),prep_hours=0.) for k in ('S1','S2')},planning_days=(1,))
    first,error=build_window_schedule(net,cat,'Z',1,'W1',jobs[:1],earliest_window_start_hour=9.)
    assert first,error
    second,error=build_window_schedule(net,cat,'Z',1,'W2',jobs[1:],earliest_window_start_hour=first.end_hour+gap)
    assert second,error
    return net,jobs,cat,ExecutionPlan('time','CUSTOM','time',0,windows=[first,second],completed_service_ids=('S1','S2'))


def shift_window(win,delta):
    return replace(win,
        **{name:getattr(win,name)+delta for name in ('start_hour','work_start_hour','work_end_hour','end_hour')},
        maneuver_steps=tuple(replace(st,start_hour=st.start_hour+delta,end_hour=st.end_hour+delta) for st in win.maneuver_steps),
        crew_intervals=tuple(replace(iv,**{name:getattr(iv,name)+delta for name in
                             ('travel_start_hour','prep_start_hour','exec_start_hour','exec_end_hour','recomp_end_hour')})
                             for iv in win.crew_intervals))
