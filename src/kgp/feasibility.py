"""Verificador independente de factibilidade elétrica, temporal e de recursos (KGP).

Módulo estritamente separado do otimizador e dos comparadores que audita
qualquer ExecutionPlan exportado recalculando de forma autônoma:
- conclusão de 100% da carteira obrigatória conhecida na data da decisão;
- ausência de vazamento temporal (serviços futuros) e cumprimento de ready_at/deadline;
- validação cadastral de ativos-alvo (`target_assets` não vazio, existente e em serviço)
  e permissão operacional de chaves (`is_maneuverable=True` e `in_service=True`);
- integridade dos impactos exportados (confrontando etapas, manobras físicas,
  consumidores interrompidos e clientes-hora exportados contra o recálculo);
- isolamento elétrico pós-manobra real por fase (incluindo regra conservadora de
  ambas as extremidades desenergizadas para ativos de trecho e verificação elétrica
  de transferência estritamente por fase);
- cronologia conjunta de estados de chaves no dia e ausência de sobreposição entre
  janelas que compartilhem zona, chaves ou ativos isolados;
- ocupação contínua de cada equipe no tempo, coordenadas canônicas de deslocamento
  (`svc.x, svc.y` vs. `iv.x, iv.y`) e interseção simultânea obrigatória quando um
  serviço requer 2+ equipes atuando juntas;
- precedências entre serviços (entre dias ou intrajanela) e simultaneidade na zona;
- consumo acumulado de materiais frente ao estoque.
"""

from __future__ import annotations

from collections import deque
import math
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .contracts import (
    CrewResource,
    CrewScheduleInterval,
    ExecutionPlan,
    FeasibilityReport,
    GridBranch,
    GridNetwork,
    IsolationZoneSpec,
    ManeuverStage,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
    SwitchState,
)
from .eligibility import detect_lifecycle_conflicts
from .metrics import merge_intervals
from .topology import (
    simulate_zone_intervention,
    validate_asset_registration,
    validate_switch_maneuverability,
)


def _canonical_travel_hours(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    speed_kmh: float,
) -> float:
    dist_km = math.hypot(x2 - x1, y2 - y1)
    if dist_km <= 1e-6:
        return 0.0
    return round(dist_km / max(5.0, speed_kmh), 4)


def _crew_has_all_skills(
    crew: CrewResource,
    required_skills: Tuple[str, ...],
) -> bool:
    c_set = set(crew.skills)
    return all(req in c_set for req in required_skills)


def _independent_energized_phases_and_sources(
    network: GridNetwork,
    open_switches: Set[str],
    closed_switches: Set[str],
) -> Tuple[Dict[str, Set[str]], Dict[str, Dict[str, Set[str]]], Dict[str, GridBranch]]:
    """Recálculo autônomo de fases energizadas e fontes supridoras por fase via BFS."""
    branch_switches: Dict[str, List[str]] = {}
    for sw_id, sw in network.switches.items():
        branch_switches.setdefault(sw.branch_id, []).append(sw_id)

    active_branches: Dict[str, GridBranch] = {}
    adj_by_phase: Dict[str, Dict[str, List[Tuple[str, GridBranch]]]] = {
        ph: {nid: [] for nid in network.nodes} for ph in ("A", "B", "C")
    }
    for br_id, br in network.branches.items():
        if not br.in_service:
            continue
        if br.from_node not in network.nodes or br.to_node not in network.nodes:
            continue
        if (
            not network.nodes[br.from_node].in_service
            or not network.nodes[br.to_node].in_service
        ):
            continue
        is_open = False
        for sw_id in branch_switches.get(br_id, []):
            sw = network.switches[sw_id]
            if not sw.in_service:
                is_open = True
                break
            if not sw.is_maneuverable:
                eff_state = sw.current_state
            elif sw_id in open_switches:
                eff_state = SwitchState.OPEN
            elif sw_id in closed_switches:
                eff_state = SwitchState.CLOSED
            else:
                eff_state = sw.current_state
            if eff_state == SwitchState.OPEN:
                is_open = True
                break
        if is_open:
            continue
        active_branches[br_id] = br
        for ph in br.phases:
            if ph in adj_by_phase:
                adj_by_phase[ph][br.from_node].append((br.to_node, br))
                adj_by_phase[ph][br.to_node].append((br.from_node, br))

    energized: Dict[str, Set[str]] = {nid: set() for nid in network.nodes}
    sources_by_ph: Dict[str, Dict[str, Set[str]]] = {
        ph: {nid: set() for nid in network.nodes} for ph in ("A", "B", "C")
    }

    for ph in ("A", "B", "C"):
        q: deque[Tuple[str, str]] = deque()
        visited: Set[Tuple[str, str]] = set()
        for nid, node in network.nodes.items():
            if not node.in_service or ph not in node.phases:
                continue
            is_src = node.is_source or (
                network.allow_dg_islanding and node.distributed_gen_kw > 0.0
            )
            if is_src:
                energized[nid].add(ph)
                sources_by_ph[ph][nid].add(nid)
                visited.add((nid, nid))
                q.append((nid, nid))
        while q:
            curr, src_id = q.popleft()
            for nbr, _ in adj_by_phase[ph].get(curr, []):
                nbr_node = network.nodes[nbr]
                if nbr_node.in_service and ph in nbr_node.phases:
                    energized[nbr].add(ph)
                    sources_by_ph[ph][nbr].add(src_id)
                    if (nbr, src_id) not in visited:
                        visited.add((nbr, src_id))
                        q.append((nbr, src_id))

    return energized, sources_by_ph, active_branches


