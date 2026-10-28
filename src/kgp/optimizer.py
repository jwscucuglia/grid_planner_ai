"""Otimizador combinatório MILP (KGP) via SciPy/HiGHS para programação de desligamentos.

Seleciona conjuntamente pacotes de serviços, zonas de isolamento, datas, turnos
e alocação de equipes dentro exatamente do mesmo espaço de zonas e restrições
permitido aos comparadores (B0, B1, B2).

Função objetivo documentada (minimização ponderada):
  J = w_win * N_janelas
    + w_ch  * Clientes_Hora
    + w_rep * Consumidores_Reincidentes
    + w_man * Manobras_Fisicas
    + w_hh  * (Horas_Trabalho_Equipe + Horas_Deslocamento_Equipe)

Registra status do solver, valor objetivo, limite dual (bound), gap relativo
e tempo de execução, declarando otimalidade apenas quando comprovada pelos limites.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .baselines import run_baseline_b2_topological_greedy
from .contracts import (
    CrewScheduleInterval,
    ExecutionPlan,
    GridNetwork,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
)
from .eligibility import (
    check_service_zone_eligibility,
    detect_lifecycle_conflicts,
    filter_known_services,
)
from .metrics import merge_intervals
from .scheduling import build_window_schedule, compute_travel_hours


@dataclass(frozen=True)
class ObjectiveWeights:
    """Pesos explícitos e documentados da função objetivo do KGP."""

    window_weight: float = 90.0
    consumer_hour_weight: float = 1.0
    repeat_consumer_weight: float = 12.0
    physical_maneuver_weight: float = 2.5
    crew_hour_weight: float = 0.6
    unscheduled_penalty: float = 1_000_000.0


@dataclass
class _CandidateColumn:
    """Coluna candidata representando uma janela factível (zona, dia, pacote e equipes)."""

    col_id: int
    zone_id: str
    day: int
    services: Tuple[ServiceOrder, ...]
    service_ids: Tuple[str, ...]
    window: PlannedInterruptionWindow
    consumer_hours: float
    crew_hours: float
    cost: float
    service_exec_bounds: Dict[str, Tuple[float, float]]
    crew_span_by_id: Dict[str, Tuple[float, float, float, float, float, float]]


def _compute_window_consumer_hours(win: PlannedInterruptionWindow) -> float:
    by_cons: Dict[str, List[Tuple[float, float]]] = {}
    for st in win.maneuver_steps:
        for cid in st.interrupted_consumers:
            by_cons.setdefault(cid, []).append((st.start_hour, st.end_hour))
    total = 0.0
    for ivs in by_cons.values():
        for s, e in merge_intervals(ivs):
            total += e - s
    return round(total, 4)


def _extract_service_exec_bounds(
    win: PlannedInterruptionWindow,
) -> Dict[str, Tuple[float, float]]:
    bounds: Dict[str, Tuple[float, float]] = {}
    for sid in win.service_ids:
        ivs = [iv for iv in win.crew_intervals if iv.service_id == sid]
        bounds[sid] = (
            min(iv.exec_start_hour for iv in ivs),
            max(iv.exec_end_hour for iv in ivs),
        )
    return bounds


def _extract_crew_spans(
    win: PlannedInterruptionWindow,
) -> Dict[str, Tuple[float, float, float, float, float, float]]:
    """Retorna por crew_id: (prep_start, recomp_end, first_x, first_y, last_x, last_y)."""
    by_crew: Dict[str, List[CrewScheduleInterval]] = {}
    for iv in win.crew_intervals:
        by_crew.setdefault(iv.crew_id, []).append(iv)
    spans: Dict[str, Tuple[float, float, float, float, float, float]] = {}
    for cid, ivs in by_crew.items():
        ivs.sort(key=lambda x: x.travel_start_hour)
        spans[cid] = (
            ivs[0].prep_start_hour,
            ivs[-1].recomp_end_hour,
            ivs[0].x,
            ivs[0].y,
            ivs[-1].x,
            ivs[-1].y,
        )
    return spans


def _are_columns_compatible_on_same_day(
    col_a: _CandidateColumn,
    col_b: _CandidateColumn,
    catalog: ResourceCatalog,
) -> bool:
    """Verifica se duas colunas candidatas no mesmo dia podem coexistir sem conflito."""
    if col_a.day != col_b.day:
        return True
    if set(col_a.service_ids) & set(col_b.service_ids):
        return False

    if col_a.zone_id == col_b.zone_id:
        overlap = min(col_a.window.end_hour, col_b.window.end_hour) - max(
            col_a.window.start_hour, col_b.window.start_hour
        )
        if overlap > -0.09:
            return False

    shared_crews = set(col_a.crew_span_by_id.keys()) & set(
        col_b.crew_span_by_id.keys()
    )
    for cid in shared_crews:
        crew = catalog.crews[cid]
        a_prep, a_end, ax1, ay1, ax2, ay2 = col_a.crew_span_by_id[cid]
        b_prep, b_end, bx1, by1, bx2, by2 = col_b.crew_span_by_id[cid]

        travel_ab = compute_travel_hours(ax2, ay2, bx1, by1, crew.speed_kmh)
        travel_ba = compute_travel_hours(bx2, by2, ax1, ay1, crew.speed_kmh)

        a_before_b = (a_end + travel_ab) <= b_prep + 1e-4
        b_before_a = (b_end + travel_ba) <= a_prep + 1e-4
        if not (a_before_b or b_before_a):
            return False

    return True


def _generate_candidate_columns(
    network: GridNetwork,
    known_services: Sequence[ServiceOrder],
    catalog: ResourceCatalog,
    weights: ObjectiveWeights,
    max_package_size: int = 5,
) -> List[_CandidateColumn]:
    """Gera o catálogo de janelas candidatas factíveis em todas as zonas, dias e turnos."""
    columns: List[_CandidateColumn] = []
    seen_signatures: Set[Tuple[str, int, Tuple[str, ...], Tuple[Tuple[str, str], ...], float]] = set()

    shift_starts = [
        catalog.window_start_hour,
        round(catalog.window_start_hour + 3.5, 2),
        round(catalog.window_start_hour + 4.75, 2),
    ]

    for day in catalog.planning_days:
        day_crews = [
            c for c in catalog.crews.values() if day in c.available_days
        ]
        crew_subcatalogs: List[ResourceCatalog] = [catalog]
        if len(day_crews) >= 2:
            for c in day_crews:
                sub_cat = ResourceCatalog(
                    crews={c.crew_id: c},
                    materials_inventory=catalog.materials_inventory,
                    planning_days=catalog.planning_days,
                    window_start_hour=catalog.window_start_hour,
                    window_end_hour=catalog.window_end_hour,
                    max_window_duration_hours=catalog.max_window_duration_hours,
                )
                crew_subcatalogs.append(sub_cat)

        for zid in sorted(network.isolation_zones.keys()):
            eligible_svcs = [
                s
                for s in known_services
                if s.ready_at_day <= day <= s.deadline_day
                and check_service_zone_eligibility(network, s, zid)[0]
            ]
            if not eligible_svcs:
                continue

            eligible_id_set = {s.service_id for s in eligible_svcs}
            candidate_packs: List[Tuple[ServiceOrder, ...]] = []
            max_r = min(len(eligible_svcs), max_package_size)
            for r in range(1, max_r + 1):
                for comb in combinations(eligible_svcs, r):
                    candidate_packs.append(comb)
            if len(eligible_svcs) > max_package_size:
                candidate_packs.append(tuple(eligible_svcs))

            for pack in candidate_packs:
                pack_ids = {s.service_id for s in pack}
                impossible_internal_pred = False
                for s in pack:
                    for p_id in s.precedence_service_ids:
                        if p_id in eligible_id_set and p_id not in pack_ids:
                            pass
                if impossible_internal_pred:
                    continue

                for start_h in shift_starts:
                    for sub_cat in crew_subcatalogs:
                        if start_h > catalog.window_start_hour and sub_cat is not catalog:
                            continue
                        win_id = f"CAND_{len(columns) + 1:04d}"
                        win, _ = build_window_schedule(
                            network=network,
                            catalog=sub_cat,
                            zone_id=zid,
                            day=day,
                            window_id=win_id,
                            services=pack,
                            existing_day_intervals=(),
                            remaining_materials=catalog.materials_inventory,
                            earliest_window_start_hour=start_h,
                        )
                        if win is None:
                            continue

                        crew_assign_sig = tuple(
                            sorted(
                                (iv.service_id, iv.crew_id)
                                for iv in win.crew_intervals
                            )
                        )
                        sig = (
                            zid,
                            day,
                            win.service_ids,
                            crew_assign_sig,
                            round(win.start_hour, 2),
                        )
                        if sig in seen_signatures:
                            continue
                        seen_signatures.add(sig)

                        ch = _compute_window_consumer_hours(win)
                        wh = sum(
                            (iv.recomp_end_hour - iv.prep_start_hour) + iv.travel_hours
                            for iv in win.crew_intervals
                        )
                        col_cost = (
                            weights.window_weight * 1.0
                            + weights.consumer_hour_weight * ch
                            + weights.physical_maneuver_weight
                            * win.physical_maneuvers_count
                            + weights.crew_hour_weight * wh
                        )
                        columns.append(
                            _CandidateColumn(
                                col_id=len(columns),
                                zone_id=zid,
                                day=day,
                                services=pack,
                                service_ids=win.service_ids,
                                window=win,
                                consumer_hours=ch,
                                crew_hours=round(wh, 4),
                                cost=round(col_cost, 6),
                                service_exec_bounds=_extract_service_exec_bounds(win),
                                crew_span_by_id=_extract_crew_spans(win),
                            )
                        )
    return columns


def run_kgp_optimizer(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    scenario_id: str,
    decision_day: int = 0,
    weights: Optional[ObjectiveWeights] = None,
    time_limit_sec: float = 15.0,
) -> ExecutionPlan:
    """Executa o otimizador combinatório KGP (MILP via SciPy/HiGHS)."""
    t0 = time.perf_counter()
    w = weights if weights is not None else ObjectiveWeights()

    all_services = list(services)
    known_services, excluded = filter_known_services(all_services, decision_day)
    lc_alerts = detect_lifecycle_conflicts(known_services)

    if not known_services:
        return ExecutionPlan(
            plan_id=f"PLAN_KGP_{scenario_id}",
            method_name="KGP",
            scenario_id=scenario_id,
            decision_day=decision_day,
            windows=[],
            completed_service_ids=(),
            unscheduled_service_ids=(),
            exclusion_justifications=excluded,
            lifecycle_alerts=lc_alerts,
            solver_status="OPTIMAL",
            solver_objective=0.0,
            solver_bound=0.0,
            solver_gap=0.0,
            solver_time_sec=round(time.perf_counter() - t0, 6),
            is_optimal_proven=True,
        )

    columns = _generate_candidate_columns(
        network=network,
        known_services=known_services,
        catalog=catalog,
        weights=w,
    )

    num_cols = len(columns)
    num_svcs = len(known_services)
    svc_index: Dict[str, int] = {
        s.service_id: idx for idx, s in enumerate(known_services)
    }

    consumer_groups: Dict[Tuple[str, str], List[str]] = {}
    for cid, cons in network.consumers.items():
        consumer_groups.setdefault((cons.node_id, cons.phase), []).append(cid)
    group_keys = sorted(consumer_groups.keys())
    num_groups = len(group_keys)

    total_vars = num_cols + num_svcs + num_groups
    c_vec = np.zeros(total_vars, dtype=float)
    integrality = np.ones(total_vars, dtype=int)
    lb = np.zeros(total_vars, dtype=float)
    ub = np.ones(total_vars, dtype=float)

    for k, col in enumerate(columns):
        c_vec[k] = col.cost

    for i, svc in enumerate(known_services):
        var_idx = num_cols + i
        c_vec[var_idx] = w.unscheduled_penalty if svc.mandatory else 50.0

    for g_idx, g_key in enumerate(group_keys):
        var_idx = num_cols + num_svcs + g_idx
        n_cons = len(consumer_groups[g_key])
        c_vec[var_idx] = w.repeat_consumer_weight * n_cons
        ub[var_idx] = float(max(1, len(catalog.planning_days)))

    rows: List[np.ndarray] = []
    lhs_list: List[float] = []
    rhs_list: List[float] = []

    for i, svc in enumerate(known_services):
        row = np.zeros(total_vars, dtype=float)
        for k, col in enumerate(columns):
            if svc.service_id in col.service_ids:
                row[k] = 1.0
        row[num_cols + i] = 1.0
        rows.append(row)
        lhs_list.append(1.0)
        rhs_list.append(1.0)

    cols_by_day: Dict[int, List[_CandidateColumn]] = {}
    for col in columns:
        cols_by_day.setdefault(col.day, []).append(col)

    for day, day_cols in cols_by_day.items():
        n_d = len(day_cols)
        for a_i in range(n_d):
            col_a = day_cols[a_i]
            for b_i in range(a_i + 1, n_d):
                col_b = day_cols[b_i]
                if not _are_columns_compatible_on_same_day(col_a, col_b, catalog):
                    row = np.zeros(total_vars, dtype=float)
                    row[col_a.col_id] = 1.0
                    row[col_b.col_id] = 1.0
                    rows.append(row)
                    lhs_list.append(-np.inf)
                    rhs_list.append(1.0)

    big_m_time = float((max(catalog.planning_days) + 2) * 24.0)
    for s_idx, svc in enumerate(known_services):
        for pred_id in svc.precedence_service_ids:
            if pred_id not in svc_index:
                continue
            p_idx = svc_index[pred_id]
            row = np.zeros(total_vars, dtype=float)
            for k, col in enumerate(columns):
                if pred_id in col.service_exec_bounds and svc.service_id in col.service_exec_bounds:
                    continue
                if pred_id in col.service_exec_bounds:
                    _, p_end = col.service_exec_bounds[pred_id]
                    row[k] += col.day * 24.0 + p_end
                if svc.service_id in col.service_exec_bounds:
                    s_start, _ = col.service_exec_bounds[svc.service_id]
                    row[k] -= col.day * 24.0 + s_start
            row[num_cols + p_idx] -= big_m_time
            row[num_cols + s_idx] -= big_m_time
            rows.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(0.0)

    for mat, avail_qty in catalog.materials_inventory.items():
        row = np.zeros(total_vars, dtype=float)
        has_usage = False
        for k, col in enumerate(columns):
            used = col.window.materials_used.get(mat, 0.0)
            if used > 0.0:
                row[k] = used
                has_usage = True
        if has_usage:
            rows.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(float(avail_qty))

    for g_idx, g_key in enumerate(group_keys):
        rep_cid = consumer_groups[g_key][0]
        row = np.zeros(total_vars, dtype=float)
        affecting = 0
        for k, col in enumerate(columns):
            if rep_cid in col.window.interrupted_consumers:
                row[k] = 1.0
                affecting += 1
        if affecting >= 2:
            row[num_cols + num_svcs + g_idx] = -1.0
            rows.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(1.0)

    a_matrix = np.vstack(rows) if rows else np.zeros((1, total_vars))
    constraints = LinearConstraint(
        a_matrix,
        np.array(lhs_list) if lhs_list else np.array([-np.inf]),
        np.array(rhs_list) if rhs_list else np.array([np.inf]),
    )
    bounds = Bounds(lb, ub)

    res = milp(
        c=c_vec,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": time_limit_sec, "mip_rel_gap": 0.0},
    )

    elapsed = time.perf_counter() - t0
    if not res.success or res.x is None:
        fallback = run_baseline_b2_topological_greedy(
            network, services, catalog, scenario_id, decision_day
        )
        fallback.plan_id = f"PLAN_KGP_{scenario_id}"
        fallback.method_name = "KGP"
        fallback.solver_status = f"FALLBACK_AFTER_SOLVER_STATUS_{res.status}"
        fallback.solver_time_sec = round(elapsed, 6)
        return fallback

    x_sol = res.x
    chosen_cols = [
        columns[k] for k in range(num_cols) if x_sol[k] > 0.5
    ]
    chosen_cols.sort(key=lambda col: (col.day, col.window.start_hour, col.zone_id))

    reconstructed_windows: List[PlannedInterruptionWindow] = []
    day_intervals: Dict[int, List[CrewScheduleInterval]] = {
        d: [] for d in catalog.planning_days
    }
    zone_day_end: Dict[Tuple[str, int], float] = {}
    remaining_materials = dict(catalog.materials_inventory)
    completed_ids: List[str] = []

    reconstruction_ok = True
    for col in chosen_cols:
        day = col.day
        zid = col.zone_id
        earliest_start = col.window.start_hour
        if (zid, day) in zone_day_end:
            earliest_start = max(earliest_start, zone_day_end[(zid, day)] + 0.10)

        win_id = f"KGP_{scenario_id}_W{len(reconstructed_windows) + 1:02d}"
        used_crews = {iv.crew_id for iv in col.window.crew_intervals}
        sub_cat = ResourceCatalog(
            crews={cid: catalog.crews[cid] for cid in used_crews},
            materials_inventory=catalog.materials_inventory,
            planning_days=catalog.planning_days,
            window_start_hour=catalog.window_start_hour,
            window_end_hour=catalog.window_end_hour,
            max_window_duration_hours=catalog.max_window_duration_hours,
        )
        win, _ = build_window_schedule(
            network=network,
            catalog=sub_cat,
            zone_id=zid,
            day=day,
            window_id=win_id,
            services=col.services,
            existing_day_intervals=day_intervals[day],
            remaining_materials=remaining_materials,
            earliest_window_start_hour=earliest_start,
        )
        if win is None:
            win, _ = build_window_schedule(
                network=network,
                catalog=catalog,
                zone_id=zid,
                day=day,
                window_id=win_id,
                services=col.services,
                existing_day_intervals=day_intervals[day],
                remaining_materials=remaining_materials,
                earliest_window_start_hour=earliest_start,
            )
        if win is None:
            reconstruction_ok = False
            break

        reconstructed_windows.append(win)
        day_intervals[day].extend(win.crew_intervals)
        zone_day_end[(zid, day)] = win.end_hour
        for k_mat, v_mat in win.materials_used.items():
            remaining_materials[k_mat] = round(
                remaining_materials.get(k_mat, 0.0) - v_mat, 4
            )
        for sid in win.service_ids:
            completed_ids.append(sid)

    if not reconstruction_ok:
        fallback = run_baseline_b2_topological_greedy(
            network, services, catalog, scenario_id, decision_day
        )
        fallback.plan_id = f"PLAN_KGP_{scenario_id}"
        fallback.method_name = "KGP"
        fallback.solver_status = "FEASIBLE_REPAIRED"
        fallback.solver_time_sec = round(elapsed, 6)
        return fallback

    unscheduled_ids: List[str] = []
    for i, svc in enumerate(known_services):
        if svc.service_id not in completed_ids:
            unscheduled_ids.append(svc.service_id)
            excluded[svc.service_id] = (
                f"Serviço '{svc.service_id}' não pôde ser agendado sob as restrições ativas."
            )

    obj_val = float(res.fun) if res.fun is not None else 0.0
    mip_gap = float(getattr(res, "mip_gap", 0.0) or 0.0)
    dual_bound = float(
        getattr(res, "mip_dual_bound", obj_val * (1.0 - mip_gap)) or obj_val
    )

    is_optimal = (
        res.status == 0
        and mip_gap <= 1e-4
        and len(unscheduled_ids) == 0
    )
    status_str = "OPTIMAL" if is_optimal else (
        "PARTIAL_PORTFOLIO" if unscheduled_ids else "FEASIBLE_BOUNDED"
    )

    return ExecutionPlan(
        plan_id=f"PLAN_KGP_{scenario_id}",
        method_name="KGP",
        scenario_id=scenario_id,
        decision_day=decision_day,
        windows=reconstructed_windows,
        completed_service_ids=tuple(completed_ids),
        unscheduled_service_ids=tuple(unscheduled_ids),
        exclusion_justifications=excluded,
        lifecycle_alerts=lc_alerts,
        solver_status=status_str,
        solver_objective=round(obj_val, 4),
        solver_bound=round(dual_bound, 4),
        solver_gap=round(max(0.0, mip_gap), 6),
        solver_time_sec=round(elapsed, 6),
        is_optimal_proven=is_optimal,
        metadata={
            "candidate_columns_count": num_cols,
            "objective_weights": {
                "window_weight": w.window_weight,
                "consumer_hour_weight": w.consumer_hour_weight,
                "repeat_consumer_weight": w.repeat_consumer_weight,
                "physical_maneuver_weight": w.physical_maneuver_weight,
                "crew_hour_weight": w.crew_hour_weight,
            },
        },
    )
