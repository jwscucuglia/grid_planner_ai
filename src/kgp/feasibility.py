"""Verificador independente de factibilidade elétrica, temporal e de recursos (KGP).

Módulo estritamente separado do otimizador e dos comparadores que audita
qualquer ExecutionPlan exportado recalculando do zero:
- conclusão de 100% da carteira obrigatória conhecida na data da decisão;
- ausência de vazamento temporal (serviços futuros) e cumprimento de ready_at/deadline;
- isolamento elétrico pós-manobra real e validade de transferência de carga;
- ocupação contínua de cada equipe no tempo (competências, turno, deslocamento,
  preparação, execução, recomposição e ausência de sobreposição);
- precedências entre serviços (entre dias ou intrajanela) e simultaneidade na zona;
- consumo acumulado de materiais frente ao estoque;
- recálculo independente de janelas, manobras físicas e clientes-hora.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple

from .contracts import (
    CrewScheduleInterval,
    ExecutionPlan,
    FeasibilityReport,
    GridNetwork,
    ManeuverStage,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
)
from .eligibility import detect_lifecycle_conflicts
from .metrics import merge_intervals
from .scheduling import compute_travel_hours, crew_has_skills
from .topology import simulate_zone_intervention, validate_asset_registration


def verify_plan_feasibility(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    plan: ExecutionPlan,
) -> FeasibilityReport:
    """Audita integralmente um plano exportado contra todas as restrições do problema."""
    svc_map: Dict[str, ServiceOrder] = {s.service_id: s for s in services}
    violations: List[str] = []
    warnings: List[str] = []

    known_mandatory = [
        s.service_id
        for s in svc_map.values()
        if s.mandatory
        and s.scope_status == "ACTIVE"
        and s.created_at_day <= plan.decision_day
    ]

    scheduled_service_occurrences: Dict[str, int] = {}
    service_to_window: Dict[str, PlannedInterruptionWindow] = {}
    service_exec_times: Dict[str, Tuple[int, float, float]] = {}

    for win in plan.windows:
        if not win.service_ids:
            violations.append(f"Janela '{win.window_id}' vazia (sem serviços).")
        for sid in win.service_ids:
            scheduled_service_occurrences[sid] = (
                scheduled_service_occurrences.get(sid, 0) + 1
            )
            service_to_window[sid] = win

    for sid, count in scheduled_service_occurrences.items():
        if count > 1:
            violations.append(
                f"Serviço '{sid}' programado em duplicidade ({count} vezes)."
            )
        if sid not in svc_map:
            violations.append(
                f"Serviço '{sid}' no plano não existe no catálogo da carteira."
            )
        elif svc_map[sid].scope_status != "ACTIVE":
            violations.append(
                f"Serviço '{sid}' programado com status inválido '{svc_map[sid].scope_status}'."
            )

    completed_mandatory_count = sum(
        1 for sid in known_mandatory if sid in scheduled_service_occurrences
    )
    missing_mandatory = [
        sid for sid in known_mandatory if sid not in scheduled_service_occurrences
    ]
    mandatory_complete = len(missing_mandatory) == 0
    if not mandatory_complete:
        violations.append(
            f"Carteira obrigatória incompleta: faltam {len(missing_mandatory)} "
            f"serviço(s) obrigatório(s): {missing_mandatory}."
        )

    recalculated_materials: Dict[str, float] = {}
    recalculated_maneuvers = 0
    recalc_consumer_intervals: Dict[str, List[Tuple[float, float]]] = {}

    all_crew_intervals_by_day: Dict[int, Dict[str, List[CrewScheduleInterval]]] = {}

    for win in plan.windows:
        if win.day not in catalog.planning_days:
            violations.append(
                f"Janela '{win.window_id}' agendada em dia inválido ({win.day})."
            )
        if win.zone_id not in network.isolation_zones:
            violations.append(
                f"Janela '{win.window_id}' referencia zona inexistente '{win.zone_id}'."
            )
            continue

        zone = network.isolation_zones[win.zone_id]

        if win.start_hour < catalog.window_start_hour - 1e-4:
            violations.append(
                f"Janela '{win.window_id}' inicia às {win.start_hour:.2f}h antes do "
                f"permitido ({catalog.window_start_hour:.2f}h)."
            )
        if win.end_hour > catalog.window_end_hour + 1e-4:
            violations.append(
                f"Janela '{win.window_id}' termina às {win.end_hour:.2f}h após o "
                f"limite ({catalog.window_end_hour:.2f}h)."
            )
        if (win.end_hour - win.start_hour) > catalog.max_window_duration_hours + 1e-4:
            violations.append(
                f"Janela '{win.window_id}' excede duração máxima "
                f"({win.end_hour - win.start_hour:.2f}h > {catalog.max_window_duration_hours:.2f}h)."
            )

        if win.work_start_hour - win.start_hour < zone.prep_duration_hours - 1e-4:
            violations.append(
                f"Janela '{win.window_id}' não respeita tempo de manobra de isolamento "
                f"da zona '{win.zone_id}' ({zone.prep_duration_hours:.2f}h)."
            )
        if win.end_hour - win.work_end_hour < zone.recomp_duration_hours - 1e-4:
            violations.append(
                f"Janela '{win.window_id}' não respeita tempo de recomposição "
                f"da zona '{win.zone_id}' ({zone.recomp_duration_hours:.2f}h)."
            )

        recalc_steps, recalc_phys = simulate_zone_intervention(
            network=network,
            zone_id=win.zone_id,
            work_start_hour=win.work_start_hour,
            work_end_hour=win.work_end_hour,
        )
        recalculated_maneuvers += recalc_phys

        day_offset = float(win.day) * 24.0
        for step in recalc_steps:
            for cid in step.interrupted_consumers:
                recalc_consumer_intervals.setdefault(cid, []).append(
                    (day_offset + step.start_hour, day_offset + step.end_hour)
                )

        exec_steps = [s for s in recalc_steps if s.stage == ManeuverStage.EXECUTION]
        if not exec_steps:
            violations.append(
                f"Janela '{win.window_id}' sem etapa de execução elétrica recalculada."
            )
            continue
        exec_step = exec_steps[0]
        if zone.requires_transfer_validation and not exec_step.transfer_feasible:
            violations.append(
                f"Janela '{win.window_id}' utiliza transferência eletricamente inviável: {exec_step.notes}"
            )
        deenergized_assets = set(exec_step.deenergized_assets)

        win_svc_exec_bounds: Dict[str, Tuple[float, float]] = {}

        for sid in win.service_ids:
            if sid not in svc_map:
                continue
            svc = svc_map[sid]

            if svc.created_at_day > plan.decision_day:
                violations.append(
                    f"Vazamento temporal: serviço '{sid}' criado no dia {svc.created_at_day} "
                    f"após a decisão no dia {plan.decision_day}."
                )
            if win.day < svc.ready_at_day:
                violations.append(
                    f"Serviço '{sid}' agendado no dia {win.day} antes de ready_at ({svc.ready_at_day})."
                )
            if win.day > svc.deadline_day:
                violations.append(
                    f"Serviço '{sid}' agendado no dia {win.day} após o prazo ({svc.deadline_day})."
                )

            if svc.compatible_zone_ids and win.zone_id not in svc.compatible_zone_ids:
                violations.append(
                    f"Serviço '{sid}' alocado na zona '{win.zone_id}' incompatível com "
                    f"{svc.compatible_zone_ids}."
                )

            for asset_id in svc.target_assets:
                reg_err = validate_asset_registration(network, asset_id)
                if reg_err:
                    violations.extend(reg_err)
                elif svc.requires_deenergized and asset_id not in deenergized_assets:
                    violations.append(
                        f"Violação de isolamento elétrico: ativo '{asset_id}' do serviço "
                        f"'{sid}' permanece energizado na zona '{win.zone_id}'."
                    )

            for mat, qty in svc.required_materials.items():
                recalculated_materials[mat] = round(
                    recalculated_materials.get(mat, 0.0) + qty, 4
                )

            svc_intervals = [
                iv for iv in win.crew_intervals if iv.service_id == sid
            ]
            distinct_crews = {iv.crew_id for iv in svc_intervals}
            if len(distinct_crews) < svc.required_crews_count:
                violations.append(
                    f"Serviço '{sid}' exige {svc.required_crews_count} equipe(s), "
                    f"mas recebeu {len(distinct_crews)}."
                )

            if not svc_intervals:
                violations.append(
                    f"Serviço '{sid}' na janela '{win.window_id}' sem alocação de equipe."
                )
                continue

            s_exec_start = min(iv.exec_start_hour for iv in svc_intervals)
            s_exec_end = max(iv.exec_end_hour for iv in svc_intervals)
            win_svc_exec_bounds[sid] = (s_exec_start, s_exec_end)
            service_exec_times[sid] = (win.day, s_exec_start, s_exec_end)

            for iv in svc_intervals:
                if iv.day != win.day:
                    violations.append(
                        f"Intervalo da equipe '{iv.crew_id}' no serviço '{sid}' com dia "
                        f"inconsistente ({iv.day} != {win.day})."
                    )
                if iv.crew_id not in catalog.crews:
                    violations.append(
                        f"Equipe '{iv.crew_id}' inexistente no catálogo de recursos."
                    )
                    continue
                crew = catalog.crews[iv.crew_id]
                if win.day not in crew.available_days:
                    violations.append(
                        f"Equipe '{iv.crew_id}' indisponível no calendário para o dia {win.day}."
                    )
                if not crew_has_skills(crew, svc.required_skills):
                    violations.append(
                        f"Equipe '{iv.crew_id}' ({crew.skills}) não possui todas as "
                        f"competências exigidas por '{sid}' ({svc.required_skills})."
                    )

                req_prep = max(svc.prep_hours, crew.prep_hours)
                if (iv.exec_start_hour - iv.prep_start_hour) < req_prep - 1e-4:
                    violations.append(
                        f"Preparação insuficiente da equipe '{iv.crew_id}' no serviço '{sid}' "
                        f"({iv.exec_start_hour - iv.prep_start_hour:.2f}h < {req_prep:.2f}h)."
                    )
                if (iv.exec_end_hour - iv.exec_start_hour) < svc.duration_hours - 1e-4:
                    violations.append(
                        f"Duração de execução insuficiente para '{sid}' na equipe '{iv.crew_id}' "
                        f"({iv.exec_end_hour - iv.exec_start_hour:.2f}h < {svc.duration_hours:.2f}h)."
                    )
                if (iv.recomp_end_hour - iv.exec_end_hour) < svc.recomp_hours - 1e-4:
                    violations.append(
                        f"Recomposição de equipe insuficiente para '{sid}' na equipe '{iv.crew_id}'."
                    )
                if iv.exec_start_hour < win.work_start_hour - 1e-4:
                    violations.append(
                        f"Serviço '{sid}' inicia execução ({iv.exec_start_hour:.2f}h) antes "
                        f"do isolamento da janela ({win.work_start_hour:.2f}h)."
                    )
                if iv.exec_end_hour > win.work_end_hour + 1e-4:
                    violations.append(
                        f"Serviço '{sid}' termina execução ({iv.exec_end_hour:.2f}h) após "
                        f"o fim do trabalho da janela ({win.work_end_hour:.2f}h)."
                    )
                if iv.travel_start_hour < crew.shift_start_hour - 1e-4:
                    violations.append(
                        f"Equipe '{iv.crew_id}' inicia deslocamento ({iv.travel_start_hour:.2f}h) "
                        f"antes do início do turno ({crew.shift_start_hour:.2f}h)."
                    )
                if iv.recomp_end_hour > crew.shift_end_hour + 1e-4:
                    violations.append(
                        f"Equipe '{iv.crew_id}' encerra atividades ({iv.recomp_end_hour:.2f}h) "
                        f"após o fim do turno ({crew.shift_end_hour:.2f}h)."
                    )

                all_crew_intervals_by_day.setdefault(win.day, {}).setdefault(
                    iv.crew_id, []
                ).append(iv)

        win_sids = list(win_svc_exec_bounds.keys())
        for i in range(len(win_sids)):
            sid_a = win_sids[i]
            svc_a = svc_map[sid_a]
            a_start, a_end = win_svc_exec_bounds[sid_a]
            for j in range(i + 1, len(win_sids)):
                sid_b = win_sids[j]
                svc_b = svc_map[sid_b]
                b_start, b_end = win_svc_exec_bounds[sid_b]
                if not svc_a.allow_simultaneous_in_zone or not svc_b.allow_simultaneous_in_zone:
                    overlap = min(a_end, b_end) - max(a_start, b_start)
                    if overlap > 1e-4:
                        violations.append(
                            f"Violação de simultaneidade na janela '{win.window_id}': "
                            f"serviços '{sid_a}' e '{sid_b}' sobrepostos em {overlap:.2f}h."
                        )

    for day, crew_dict in all_crew_intervals_by_day.items():
        for crew_id, iv_list in crew_dict.items():
            crew = catalog.crews[crew_id]
            iv_list.sort(key=lambda x: (x.travel_start_hour, x.exec_start_hour))
            for idx, iv in enumerate(iv_list):
                if idx == 0:
                    from_x, from_y = crew.base_x, crew.base_y
                else:
                    prev_iv = iv_list[idx - 1]
                    from_x, from_y = prev_iv.x, prev_iv.y
                    if iv.travel_start_hour < prev_iv.recomp_end_hour - 1e-4:
                        violations.append(
                            f"Sobreposição de agenda da equipe '{crew_id}' no dia {day}: "
                            f"serviço '{iv.service_id}' inicia às {iv.travel_start_hour:.2f}h "
                            f"antes do término de '{prev_iv.service_id}' às {prev_iv.recomp_end_hour:.2f}h."
                        )
                expected_travel = compute_travel_hours(
                    from_x, from_y, iv.x, iv.y, crew.speed_kmh
                )
                actual_travel_window = iv.prep_start_hour - iv.travel_start_hour
                if actual_travel_window < expected_travel - 1e-3:
                    violations.append(
                        f"Tempo de deslocamento insuficiente para a equipe '{crew_id}' "
                        f"no serviço '{iv.service_id}' (alocado {actual_travel_window:.3f}h < "
                        f"necessário {expected_travel:.3f}h)."
                    )

    for sid, (s_day, s_start, _) in service_exec_times.items():
        svc = svc_map[sid]
        for pred_id in svc.precedence_service_ids:
            if pred_id not in service_exec_times:
                violations.append(
                    f"Precedência violada: serviço '{sid}' executado sem que seu "
                    f"predecessor '{pred_id}' tenha sido programado."
                )
                continue
            p_day, _, p_end = service_exec_times[pred_id]
            if p_day > s_day:
                violations.append(
                    f"Precedência violada entre dias: '{pred_id}' (dia {p_day}) agendado "
                    f"após '{sid}' (dia {s_day})."
                )
            elif p_day == s_day and p_end > s_start + 1e-4:
                violations.append(
                    f"Precedência violada no mesmo dia ({s_day}): '{pred_id}' termina às "
                    f"{p_end:.2f}h após início de '{sid}' às {s_start:.2f}h."
                )

    for mat, qty in recalculated_materials.items():
        avail = catalog.materials_inventory.get(mat, 0.0)
        if qty > avail + 1e-4:
            violations.append(
                f"Violação de estoque de material '{mat}': plano consome {qty:.2f}, "
                f"disponível {avail:.2f}."
            )

    active_known_services = [
        s
        for s in svc_map.values()
        if s.scope_status == "ACTIVE" and s.created_at_day <= plan.decision_day
    ]
    lc_alerts = detect_lifecycle_conflicts(active_known_services)
    for alert in lc_alerts:
        warnings.append(alert.explanation)

    recalc_consumer_hours = 0.0
    for ivs in recalc_consumer_intervals.values():
        merged = merge_intervals(ivs)
        recalc_consumer_hours += sum(e - s for s, e in merged)

    is_feasible = len(violations) == 0 and mandatory_complete

    return FeasibilityReport(
        plan_id=plan.plan_id,
        method_name=plan.method_name,
        scenario_id=plan.scenario_id,
        is_feasible=is_feasible,
        mandatory_portfolio_complete=mandatory_complete,
        completed_mandatory_count=completed_mandatory_count,
        total_mandatory_count=len(known_mandatory),
        violations=violations,
        warnings=warnings,
        recalculated_materials=recalculated_materials,
        recalculated_consumer_hours=round(recalc_consumer_hours, 4),
        recalculated_windows_count=len(plan.windows),
        recalculated_physical_maneuvers=recalculated_maneuvers,
    )
