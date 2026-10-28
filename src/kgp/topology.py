"""Modelagem elétrica e topológica pós-manobra do KAPTUS GRID PLANNER (KGP).

Calcula componentes conectados às fontes por fase, barras e ativos efetivamente
desenergizados, consumidores sem alimentação em cada etapa da manobra (isolamento,
transferência por chave NA/NF, execução e recomposição) e verificação elétrica
simplificada de transferência de carga (carregamento térmico e queda de tensão).
"""

from __future__ import annotations

from collections import deque
import math
from typing import Dict, List, Optional, Set, Tuple

from .contracts import (
    GridAsset,
    GridBranch,
    GridNetwork,
    GridNode,
    IsolationZoneSpec,
    ManeuverStage,
    ManeuverStepResult,
    SwitchState,
)


class TopologyValidationError(ValueError):
    """Erro de integridade cadastral de ativo, barra, trecho ou fase."""


def validate_asset_registration(network: GridNetwork, asset_id: str) -> List[str]:
    """Verifica se um ativo possui cadastro topológico e de fases válido na rede."""
    issues: List[str] = []
    if asset_id not in network.assets:
        issues.append(f"Ativo '{asset_id}' ausente no cadastro da rede.")
        return issues

    asset: GridAsset = network.assets[asset_id]
    if not asset.in_service:
        issues.append(f"Ativo '{asset_id}' fora de serviço no cadastro.")
    if not asset.phases:
        issues.append(f"Ativo '{asset_id}' sem fases cadastradas.")
    for phase in asset.phases:
        if phase not in ("A", "B", "C"):
            issues.append(f"Ativo '{asset_id}' possui fase inválida '{phase}'.")

    if asset.node_id is None and asset.branch_id is None:
        issues.append(f"Ativo '{asset_id}' órfão (sem node_id e sem branch_id).")
    if asset.node_id is not None:
        if asset.node_id not in network.nodes:
            issues.append(
                f"Ativo '{asset_id}' referencia barra inexistente '{asset.node_id}'."
            )
        else:
            node = network.nodes[asset.node_id]
            missing_phases = [p for p in asset.phases if p not in node.phases]
            if missing_phases:
                issues.append(
                    f"Ativo '{asset_id}' requer fase(s) {missing_phases} ausente(s) "
                    f"na barra '{asset.node_id}' ({node.phases})."
                )
    if asset.branch_id is not None:
        if asset.branch_id not in network.branches:
            issues.append(
                f"Ativo '{asset_id}' referencia trecho inexistente '{asset.branch_id}'."
            )
        else:
            branch = network.branches[asset.branch_id]
            missing_phases = [p for p in asset.phases if p not in branch.phases]
            if missing_phases:
                issues.append(
                    f"Ativo '{asset_id}' requer fase(s) {missing_phases} ausente(s) "
                    f"no trecho '{asset.branch_id}' ({branch.phases})."
                )
    return issues


def get_effective_switch_states(
    network: GridNetwork,
    open_switches_override: Set[str],
    closed_switches_override: Set[str],
) -> Dict[str, SwitchState]:
    """Determina o estado efetivo de cada chave considerando overrides da etapa."""
    states: Dict[str, SwitchState] = {}
    for sw_id, sw in network.switches.items():
        if not sw.in_service:
            states[sw_id] = SwitchState.OPEN
        elif sw_id in open_switches_override:
            states[sw_id] = SwitchState.OPEN
        elif sw_id in closed_switches_override:
            states[sw_id] = SwitchState.CLOSED
        else:
            states[sw_id] = sw.current_state
    return states


def get_active_branches(
    network: GridNetwork,
    switch_states: Dict[str, SwitchState],
) -> Dict[str, GridBranch]:
    """Retorna os trechos eletricamente fechados na etapa considerada."""
    branch_to_switches: Dict[str, List[str]] = {}
    for sw_id, sw in network.switches.items():
        branch_to_switches.setdefault(sw.branch_id, []).append(sw_id)

    active: Dict[str, GridBranch] = {}
    for br_id, br in network.branches.items():
        if not br.in_service:
            continue
        u = network.nodes.get(br.from_node)
        v = network.nodes.get(br.to_node)
        if u is None or v is None or not u.in_service or not v.in_service:
            continue
        sw_ids = branch_to_switches.get(br_id, [])
        if any(switch_states.get(sw_id) == SwitchState.OPEN for sw_id in sw_ids):
            continue
        active[br_id] = br
    return active


