"""Enumerate tiny generated catalogs independently of the MILP constraints."""
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kgp.contracts import *
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import ObjectiveWeights, run_kgp_optimizer
import kgp.optimizer as optimizer

nodes = {n: GridNode(n, n, 'F', 0., 0., is_source=n == 'SRC') for n in ('SRC', 'N1', 'N2')}
branches = {b: GridBranch(b, a, c, 'F') for b, a, c in [('B1', 'SRC', 'N1'), ('B2', 'N1', 'N2')]}
switches = {s: GridSwitch(s, b, SwitchState.CLOSED, SwitchState.CLOSED) for s, b in [('SW1', 'B1'), ('SW2', 'B2')]}
assets = {a: GridAsset(a, n, None, 'F', 'TEST') for a, n in [('A1', 'N1'), ('A2', 'N2')]}
zones = {'TRUNK': IsolationZoneSpec('TRUNK', 'trunk', 'F', ('SW1',), prep_duration_hours=.1, recomp_duration_hours=.1),
         'TAIL': IsolationZoneSpec('TAIL', 'tail', 'F', ('SW2',), prep_duration_hours=.1, recomp_duration_hours=.1)}
network = GridNetwork('ENUM', '1', 0, nodes=nodes, branches=branches, switches=switches, assets=assets,
                      isolation_zones=zones, consumers={c: ConsumerUnit(c, n, a) for c, n, a in [('C1', 'N1', 'A1'), ('C2', 'N2', 'A2')]})
catalog = ResourceCatalog(crews={'EQ': CrewResource('EQ', 'crew', ('WORK',), (1, 2), shift_end_hour=12., prep_hours=.1)},
                          planning_days=(1, 2), window_end_hour=12., max_window_duration_hours=4.)
base = [ServiceOrder('J1', 'one', ServiceType.PODA, ('A1',), True, 0, 1, 2, .6, ('WORK',), prep_hours=.1, recomp_hours=.1),
        ServiceOrder('J2', 'two', ServiceType.PODA, ('A2',), True, 0, 1, 2, .8, ('WORK',), prep_hours=.1, recomp_hours=.1)]
results = []
for name, optional, penalty, predecessor in [('mandatory_pair', False, 50., False), ('optional_50', True, 50., False),
                                           ('optional_250', True, 250., False), ('optional_predecessor', True, 50., True)]:
    services = [replace(base[0], precedence_service_ids=('J2',) if predecessor else ()), replace(base[1], mandatory=not optional)]
    weights = ObjectiveWeights(optional_unscheduled_penalty=penalty)
    captured = []
    original = optimizer._generate_day_schedule_columns
    def capture(*args, **kwargs):
        columns = original(*args, **kwargs)
        captured.extend(columns)
        return columns
    with patch.object(optimizer, '_generate_day_schedule_columns', capture):
        solution = run_kgp_optimizer(network, services, catalog, name, weights=weights)
    by_day = {day: [None] + [c for c in captured if c.day == day] for day in catalog.planning_days}
    best, candidates, feasible_count = None, 0, 0
    for selection in product(*(by_day[day] for day in catalog.planning_days)):
        candidates += 1
        windows = []
        for column in selection:
            if column:
                for win in column.windows:
                    wid = 'ENUM_' + str(len(windows))
                    windows.append(replace(win, window_id=wid, crew_intervals=tuple(replace(iv, window_id=wid) for iv in win.crew_intervals)))
        done = tuple(sid for w in windows for sid in w.service_ids)
        plan = ExecutionPlan('ENUM', 'ENUM', name, 0, windows=windows, completed_service_ids=done,
                              unscheduled_service_ids=tuple(s.service_id for s in services if s.service_id not in done))
        report = verify_plan_feasibility(network, services, catalog, plan)
        if not report.is_feasible:
            continue
        feasible_count += 1
        team_hours = sum(iv.recomp_end_hour - iv.prep_start_hour + iv.travel_hours for w in windows for iv in w.crew_intervals)
        omitted = sum(not s.mandatory and s.service_id not in done for s in services)
        value = round(90 * len(windows) + report.recalculated_consumer_hours + 12 * report.recalculated_repeat_consumers +
                      2.5 * report.recalculated_physical_maneuvers + .6 * team_hours + penalty * omitted, 4)
        if best is None or value < best:
            best = value
    assert solution.is_optimal_proven, solution.solver_status
    assert best is not None and abs(solution.solver_objective - best) <= 1e-3
    assert abs(solution.solver_bound - best) <= 1e-3
    results.append({'case': name, 'columns': len(captured), 'enumerated_combinations': candidates,
                    'independently_feasible_combinations': feasible_count, 'enumerated_best_J': best,
                    'solver_J': solution.solver_objective, 'solver_bound': solution.solver_bound, 'matches': True})
target = ROOT / 'reports/ENUMERACAO_INDEPENDENTE_CATALOGO_PEQUENO.json'
target.write_text(json.dumps({'scope': 'Known designed tiny catalogs; no claim of continuous operational optimum', 'results': results}, indent=2), encoding='utf-8')
print(target.read_text(encoding='utf-8'))
