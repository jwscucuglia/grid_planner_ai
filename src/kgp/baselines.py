"""Comparadores competentes (Baselines B0, B1 e B2) do KAPTUS GRID PLANNER.

Implementa:
- B0 (Individual): programação por prioridade e prazo, com cada serviço em sua
  própria janela e alocação factível de recursos.
- B1 (Geográfico): agrupa candidatos por proximidade espacial (raio documentado
  em km) e submete cada grupo ao mesmo verificador elétrico e de recursos (sem
  executar serviços em isolamento incompatível).
- B2 (Topológico Guloso + Melhoria Local): agrupa candidatos por compatibilidade
  elétrica pós-manobra, prioridade e prazo, preenchendo janelas sob as mesmas
  restrições de equipes e materiais.
"""

from __future__ import annotations

import math
import time
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    find_eligible_zones_for_service,
)
from .scheduling import build_window_schedule, topological_sort_services
from .topology import simulate_zone_intervention


def _rank_zones_by_impact(
    network: GridNetwork,
    zone_ids: Sequence[str],
) -> List[str]:
    """Ordena zonas elegíveis pelo menor número de consumidores interrompidos na execução."""
    scored: List[Tuple[int, int, str]] = []
    for zid in zone_ids:
        steps, phys = simulate_zone_intervention(network, zid, 9.0, 11.0)
        interrupted: Set[str] = set()
        for st in steps:
            interrupted.update(st.interrupted_consumers)
        scored.append((len(interrupted), phys, zid))
    scored.sort()
    return [zid for _, _, zid in scored]


def _order_portfolio_with_precedences(
    services: Sequence[ServiceOrder],
) -> List[ServiceOrder]:
    """Ordena a carteira global respeitando precedências, prioridade e prazo."""
    ordered, _ = topological_sort_services(services)
    if ordered is not None:
        return ordered
    return sorted(
        services,
        key=lambda s: (s.priority, s.deadline_day, s.ready_at_day, s.service_id),
    )


def _subtract_materials(
    stock: Dict[str, float],
    used: Dict[str, float],
) -> Dict[str, float]:
    updated = dict(stock)
    for k, v in used.items():
        updated[k] = round(updated.get(k, 0.0) - v, 4)
    return updated


