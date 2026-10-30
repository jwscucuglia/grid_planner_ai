"""Prepare a real public MV graph plus a separate, explicit synthetic test overlay."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kgp.contracts import (ConsumerUnit, CrewResource, GridAsset, GridBranch, GridNode,
                           GridSwitch, IsolationZoneSpec, ServiceOrder, ServiceType, SwitchState)

SOURCE = ROOT / 'data/gis_reference/eflul_2025_ctmt002/source'


def _read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def _source_integrity(source):
    source = source.resolve()
    records = json.loads((source / 'manifest_sha256.json').read_text(encoding='utf-8'))
    for record in records:
        path = (source / record['path']).resolve()
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError('source hash verification: invalid file path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('source hash mismatch: ' + record['path'])


def _phase_reachable(rows, source_pac, opened=frozenset()):
    """Reference traversal from raw PAC relations, before the KGP importer."""
    import networkx as nx
    reached = {}
    for phase in 'ABC':
        graph = nx.MultiGraph()
        graph.add_node(source_pac)
        for row in rows:
            if phase not in row['FAS_CON'] or row['P_N_OPE'] == 'A' or row['edge_id'] in opened:
                continue
            graph.add_edge(row['PAC_1'], row['PAC_2'], key=row['edge_id'])
        reached[phase] = set(nx.node_connected_component(graph, source_pac))
    return reached


def prepare_reference_bundle(output_dir: Path, source_dir: Path = SOURCE) -> Path:
    """Preserve every MV PAC/edge and identify all added integration assumptions."""
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    _source_integrity(source_dir)
    source = json.loads((source_dir / 'source.json').read_text(encoding='utf-8'))
    quality = json.loads((source_dir / 'quality_report.json').read_text(encoding='utf-8'))
    raw = _read_csv(source_dir / 'mv_connectivity.csv')
    node_rows = _read_csv(source_dir / 'mv_nodes_display.csv')
    source_pacs = [row['PAC'] for row in node_rows if row['is_source'] == 'True']
    if len(source_pacs) != 1:
        raise ValueError('reference profile requires one declared source')
    source_pac = source_pacs[0]
    phase_sets = {row['PAC']: set() for row in node_rows}
    for row in raw:
        if not row['FAS_CON'] or set(row['FAS_CON']) - set('ABC'):
            raise ValueError('unsupported original phase code: ' + row['FAS_CON'])
        for pac in (row['PAC_1'], row['PAC_2']):
            phase_sets[pac].update(row['FAS_CON'])
    baseline = _phase_reachable(raw, source_pac)
    live = set.union(*baseline.values())
    candidates = []
    for row in sorted(raw, key=lambda r: r['edge_id']):
        if row['source_layer'] != 'UNSEMT' or row['P_N_OPE'] != 'F':
            continue
        state = _phase_reachable(raw, source_pac, frozenset([row['edge_id']]))
        isolated = live - set.union(*state.values())
        if len(isolated) >= 2:
            candidates.append((row['edge_id'], sorted(isolated)))
    if not candidates:
        raise ValueError('reference has no suitable declared closed switch for the test overlay')
    chosen_switch, isolated = candidates[0]
    # Geometry remains in source/* for display. Scheduling coordinates deliberately
    # use a synthetic grid: inconsistent source positions are not treated as roads.
    nodes = [GridNode(pac, pac, '002', index * .01, 0.,
                      tuple(sorted(phase_sets[pac])), pac == source_pac, True, 13.8, 0., 0.)
             for index, pac in enumerate(sorted(phase_sets))]
    by_node = {node.node_id: node for node in nodes}
    branches, switches = [], []
    for row in raw:
        length = float(row['COMP_m']) / 1000 if row['COMP_m'] else 0.
        # Illustrative electrical parameters, not inferred from the source geometry
        # or ampere codes. They are named assumptions of this integration fixture.
        branches.append(GridBranch(row['edge_id'], row['PAC_1'], row['PAC_2'], '002',
                                   tuple(row['FAS_CON']), True, .1 * length, .1 * length,
                                   1000., length))
        if row['source_layer'] == 'UNSEMT':
            state = {'F': SwitchState.CLOSED, 'A': SwitchState.OPEN}[row['P_N_OPE']]
            switches.append(GridSwitch(row['edge_id'], row['edge_id'], state, state,
                                       True, .1, True))
    targets = [isolated[0], isolated[-1]]
    assets, consumers, services = [], [], []
    for index, pac in enumerate(targets, 1):
        node = by_node[pac]
        aid = f'SYN_ASSET_{index}'
        assets.append(GridAsset(aid, pac, None, '002', 'SYNTHETIC_WORK_TARGET',
                                node.phases, True, node.x, node.y))
        phase = next(ph for ph in node.phases if pac in baseline[ph])
        consumers.append(ConsumerUnit(f'SYN_CONSUMER_{index}', pac, aid, phase,
                                      'SYNTHETIC', 1, 1.))
        services.append(ServiceOrder(f'SYN_SERVICE_{index}', f'Synthetic integration work {index}',
                                     ServiceType.PREVENTIVA if index == 1 else ServiceType.PODA,
                                     (aid,), True, 0, 0, 1, 1. if index == 1 else 1.5,
                                     ('ELEC',) if index == 1 else ('PRUNE',),
                                     required_materials={'TEST_MATERIAL': 1.},
                                     compatible_zone_ids=('SYN_ZONE_1',), prep_hours=.1,
                                     recomp_hours=.1, x=node.x, y=node.y))
    zone = IsolationZoneSpec('SYN_ZONE_1', 'Synthetic work scenario on a declared public switch',
                             '002', (chosen_switch,), prep_duration_hours=.25,
                             recomp_duration_hours=.25)
    crews = [CrewResource('SYN_CREW_1', 'Synthetic electrical crew', ('ELEC',), (1,),
                          8., 17., 0., 0., 35., .1),
             CrewResource('SYN_CREW_2', 'Synthetic pruning crew', ('PRUNE',), (1,),
                          8., 17., 0., 0., 35., .1)]
    tables = {'nodes': nodes, 'branches': branches, 'switches': switches, 'assets': assets,
              'consumers': consumers, 'isolation_zones': [zone], 'services': services, 'crews': crews}
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, values in tables.items():
        path = output_dir / f'{name}.json'
        path.write_text(json.dumps([asdict(v) for v in values], indent=2, ensure_ascii=False,
                                   allow_nan=False), encoding='utf-8', newline='\n')
        files[name] = {'path': path.name, 'format': 'json'}
    overlay = {
        'consumers_are_synthetic': True, 'operational_validation': False,
        'all_service_resource_load_and_missing_parameter_values_are_assumptions': True,
        'scheduling_coordinates': 'Synthetic grid x=sorted_node_index*0.01km,y=0; not source geometry or road distance.',
        'loads': 'Only two synthetic consumers, 1kW each; node aggregate and distributed generation set to zero.',
        'branches': 'R=X=0.1ohm/km times declared line length; zero-length switch/regulator equivalents; limit=1000kW.',
        'regulator': 'UNREMT represented as a conducting edge only; tap/voltage regulation not modeled.',
        'switches': 'P_N_OPE normal A/F assumed as current state; all declared maneuverable; 0.1h aggregate duration.',
        'equipment_in_service': 'All extracted MV elements assumed in service for this computational fixture.',
        'intervention': 'First lexicographic closed declared switch isolating at least two normally energized PACs; no transfer.',
        'portfolio': 'Two mandatory synthetic services, 1h/1.5h, one ELEC and one PRUNE crew, one day, explicit materials.',
        'selection_is_not_benefit_tuning': 'Selection uses topology and stable ordering only; no performance target or seed search.',
        'limits': 'Single CTMT excerpt with open boundary stubs; no real UC, LV, actual switching order, AC load flow, or Cemig data.'}
    manifest = {
        'schema_version': 'kgp.ingest.v1', 'decision_day': 0,
        'network': {'network_id': 'PUBLIC_BDGD_EFLUL_2025_CTMT002_WITH_SYNTHETIC_OVERLAY',
                    'version': 'ingestion01', 'valid_from_day': 0, 'coordinate_system': 'CARTESIAN_KM',
                    'origin': 'Real public ANEEL/EFLUL MV connectivity plus declared synthetic integration assumptions',
                    'license': 'ODbL for derived public database; see source/NOTICE.md'},
        'catalog': {'materials_inventory': {'TEST_MATERIAL': 2.}, 'planning_days': [1],
                    'window_start_hour': 8., 'window_end_hour': 17., 'max_window_duration_hours': 8.},
        'units': {'coordinates': 'km', 'length': 'km', 'power': 'kW', 'voltage': 'kV',
                  'impedance': 'ohm', 'duration': 'h'}, 'tables': files,
        'provenance': {'public_source': source, 'synthetic_overlay': overlay,
                       'source_quality': {'coordinate_conflicts_are_preserved_in_source_report': True,
                                          'source_geometry_is_display_only': True},
                       'source_counts': {'nodes': len(nodes), 'edges': len(raw),
                                         'normal_energized_any_phase': len(live)},
                       'selected_declared_switch': chosen_switch,
                       'independent_source_phase_reachability': {ph: sorted(ids) for ph, ids in baseline.items()}}}
    path = output_dir / 'manifest.json'
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
                    encoding='utf-8', newline='\n')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/gis_reference/eflul_2025_ctmt002/bundle')
    parser.add_argument('--source', type=Path, default=SOURCE)
    args = parser.parse_args()
    print(prepare_reference_bundle(args.output, args.source))
