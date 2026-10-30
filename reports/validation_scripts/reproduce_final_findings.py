"""Small independent review reproductions; production sources remain unchanged.

Run: ./.venv/Scripts/python.exe .audit_cache/final_review/reproduce_final_findings.py
Outputs observed behavior; after fixes use the expected values in review report.
"""
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from kgp.baselines import run_baseline_b1_geographic
from kgp.experiment import ScenarioInstance, evaluate_scenario_all_methods
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import ObjectiveWeights, compute_exact_plan_objective, run_kgp_optimizer
from kgp.revalidation import run_revalidation

spec = importlib.util.spec_from_file_location('small', ROOT / 'tests/test_optimizer_audit.py')
small = importlib.util.module_from_spec(spec)
spec.loader.exec_module(small)

net, cat, jobs = small.small_instance(mandatory=True)
stopped = SimpleNamespace(x=None, status=1, mip_dual_bound=0., mip_gap=None, fun=None)
with patch('kgp.optimizer.milp', return_value=stopped):
    p = run_kgp_optimizer(net, jobs, cat, 'timeout_without_solver_incumbent')
print('TIMEOUT_FALLBACK', {
    'feasible': verify_plan_feasibility(net, jobs, cat, p).is_feasible,
    'objective': p.solver_objective, 'bound': p.solver_bound, 'gap': p.solver_gap,
    'status': p.solver_status,
    'true_objective': compute_exact_plan_objective(p, ObjectiveWeights(), jobs),
    'optimality_scope': p.metadata.get('optimality_scope'),
})

bad_jobs = [replace(jobs[0], required_skills=('missing',))]
sc = ScenarioInstance('FINAL_REVIEW_INVALID', 'EVAL', 1, 'small impossible skills', 0, net, bad_jobs, cat)
dest = ROOT / '.audit_cache/final_review/stale_artifact'
(dest / 'reports').mkdir(parents=True, exist_ok=True)
old = dest / 'reports/comparativo_kgp_desafio07.png'
old.write_bytes(b'OLD SUCCESSFUL STUDY FIGURE')
with patch('kgp.experiment.DEV_SEEDS', ()), patch('kgp.experiment.EVAL_SEEDS', (1,)), patch('kgp.experiment.generate_scenario_instance', return_value=sc):
    result = run_revalidation(dest)
manifest = json.loads((dest / 'reports/manifest_sha256.json').read_text())
print('STALE_FIGURE', {
    'comparable': len(result['paired_eval_comparisons']),
    'old_figure_unchanged': old.exists() and old.read_bytes() == b'OLD SUCCESSFUL STUDY FIGURE',
    'figure_in_new_manifest': 'reports/comparativo_kgp_desafio07.png' in manifest['files'],
})

sc = ScenarioInstance('SEED_RADIUS', 'EVAL', 1, 'small', 0, net, jobs, cat)
calls = []
def spy(*args, **kwargs):
    calls.append(kwargs.get('radius_km', 1.25))
    return run_baseline_b1_geographic(*args, **kwargs)
with patch('kgp.experiment.run_baseline_b1_geographic', spy), patch('kgp.optimizer.run_baseline_b1_geographic', spy):
    evaluate_scenario_all_methods(sc, b1_radius_km=.75)
print('B1_RADIUS_COMPARATOR_THEN_SEED', calls)