def run_baseline_b0_individual(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    scenario_id: str,
    decision_day: int = 0,
) -> ExecutionPlan:
    """B0 — Programação individual por prioridade e prazo (1 serviço por janela)."""
    t0 = time.perf_counter()
    all_services = list(services)
    known_services, excluded = filter_known_services(all_services, decision_day)
    ordered = _order_portfolio_with_precedences(known_services)
    lc_alerts = detect_lifecycle_conflicts(known_services)

    remaining_materials = dict(catalog.materials_inventory)
    day_intervals: Dict[int, List[CrewScheduleInterval]] = {
        d: [] for d in catalog.planning_days
    }
    zone_day_end: Dict[Tuple[str, int], float] = {}
    service_scheduled_day: Dict[str, int] = {}
    service_scheduled_end: Dict[str, float] = {}

    windows: List[PlannedInterruptionWindow] = []
    completed: List[str] = []
    unscheduled: List[str] = []

    for svc in ordered:
        eligible_zones = _rank_zones_by_impact(
            network, find_eligible_zones_for_service(network, svc)
        )
        if not eligible_zones:
            unscheduled.append(svc.service_id)
            excluded[svc.service_id] = (
                f"Nenhuma zona de isolamento elegível para '{svc.service_id}'."
            )
            continue

        min_day = svc.ready_at_day
        for pred_id in svc.precedence_service_ids:
            if pred_id in service_scheduled_day:
                min_day = max(min_day, service_scheduled_day[pred_id])
            else:
                min_day = 999999

        scheduled_win: Optional[PlannedInterruptionWindow] = None
        last_err = "Sem janela disponível no horizonte."

        candidate_days = [
            d
            for d in catalog.planning_days
            if min_day <= d <= svc.deadline_day
        ]
        candidate_days.sort(
            key=lambda d: (len(day_intervals[d]), d)
        )

        for day in candidate_days:
            for zid in eligible_zones:
                earliest_start = catalog.window_start_hour
                if (zid, day) in zone_day_end:
                    earliest_start = max(
                        earliest_start, zone_day_end[(zid, day)] + 0.10
                    )
                for pred_id in svc.precedence_service_ids:
                    if service_scheduled_day.get(pred_id) == day:
                        zone_prep = network.isolation_zones[zid].prep_duration_hours
                        earliest_start = max(
                            earliest_start,
                            service_scheduled_end[pred_id] - zone_prep,
                        )

                win_id = f"B0_{scenario_id}_W{len(windows) + 1:02d}"
                win, err = build_window_schedule(
                    network=network,
                    catalog=catalog,
                    zone_id=zid,
                    day=day,
                    window_id=win_id,
                    services=[svc],
                    existing_day_intervals=day_intervals[day],
                    remaining_materials=remaining_materials,
                    earliest_window_start_hour=earliest_start,
                )
                if win is not None:
                    scheduled_win = win
                    break
                if err:
                    last_err = err
            if scheduled_win is not None:
                break

        if scheduled_win is not None:
            windows.append(scheduled_win)
            completed.append(svc.service_id)
            day_intervals[scheduled_win.day].extend(scheduled_win.crew_intervals)
            zone_day_end[(scheduled_win.zone_id, scheduled_win.day)] = (
                scheduled_win.end_hour
            )
            service_scheduled_day[svc.service_id] = scheduled_win.day
            service_scheduled_end[svc.service_id] = scheduled_win.work_end_hour
            remaining_materials = _subtract_materials(
                remaining_materials, scheduled_win.materials_used
            )
        else:
            unscheduled.append(svc.service_id)
            excluded[svc.service_id] = f"B0 não alocou '{svc.service_id}': {last_err}"

    elapsed = time.perf_counter() - t0
    return ExecutionPlan(
        plan_id=f"PLAN_B0_{scenario_id}",
        method_name="B0",
        scenario_id=scenario_id,
        decision_day=decision_day,
        windows=windows,
        completed_service_ids=tuple(completed),
        unscheduled_service_ids=tuple(unscheduled),
        exclusion_justifications=excluded,
        lifecycle_alerts=lc_alerts,
        solver_status="HEURISTIC_INDIVIDUAL",
        solver_time_sec=round(elapsed, 6),
    )


