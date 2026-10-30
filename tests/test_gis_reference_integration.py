"""Public GIS preservation and end-to-end checks with an explicit synthetic overlay."""
import csv
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from kgp.baselines import (run_baseline_b0_individual, run_baseline_b1_geographic,
                           run_baseline_b2_topological_greedy)
from kgp.contracts import plan_from_json
from kgp.feasibility import verify_plan_feasibility
from kgp.ingestion import import_bundle
from kgp.optimizer import run_kgp_optimizer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/gis_reference/eflul_2025_ctmt002/source'


def prepare(output, source=SOURCE):
    path = ROOT / 'scripts/prepare_gis_reference.py'
    assert path.exists(), 'Public GIS adapter is not implemented'
    spec = importlib.util.spec_from_file_location('public_gis_adapter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_reference_bundle(output, source)


def test_imported_public_graph_preserves_all_declared_connections_and_switches(tmp_path):
    manifest = prepare(tmp_path)
    result = import_bundle(manifest)
    assert result.is_valid, result.issues
    rows = list(csv.DictReader((SOURCE / 'mv_connectivity.csv').open(encoding='utf-8')))
    expected_nodes = {r[k] for r in rows for k in ('PAC_1', 'PAC_2')}
    assert set(result.network.nodes) == expected_nodes
    assert len(expected_nodes) == 354
    assert len(result.network.branches) == len(rows) == 358
    for row in rows:
        branch = result.network.branches[row['edge_id']]
        assert (branch.from_node, branch.to_node) == (row['PAC_1'], row['PAC_2'])
        assert branch.phases == tuple(row['FAS_CON'])
        if row['source_layer'] == 'UNSEMT':
            switch = result.network.switches[row['edge_id']]
            assert switch.current_state.value == {'A': 'OPEN', 'F': 'CLOSED'}[row['P_N_OPE']]
    data = json.loads(Path(manifest).read_text(encoding='utf-8'))
    assert data['provenance']['public_source']['zip_sha256'] == '35a071a48728cd3bf5dbd9aa960026c9f6290f04c3d4abea25038eae476df168'
    assert data['provenance']['synthetic_overlay']['consumers_are_synthetic'] is True
    assert data['provenance']['synthetic_overlay']['operational_validation'] is False


@pytest.mark.parametrize('method', ['B0', 'B1', 'B2', 'KGP'])
def test_public_gis_multigraph_preserves_input_and_blocks_unsupported_physical_plans(tmp_path, method):
    result = import_bundle(prepare(tmp_path))
    assert result.is_valid, result.issues
    funcs = {'B0': run_baseline_b0_individual, 'B1': run_baseline_b1_geographic,
             'B2': run_baseline_b2_topological_greedy, 'KGP': run_kgp_optimizer}
    plan = funcs[method](result.network, result.services, result.catalog,
                         scenario_id='PUBLIC_GIS_SYNTHETIC_OVERLAY', decision_day=0)
    exported = plan_from_json(json.dumps(asdict(plan), allow_nan=False))
    audit = verify_plan_feasibility(result.network, result.services, result.catalog, exported)
    assert not audit.is_feasible
    assert not audit.mandatory_portfolio_complete
    assert plan.solver_status == 'UNSUPPORTED_INPUT'
    assert not plan.is_optimal_proven
    assert not plan.completed_service_ids
    assert set(plan.unscheduled_service_ids) == {s.service_id for s in result.services}
    assert len(result.network.nodes) == 354 and len(result.network.branches) == 358
    assert len(result.services) == 2


def test_altered_source_is_not_silently_imported_as_verified_public_reference(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(SOURCE, source)
    path = source / 'mv_connectivity.csv'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='hash'):
        prepare(tmp_path / 'bundle', source)


def test_orphan_connection_in_normalized_bundle_blocks_planning(tmp_path):
    manifest = prepare(tmp_path)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    path = manifest.parent / data['tables']['branches']['path']
    rows = json.loads(path.read_text(encoding='utf-8'))
    rows[0]['to_node'] = 'UNKNOWN_TERMINAL'
    path.write_text(json.dumps(rows), encoding='utf-8')
    result = import_bundle(manifest)
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(issue.code == 'ORPHAN_REFERENCE' for issue in result.issues)
