"""Cálculo exato de métricas operacionais, elétricas e do solver no KGP.

Calcula:
- janelas de interrupção vs. manobras físicas de chaves (recalculadas pelo verificador);
- taxa de acoplamento (janelas com 2+ serviços / total de janelas);
- clientes-hora pela soma, por consumidor, da duração da união dos intervalos
  sem alimentação (sem duplicar intervalos sobrepostos ou contíguos);
- reincidência (consumidores com 2+ interrupções distintas separadas por recomposição);
- métricas de ocupação e deslocamento de equipes;
- status, objetivo, bound, gap e tempo do solver (declarando otimalidade no espaço
  discreto de colunas apenas quando comprovada pelos limites numéricos e factível).
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

from .contracts import (
    ExecutionPlan,
    FeasibilityReport,
    GridNetwork,
    PlanMetrics,
    ServiceOrder,
)


def merge_intervals(
    intervals: Sequence[Tuple[float, float]],
    tol: float = 1e-5,
) -> List[Tuple[float, float]]:
    """Calcula a união disjunta de intervalos [inicio, fim], fundindo sobreposições e adjacências."""
    valid = [(float(s), float(e)) for s, e in intervals if e > s + 1e-9]
    if not valid:
        return []
    valid.sort(key=lambda x: (x[0], x[1]))
    merged: List[Tuple[float, float]] = [valid[0]]
    for curr_s, curr_e in valid[1:]:
        prev_s, prev_e = merged[-1]
        if curr_s <= prev_e + tol:
            merged[-1] = (prev_s, max(prev_e, curr_e))
        else:
            merged.append((curr_s, curr_e))
    return merged


def compute_consumer_interruption_unions(
    plan: ExecutionPlan,
) -> Dict[str, List[Tuple[float, float]]]:
    """Retorna, por consumidor, a lista de intervalos disjuntos sem alimentação (em horas absolutas)."""
    raw_intervals_by_consumer: Dict[str, List[Tuple[float, float]]] = {}

    for win in plan.windows:
        day_offset = float(win.day) * 24.0
        if win.maneuver_steps:
            for step in win.maneuver_steps:
                abs_start = day_offset + step.start_hour
                abs_end = day_offset + step.end_hour
                if abs_end <= abs_start:
                    continue
                for cid in step.interrupted_consumers:
                    raw_intervals_by_consumer.setdefault(cid, []).append(
                        (abs_start, abs_end)
                    )
        else:
            abs_start = day_offset + win.start_hour
            abs_end = day_offset + win.end_hour
            if abs_end > abs_start:
                for cid in win.interrupted_consumers:
                    raw_intervals_by_consumer.setdefault(cid, []).append(
                        (abs_start, abs_end)
                    )

    merged_by_consumer: Dict[str, List[Tuple[float, float]]] = {}
    for cid, ivs in raw_intervals_by_consumer.items():
        merged = merge_intervals(ivs)
        if merged:
            merged_by_consumer[cid] = merged
    return merged_by_consumer


def compute_plan_metrics(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    plan: ExecutionPlan,
    feasibility: FeasibilityReport,
) -> PlanMetrics:
    """Apura todas as métricas auditáveis a partir do plano e do verificador independente."""
    svc_map: Dict[str, ServiceOrder] = {s.service_id: s for s in services}
    known_mandatory = [
        s
        for s in svc_map.values()
        if s.mandatory
        and s.scope_status == "ACTIVE"
        and s.created_at_day <= plan.decision_day
    ]

    scheduled_services = list(feasibility.recalculated_completed_services)
    if not scheduled_services:
        for win in plan.windows:
            for sid in win.service_ids:
                if sid not in scheduled_services:
                    scheduled_services.append(sid)

    completed_mandatory = [
        s.service_id
        for s in known_mandatory
        if s.service_id in scheduled_services
    ]
    mandatory_rate = (
        len(completed_mandatory) / len(known_mandatory)
        if known_mandatory
        else 1.0
    )

    windows_count = len(plan.windows)
    physical_maneuvers = feasibility.recalculated_physical_maneuvers
    coupled_windows = sum(1 for w in plan.windows if len(w.service_ids) >= 2)
    coupling_rate = (
        coupled_windows / windows_count if windows_count > 0 else 0.0
    )
    avg_services_per_win = (
        len(scheduled_services) / windows_count if windows_count > 0 else 0.0
    )

    unique_consumers = feasibility.recalculated_unique_consumers
    repeat_consumers = feasibility.recalculated_repeat_consumers
    total_interruptions = feasibility.recalculated_total_interruptions
    consumer_hours = feasibility.recalculated_consumer_hours

    total_work_h = 0.0
    total_travel_h = 0.0
    for win in plan.windows:
        for iv in win.crew_intervals:
            total_work_h += iv.recomp_end_hour - iv.prep_start_hour
            total_travel_h += iv.travel_hours

    deadline_violations = 0
    for win in plan.windows:
        for sid in win.service_ids:
            svc = svc_map.get(sid)
            if svc is not None and win.day > svc.deadline_day:
                deadline_violations += 1

    proven_optimal = False
    if (
        feasibility.is_feasible
        and plan.is_optimal_proven
        and plan.solver_status == "OPTIMAL_ON_DISCRETE_COLUMNS"
        and plan.solver_objective is not None
        and plan.solver_bound is not None
        and plan.solver_gap is not None
        and plan.solver_gap <= 1e-4
        and abs(plan.solver_objective - plan.solver_bound) <= 1e-4 * max(1.0, abs(plan.solver_objective))
    ):
        proven_optimal = True

    return PlanMetrics(
        plan_id=plan.plan_id,
        method_name=plan.method_name,
        scenario_id=plan.scenario_id,
        is_feasible=feasibility.is_feasible,
        mandatory_completion_rate=round(mandatory_rate, 4),
        interruption_windows_count=windows_count,
        physical_maneuvers_count=physical_maneuvers,
        coupled_windows_count=coupled_windows,
        coupling_rate=round(coupling_rate, 4),
        avg_services_per_window=round(avg_services_per_win, 4),
        total_services_scheduled=len(scheduled_services),
        unique_consumers_interrupted=unique_consumers,
        repeat_interrupted_consumers_count=repeat_consumers,
        total_consumer_interruptions=total_interruptions,
        consumer_hours_total=round(consumer_hours, 4),
        total_crew_work_hours=round(total_work_h, 4),
        total_crew_travel_hours=round(total_travel_h, 4),
        deadline_violations_count=deadline_violations,
        lifecycle_alerts_count=len(plan.lifecycle_alerts),
        planning_time_sec=round(plan.solver_time_sec, 6),
        solver_status=plan.solver_status,
        solver_objective=(
            round(plan.solver_objective, 4)
            if plan.solver_objective is not None
            else None
        ),
        solver_bound=(
            round(plan.solver_bound, 4)
            if plan.solver_bound is not None
            else None
        ),
        solver_gap=(
            round(plan.solver_gap, 6)
            if plan.solver_gap is not None
            else None
        ),
        is_optimal_proven=proven_optimal,
    )