def run_baseline_b1_geographic(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    scenario_id: str,
    decision_day: int = 0,
    radius_km: float = 1.25,
) -> ExecutionPlan:
    """B1 — Agrupamento geográfico por raio (km) submetido ao verificador elétrico e de recursos.

    Agrupa candidatos dentro de `radius_km` do serviço semente, mas só acopla na mesma
    janela aqueles que passam na verificação de isolamento elétrico da zona escolhida
    e nas restrições de recursos, datas e precedências.
    """
    t0 = time.perf_counter()
    all_services = list(services)
    known_services, excluded = filter_known_services(all_services, decision_day)
    ordered = _order_portfolio_with_precedences(known_services)
    lc_alerts = detect_lifecycle_conflicts(known_services)

    remaining_materials = dict(catalog.materials_inventory)
    day_intervals: Dict[int, List[CrewScheduleInterval]] = {
        d: [] for d in catalog.planning_days
    }
    zone_day_end: Dict[Tuple[str, int], float] = {}
    service_scheduled_day: Dict[str, int] = {}
    service_scheduled_end: Dict[str, float] = {}

    unassigned_ids: Set[str] = {s.service_id for s in ordered}
    windows: List[PlannedInterruptionWindow] = []
    completed: List[str] = []
    unscheduled: List[str] = []

    for seed_svc in ordered:
        if seed_svc.service_id not in unassigned_ids:
            continue

        eligible_zones = _rank_zones_by_impact(
            network, find_eligible_zones_for_service(network, seed_svc)
        )
        if not eligible_zones:
            unassigned_ids.remove(seed_svc.service_id)
            unscheduled.append(seed_svc.service_id)
            excluded[seed_svc.service_id] = (
                f"Nenhuma zona elegível para '{seed_svc.service_id}'."
            )
            continue

        geo_neighbors: List[ServiceOrder] = []
        for cand in ordered:
            if (
                cand.service_id == seed_svc.service_id
                or cand.service_id not in unassigned_ids
            ):
                continue
            dist = math.hypot(cand.x - seed_svc.x, cand.y - seed_svc.y)
            if dist <= radius_km:
                geo_neighbors.append(cand)

        min_seed_day = seed_svc.ready_at_day
        for pred_id in seed_svc.precedence_service_ids:
            if pred_id in service_scheduled_day:
                min_seed_day = max(min_seed_day, service_scheduled_day[pred_id])
            else:
                min_seed_day = 999999

        scheduled_win: Optional[PlannedInterruptionWindow] = None
        scheduled_pack: List[ServiceOrder] = []
        last_err = "Sem janela factível no horizonte."

        candidate_days = [
            d
            for d in catalog.planning_days
            if min_seed_day <= d <= seed_svc.deadline_day
        ]

        for day in candidate_days:
            for zid in eligible_zones:
                earliest_start = catalog.window_start_hour
                if (zid, day) in zone_day_end:
                    earliest_start = max(
                        earliest_start, zone_day_end[(zid, day)] + 0.10
                    )
                for pred_id in seed_svc.precedence_service_ids:
                    if service_scheduled_day.get(pred_id) == day:
                        zone_prep = network.isolation_zones[zid].prep_duration_hours
                        earliest_start = max(
                            earliest_start,
                            service_scheduled_end[pred_id] - zone_prep,
                        )

                win_id = f"B1_{scenario_id}_W{len(windows) + 1:02d}"
                base_win, err = build_window_schedule(
                    network=network,
                    catalog=catalog,
                    zone_id=zid,
                    day=day,
                    window_id=win_id,
                    services=[seed_svc],
                    existing_day_intervals=day_intervals[day],
                    remaining_materials=remaining_materials,
                    earliest_window_start_hour=earliest_start,
                )
                if base_win is None:
                    if err:
                        last_err = err
                    continue

                current_pack = [seed_svc]
                current_win = base_win
                pack_ids = {seed_svc.service_id}

                for nbr in geo_neighbors:
                    if not (nbr.ready_at_day <= day <= nbr.deadline_day):
                        continue
                    ok_zone, _ = check_service_zone_eligibility(network, nbr, zid)
                    if not ok_zone:
                        continue
                    preds_ok = True
                    for p_id in nbr.precedence_service_ids:
                        if p_id in pack_ids:
                            continue
                        p_day = service_scheduled_day.get(p_id)
                        if p_day is None or p_day > day:
                            preds_ok = False
                            break
                        if p_day == day and service_scheduled_end[p_id] > earliest_start + network.isolation_zones[zid].prep_duration_hours:
                            preds_ok = False
                            break
                    if not preds_ok:
                        continue

                    trial_pack = current_pack + [nbr]
                    trial_win, _ = build_window_schedule(
                        network=network,
                        catalog=catalog,
                        zone_id=zid,
                        day=day,
                        window_id=win_id,
                        services=trial_pack,
                        existing_day_intervals=day_intervals[day],
                        remaining_materials=remaining_materials,
                        earliest_window_start_hour=earliest_start,
                    )
                    if trial_win is not None:
                        current_pack = trial_pack
                        current_win = trial_win
                        pack_ids.add(nbr.service_id)

                scheduled_win = current_win
                scheduled_pack = current_pack
                break
            if scheduled_win is not None:
                break

        if scheduled_win is not None:
            windows.append(scheduled_win)
            day_intervals[scheduled_win.day].extend(scheduled_win.crew_intervals)
            zone_day_end[(scheduled_win.zone_id, scheduled_win.day)] = (
                scheduled_win.end_hour
            )
            remaining_materials = _subtract_materials(
                remaining_materials, scheduled_win.materials_used
            )
            for s in scheduled_pack:
                unassigned_ids.discard(s.service_id)
                completed.append(s.service_id)
                service_scheduled_day[s.service_id] = scheduled_win.day
                s_end = max(
                    iv.exec_end_hour
                    for iv in scheduled_win.crew_intervals
                    if iv.service_id == s.service_id
                )
                service_scheduled_end[s.service_id] = s_end
        else:
            unassigned_ids.discard(seed_svc.service_id)
            unscheduled.append(seed_svc.service_id)
            excluded[seed_svc.service_id] = (
                f"B1 não alocou '{seed_svc.service_id}': {last_err}"
            )

    elapsed = time.perf_counter() - t0
    return ExecutionPlan(
        plan_id=f"PLAN_B1_{scenario_id}",
        method_name="B1",
        scenario_id=scenario_id,
        decision_day=decision_day,
        windows=windows,
        completed_service_ids=tuple(completed),
        unscheduled_service_ids=tuple(unscheduled),
        exclusion_justifications=excluded,
        lifecycle_alerts=lc_alerts,
        solver_status="HEURISTIC_GEOGRAPHIC",
        solver_time_sec=round(elapsed, 6),
        metadata={"radius_km": radius_km},
    )