def compute_energized_phases_by_node(
    network: GridNetwork,
    open_switches: Set[str],
    closed_switches: Set[str],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], List[str]]:
    """Calcula, por fase, os nós conectados a fontes ativas e a origem do suprimento.

    Retorna:
        energized_phases: mapa node_id -> conjunto de fases energizadas
        supplying_sources: mapa node_id -> conjunto de fontes que alimentam o nó
        topology_notes: observações sobre ilhamento de GD ou fontes inativas
    """
    switch_states = get_effective_switch_states(network, open_switches, closed_switches)
    active_branches = get_active_branches(network, switch_states)

    adj: Dict[str, List[Tuple[str, GridBranch]]] = {
        node_id: [] for node_id in network.nodes
    }
    for br in active_branches.values():
        adj[br.from_node].append((br.to_node, br))
        adj[br.to_node].append((br.from_node, br))

    energized_phases: Dict[str, Set[str]] = {
        node_id: set() for node_id in network.nodes
    }
    supplying_sources: Dict[str, Set[str]] = {
        node_id: set() for node_id in network.nodes
    }
    notes: List[str] = []

    for phase in ("A", "B", "C"):
        queue: deque[Tuple[str, str]] = deque()
        visited_node_source: Set[Tuple[str, str]] = set()

        for node_id, node in network.nodes.items():
            if not node.in_service or phase not in node.phases:
                continue
            is_valid_source = node.is_source or (
                network.allow_dg_islanding and node.distributed_gen_kw > 0.0
            )
            if is_valid_source:
                energized_phases[node_id].add(phase)
                supplying_sources[node_id].add(node_id)
                queue.append((node_id, node_id))
                visited_node_source.add((node_id, node_id))

        while queue:
            curr_id, src_id = queue.popleft()
            for nbr_id, br in adj.get(curr_id, []):
                if phase not in br.phases:
                    continue
                nbr_node = network.nodes[nbr_id]
                if not nbr_node.in_service or phase not in nbr_node.phases:
                    continue
                energized_phases[nbr_id].add(phase)
                supplying_sources[nbr_id].add(src_id)
                if (nbr_id, src_id) not in visited_node_source:
                    visited_node_source.add((nbr_id, src_id))
                    queue.append((nbr_id, src_id))

    if not network.allow_dg_islanding:
        for node_id, node in network.nodes.items():
            if node.distributed_gen_kw > 0.0 and not energized_phases[node_id]:
                notes.append(
                    f"GD em '{node_id}' ({node.distributed_gen_kw:.1f} kW) desconectada "
                    "por proteção anti-ilhamento (sem fonte principal conectada)."
                )

    return energized_phases, supplying_sources, notes


