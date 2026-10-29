"""Validate released inputs, serialized plans, independently recalculated J and hashes."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kgp.contracts import _decode_typed, strict_json_loads, plan_from_json
from kgp.experiment import ScenarioInstance
from kgp.feasibility import verify_plan_feasibility

summary = strict_json_loads((ROOT / 'reports/kgp_evaluation_summary.json').read_text(encoding='utf-8'))
weights = summary['protocol_config']['objective_weights']
checks = []
for sid, entry in summary['scenarios_detail'].items():
    sc = _decode_typed(strict_json_loads((ROOT / f'reports/revalidation_20261029/inputs/{sid}.json').read_text(encoding='utf-8')),
                       ScenarioInstance, 'scenario')
    for method in ('B0', 'B1', 'B2', 'KGP'):
        plan = plan_from_json((ROOT / f'reports/revalidation_20261029/plans/{sid}_{method}.json').read_text(encoding='utf-8'))
        report = verify_plan_feasibility(sc.network, sc.services, sc.catalog, plan)
        assert report.is_feasible and report.mandatory_portfolio_complete, (sid, method, report.violations)
        assert report.recalculated_consumer_hours == entry['methods'][method]['metrics']['consumer_hours_total']
        independent_objective = None
        if method == 'KGP':
            crew_hours = sum(iv.recomp_end_hour - iv.prep_start_hour + iv.travel_hours for win in plan.windows for iv in win.crew_intervals)
            optional_omitted = sum(not svc.mandatory and svc.scope_status == 'ACTIVE' and svc.created_at_day <= plan.decision_day
                                   and svc.service_id not in report.recalculated_completed_services for svc in sc.services)
            independent_objective = round(weights['window_weight'] * report.recalculated_windows_count +
                                          weights['consumer_hour_weight'] * report.recalculated_consumer_hours +
                                          weights['repeat_consumer_weight'] * report.recalculated_repeat_consumers +
                                          weights['physical_maneuver_weight'] * report.recalculated_physical_maneuvers +
                                          weights['crew_hour_weight'] * crew_hours +
                                          weights['optional_unscheduled_penalty'] * optional_omitted, 4)
            assert abs(independent_objective - plan.solver_objective) <= 1e-3, (sid, independent_objective, plan.solver_objective)
            if plan.is_optimal_proven:
                assert plan.solver_status == 'OPTIMAL_ON_DISCRETE_COLUMNS'
                assert abs(plan.solver_bound - independent_objective) <= 1e-3 and plan.solver_gap <= 1e-4
        checks.append({'scenario': sid, 'method': method, 'feasible': True,
                       'consumer_hours': report.recalculated_consumer_hours, 'independent_objective': independent_objective})
manifest = strict_json_loads((ROOT / 'reports/manifest_sha256.json').read_text(encoding='utf-8'))
failed = [name for name, digest in manifest['files'].items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
assert not failed, failed
result = {'plans_verified_from_frozen_inputs': len(checks), 'kgp_objectives_recalculated_independently': sum(c['independent_objective'] is not None for c in checks),
          'manifest_hashes_checked': len(manifest['files']), 'hash_failures': failed, 'checks': checks}
(ROOT / '.audit_cache/delivery_validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'checks'}, indent=2))
