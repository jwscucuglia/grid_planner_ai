"""Sequenciamento temporal real de equipes, deslocamento e janelas no KGP.

Calcula a alocação exata no tempo contínuo para cada equipe considerando:
- especialidades/competências requeridas;
- disponibilidade no calendário e limites de turno (shift_start_hour a shift_end_hour);
- tempo de deslocamento euclidiano a partir da base ou do serviço anterior no mesmo dia;
- tempo de preparação antes da execução e recomposição ao término;
- precedências entre serviços e restrições de simultaneidade na mesma zona;
- duração emergente da janela de interrupção (makespan real da sequência + manobras).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .contracts import (
    CrewResource,
    CrewScheduleInterval,
    GridNetwork,
    IsolationZoneSpec,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
)
from .eligibility import detect_lifecycle_conflicts
from .topology import simulate_zone_intervention


def compute_travel_hours(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    speed_kmh: float,
) -> float:
    """Calcula o tempo de deslocamento em horas entre duas coordenadas em km."""
    dist_km = math.hypot(x2 - x1, y2 - y1)
    if dist_km <= 1e-6:
        return 0.0
    effective_speed = max(5.0, speed_kmh)
    return round(dist_km / effective_speed, 4)


def topological_sort_services(
    services: Sequence[ServiceOrder],
) -> Tuple[Optional[List[ServiceOrder]], Optional[str]]:
    """Ordena serviços respeitando precedências internas ao pacote e prioridade."""
    svc_map: Dict[str, ServiceOrder] = {s.service_id: s for s in services}
    in_degree: Dict[str, int] = {s.service_id: 0 for s in services}
    dependents: Dict[str, List[str]] = {s.service_id: [] for s in services}

    for s in services:
        for pred_id in s.precedence_service_ids:
            if pred_id in svc_map:
                in_degree[s.service_id] += 1
                dependents[pred_id].append(s.service_id)

    ready = [
        s for s in services if in_degree[s.service_id] == 0
    ]
    ready.sort(
        key=lambda x: (
            x.priority,
            x.deadline_day,
            -x.duration_hours,
            x.service_id,
        )
    )

    ordered: List[ServiceOrder] = []
    while ready:
        curr = ready.pop(0)
        ordered.append(curr)
        for dep_id in dependents[curr.service_id]:
            in_degree[dep_id] -= 1
            if in_degree[dep_id] == 0:
                ready.append(svc_map[dep_id])
                ready.sort(
                    key=lambda x: (
                        x.priority,
                        x.deadline_day,
                        -x.duration_hours,
                        x.service_id,
                    )
                )

    if len(ordered) != len(services):
        return None, "Ciclo de precedência detectado entre os serviços do pacote."
    return ordered, None


def crew_has_skills(crew: CrewResource, required_skills: Tuple[str, ...]) -> bool:
    """Verifica se a equipe possui todas as competências exigidas pelo serviço."""
    crew_skill_set = set(crew.skills)
    return all(req in crew_skill_set for req in required_skills)


def build_window_schedule(
    network: GridNetwork,
    catalog: ResourceCatalog,
    zone_id: str,
    day: int,
    window_id: str,
    services: Sequence[ServiceOrder],
    existing_day_intervals: Sequence[CrewScheduleInterval] = (),
    remaining_materials: Optional[Dict[str, float]] = None,
    earliest_window_start_hour: Optional[float] = None,
) -> Tuple[Optional[PlannedInterruptionWindow], Optional[str]]:
    """Constrói uma janela programada factível para um pacote de serviços na zona e dia.

    A duração da janela emerge exatamente da sequência de execução das equipes,
    deslocamentos, preparações, precedências e restrições de simultaneidade,
    acrescida das etapas elétricas de isolamento/transferência e recomposição.
    """
    if not services:
        return None, "Pacote vazio de serviços."
    if zone_id not in network.isolation_zones:
        return None, f"Zona de isolamento '{zone_id}' inexistente."
    if day not in catalog.planning_days:
        return None, f"Dia {day} fora do horizonte de planejamento {catalog.planning_days}."

    zone: IsolationZoneSpec = network.isolation_zones[zone_id]

    materials_needed: Dict[str, float] = {}
    for svc in services:
        for mat, qty in svc.required_materials.items():
            materials_needed[mat] = round(materials_needed.get(mat, 0.0) + qty, 4)

    mat_stock = (
        remaining_materials
        if remaining_materials is not None
        else catalog.materials_inventory
    )
    for mat, qty in materials_needed.items():
        avail = mat_stock.get(mat, 0.0)
        if qty > avail + 1e-6:
            return (
                None,
                f"Material insuficiente '{mat}': necessário {qty:.2f}, disponível {avail:.2f}.",
            )

    ordered_services, sort_err = topological_sort_services(services)
    if ordered_services is None:
        return None, sort_err

    crew_history: Dict[str, List[CrewScheduleInterval]] = {
        c_id: [] for c_id in catalog.crews
    }
    for iv in existing_day_intervals:
        if iv.day == day and iv.crew_id in crew_history:
            crew_history[iv.crew_id].append(iv)
    for c_id in crew_history:
        crew_history[c_id].sort(key=lambda x: x.travel_start_hour)

    min_win_start = max(
        catalog.window_start_hour,
        earliest_window_start_hour
        if earliest_window_start_hour is not None
        else catalog.window_start_hour,
    )
    earliest_work_start_by_zone = min_win_start + zone.prep_duration_hours

    service_exec_bounds: Dict[str, Tuple[float, float]] = {}
    new_intervals: List[CrewScheduleInterval] = []

    for svc in ordered_services:
        eligible_crews = [
            crew
            for crew in catalog.crews.values()
            if day in crew.available_days
            and crew_has_skills(crew, svc.required_skills)
        ]
        if len(eligible_crews) < svc.required_crews_count:
            return (
                None,
                f"Serviço '{svc.service_id}' requer {svc.required_crews_count} equipe(s) "
                f"com competências {svc.required_skills} no dia {day}, mas apenas "
                f"{len(eligible_crews)} disponível(is).",
            )

        min_exec_start = earliest_work_start_by_zone
        for pred_id in svc.precedence_service_ids:
            if pred_id in service_exec_bounds:
                min_exec_start = max(
                    min_exec_start, service_exec_bounds[pred_id][1]
                )

        if not svc.allow_simultaneous_in_zone:
            for prev_id, (_, prev_end) in service_exec_bounds.items():
                min_exec_start = max(min_exec_start, prev_end)
        else:
            for prev_svc in ordered_services:
                if (
                    prev_svc.service_id in service_exec_bounds
                    and not prev_svc.allow_simultaneous_in_zone
                ):
                    min_exec_start = max(
                        min_exec_start,
                        service_exec_bounds[prev_svc.service_id][1],
                    )

        crew_candidates: List[Tuple[float, float, float, CrewResource]] = []
        for crew in eligible_crews:
            hist = crew_history[crew.crew_id]
            if not hist:
                from_x, from_y = crew.base_x, crew.base_y
                avail_time = crew.shift_start_hour
            else:
                last_iv = hist[-1]
                from_x, from_y = last_iv.x, last_iv.y
                avail_time = last_iv.recomp_end_hour

            travel_h = compute_travel_hours(
                from_x, from_y, svc.x, svc.y, crew.speed_kmh
            )
            prep_h = max(svc.prep_hours, crew.prep_hours)
            earliest_crew_exec = avail_time + travel_h + prep_h
            crew_candidates.append((earliest_crew_exec, travel_h, prep_h, crew))

        crew_candidates.sort(key=lambda item: (item[0], item[1], item[3].crew_id))
        chosen = crew_candidates[: svc.required_crews_count]

        exec_start = max(
            min_exec_start,
            max(item[0] for item in chosen),
        )
        exec_end = round(exec_start + svc.duration_hours, 4)
        recomp_end = round(exec_end + svc.recomp_hours, 4)

        for _, travel_h, prep_h, crew in chosen:
            if recomp_end > crew.shift_end_hour + 1e-6:
                return (
                    None,
                    f"Equipe '{crew.crew_id}' excede fim de turno ({recomp_end:.2f}h > "
                    f"{crew.shift_end_hour:.2f}h) no serviço '{svc.service_id}'.",
                )
            prep_start = round(exec_start - prep_h, 4)
            travel_start = round(prep_start - travel_h, 4)
            if travel_start < crew.shift_start_hour - 1e-6:
                return (
                    None,
                    f"Equipe '{crew.crew_id}' iniciaria deslocamento antes do turno "
                    f"({travel_start:.2f}h < {crew.shift_start_hour:.2f}h).",
                )
            iv = CrewScheduleInterval(
                crew_id=crew.crew_id,
                service_id=svc.service_id,
                window_id=window_id,
                day=day,
                travel_start_hour=travel_start,
                prep_start_hour=prep_start,
                exec_start_hour=round(exec_start, 4),
                exec_end_hour=exec_end,
                recomp_end_hour=recomp_end,
                travel_hours=travel_h,
                x=svc.x,
                y=svc.y,
            )
            crew_history[crew.crew_id].append(iv)
            new_intervals.append(iv)

        service_exec_bounds[svc.service_id] = (round(exec_start, 4), exec_end)

    work_start_hour = min(bounds[0] for bounds in service_exec_bounds.values())
    work_end_hour = max(bounds[1] for bounds in service_exec_bounds.values())
    window_start_hour = round(work_start_hour - zone.prep_duration_hours, 4)
    window_end_hour = round(work_end_hour + zone.recomp_duration_hours, 4)

    if window_start_hour < catalog.window_start_hour - 1e-6:
        return (
            None,
            f"Início da janela ({window_start_hour:.2f}h) anterior ao limite permitido ({catalog.window_start_hour:.2f}h).",
        )
    if window_end_hour > catalog.window_end_hour + 1e-6:
        return (
            None,
            f"Fim da janela ({window_end_hour:.2f}h) excede limite diário ({catalog.window_end_hour:.2f}h).",
        )
    if (window_end_hour - window_start_hour) > catalog.max_window_duration_hours + 1e-6:
        return (
            None,
            f"Duração total da janela ({window_end_hour - window_start_hour:.2f}h) excede "
            f"máximo permitido ({catalog.max_window_duration_hours:.2f}h).",
        )

    maneuver_steps, physical_maneuvers = simulate_zone_intervention(
        network=network,
        zone_id=zone_id,
        work_start_hour=work_start_hour,
        work_end_hour=work_end_hour,
    )

    all_interrupted: Set[str] = set()
    for step in maneuver_steps:
        all_interrupted.update(step.interrupted_consumers)

    lifecycle_alerts = tuple(detect_lifecycle_conflicts(ordered_services))
    justifications: Dict[str, str] = {}
    for svc in ordered_services:
        s_start, s_end = service_exec_bounds[svc.service_id]
        assigned_crews = [
            iv.crew_id for iv in new_intervals if iv.service_id == svc.service_id
        ]
        justifications[svc.service_id] = (
            f"Alocado na zona '{zone_id}' (dia {day}, execução {s_start:.2f}h-{s_end:.2f}h) "
            f"com equipe(s) {assigned_crews}; ativos {list(svc.target_assets)} isolados."
        )

    return (
        PlannedInterruptionWindow(
            window_id=window_id,
            day=day,
            zone_id=zone_id,
            service_ids=tuple(s.service_id for s in ordered_services),
            start_hour=window_start_hour,
            end_hour=window_end_hour,
            work_start_hour=work_start_hour,
            work_end_hour=work_end_hour,
            maneuver_steps=maneuver_steps,
            crew_intervals=tuple(new_intervals),
            materials_used=materials_needed,
            physical_maneuvers_count=physical_maneuvers,
            interrupted_consumers=tuple(sorted(all_interrupted)),
            lifecycle_alerts=lifecycle_alerts,
            inclusion_justifications=justifications,
        ),
        None,
    )