def verify_transfer_electrical_feasibility(
    network: GridNetwork,
    zone: IsolationZoneSpec,
    open_switches: Set[str],
    closed_switches: Set[str],
    energized_phases: Dict[str, Set[str]],
    supplying_sources: Dict[str, Set[str]],
) -> Tuple[bool, float, float, str]:
    """Verifica factibilidade elétrica (fluxo radial aproximado, carregamento e queda de tensão).

    Se parâmetros elétricos forem insuficientes na zona ou nos trechos, desabilita
    a transferência e declara explicitamente a limitação.
    """
    if not closed_switches and not zone.requires_transfer_validation:
        return True, 0.0, 0.0, "Sem transferência por chave NA."

    if not zone.transfer_params_complete:
        return (
            False,
            0.0,
            0.0,
            f"Zona '{zone.zone_id}' sem parâmetros elétricos completos para validar transferência.",
        )

    switch_states = get_effective_switch_states(network, open_switches, closed_switches)
    active_branches = get_active_branches(network, switch_states)

    for br in active_branches.values():
        if br.max_capacity_kw <= 0.0 or br.r_ohm < 0.0 or br.x_ohm < 0.0:
            return (
                False,
                0.0,
                0.0,
                f"Trecho '{br.branch_id}' com parâmetros elétricos inválidos/ausentes.",
            )

    node_demand_kw: Dict[str, float] = {
        node_id: node.load_kw for node_id, node in network.nodes.items()
    }
    for consumer in network.consumers.values():
        if consumer.node_id in node_demand_kw:
            if consumer.phase in energized_phases.get(consumer.node_id, set()):
                node_demand_kw[consumer.node_id] += consumer.avg_demand_kw

    adj: Dict[str, List[Tuple[str, GridBranch]]] = {
        node_id: [] for node_id in network.nodes
    }
    for br in active_branches.values():
        adj[br.from_node].append((br.to_node, br))
        adj[br.to_node].append((br.from_node, br))

    max_loading_pct = 0.0
    max_vdrop_pct = 0.0

    active_sources = [
        n.node_id
        for n in network.nodes.values()
        if n.is_source and n.in_service and energized_phases.get(n.node_id)
    ]

    for src_id in active_sources:
        src_node = network.nodes[src_id]
        if src_node.nominal_voltage_kv <= 0.0:
            return (
                False,
                0.0,
                0.0,
                f"Fonte '{src_id}' sem tensão nominal válida.",
            )

        parent: Dict[str, Optional[str]] = {src_id: None}
        parent_branch: Dict[str, GridBranch] = {}
        order: List[str] = []
        queue: deque[str] = deque([src_id])

        while queue:
            curr = queue.popleft()
            order.append(curr)
            for nbr, br in adj.get(curr, []):
                if nbr not in parent and energized_phases.get(nbr):
                    parent[nbr] = curr
                    parent_branch[nbr] = br
                    queue.append(nbr)

        subtree_kw: Dict[str, float] = {
            n_id: node_demand_kw.get(n_id, 0.0) for n_id in order
        }
        for n_id in reversed(order):
            p_id = parent[n_id]
            if p_id is not None:
                num_sources = max(1, len(supplying_sources.get(n_id, {src_id})))
                subtree_kw[p_id] += subtree_kw[n_id] / num_sources

        v_drop_node_pct: Dict[str, float] = {src_id: 0.0}
        v_base_kv = src_node.nominal_voltage_kv
        power_factor = 0.92
        tan_phi = math.tan(math.acos(power_factor))

        for n_id in order:
            if n_id == src_id:
                continue
            p_id = parent[n_id]
            br = parent_branch[n_id]
            assert p_id is not None
            flow_kw = subtree_kw[n_id]
            loading_pct = (flow_kw / br.max_capacity_kw) * 100.0
            max_loading_pct = max(max_loading_pct, loading_pct)

            z_eff_ohm = br.r_ohm + br.x_ohm * tan_phi
            p_mw = flow_kw / 1000.0
            step_vdrop_pct = (p_mw * z_eff_ohm / (v_base_kv ** 2)) * 100.0
            v_drop_node_pct[n_id] = v_drop_node_pct[p_id] + step_vdrop_pct
            max_vdrop_pct = max(max_vdrop_pct, v_drop_node_pct[n_id])

    if max_loading_pct > 100.0:
        return (
            False,
            max_loading_pct,
            max_vdrop_pct,
            f"Sobrecarga térmica em transferência ({max_loading_pct:.1f}% > 100%).",
        )
    if max_vdrop_pct > zone.max_transfer_voltage_drop_pct:
        return (
            False,
            max_loading_pct,
            max_vdrop_pct,
            f"Queda de tensão excessiva na transferência ({max_vdrop_pct:.2f}% > "
            f"{zone.max_transfer_voltage_drop_pct:.2f}%).",
        )

    return (
        True,
        max_loading_pct,
        max_vdrop_pct,
        f"Transferência factível (carregamento máx {max_loading_pct:.1f}%, "
        f"queda máx {max_vdrop_pct:.2f}%).",
    )


