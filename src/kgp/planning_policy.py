"""Políticas de escopo aplicadas antes de qualquer simulação de planejamento."""

from __future__ import annotations

from .contracts import ExecutionPlan, GridNetwork, ServiceOrder


def unsupported_input_plan(
    network: GridNetwork,
    services: list[ServiceOrder],
    scenario_id: str,
    decision_day: int,
    method_name: str,
) -> ExecutionPlan | None:
    """Bloqueia ilhamento por GD, ainda sem modelo operacional suportado."""
    if not network.allow_dg_islanding:
        return None
    known_ids = tuple(
        svc.service_id for svc in services
        if svc.scope_status == "ACTIVE" and svc.created_at_day <= decision_day
    )
    reason = (
        "allow_dg_islanding=True não é suportado: o demonstrador usa política "
        "anti-ilhamento e não modela formação/estabilidade de ilhas por GD."
    )
    return ExecutionPlan(
        plan_id=f"PLAN_{method_name}_{scenario_id}",
        method_name=method_name,
        scenario_id=scenario_id,
        decision_day=decision_day,
        unscheduled_service_ids=known_ids,
        exclusion_justifications={sid: reason for sid in known_ids},
        solver_status="UNSUPPORTED_INPUT",
        metadata={"unsupported_option": "allow_dg_islanding", "reason": reason},
    )
