"""Standalone, read-only scientific audit witness (run with project venv)."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from audit_support import TABLES, write_bundle
from kgp.ingestion import import_bundle
from kgp.baselines import run_baseline_b0_individual, run_baseline_b1_geographic, run_baseline_b2_topological_greedy
from kgp.optimizer import run_kgp_optimizer
from kgp.contracts import plan_from_json
from kgp.feasibility import verify_plan_feasibility

OUT = ROOT / '.audit_cache' / 'banca_audit' / 'public_reproduction'
OUT.mkdir(parents=True, exist_ok=True)
tables = deepcopy(TABLES)
tables['services'][0]['required_materials'] = {'M': 0.00004}
manifest = write_bundle(OUT / 'material_bundle', tables)
loaded = import_bundle(manifest)
assert loaded.is_valid, loaded.issues
results = {'import_valid': loaded.is_valid, 'exact_required': 0.00004, 'available': 0.0, 'methods': {}}
methods = {'B0': run_baseline_b0_individual, 'B1': run_baseline_b1_geographic,
           'B2': run_baseline_b2_topological_greedy, 'KGP': run_kgp_optimizer}
for name, fn in methods.items():
    plan = fn(loaded.network, loaded.services, loaded.catalog, 'material_probe', decision_day=0)
    raw = json.dumps(asdict(plan), ensure_ascii=False, allow_nan=False)
    (OUT / f'material_{name}_plan.json').write_text(raw, encoding='utf-8')
    restored = plan_from_json(raw)
    report = verify_plan_feasibility(loaded.network, loaded.services, loaded.catalog, restored)
    results['methods'][name] = {'feasible': report.is_feasible, 'completed': list(plan.completed_service_ids),
        'recalculated_materials': report.recalculated_materials,
        'exported_materials': [w.materials_used for w in plan.windows],
        'solver_status': plan.solver_status, 'optimal_proven': plan.is_optimal_proven,
        'violations': report.violations}
(OUT / 'material_probe_results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
