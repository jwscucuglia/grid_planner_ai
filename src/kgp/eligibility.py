"""Elegibilidade elétrica, temporal, precedências e ciclo de vida no KGP.

Verifica se os ativos de trabalho de cada serviço estão efetivamente desenergizados
durante a etapa de execução da zona de isolamento, filtra conhecimento temporal
na data da decisão (prevenção de vazamento futuro) e detecta conflitos entre
manutenção e substituição sem eliminar ordens obrigatórias.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from .contracts import (
    GridNetwork,
    LifecycleConflictAlert,
    ManeuverStage,
    ServiceOrder,
    ServiceType,
)
from .topology import simulate_zone_intervention, validate_asset_registration


def filter_known_services(
    services: Iterable[ServiceOrder],
    decision_day: int,
) -> Tuple[List[ServiceOrder], Dict[str, str]]:
    """Filtra a carteira mantendo apenas serviços ativos e conhecidos na data da decisão."""
    known: List[ServiceOrder] = []
    excluded: Dict[str, str] = {}
    for svc in services:
        if svc.scope_status != "ACTIVE":
            excluded[svc.service_id] = (
                f"Serviço '{svc.service_id}' com status de escopo '{svc.scope_status}'."
            )
            continue
        if svc.created_at_day > decision_day:
            excluded[svc.service_id] = (
                f"Serviço '{svc.service_id}' criado no dia {svc.created_at_day}, "
                f"após a data de decisão (dia {decision_day}) — bloqueio anti-vazamento temporal."
            )
            continue
        known.append(svc)
    return known, excluded


def get_zone_deenergized_assets(
    network: GridNetwork,
    zone_id: str,
) -> Tuple[Set[str], bool, str]:
    """Obtém o conjunto de ativos efetivamente desenergizados na etapa EXECUTION da zona."""
    if zone_id not in network.isolation_zones:
        return set(), False, f"Zona '{zone_id}' inexistente na rede."

    steps, _ = simulate_zone_intervention(
        network=network,
        zone_id=zone_id,
        work_start_hour=9.0,
        work_end_hour=12.0,
    )
    exec_steps = [s for s in steps if s.stage == ManeuverStage.EXECUTION]
    if not exec_steps:
        return set(), False, f"Zona '{zone_id}' sem etapa de execução válida."

    exec_step = exec_steps[0]
    zone = network.isolation_zones[zone_id]
    if zone.requires_transfer_validation and not exec_step.transfer_feasible:
        return (
            set(),
            False,
            f"Zona '{zone_id}' depende de transferência inválida: {exec_step.notes}",
        )

    return set(exec_step.deenergized_assets), True, exec_step.notes


def check_service_zone_eligibility(
    network: GridNetwork,
    service: ServiceOrder,
    zone_id: str,
) -> Tuple[bool, str]:
    """Verifica se um serviço é eletricamente elegível em uma zona de isolamento."""
    if not service.target_assets:
        return False, f"Serviço '{service.service_id}' sem ativos de trabalho definidos."

    for asset_id in service.target_assets:
        reg_issues = validate_asset_registration(network, asset_id)
        if reg_issues:
            return False, "; ".join(reg_issues)

    if service.compatible_zone_ids and zone_id not in service.compatible_zone_ids:
        return (
            False,
            f"Zona '{zone_id}' fora das zonas permitidas {service.compatible_zone_ids} "
            f"para o serviço '{service.service_id}'.",
        )

    deenergized_assets, zone_ok, notes = get_zone_deenergized_assets(network, zone_id)
    if not zone_ok:
        return False, notes

    if service.requires_deenergized:
        missing_assets = [
            a for a in service.target_assets if a not in deenergized_assets
        ]
        if missing_assets:
            return (
                False,
                f"Ativo(s) {missing_assets} do serviço '{service.service_id}' "
                f"permanecem energizados na zona '{zone_id}'.",
            )

    return (
        True,
        f"Ativos {list(service.target_assets)} efetivamente isolados e desenergizados na zona '{zone_id}'.",
    )


def find_eligible_zones_for_service(
    network: GridNetwork,
    service: ServiceOrder,
) -> List[str]:
    """Retorna todas as zonas de isolamento que isolam com segurança o serviço."""
    candidate_zones = (
        list(service.compatible_zone_ids)
        if service.compatible_zone_ids
        else sorted(network.isolation_zones.keys())
    )
    eligible: List[str] = []
    for z_id in candidate_zones:
        ok, _ = check_service_zone_eligibility(network, service, z_id)
        if ok:
            eligible.append(z_id)
    return eligible


def detect_lifecycle_conflicts(
    services: Iterable[ServiceOrder],
) -> List[LifecycleConflictAlert]:
    """Identifica conflitos entre manutenção preventiva/poda e substituição de ativo.

    Não elimina ordens obrigatórias; gera alerta auditável e recomenda precedência
    explícita ou revisão formal de escopo pela distribuidora.
    """
    svc_list = list(services)
    alerts: List[LifecycleConflictAlert] = []

    maint_by_asset: Dict[str, List[ServiceOrder]] = {}
    replace_by_asset: Dict[str, List[ServiceOrder]] = {}

    for svc in svc_list:
        if svc.scope_status != "ACTIVE":
            continue
        maint_assets = set(svc.maintains_asset_ids)
        if not maint_assets and svc.service_type in (
            ServiceType.PREVENTIVA,
            ServiceType.PODA,
            ServiceType.TRANSFORMADOR,
        ):
            maint_assets = set(svc.target_assets)

        repl_assets = set(svc.replaces_asset_ids)
        if not repl_assets and svc.service_type == ServiceType.SUBSTITUICAO:
            repl_assets = set(svc.target_assets)

        for a_id in maint_assets:
            maint_by_asset.setdefault(a_id, []).append(svc)
        for a_id in repl_assets:
            replace_by_asset.setdefault(a_id, []).append(svc)

    seen_pairs: Set[Tuple[str, str, str]] = set()
    for asset_id, m_list in sorted(maint_by_asset.items()):
        r_list = replace_by_asset.get(asset_id, [])
        for m_svc in m_list:
            for r_svc in r_list:
                if m_svc.service_id == r_svc.service_id:
                    continue
                key = (m_svc.service_id, r_svc.service_id, asset_id)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                alerts.append(
                    LifecycleConflictAlert(
                        maintenance_service_id=m_svc.service_id,
                        replacement_service_id=r_svc.service_id,
                        asset_id=asset_id,
                        recommended_precedence=(r_svc.service_id, m_svc.service_id),
                        explanation=(
                            f"Conflito de ciclo de vida no ativo '{asset_id}': "
                            f"manutenção '{m_svc.service_id}' ({m_svc.service_type.value}) "
                            f"concorre com substituição '{r_svc.service_id}'. "
                            "Ambas as ordens obrigatórias foram preservadas na carteira; "
                            "recomenda-se acoplamento na mesma janela ou revisão formal de escopo pela Cemig."
                        ),
                    )
                )
    return alerts


def check_temporal_and_precedence_eligibility(
    service: ServiceOrder,
    day: int,
    decision_day: int,
    service_day_Lookup: Dict[str, int],
    same_window_services: Set[str],
) -> Tuple[bool, str]:
    """Valida datas de criação, liberação (ready_at), prazo (deadline) e precedências."""
    if service.created_at_day > decision_day:
        return (
            False,
            f"Serviço '{service.service_id}' criado após data da decisão ({service.created_at_day} > {decision_day}).",
        )
    if day < service.ready_at_day:
        return (
            False,
            f"Serviço '{service.service_id}' agendado no dia {day} antes da liberação (ready_at={service.ready_at_day}).",
        )
    if day > service.deadline_day:
        return (
            False,
            f"Serviço '{service.service_id}' agendado no dia {day} após o prazo limite (deadline={service.deadline_day}).",
        )

    for pred_id in service.precedence_service_ids:
        if pred_id in same_window_services:
            continue
        pred_day = service_day_Lookup.get(pred_id)
        if pred_day is None:
            return (
                False,
                f"Precedência '{pred_id}' do serviço '{service.service_id}' não foi programada.",
            )
        if pred_day > day:
            return (
                False,
                f"Precedência '{pred_id}' programada no dia {pred_day} após '{service.service_id}' (dia {day}).",
            )

    return True, "Elegibilidade temporal e precedências atendidas."