def run_baseline_b2_topological_greedy(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    scenario_id: str,
    decision_day: int = 0,
) -> ExecutionPlan:
    """B2 — Heurística topológica gulosa por compatibilidade elétrica, prioridade e prazo com melhoria local.

    Seleciona o próximo serviço prioritário, escolhe a zona elegível na menor data
    disponível (desempatando por menor impacto) e empacota gulosamente todos os
    demais serviços eletricamente compatíveis com aquela zona naquela data,
    Independentemente da distância geográfica.
    """
    t0 = time.perf_counter()
    all_services = list(services)
    known_services, excluded = filter_known_services(all_services, decision_day)
    ordered = _order_portfolio_with_precedences(known_services)
    lc_alerts = detect_lifecycle_conflicts(known_services)

    remaining_materials = dict(catalog.materials_inventory)
    day_intervals: Dict[int, List[CrewScheduleInterval]] = {
        d: [] for d in catalog.planning_days
    }
    zone_day_end: Dict[Tuple[str, int], float] = {}
    service_scheduled_day: Dict[str, int] = {}
    service_scheduled_end: Dict[str, float] = {}

    unassigned_ids: Set[str] = {s.service_id for s in ordered}
    windows: List[PlannedInterruptionWindow] = []
    completed: List[str] = []
    unscheduled: List[str] = []

    for seed_svc in ordered:
        if seed_svc.service_id not in unassigned_ids:
            continue

        eligible_zones = _rank_zones_by_impact(
            network, find_eligible_zones_for_service(network, seed_svc)
        )
        if not eligible_zones:
            unassigned_ids.remove(seed_svc.service_id)
            unscheduled.append(seed_svc.service_id)
            excluded[seed_svc.service_id] = (
                f"Nenhuma zona elegível para '{seed_svc.service_id}'."
            )
            continue

        min_seed_day = seed_svc.ready_at_day
        for pred_id in seed_svc.precedence_service_ids:
            if pred_id in service_scheduled_day:
                min_seed_day = max(min_seed_day, service_scheduled_day[pred_id])
            else:
                min_seed_day = 999999

        scheduled_win: Optional[PlannedInterruptionWindow] = None
        scheduled_pack: List[ServiceOrder] = []
        last_err = "Sem janela factível no horizonte."

        candidate_days = [
            d
            for d in catalog.planning_days
            if min_seed_day <= d <= seed_svc.deadline_day
        ]

        for day in candidate_days:
            best_day_win: Optional[PlannedInterruptionWindow] = None
            best_day_pack: List[ServiceOrder] = []
            best_day_score: Tuple[int, float] = (-1, float("inf"))

            for zid in eligible_zones:
                earliest_start = catalog.window_start_hour
                if (zid, day) in zone_day_end:
                    earliest_start = max(
                        earliest_start, zone_day_end[(zid, day)] + 0.10
                    )
                for pred_id in seed_svc.precedence_service_ids:
                    if service_scheduled_day.get(pred_id) == day:
                        zone_prep = network.isolation_zones[zid].prep_duration_hours
                        earliest_start = max(
                            earliest_start,
                            service_scheduled_end[pred_id] - zone_prep,
                        )

                win_id = f"B2_{scenario_id}_W{len(windows) + 1:02d}"
                base_win, err = build_window_schedule(
                    network=network,
                    catalog=catalog,
                    zone_id=zid,
                    day=day,
                    window_id=win_id,
                    services=[seed_svc],
                    existing_day_intervals=day_intervals[day],
                    remaining_materials=remaining_materials,
                    earliest_window_start_hour=earliest_start,
                )
                if base_win is None:
                    if err:
                        last_err = err
                    continue

                current_pack = [seed_svc]
                current_win = base_win
                pack_ids = {seed_svc.service_id}

                topo_candidates = [
                    cand
                    for cand in ordered
                    if cand.service_id != seed_svc.service_id
                    and cand.service_id in unassigned_ids
                    and cand.ready_at_day <= day <= cand.deadline_day
                    and check_service_zone_eligibility(network, cand, zid)[0]
                ]

                for cand in topo_candidates:
                    preds_ok = True
                    for p_id in cand.precedence_service_ids:
                        if p_id in pack_ids:
                            continue
                        p_day = service_scheduled_day.get(p_id)
                        if p_day is None or p_day > day:
                            preds_ok = False
                            break
                        if p_day == day and service_scheduled_end[p_id] > earliest_start + network.isolation_zones[zid].prep_duration_hours:
                            preds_ok = False
                            break
                    if not preds_ok:
                        continue

                    trial_pack = current_pack + [cand]
                    trial_win, _ = build_window_schedule(
                        network=network,
                        catalog=catalog,
                        zone_id=zid,
                        day=day,
                        window_id=win_id,
                        services=trial_pack,
                        existing_day_intervals=day_intervals[day],
                        remaining_materials=remaining_materials,
                        earliest_window_start_hour=earliest_start,
                    )
                    if trial_win is not None:
                        current_pack = trial_pack
                        current_win = trial_win
                        pack_ids.add(cand.service_id)

                win_ch = sum(
                    (st.end_hour - st.start_hour) * len(st.interrupted_consumers)
                    for st in current_win.maneuver_steps
                )
                score = (len(current_pack), -win_ch)
                if best_day_win is None or score > (
                    best_day_score[0],
                    -best_day_score[1],
                ):
                    best_day_win = current_win
                    best_day_pack = current_pack
                    best_day_score = (len(current_pack), win_ch)

            if best_day_win is not None:
                scheduled_win = best_day_win
                scheduled_pack = best_day_pack
                break

        if scheduled_win is not None:
            windows.append(scheduled_win)
            day_intervals[scheduled_win.day].extend(scheduled_win.crew_intervals)
            zone_day_end[(scheduled_win.zone_id, scheduled_win.day)] = (
                scheduled_win.end_hour
            )
            remaining_materials = _subtract_materials(
                remaining_materials, scheduled_win.materials_used
            )
            for s in scheduled_pack:
                unassigned_ids.discard(s.service_id)
                completed.append(s.service_id)
                service_scheduled_day[s.service_id] = scheduled_win.day
                s_end = max(
                    iv.exec_end_hour
                    for iv in scheduled_win.crew_intervals
                    if iv.service_id == s.service_id
                )
                service_scheduled_end[s.service_id] = s_end
        else:
            unassigned_ids.discard(seed_svc.service_id)
            unscheduled.append(seed_svc.service_id)
            excluded[seed_svc.service_id] = (
                f"B2 não alocou '{seed_svc.service_id}': {last_err}"
            )

    elapsed = time.perf_counter() - t0
    return ExecutionPlan(
        plan_id=f"PLAN_B2_{scenario_id}",
        method_name="B2",
        scenario_id=scenario_id,
        decision_day=decision_day,
        windows=windows,
        completed_service_ids=tuple(completed),
        unscheduled_service_ids=tuple(unscheduled),
        exclusion_justifications=excluded,
        lifecycle_alerts=lc_alerts,
        solver_status="HEURISTIC_TOPOLOGICAL_GREEDY",
        solver_time_sec=round(elapsed, 6),
    )