def _independent_phase_transfer_check(
    network: GridNetwork,
    zone: IsolationZoneSpec,
    open_switches: Set[str],
    closed_switches: Set[str],
    energized_phases: Dict[str, Set[str]],
    sources_by_ph: Dict[str, Dict[str, Set[str]]],
    active_branches: Dict[str, GridBranch],
) -> Tuple[bool, float, float, str]:
    """Recálculo autônomo por fase do fluxo de transferência, carregamento e queda de tensão."""
    if not closed_switches and not zone.requires_transfer_validation:
        return True, 0.0, 0.0, "OK"

    sw_err = validate_switch_maneuverability(
        network, open_switches, closed_switches
    )
    if sw_err:
        return False, 0.0, 0.0, "; ".join(sw_err)

    if not zone.transfer_params_complete:
        return (
            False,
            0.0,
            0.0,
            f"Parâmetros de transferência incompletos na zona '{zone.zone_id}'.",
        )

    for br in active_branches.values():
        if br.max_capacity_kw <= 0.0 or br.r_ohm < 0.0 or br.x_ohm < 0.0:
            return (
                False,
                0.0,
                0.0,
                f"Parâmetros elétricos inválidos no trecho '{br.branch_id}'.",
            )

    node_ph_demand: Dict[str, Dict[str, float]] = {
        ph: {nid: 0.0 for nid in network.nodes} for ph in ("A", "B", "C")
    }
    for nid, node in network.nodes.items():
        act_ph = energized_phases.get(nid, set())
        if act_ph and node.load_kw > 0.0:
            share = node.load_kw / float(len(act_ph))
            for ph in act_ph:
                node_ph_demand[ph][nid] += share

    for cons in network.consumers.values():
        if cons.node_id in network.nodes:
            if cons.phase in energized_phases.get(cons.node_id, set()):
                node_ph_demand[cons.phase][cons.node_id] += cons.avg_demand_kw

    br_ph_flow: Dict[str, Dict[str, float]] = {
        br_id: {ph: 0.0 for ph in ("A", "B", "C")} for br_id in active_branches
    }
    trees: List[
        Tuple[
            str,
            str,
            List[str],
            Dict[str, Optional[str]],
            Dict[str, GridBranch],
        ]
    ] = []

    for ph in ("A", "B", "C"):
        adj_ph: Dict[str, List[Tuple[str, GridBranch]]] = {
            nid: [] for nid in network.nodes
        }
        for br in active_branches.values():
            if ph not in br.phases:
                continue
            if (
                ph in energized_phases.get(br.from_node, set())
                and ph in energized_phases.get(br.to_node, set())
            ):
                adj_ph[br.from_node].append((br.to_node, br))
                adj_ph[br.to_node].append((br.from_node, br))

        srcs_ph = [
            n.node_id
            for n in network.nodes.values()
            if n.is_source
            and n.in_service
            and ph in energized_phases.get(n.node_id, set())
        ]
        for src_id in srcs_ph:
            if network.nodes[src_id].nominal_voltage_kv <= 0.0:
                return False, 0.0, 0.0, f"Tensão nominal inválida em '{src_id}'."
            parent: Dict[str, Optional[str]] = {src_id: None}
            parent_br: Dict[str, GridBranch] = {}
            order: List[str] = []
            q: deque[str] = deque([src_id])
            while q:
                curr = q.popleft()
                order.append(curr)
                for nbr, br in adj_ph.get(curr, []):
                    if nbr not in parent:
                        parent[nbr] = curr
                        parent_br[nbr] = br
                        q.append(nbr)

            sub_kw: Dict[str, float] = {}
            for nid in order:
                n_s = max(1, len(sources_by_ph.get(ph, {}).get(nid, {src_id})))
                sub_kw[nid] = node_ph_demand[ph].get(nid, 0.0) / float(n_s)

            for nid in reversed(order):
                p_id = parent[nid]
                if p_id is not None:
                    br = parent_br[nid]
                    sub_kw[p_id] += sub_kw[nid]
                    br_ph_flow[br.branch_id][ph] += sub_kw[nid]

            trees.append((ph, src_id, order, parent, parent_br))

    max_loading = 0.0
    max_vdrop = 0.0
    br_tot_flow: Dict[str, float] = {}
    for br_id, br in active_branches.items():
        tot = sum(br_ph_flow[br_id].values())
        br_tot_flow[br_id] = tot
        max_loading = max(max_loading, (tot / br.max_capacity_kw) * 100.0)

    tan_phi = math.tan(math.acos(0.92))
    for ph, src_id, order, parent, parent_br in trees:
        v_base = network.nodes[src_id].nominal_voltage_kv
        vdrop: Dict[str, float] = {src_id: 0.0}
        for nid in order:
            if nid == src_id:
                continue
            p_id = parent[nid]
            br = parent_br[nid]
            assert p_id is not None
            flow_kw = br_tot_flow.get(br.branch_id, 0.0)
            z_eff = br.r_ohm + br.x_ohm * tan_phi
            step_vd = ((flow_kw / 1000.0) * z_eff / (v_base ** 2)) * 100.0
            vdrop[nid] = vdrop[p_id] + step_vd
            max_vdrop = max(max_vdrop, vdrop[nid])

    if max_loading > 100.0:
        return (
            False,
            max_loading,
            max_vdrop,
            f"Sobrecarga térmica por fase na transferência ({max_loading:.1f}% > 100%).",
        )
    if max_vdrop > zone.max_transfer_voltage_drop_pct:
        return (
            False,
            max_loading,
            max_vdrop,
            f"Queda de tensão excessiva na transferência ({max_vdrop:.2f}% > {zone.max_transfer_voltage_drop_pct:.2f}%).",
        )
    return True, max_loading, max_vdrop, "OK"


