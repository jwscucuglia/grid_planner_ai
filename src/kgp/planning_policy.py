"""Políticas de escopo aplicadas antes de qualquer simulação de planejamento."""

from __future__ import annotations

from .contracts import ExecutionPlan, GridNetwork, ServiceOrder, ResourceCatalog, operational_time_ticks, TICKS_PER_HOUR


def unsupported_input_plan(
    network: GridNetwork,
    services: list[ServiceOrder],
    scenario_id: str,
    decision_day: int,
    method_name: str,
    catalog: ResourceCatalog | None = None,
) -> ExecutionPlan | None:
    """Bloqueia opções fora do catálogo elétrico e temporal suportado."""
    no_daily_gap = catalog is not None and (operational_time_ticks(catalog.window_end_hour) - operational_time_ticks(catalog.window_start_hour) >= 24*TICKS_PER_HOUR)
    if not network.allow_dg_islanding and not no_daily_gap:
        return None
    known_ids = tuple(
        svc.service_id for svc in services
        if svc.scope_status == "ACTIVE" and svc.created_at_day <= decision_day
    )
    reason = (
        "allow_dg_islanding=True não é suportado: o demonstrador usa política "
        "anti-ilhamento e não modela formação/estabilidade de ilhas por GD."
    )
    option = 'allow_dg_islanding'
    if no_daily_gap and not network.allow_dg_islanding:
        option = 'continuous_24h_catalog'
        reason = 'Catálogo contínuo 24h não suportado: as colunas diárias exigem gap positivo de recomposição entre dias.'
    return ExecutionPlan(
        plan_id=f"PLAN_{method_name}_{scenario_id}",
        method_name=method_name,
        scenario_id=scenario_id,
        decision_day=decision_day,
        unscheduled_service_ids=known_ids,
        exclusion_justifications={sid: reason for sid in known_ids},
        solver_status="UNSUPPORTED_INPUT",
        metadata={"unsupported_option": option, "reason": reason},
    )