def evaluate_maneuver_step(
    network: GridNetwork,
    step_order: int,
    stage: ManeuverStage,
    start_hour: float,
    end_hour: float,
    open_switches: Tuple[str, ...],
    closed_switches: Tuple[str, ...],
    zone: Optional[IsolationZoneSpec] = None,
) -> ManeuverStepResult:
    """Avalia o estado completo da rede em uma etapa específica da manobra."""
    open_set = set(open_switches)
    closed_set = set(closed_switches)

    energized_phases, supplying_sources, topo_notes = compute_energized_phases_by_node(
        network, open_set, closed_set
    )

    energized_nodes: List[str] = []
    deenergized_nodes: List[str] = []
    for node_id in sorted(network.nodes.keys()):
        node = network.nodes[node_id]
        active_ph = energized_phases.get(node_id, set())
        if not active_ph:
            deenergized_nodes.append(node_id)
        else:
            energized_nodes.append(node_id)
            missing = [p for p in node.phases if p not in active_ph]
            if missing:
                topo_notes.append(
                    f"Barra '{node_id}' parcialmente energizada (fases ativas: "
                    f"{sorted(active_ph)}, fases sem tensão: {missing}). Não liberada "
                    "para intervenção desenergizada."
                )

    deenergized_assets: List[str] = []
    for asset_id in sorted(network.assets.keys()):
        asset = network.assets[asset_id]
        issues = validate_asset_registration(network, asset_id)
        if issues:
            continue
        is_asset_live = False
        if asset.node_id is not None:
            if energized_phases.get(asset.node_id, set()):
                is_asset_live = True
        if asset.branch_id is not None:
            br = network.branches[asset.branch_id]
            if energized_phases.get(br.from_node, set()) or energized_phases.get(
                br.to_node, set()
            ):
                switch_states = get_effective_switch_states(
                    network, open_set, closed_set
                )
                active_branches = get_active_branches(network, switch_states)
                if asset.branch_id in active_branches:
                    is_asset_live = True
                elif energized_phases.get(br.from_node, set()) and energized_phases.get(
                    br.to_node, set()
                ):
                    is_asset_live = True
        if not is_asset_live:
            deenergized_assets.append(asset_id)

    interrupted_consumers: List[str] = []
    for cid in sorted(network.consumers.keys()):
        consumer = network.consumers[cid]
        if consumer.node_id not in network.nodes:
            interrupted_consumers.append(cid)
            continue
        active_ph = energized_phases.get(consumer.node_id, set())
        if consumer.phase not in active_ph:
            interrupted_consumers.append(cid)

    active_sources = sorted(
        {
            src
            for srcs in supplying_sources.values()
            for src in srcs
        }
    )

    transfer_ok = True
    max_loading = 0.0
    max_vdrop = 0.0
    if zone is not None and (closed_set or zone.requires_transfer_validation):
        transfer_ok, max_loading, max_vdrop, tr_note = (
            verify_transfer_electrical_feasibility(
                network,
                zone,
                open_set,
                closed_set,
                energized_phases,
                supplying_sources,
            )
        )
        topo_notes.append(tr_note)

    return ManeuverStepResult(
        step_order=step_order,
        stage=stage,
        start_hour=round(start_hour, 4),
        end_hour=round(end_hour, 4),
        open_switches=tuple(sorted(open_set)),
        closed_switches=tuple(sorted(closed_set)),
        energized_nodes=tuple(energized_nodes),
        deenergized_nodes=tuple(deenergized_nodes),
        deenergized_assets=tuple(deenergized_assets),
        interrupted_consumers=tuple(interrupted_consumers),
        active_sources=tuple(active_sources),
        transfer_feasible=transfer_ok,
        max_loading_pct=round(max_loading, 2),
        estimated_voltage_drop_pct=round(max_vdrop, 3),
        notes="; ".join(topo_notes),
    )