def _is_asset_conservatively_deenergized(
    network: GridNetwork,
    asset_id: str,
    energized_phases: Dict[str, Set[str]],
) -> bool:
    """Verifica de forma conservadora se um ativo nodal ou de trecho está 100% desenergizado."""
    if asset_id not in network.assets:
        return False
    asset = network.assets[asset_id]
    if not asset.in_service or not asset.phases:
        return False
    if asset.node_id is None and asset.branch_id is None:
        return False
    if asset.node_id is not None:
        if asset.node_id not in network.nodes:
            return False
        if energized_phases.get(asset.node_id, set()):
            return False
    if asset.branch_id is not None:
        if asset.branch_id not in network.branches:
            return False
        br = network.branches[asset.branch_id]
        # Regra conservadora: qualquer extremidade energizada mantém o trecho em risco
        if energized_phases.get(br.from_node, set()) or energized_phases.get(
            br.to_node, set()
        ):
            return False
    return True


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
    service_exec_times: Dict[str, Tuple[int, float, float]] = {}

    for win in plan.windows:
        if not win.service_ids:
            violations.append(f"Janela '{win.window_id}' vazia (sem serviços).")
        for sid in win.service_ids:
            scheduled_service_occurrences[sid] = (
                scheduled_service_occurrences.get(sid, 0) + 1
            )

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
    exported_consumer_intervals: Dict[str, List[Tuple[float, float]]] = {}

    all_crew_intervals_by_day: Dict[int, Dict[str, List[CrewScheduleInterval]]] = {}
    windows_by_day: Dict[int, List[PlannedInterruptionWindow]] = {}
    window_deenergized_assets: Dict[str, Set[str]] = {}

    for win in plan.windows:
        windows_by_day.setdefault(win.day, []).append(win)

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

        # Validação de permissão operacional de todas as chaves da zona e da janela:
        sw_errors = validate_switch_maneuverability(
            network,
            set(zone.open_switches),
            set(zone.tie_switches_closed),
        )
        if sw_errors:
            for err in sw_errors:
                violations.append(
                    f"Violação de permissão de manobra na janela '{win.window_id}' (zona '{win.zone_id}'): {err}"
                )

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

        if not win.maneuver_steps:
            violations.append(
                f"Violação de integridade na janela '{win.window_id}': `maneuver_steps` "
                "ausente ou vazio no plano exportado."
            )
        elif len(win.maneuver_steps) != len(recalc_steps):
            violations.append(
                f"Violação de integridade na janela '{win.window_id}': quantidade de etapas "
                f"exportadas ({len(win.maneuver_steps)}) diverge do recálculo ({len(recalc_steps)})."
            )
        else:
            for exp_st, rec_st in zip(win.maneuver_steps, recalc_steps):
                if (
                    exp_st.stage != rec_st.stage
                    or abs(exp_st.start_hour - rec_st.start_hour) > 1e-3
                    or abs(exp_st.end_hour - rec_st.end_hour) > 1e-3
                    or set(exp_st.open_switches) != set(rec_st.open_switches)
                    or set(exp_st.closed_switches) != set(rec_st.closed_switches)
                    or set(exp_st.interrupted_consumers)
                    != set(rec_st.interrupted_consumers)
                ):
                    violations.append(
                        f"Violação de integridade na etapa {exp_st.step_order} ({exp_st.stage.value}) "
                        f"da janela '{win.window_id}': dados exportados divergem do recálculo."
                    )

        if win.physical_maneuvers_count != recalc_phys:
            violations.append(
                f"Violação de integridade na janela '{win.window_id}': manobras físicas "
                f"exportadas ({win.physical_maneuvers_count}) divergem do recálculo ({recalc_phys})."
            )

        recalc_win_interrupted: Set[str] = set()
        day_offset = float(win.day) * 24.0
        for step in recalc_steps:
            recalc_win_interrupted.update(step.interrupted_consumers)
            for cid in step.interrupted_consumers:
                recalc_consumer_intervals.setdefault(cid, []).append(
                    (day_offset + step.start_hour, day_offset + step.end_hour)
                )

        for step in win.maneuver_steps:
            for cid in step.interrupted_consumers:
                exported_consumer_intervals.setdefault(cid, []).append(
                    (day_offset + step.start_hour, day_offset + step.end_hour)
                )

        if set(win.interrupted_consumers) != recalc_win_interrupted:
            violations.append(
                f"Violação de integridade na janela '{win.window_id}': lista de consumidores "
                f"interrompidos exportada ({len(win.interrupted_consumers)}) diverge do "
                f"recálculo ({len(recalc_win_interrupted)})."
            )

        exec_steps = [s for s in recalc_steps if s.stage == ManeuverStage.EXECUTION]
        if not exec_steps:
            violations.append(
                f"Janela '{win.window_id}' sem etapa de execução elétrica recalculada."
            )
            continue
        exec_step = exec_steps[0]

        indep_energized, indep_sources_ph, indep_active_branches = (
            _independent_energized_phases_and_sources(
                network=network,
                open_switches=set(zone.open_switches),
                closed_switches=set(zone.tie_switches_closed),
            )
        )
        indep_tr_ok, indep_load_pct, indep_vdrop_pct, indep_tr_msg = (
            _independent_phase_transfer_check(
                network=network,
                zone=zone,
                open_switches=set(zone.open_switches),
                closed_switches=set(zone.tie_switches_closed),
                energized_phases=indep_energized,
                sources_by_ph=indep_sources_ph,
                active_branches=indep_active_branches,
            )
        )
        if (
            zone.requires_transfer_validation or zone.tie_switches_closed
        ) and (not exec_step.transfer_feasible or not indep_tr_ok):
            violations.append(
                f"Janela '{win.window_id}' utiliza transferência eletricamente inviável "
                f"(carregamento={indep_load_pct:.1f}%, queda={indep_vdrop_pct:.2f}%): "
                f"{indep_tr_msg} | {exec_step.notes}"
            )

        deenergized_assets = set(exec_step.deenergized_assets)
        window_deenergized_assets[win.window_id] = deenergized_assets

        win_svc_exec_bounds: Dict[str, Tuple[float, float]] = {}

        for sid in win.service_ids:
            if sid not in svc_map:
                continue
            svc = svc_map[sid]

            if not svc.target_assets:
                violations.append(
                    f"Violação cadastral: serviço '{sid}' na janela '{win.window_id}' "
                    "não possui ativos-alvo definidos (target_assets vazio)."
                )

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
                elif svc.requires_deenergized:
                    is_safe_indep = _is_asset_conservatively_deenergized(
                        network, asset_id, indep_energized
                    )
                    if asset_id not in deenergized_assets or not is_safe_indep:
                        violations.append(
                            f"Violação de isolamento elétrico: ativo '{asset_id}' do serviço "
                            f"'{sid}' permanece energizado (ou com extremidade de trecho energizada) "
                            f"na zona '{win.zone_id}'."
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

            # Verificação de simultaneidade real quando o serviço exige 2+ equipes atuando juntas:
            simultaneous_start = max(iv.exec_start_hour for iv in svc_intervals)
            simultaneous_end = min(iv.exec_end_hour for iv in svc_intervals)
            simultaneous_overlap = simultaneous_end - simultaneous_start
            if simultaneous_overlap < svc.duration_hours - 1e-4:
                violations.append(
                    f"Violação de simultaneidade de equipes no serviço '{sid}': "
                    f"interseção temporal entre as {len(svc_intervals)} equipes alocadas "
                    f"({max(0.0, simultaneous_overlap):.2f}h) é inferior à duração exigida "
                    f"({svc.duration_hours:.2f}h)."
                )

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
                if (
                    abs(iv.x - svc.x) > 1e-4
                    or abs(iv.y - svc.y) > 1e-4
                ):
                    violations.append(
                        f"Violação de coordenadas canônicas no serviço '{sid}' (equipe '{iv.crew_id}'): "
                        f"intervalo exportou destino ({iv.x:.4f}, {iv.y:.4f}) divergente da "
                        f"coordenada canônica do serviço ({svc.x:.4f}, {svc.y:.4f})."
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
                if not _crew_has_all_skills(crew, svc.required_skills):
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
                if (
                    not svc_a.allow_simultaneous_in_zone
                    or not svc_b.allow_simultaneous_in_zone
                ):
                    overlap = min(a_end, b_end) - max(a_start, b_start)
                    if overlap > 1e-4:
                        violations.append(
                            f"Violação de simultaneidade na janela '{win.window_id}': "
                            f"serviços '{sid_a}' e '{sid_b}' sobrepostos em {overlap:.2f}h."
                        )

    # Validação da sequência conjunta de janelas e estados de chaves no mesmo dia:
    for day, day_wins in windows_by_day.items():
        n_w = len(day_wins)
        for i in range(n_w):
            wa = day_wins[i]
            if wa.zone_id not in network.isolation_zones:
                continue
            za = network.isolation_zones[wa.zone_id]
            sw_a = set(za.open_switches) | set(za.tie_switches_closed)
            assets_a = window_deenergized_assets.get(wa.window_id, set())
            for j in range(i + 1, n_w):
                wb = day_wins[j]
                if wb.zone_id not in network.isolation_zones:
                    continue
                zb = network.isolation_zones[wb.zone_id]
                sw_b = set(zb.open_switches) | set(zb.tie_switches_closed)
                assets_b = window_deenergized_assets.get(wb.window_id, set())
                win_overlap = min(wa.end_hour, wb.end_hour) - max(
                    wa.start_hour, wb.start_hour
                )
                if win_overlap > 1e-4 and (
                    wa.zone_id == wb.zone_id
                    or (sw_a & sw_b)
                    or (assets_a & assets_b)
                ):
                    violations.append(
                        f"Conflito entre janelas sobrepostas no dia {day}: janelas "
                        f"'{wa.window_id}' ({wa.start_hour:.2f}h-{wa.end_hour:.2f}h, zona '{wa.zone_id}') e "
                        f"'{wb.window_id}' ({wb.start_hour:.2f}h-{wb.end_hour:.2f}h, zona '{wb.zone_id}') "
                        "compartilham zona, chaves ou ativos desenergizados com sobreposição de manobra/recomposição."
                    )

    # Validação de deslocamento usando EXCLUSIVAMENTE coordenadas canônicas (svc.x, svc.y):
    for day, crew_dict in all_crew_intervals_by_day.items():
        for crew_id, iv_list in crew_dict.items():
            crew = catalog.crews[crew_id]
            iv_list.sort(key=lambda x: (x.travel_start_hour, x.exec_start_hour))
            for idx, iv in enumerate(iv_list):
                curr_svc = svc_map.get(iv.service_id)
                to_x = curr_svc.x if curr_svc is not None else iv.x
                to_y = curr_svc.y if curr_svc is not None else iv.y

                if idx == 0:
                    from_x, from_y = crew.base_x, crew.base_y
                else:
                    prev_iv = iv_list[idx - 1]
                    prev_svc = svc_map.get(prev_iv.service_id)
                    from_x = prev_svc.x if prev_svc is not None else prev_iv.x
                    from_y = prev_svc.y if prev_svc is not None else prev_iv.y
                    if iv.travel_start_hour < prev_iv.recomp_end_hour - 1e-4:
                        violations.append(
                            f"Sobreposição de agenda da equipe '{crew_id}' no dia {day}: "
                            f"serviço '{iv.service_id}' inicia às {iv.travel_start_hour:.2f}h "
                            f"antes do término de '{prev_iv.service_id}' às {prev_iv.recomp_end_hour:.2f}h."
                        )
                expected_travel = _canonical_travel_hours(
                    from_x, from_y, to_x, to_y, crew.speed_kmh
                )
                actual_travel_window = iv.prep_start_hour - iv.travel_start_hour
                if (
                    actual_travel_window < expected_travel - 1e-3
                    or abs(iv.travel_hours - expected_travel) > 1e-3
                ):
                    violations.append(
                        f"Tempo de deslocamento canônico violado para a equipe '{crew_id}' "
                        f"no serviço '{iv.service_id}' (alocado {actual_travel_window:.4f}h / "
                        f"declarado {iv.travel_hours:.4f}h vs. canônico {expected_travel:.4f}h)."
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
    recalc_repeat_consumers = 0
    for ivs in recalc_consumer_intervals.values():
        merged = merge_intervals(ivs)
        recalc_consumer_hours += sum(e - s for s, e in merged)
        if len(merged) >= 2:
            recalc_repeat_consumers += 1

    exported_consumer_hours = 0.0
    for ivs in exported_consumer_intervals.values():
        merged = merge_intervals(ivs)
        exported_consumer_hours += sum(e - s for s, e in merged)

    if abs(exported_consumer_hours - recalc_consumer_hours) > 1e-3:
        violations.append(
            f"Violação de integridade de impacto: clientes-hora exportados nas etapas "
            f"({exported_consumer_hours:.4f}) divergem do recálculo do verificador "
            f"({recalc_consumer_hours:.4f})."
        )

    isolation_ok = not any(
        "Violação de isolamento" in v
        or "Violação de integridade" in v
        or "Violação de permissão de manobra" in v
        or "transferência eletricamente inviável" in v
        for v in violations
    )
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
        recalculated_repeat_consumers=recalc_repeat_consumers,
        recalculated_completed_services=tuple(
            scheduled_service_occurrences.keys()
        ),
        isolation_verified=isolation_ok,
    )
