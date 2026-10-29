"""Reproduce public-GIS import preservation and a synthetic planning integration."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kgp import __version__
from kgp.baselines import (run_baseline_b0_individual, run_baseline_b1_geographic,
                           run_baseline_b2_topological_greedy)
from kgp.contracts import plan_from_json
from kgp.feasibility import verify_plan_feasibility, _independent_energized_phases_and_sources
from kgp.ingestion import import_bundle
from kgp.metrics import compute_plan_metrics
from kgp.optimizer import run_kgp_optimizer
from kgp.topology import compute_energized_phases_by_node
from prepare_gis_reference import prepare_reference_bundle, SOURCE, _read_csv, _phase_reachable

OUT = ROOT / 'reports/ingestion_REV05_20261029'


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False),
                    encoding='utf-8', newline='\n')


def _figure(output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    features = json.loads((SOURCE / 'SSDMT.geojson').read_text(encoding='utf-8'))['features']
    lines = []
    for feature in features:
        geometry = feature['geometry']
        if geometry['type'] == 'LineString':
            lines.append(geometry['coordinates'])
        elif geometry['type'] == 'MultiLineString':
            lines.extend(geometry['coordinates'])
    node_features = json.loads((SOURCE / 'mv_nodes_display.geojson').read_text(encoding='utf-8'))['features']
    points = [feature['geometry']['coordinates'] for feature in node_features]
    fig, ax = plt.subplots(figsize=(16, 9), facecolor='white')
    ax.add_collection(LineCollection([[(p[0], p[1]) for p in line] for line in lines],
                                     colors='#087f8c', linewidths=1.2, label='Geometria dos 314 segmentos MV'))
    ax.scatter([p[0] for p in points], [p[1] for p in points], s=8, color='#233142',
               label='354 PACs; posições representativas para exibição')
    ax.autoscale()
    mean_lat = sum(p[1] for p in points) / len(points)
    ax.set_aspect(1 / math.cos(math.radians(mean_lat)))
    ax.set_xlabel('Longitude (graus, geometria exportada EPSG:4326)')
    ax.set_ylabel('Latitude (graus)')
    ax.set_title('GIS público real importado: BDGD/ANEEL — EFLUL, CTMT 002\n'
                 'Conectividade preservada por PAC_1/PAC_2; geometria não determina ligações',
                 loc='left', fontsize=17, fontweight='bold', pad=18)
    ax.grid(alpha=.15)
    ax.legend(loc='best', frameon=False)
    fig.text(.06, .035, 'Fonte: ANEEL/EFLUL, 31/12/2025, ODbL. 81 PACs têm posições conflitantes (até 64 m).\n'
             'Ensaio de programação: consumidores, cargas, recursos, parâmetros adicionais e coordenadas de agenda são sintéticos. '
             'Sem validação operacional ou dados Cemig.', fontsize=10, color='#555555')
    fig.subplots_adjust(bottom=.16, top=.86, left=.08, right=.95)
    fig.savefig(output, dpi=130, facecolor='white')
    plt.close(fig)


def main():
    manifest = prepare_reference_bundle(ROOT / 'data/gis_reference/eflul_2025_ctmt002/bundle')
    imported = import_bundle(manifest)
    assert imported.is_valid, imported.issues
    network, services, catalog = imported.network, imported.services, imported.catalog
    _write(OUT / 'import_quality.json', {'is_valid': imported.is_valid,
                                        'issues': [asdict(i) for i in imported.issues],
                                        'provenance': imported.provenance})
    raw = _read_csv(SOURCE / 'mv_connectivity.csv')
    manifest_data = json.loads(manifest.read_text(encoding='utf-8'))
    source_pac = next(n.node_id for n in network.nodes.values() if n.is_source)
    assert set(network.nodes) == {row[key] for row in raw for key in ('PAC_1', 'PAC_2')}
    for row in raw:
        branch = network.branches[row['edge_id']]
        assert (branch.from_node, branch.to_node, branch.phases) == (row['PAC_1'], row['PAC_2'], tuple(row['FAS_CON']))
    phase_checks = []
    zone = next(iter(network.isolation_zones.values()))
    for label, opened in [('normal', set()), ('test_isolation', set(zone.open_switches)), ('restored', set())]:
        expected = _phase_reachable(raw, source_pac, frozenset(opened))
        actual, _, _ = compute_energized_phases_by_node(network, opened, set())
        independent, _, _ = _independent_energized_phases_and_sources(network, opened, set())
        counts = {}
        for phase in 'ABC':
            got = {node for node, phases in actual.items() if phase in phases}
            checked = {node for node, phases in independent.items() if phase in phases}
            assert got == checked == expected[phase], (label, phase)
            counts[phase] = len(got)
        phase_checks.append({'stage': label, 'phase_node_counts': counts,
                             'source_reference_equals_bfs_and_union_find': True})
    methods = {'B0': run_baseline_b0_individual, 'B1': run_baseline_b1_geographic,
               'B2': run_baseline_b2_topological_greedy, 'KGP': run_kgp_optimizer}
    results = {}
    for name, method in methods.items():
        plan = method(network, services, catalog, scenario_id='PUBLIC_GIS_SYNTHETIC_OVERLAY', decision_day=0)
        _write(OUT / 'plans' / f'{name}.json', asdict(plan))
        exported = plan_from_json((OUT / 'plans' / f'{name}.json').read_text(encoding='utf-8'))
        audit = verify_plan_feasibility(network, services, catalog, exported)
        assert audit.is_feasible and audit.mandatory_portfolio_complete, (name, audit.violations)
        assert len(audit.recalculated_completed_services) == len(services) == 2
        _write(OUT / 'verification' / f'{name}.json', asdict(audit))
        results[name] = asdict(compute_plan_metrics(network, services, exported, audit))
    summary = {'scope': 'REAL_PUBLIC_MV_TOPOLOGY_WITH_SYNTHETIC_PLANNING_OVERLAY',
               'package_version': __version__,
               'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'operational_validation': False, 'cemig_data_used': False,
               'source': manifest_data['provenance']['public_source'],
               'synthetic_overlay': manifest_data['provenance']['synthetic_overlay'],
               'nodes_preserved': len(network.nodes), 'edges_preserved': len(network.branches),
               'closed_switches': sum(sw.current_state.value == 'CLOSED' for sw in network.switches.values()),
               'open_switches': sum(sw.current_state.value == 'OPEN' for sw in network.switches.values()),
               'phase_reference_checks': phase_checks, 'feasible_plans': 4, 'mandatory_services_per_plan': 2,
               'methods': results,
               'interpretation': 'Functional import/integration test only. No operational or financial benefit is estimated; '
                                 'B1 uses synthetic scheduling positions, not real geographic scheduling.'}
    _write(OUT / 'summary.json', summary)
    _figure(OUT / 'gis_public_reference.png')
    (OUT / 'RELATORIO_INGESTAO01.md').write_text(
        '# REV05 — revalidação do GIS público e integração ao KGP\n\n'
        'Fonte real: BDGD/ANEEL EFLUL 31/12/2025, CTMT 002. Os 354 PACs e 358 registros de ligação '
        'foram preservados, incluindo paralelas e estados normais das 43 chaves (33 fechadas, 10 abertas). '
        'A exportação CSV/GeoJSON foi conferida contra a GDB original e tem licença/proveniência próprias.\n\n'
        'A referência de alcançabilidade por fase dos registros de origem coincide com BFS e Union-Find '
        'na configuração normal, no isolamento do ensaio e na restauração.\n\n'
        'O importador produziu os contratos existentes e o mesmo núcleo executou B0/B1/B2/KGP. '
        'Os quatro planos foram exportados, relidos e aprovados pelo verificador, com 2/2 serviços obrigatórios em cada um.\n\n'
        '**Limite:** topologia pública real com overlay sintético de cargas, consumidores, carteira, recursos, '
        'parâmetros ausentes, cenário de manobra e coordenadas de agenda. O regulador é somente ligação passante. '
        'O estado normal das chaves foi assumido como estado do ensaio; não é telemetria histórica.\n\n'
        'A fonte apresenta 81 PACs com posições divergentes, até aproximadamente 64m; suas geometrias ficam '
        'restritas à visualização/QC. As posições de programação são sintéticas, sem interpretação de deslocamento real. '
        'Não há UC real, BT, dado Cemig, fluxo AC/proteção homologados ou estimativa de benefício operacional/financeiro.\n\n'
        'Reprodução: `python scripts/prepare_gis_reference.py`; '
        '`python scripts/import_kgp_bundle.py data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json`; '
        '`python scripts/validate_gis_ingestion.py`. Consultar `summary.json`, `import_quality.json`, '
        '`plans/`, `verification/` e `data/gis_reference/eflul_2025_ctmt002/source/NOTICE.md`.\n',
        encoding='utf-8', newline='\n')
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for base in (OUT, ROOT / 'data/gis_reference/eflul_2025_ctmt002/bundle')
             for p in sorted(base.rglob('*')) if p.is_file() and p.name != 'manifest_sha256.json'}
    _write(OUT / 'manifest_sha256.json', {'scope': summary['scope'], 'files': files})
    print(json.dumps({'nodes': len(network.nodes), 'edges': len(network.branches), 'feasible_plans': 4,
                      'mandatory_services_per_plan': 2, 'phase_reference_checks': 9,
                      'operational_validation': False}, indent=2))


if __name__ == '__main__':
    main()