def simulate_zone_intervention(
    network: GridNetwork,
    zone_id: str,
    work_start_hour: float,
    work_end_hour: float,
) -> Tuple[Tuple[ManeuverStepResult, ...], int]:
    """Simula todas as etapas de uma intervenção em uma zona de isolamento.

    Se a zona possuir chave de interligação (NA) e a transferência for eletricamente
    factível:
      1. Etapa ISOLATION: abertura das chaves NF de isolamento (duração prep_duration_hours/2).
      2. Etapa TRANSFER: fechamento da chave NA de socorro (duração prep_duration_hours/2).
      3. Etapa EXECUTION: execução dos serviços com socorro ativo [work_start_hour, work_end_hour].
      4. Etapa RECOMPOSITION: retorno das chaves NA/NF ao estado normal.

    Se a transferência não for factível (ou não existir chave NA):
      1. Etapa ISOLATION: abertura das chaves NF [work_start - prep, work_start].
      2. Etapa EXECUTION: execução sem transferência [work_start, work_end].
      3. Etapa RECOMPOSITION: fechamento das chaves NF [work_end, work_end + recomp].

    Retorna:
        (tupla de ManeuverStepResult, total de manobras físicas de chaves)
    """
    if zone_id not in network.isolation_zones:
        raise TopologyValidationError(f"Zona de isolamento '{zone_id}' inexistente.")

    zone = network.isolation_zones[zone_id]
    prep_start = work_start_hour - zone.prep_duration_hours
    recomp_end = work_end_hour + zone.recomp_duration_hours

    steps: List[ManeuverStepResult] = []

    if zone.tie_switches_closed:
        probe_step = evaluate_maneuver_step(
            network=network,
            step_order=0,
            stage=ManeuverStage.TRANSFER,
            start_hour=work_start_hour,
            end_hour=work_end_hour,
            open_switches=zone.open_switches,
            closed_switches=zone.tie_switches_closed,
            zone=zone,
        )
        if probe_step.transfer_feasible:
            mid_prep = prep_start + (zone.prep_duration_hours * 0.5)
            step1 = evaluate_maneuver_step(
                network=network,
                step_order=1,
                stage=ManeuverStage.ISOLATION,
                start_hour=prep_start,
                end_hour=mid_prep,
                open_switches=zone.open_switches,
                closed_switches=(),
                zone=zone,
            )
            step2 = evaluate_maneuver_step(
                network=network,
                step_order=2,
                stage=ManeuverStage.TRANSFER,
                start_hour=mid_prep,
                end_hour=work_start_hour,
                open_switches=zone.open_switches,
                closed_switches=zone.tie_switches_closed,
                zone=zone,
            )
            step3 = evaluate_maneuver_step(
                network=network,
                step_order=3,
                stage=ManeuverStage.EXECUTION,
                start_hour=work_start_hour,
                end_hour=work_end_hour,
                open_switches=zone.open_switches,
                closed_switches=zone.tie_switches_closed,
                zone=zone,
            )
            step4 = evaluate_maneuver_step(
                network=network,
                step_order=4,
                stage=ManeuverStage.RECOMPOSITION,
                start_hour=work_end_hour,
                end_hour=recomp_end,
                open_switches=zone.open_switches,
                closed_switches=zone.tie_switches_closed,
                zone=zone,
            )
            steps.extend([step1, step2, step3, step4])
            physical_maneuvers = 2 * (
                len(zone.open_switches) + len(zone.tie_switches_closed)
            )
            return tuple(steps), physical_maneuvers

    step1 = evaluate_maneuver_step(
        network=network,
        step_order=1,
        stage=ManeuverStage.ISOLATION,
        start_hour=prep_start,
        end_hour=work_start_hour,
        open_switches=zone.open_switches,
        closed_switches=(),
        zone=zone,
    )
    step2 = evaluate_maneuver_step(
        network=network,
        step_order=2,
        stage=ManeuverStage.EXECUTION,
        start_hour=work_start_hour,
        end_hour=work_end_hour,
        open_switches=zone.open_switches,
        closed_switches=(),
        zone=zone,
    )
    step3 = evaluate_maneuver_step(
        network=network,
        step_order=3,
        stage=ManeuverStage.RECOMPOSITION,
        start_hour=work_end_hour,
        end_hour=recomp_end,
        open_switches=zone.open_switches,
        closed_switches=(),
        zone=zone,
    )
    steps.extend([step1, step2, step3])
    physical_maneuvers = 2 * len(zone.open_switches)
    return tuple(steps), physical_maneuvers
