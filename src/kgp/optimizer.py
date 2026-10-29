"""Otimizador combinatório MILP (KGP) via SciPy/HiGHS para programação de desligamentos.

Seleciona conjuntamente pacotes de serviços, zonas de isolamento, datas, sequências
diárias de janelas e alocação de equipes sob as mesmas restrições operacionais dos
comparadores (B0, B1, B2), em um catálogo discreto limitado que inclui suas sementes.

Função objetivo documentada (minimização ponderada):
  J = w_win * N_janelas
    + w_ch  * Clientes_Hora
    + w_rep * Consumidores_Reincidentes
    + w_man * Manobras_Fisicas
    + w_hh  * (Horas_Trabalho_Equipe + Horas_Deslocamento_Equipe)
    + w_opt * N_ordens_opcionais_omitidas

Carteira obrigatória e precedências são restrições rígidas, sem troca por custo.

Cada coluna candidata no MILP representa uma programação diária completa e factível
(1 ou mais janelas encadeadas no dia `d` com o deslocamento real entre serviços e a
união exata de clientes-hora no dia), garantindo igualdade exata entre o custo
avaliado pelo solver e o custo do plano final exportado.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from itertools import combinations
import json
import math
import time
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .baselines import (
    _get_conflicting_zones_map,
    _rank_zones_by_impact,
    run_baseline_b0_individual,
    run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy,
)
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
from .metrics import compute_consumer_interruption_unions, merge_intervals
from .scheduling import build_window_schedule
from .planning_policy import unsupported_input_plan


@dataclass(frozen=True)
class ObjectiveWeights:
    """Pesos explícitos e documentados da função objetivo do KGP."""

    window_weight: float = 90.0
    consumer_hour_weight: float = 1.0
    repeat_consumer_weight: float = 12.0
    physical_maneuver_weight: float = 2.5
    crew_hour_weight: float = 0.6
    # Compatibilidade da API 0.2.0; não é usada como relaxação de carteira obrigatória.
    unscheduled_penalty: float = 1_000_000.0
    optional_unscheduled_penalty: float = 50.0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"Peso '{item.name}' deve ser finito e não negativo.")


@dataclass
class _DayScheduleColumn:
    """Coluna candidata representando uma sequência diária completa de janelas no dia `day`."""

    col_id: int
    day: int
    windows: Tuple[PlannedInterruptionWindow, ...]
    service_ids: Tuple[str, ...]
    materials_used: Dict[str, float]
    consumer_hours: float
    physical_maneuvers: int
    crew_hours: float
    cost: float
    group_interruptions_count: Dict[Tuple[str, str], int]


def _evaluate_day_windows_metrics(
    windows: Sequence[PlannedInterruptionWindow],
    consumer_groups: Dict[Tuple[str, str], List[str]],
    weights: ObjectiveWeights,
) -> Tuple[float, int, float, float, Dict[Tuple[str, str], int]]:
    """Calcula clientes-hora (união no dia), manobras, horas de equipe, custo e interrupções por grupo."""
    raw_by_cons: Dict[str, List[Tuple[float, float]]] = {}
    for win in windows:
        for st in win.maneuver_steps:
            if st.end_hour <= st.start_hour:
                continue
            for cid in st.interrupted_consumers:
                raw_by_cons.setdefault(cid, []).append(
                    (st.start_hour, st.end_hour)
                )

    merged_by_cons: Dict[str, List[Tuple[float, float]]] = {
        cid: merge_intervals(ivs) for cid, ivs in raw_by_cons.items()
    }
    ch_total = sum(
        (e - s) for ivs in merged_by_cons.values() for s, e in ivs
    )
    phys_man = sum(w.physical_maneuvers_count for w in windows)
    crew_h = sum(
        (iv.recomp_end_hour - iv.prep_start_hour) + iv.travel_hours
        for w in windows
        for iv in w.crew_intervals
    )
    group_int_count: Dict[Tuple[str, str], int] = {}
    for g_key, cids in consumer_groups.items():
        rep_cid = cids[0]
        n_int = len(merged_by_cons.get(rep_cid, []))
        if n_int > 0:
            group_int_count[g_key] = n_int

    col_cost = (
        weights.window_weight * len(windows)
        + weights.consumer_hour_weight * ch_total
        + weights.physical_maneuver_weight * phys_man
        + weights.crew_hour_weight * crew_h
    )
    return (
        round(ch_total, 6),
        phys_man,
        round(crew_h, 6),
        round(col_cost, 6),
        group_int_count,
    )


def _subtract_materials(
    stock: Dict[str, float],
    used: Dict[str, float],
) -> Dict[str, float]:
    out = dict(stock)
    for k, v in used.items():
        out[k] = round(out.get(k, 0.0) - v, 4)
    return out


def _generate_day_schedule_columns(
    network: GridNetwork,
    known_services: Sequence[ServiceOrder],
    catalog: ResourceCatalog,
    consumer_groups: Dict[Tuple[str, str], List[str]],
    weights: ObjectiveWeights,
    seed_plans: Sequence[ExecutionPlan] = (),
    max_package_size: int = 5,
    max_windows_per_day: int = 5,
    max_states_per_depth: int = 120,
    decision_day: int = 0,
) -> List[_DayScheduleColumn]:
    """Gera colunas diárias completas (1 a N janelas encadeadas por dia com deslocamento exato)."""
    svc_map: Dict[str, ServiceOrder] = {s.service_id: s for s in known_services}
    zone_conflicts = _get_conflicting_zones_map(network)
    all_day_columns: List[_DayScheduleColumn] = []

    for day in catalog.planning_days:
        if day < decision_day:
            continue
        day_svcs = [
            s
            for s in known_services
            if s.ready_at_day <= day <= s.deadline_day
        ]
        if not day_svcs:
            continue

        day_crews = [
            c for c in catalog.crews.values() if day in c.available_days
        ]
        if not day_crews:
            continue

        crew_subcatalogs: List[ResourceCatalog] = [catalog]
        if len(day_crews) >= 2:
            for c in day_crews:
                crew_subcatalogs.append(
                    ResourceCatalog(
                        crews={c.crew_id: c},
                        materials_inventory=catalog.materials_inventory,
                        planning_days=catalog.planning_days,
                        window_start_hour=catalog.window_start_hour,
                        window_end_hour=catalog.window_end_hour,
                        max_window_duration_hours=catalog.max_window_duration_hours,
                    )
                )

        # Blocos candidatos (zone_id, pacote_servicos) para o dia:
        atomic_blocks: List[Tuple[str, Tuple[ServiceOrder, ...]]] = []
        seen_blocks: Set[Tuple[str, Tuple[str, ...]]] = set()

        # Para cada serviço individual, inclui suas melhores zonas por menor impacto:
        for s in day_svcs:
            ranked_z = _rank_zones_by_impact(
                network, find_eligible_zones_for_service(network, s)
            )
            for zid in ranked_z[:3]:
                b_key = (zid, (s.service_id,))
                if b_key not in seen_blocks:
                    seen_blocks.add(b_key)
                    atomic_blocks.append((zid, (s,)))

        # Para cada zona, inclui pacotes de 1 a max_package_size serviços elegíveis:
        for zid in sorted(network.isolation_zones.keys()):
            elig_in_z = [
                s
                for s in day_svcs
                if check_service_zone_eligibility(network, s, zid)[0]
            ]
            if not elig_in_z:
                continue
            max_r = min(len(elig_in_z), max_package_size)
            for r in range(1, max_r + 1):
                for comb in combinations(elig_in_z, r):
                    sids = tuple(sorted(x.service_id for x in comb))
                    b_key = (zid, sids)
                    if b_key not in seen_blocks:
                        seen_blocks.add(b_key)
                        atomic_blocks.append((zid, comb))

        # Expansão em largura de sequências de janelas no dia `day`:
        # Cada estado é: (windows_tuple, covered_ids_set, service_exec_end_map, rem_materials)
        StateType = Tuple[
            Tuple[PlannedInterruptionWindow, ...],
            FrozenSet[str],
            Dict[str, float],
            Dict[str, float],
        ]
        empty_state: StateType = (
            (),
            frozenset(),
            {},
            dict(catalog.materials_inventory),
        )
        current_layer: List[StateType] = [empty_state]
        collected_states: List[Tuple[PlannedInterruptionWindow, ...]] = []

        for depth in range(1, max_windows_per_day + 1):
            next_layer_by_sig: Dict[
                Tuple[FrozenSet[str], Tuple[Tuple[str, str], ...]],
                Tuple[float, StateType],
            ] = {}

            for wins, cov_set, svc_end_map, rem_mat in current_layer:
                day_ivs: List[CrewScheduleInterval] = [
                    iv for w in wins for iv in w.crew_intervals
                ]

                for zid, pack in atomic_blocks:
                    pack_ids = {s.service_id for s in pack}
                    if pack_ids & cov_set:
                        continue

                    # Verifica se alguma precedência interna ao dia foi violada:
                    earliest_start = catalog.window_start_hour
                    pred_feasible = True
                    z_spec = network.isolation_zones[zid]
                    z_switches = set(z_spec.open_switches) | set(
                        z_spec.tie_switches_closed
                    )

                    # Não sobrepor com janelas anteriores na mesma zona ou que compartilhem chaves ou ativos desenergizados:
                    for prev_w in wins:
                        if prev_w.zone_id in zone_conflicts.get(zid, {zid}):
                            earliest_start = max(
                                earliest_start, prev_w.end_hour + 0.05
                            )

                    for s in pack:
                        for p_id in s.precedence_service_ids:
                            if p_id in pack_ids:
                                continue
                            if p_id in cov_set:
                                earliest_start = max(
                                    earliest_start,
                                    svc_end_map[p_id]
                                    - z_spec.prep_duration_hours,
                                )

                    sub_cats_to_try = (
                        crew_subcatalogs if depth == 1 else [catalog]
                    )
                    for sub_cat in sub_cats_to_try:
                        win_id = f"CAND_D{day}_W{depth}"
                        new_win, _ = build_window_schedule(
                            network=network,
                            catalog=sub_cat,
                            zone_id=zid,
                            day=day,
                            window_id=win_id,
                            services=pack,
                            existing_day_intervals=day_ivs,
                            remaining_materials=rem_mat,
                            earliest_window_start_hour=earliest_start,
                        )
                        if new_win is None:
                            continue

                        # Garante ausência de sobreposição de ativos desenergizados com janelas anteriores do dia:
                        new_deenerg = set(
                            new_win.maneuver_steps[0].deenergized_assets
                        )
                        for st in new_win.maneuver_steps:
                            new_deenerg.update(st.deenergized_assets)

                        conflict_with_prev = False
                        for prev_w in wins:
                            ovl = min(prev_w.end_hour, new_win.end_hour) - max(
                                prev_w.start_hour, new_win.start_hour
                            )
                            if ovl > 1e-4:
                                prev_deenerg: Set[str] = set()
                                for pst in prev_w.maneuver_steps:
                                    prev_deenerg.update(pst.deenergized_assets)
                                if prev_deenerg & new_deenerg:
                                    conflict_with_prev = True
                                    break
                        if conflict_with_prev:
                            continue

                        new_wins = wins + (new_win,)
                        new_cov = cov_set | frozenset(new_win.service_ids)
                        new_end_map = dict(svc_end_map)
                        for sid in new_win.service_ids:
                            s_end = max(
                                iv.exec_end_hour
                                for iv in new_win.crew_intervals
                                if iv.service_id == sid
                            )
                            new_end_map[sid] = s_end
                        new_rem_mat = _subtract_materials(
                            rem_mat, new_win.materials_used
                        )

                        _, _, _, state_cost, _ = _evaluate_day_windows_metrics(
                            new_wins, consumer_groups, weights
                        )
                        last_crew_pos = tuple(
                            sorted(
                                (
                                    iv.crew_id,
                                    f"{iv.x:.2f},{iv.y:.2f},{iv.recomp_end_hour:.2f}",
                                )
                                for iv in new_win.crew_intervals
                            )
                        )
                        state_sig = (
                            new_cov,
                            tuple(w.zone_id for w in new_wins),
                            last_crew_pos,
                        )
                        new_state: StateType = (
                            new_wins,
                            new_cov,
                            new_end_map,
                            new_rem_mat,
                        )
                        prev_entry = next_layer_by_sig.get(state_sig)
                        if prev_entry is None or state_cost < prev_entry[0]:
                            next_layer_by_sig[state_sig] = (
                                state_cost,
                                new_state,
                            )

            if not next_layer_by_sig:
                break

            sorted_next = sorted(
                next_layer_by_sig.values(), key=lambda item: item[0]
            )
            for _, st in sorted_next:
                collected_states.append(st[0])

            current_layer = [
                st for _, st in sorted_next[:max_states_per_depth]
            ]

        # Injeta também as sequências diárias produzidas pelos baselines (B0, B1, B2):
        for s_plan in seed_plans:
            day_seed_wins = tuple(
                w for w in s_plan.windows if w.day == day
            )
            if day_seed_wins:
                collected_states.append(day_seed_wins)

        # Consolida e deduplica as colunas do dia `day`:
        best_by_col_sig: Dict[
            Tuple[
                Tuple[str, ...],
                Tuple[Tuple[Tuple[str, str], int], ...],
                Tuple[Tuple[str, str], ...],
            ],
            _DayScheduleColumn,
        ] = {}

        for wins_tuple in collected_states:
            covered_ids_list: List[str] = []
            for w in wins_tuple:
                for sid in w.service_ids:
                    if sid not in covered_ids_list:
                        covered_ids_list.append(sid)
            covered_sorted = tuple(sorted(covered_ids_list))

            # Verifica se todas as precedências cujo predecessor E sucessor estão no mesmo dia são respeitadas:
            exec_bounds_day: Dict[str, Tuple[float, float]] = {}
            for w in wins_tuple:
                for sid in w.service_ids:
                    s_ivs = [
                        iv for iv in w.crew_intervals if iv.service_id == sid
                    ]
                    exec_bounds_day[sid] = (
                        min(iv.exec_start_hour for iv in s_ivs),
                        max(iv.exec_end_hour for iv in s_ivs),
                    )
            intra_pred_ok = True
            for sid in covered_sorted:
                svc = svc_map[sid]
                for p_id in svc.precedence_service_ids:
                    if p_id in exec_bounds_day:
                        if (
                            exec_bounds_day[p_id][1]
                            > exec_bounds_day[sid][0] + 1e-4
                        ):
                            intra_pred_ok = False
                            break
                if not intra_pred_ok:
                    break
            if not intra_pred_ok:
                continue

            mats_used: Dict[str, float] = {}
            for w in wins_tuple:
                for mk, mv in w.materials_used.items():
                    mats_used[mk] = round(mats_used.get(mk, 0.0) + mv, 4)

            ch, phys, wh, cost, grp_ints = _evaluate_day_windows_metrics(
                wins_tuple, consumer_groups, weights
            )
            grp_sig = tuple(sorted(grp_ints.items()))
            win_zones_sig = tuple(
                (w.zone_id, w.service_ids) for w in wins_tuple
            )
            col_sig = (covered_sorted, grp_sig, win_zones_sig)

            cand_col = _DayScheduleColumn(
                col_id=-1,
                day=day,
                windows=wins_tuple,
                service_ids=covered_sorted,
                materials_used=mats_used,
                consumer_hours=ch,
                physical_maneuvers=phys,
                crew_hours=wh,
                cost=cost,
                group_interruptions_count=grp_ints,
            )
            existing = best_by_col_sig.get(col_sig)
            if existing is None or cand_col.cost < existing.cost:
                best_by_col_sig[col_sig] = cand_col

        for col in best_by_col_sig.values():
            col.col_id = len(all_day_columns)
            all_day_columns.append(col)

    return all_day_columns


def compute_exact_plan_objective(
    plan: ExecutionPlan,
    weights: ObjectiveWeights,
    services: Optional[Iterable[ServiceOrder]] = None,
) -> float:
    """Recalcula J; omissões opcionais exigem a carteira canônica ``services``.

    A carteira obrigatória é restrição rígida. Um plano que a omita não possui
    objetivo factível; não recebe pontuação artificialmente favorável nem uma
    penalidade finita que possa ser trocada por custo operacional.
    """
    if services is None:
        if plan.unscheduled_service_ids:
            raise ValueError("services é obrigatório para classificar omissões do plano.")
        omitted_optional_count = 0
    else:
        known, _ = filter_known_services(list(services), plan.decision_day)
        completed = {sid for win in plan.windows for sid in win.service_ids}
        omitted = [svc for svc in known if svc.service_id not in completed]
        if any(svc.mandatory for svc in omitted):
            raise ValueError("Carteira mandatory incompleta: objetivo factível indefinido.")
        omitted_optional_count = len(omitted)
    merged_by_cons = compute_consumer_interruption_unions(plan)
    ch_total = sum(
        (end - start)
        for ivs in merged_by_cons.values()
        for start, end in ivs
    )
    repeat_count = sum(1 for ivs in merged_by_cons.values() if len(ivs) >= 2)
    phys_man = sum(w.physical_maneuvers_count for w in plan.windows)
    crew_h = sum(
        (iv.recomp_end_hour - iv.prep_start_hour) + iv.travel_hours
        for w in plan.windows
        for iv in w.crew_intervals
    )
    obj = (
        weights.window_weight * len(plan.windows)
        + weights.consumer_hour_weight * ch_total
        + weights.repeat_consumer_weight * repeat_count
        + weights.physical_maneuver_weight * phys_man
        + weights.crew_hour_weight * crew_h
        + weights.optional_unscheduled_penalty * omitted_optional_count
    )
    return round(obj, 4)


def _audit_plan_export(
    network: GridNetwork,
    services: Sequence[ServiceOrder],
    catalog: ResourceCatalog,
    plan: ExecutionPlan,
) -> Tuple[ExecutionPlan, bool]:
    """Audita o objeto e sua exportação JSON antes de publicar qualquer certificado."""
    from .contracts import plan_from_json
    from .feasibility import verify_plan_feasibility

    raw_report = verify_plan_feasibility(network, services, catalog, plan)
    raw_violations = list(raw_report.violations)
    exported_report = None
    export_error = None
    try:
        exported = plan_from_json(json.dumps(asdict(plan), ensure_ascii=False, allow_nan=False))
        exported_report = verify_plan_feasibility(network, services, catalog, exported)
        plan = exported
    except (TypeError, ValueError, OverflowError) as exc:
        export_error = f"Exportação JSON rejeitada: {exc}"

    violations = raw_violations + (
        list(exported_report.violations) if exported_report is not None else [export_error]
    )
    audit = {
        "raw_is_feasible": raw_report.is_feasible,
        "json_roundtrip_succeeded": exported_report is not None,
        "exported_is_feasible": exported_report.is_feasible if exported_report is not None else False,
        "violations": list(dict.fromkeys(violations)),
    }
    plan.metadata = dict(plan.metadata)
    plan.metadata["independent_verification"] = audit
    return plan, bool(raw_report.is_feasible and exported_report is not None and exported_report.is_feasible)


def _reject_plan_certificate(plan: ExecutionPlan) -> ExecutionPlan:
    """Um candidato rejeitado não tem objetivo factível nem certificado publicável."""
    plan.solver_status = "INVALID_PLAN_REJECTED"
    plan.solver_objective = None
    plan.solver_bound = None
    plan.solver_gap = None
    plan.is_optimal_proven = False
    plan.metadata["optimality_scope"] = (
        "Plano rejeitado pelo verificador independente ou pela exportação JSON; "
        "sem objetivo factível, bound, gap ou prova de ótimo publicáveis."
    )
    return plan


def _verified_seed_fallback(
    network: GridNetwork,
    services: Sequence[ServiceOrder],
    catalog: ResourceCatalog,
    seeds: Sequence[ExecutionPlan],
    weights: ObjectiveWeights,
    plan_id: str,
) -> Optional[ExecutionPlan]:
    """Escolhe a melhor semente que passa pela mesma auditoria da saída MILP."""
    feasible_seeds = []
    for seed in seeds:
        candidate = replace(
            seed, plan_id=plan_id, method_name="KGP", solver_status="PENDING_VERIFICATION",
            solver_objective=None, solver_bound=None, solver_gap=None,
            is_optimal_proven=False, metadata=dict(seed.metadata),
        )
        candidate, feasible = _audit_plan_export(network, services, catalog, candidate)
        if feasible:
            candidate.solver_objective = compute_exact_plan_objective(candidate, weights, services)
            feasible_seeds.append(candidate)
    return min(feasible_seeds, key=lambda p: p.solver_objective) if feasible_seeds else None


def run_kgp_optimizer(
    network: GridNetwork,
    services: Iterable[ServiceOrder],
    catalog: ResourceCatalog,
    scenario_id: str,
    decision_day: int = 0,
    weights: Optional[ObjectiveWeights] = None,
    time_limit_sec: float = 15.0,
    b1_radius_km: float = 1.25,
) -> ExecutionPlan:
    """Executa o otimizador combinatório KGP (MILP via SciPy/HiGHS sobre colunas diárias)."""
    t0 = time.perf_counter()
    w = weights if weights is not None else ObjectiveWeights()

    all_services = list(services)
    unsupported = unsupported_input_plan(network, all_services, scenario_id, decision_day, "KGP")
    if unsupported is not None:
        return unsupported
    known_services, excluded = filter_known_services(all_services, decision_day)
    lc_alerts = detect_lifecycle_conflicts(known_services)

    if not known_services:
        empty_plan = ExecutionPlan(
            plan_id=f"PLAN_KGP_{scenario_id}",
            method_name="KGP",
            scenario_id=scenario_id,
            decision_day=decision_day,
            windows=[],
            completed_service_ids=(),
            unscheduled_service_ids=(),
            exclusion_justifications=excluded,
            lifecycle_alerts=lc_alerts,
            solver_status="PENDING_VERIFICATION",
            solver_time_sec=round(time.perf_counter() - t0, 6),
        )
        empty_plan, feasible = _audit_plan_export(network, all_services, catalog, empty_plan)
        empty_plan.solver_time_sec = round(time.perf_counter() - t0, 6)
        if not feasible:
            return _reject_plan_certificate(empty_plan)
        empty_plan.solver_status = "OPTIMAL_ON_DISCRETE_COLUMNS"
        empty_plan.solver_objective = empty_plan.solver_bound = empty_plan.solver_gap = 0.0
        empty_plan.is_optimal_proven = True
        empty_plan.solver_time_sec = round(time.perf_counter() - t0, 6)
        return empty_plan

    # Gera planos sementes de B0, B1 e B2 para garantir que o espaço de colunas do KGP
    # contenha todas as alternativas construtivas dos comparadores:
    seed_b0 = run_baseline_b0_individual(
        network, all_services, catalog, scenario_id, decision_day
    )
    seed_b1 = run_baseline_b1_geographic(
        network, all_services, catalog, scenario_id, decision_day, radius_km=b1_radius_km
    )
    seed_b2 = run_baseline_b2_topological_greedy(
        network, all_services, catalog, scenario_id, decision_day
    )

    consumer_groups: Dict[Tuple[str, str], List[str]] = {}
    for cid, cons in network.consumers.items():
        consumer_groups.setdefault((cons.node_id, cons.phase), []).append(cid)
    group_keys = sorted(consumer_groups.keys())
    num_groups = len(group_keys)

    columns = _generate_day_schedule_columns(
        network=network,
        known_services=known_services,
        catalog=catalog,
        consumer_groups=consumer_groups,
        weights=w,
        seed_plans=(seed_b0, seed_b1, seed_b2),
        decision_day=decision_day,
    )

    num_cols = len(columns)
    num_svcs = len(known_services)
    svc_index: Dict[str, int] = {
        s.service_id: idx for idx, s in enumerate(known_services)
    }

    total_vars = num_cols + num_svcs + num_groups
    c_vec = np.zeros(total_vars, dtype=float)
    integrality = np.ones(total_vars, dtype=int)
    lb = np.zeros(total_vars, dtype=float)
    ub = np.ones(total_vars, dtype=float)

    for k, col in enumerate(columns):
        c_vec[k] = col.cost

    for i, svc in enumerate(known_services):
        var_idx = num_cols + i
        c_vec[var_idx] = 0.0 if svc.mandatory else w.optional_unscheduled_penalty
        # Não negociar carteira obrigatória por uma penalidade finita.
        if svc.mandatory:
            ub[var_idx] = 0.0

    # Indicador estritamente binário r_g in {0, 1} para reincidência (>= 2 interrupções):
    for g_idx, g_key in enumerate(group_keys):
        var_idx = num_cols + num_svcs + g_idx
        n_cons = len(consumer_groups[g_key])
        c_vec[var_idx] = w.repeat_consumer_weight * n_cons
        ub[var_idx] = 1.0

    rows: List[np.ndarray] = []
    lhs_list: List[float] = []
    rhs_list: List[float] = []

    # 1. Cobertura de cada serviço conhecido (exatamente 1 coluna diária OU variável de omissão):
    for i, svc in enumerate(known_services):
        row = np.zeros(total_vars, dtype=float)
        for k, col in enumerate(columns):
            if svc.service_id in col.service_ids:
                row[k] = 1.0
        row[num_cols + i] = 1.0
        rows.append(row)
        lhs_list.append(1.0)
        rhs_list.append(1.0)

    # 2. No máximo 1 coluna diária (agenda completa do dia) selecionada por dia:
    cols_by_day: Dict[int, List[_DayScheduleColumn]] = {}
    for col in columns:
        cols_by_day.setdefault(col.day, []).append(col)

    for day, day_cols in cols_by_day.items():
        row = np.zeros(total_vars, dtype=float)
        for col in day_cols:
            row[col.col_id] = 1.0
        rows.append(row)
        lhs_list.append(-np.inf)
        rhs_list.append(1.0)

    # 3. Precedências entre serviços (quando em dias distintos, day(pred) <= day(succ) - 1;
    #    quando no mesmo dia, a coluna diária já validou exec_end(pred) <= exec_start(succ)):
    big_m_day = float(max(catalog.planning_days) + 2)
    for s_idx, svc in enumerate(known_services):
        for pred_id in svc.precedence_service_ids:
            if pred_id not in svc_index:
                # Predecessor fora da carteira conhecida/ativa não pode sustentar
                # uma execução; obrigatórias tornam o catálogo inviável.
                row = np.zeros(total_vars, dtype=float)
                row[num_cols + s_idx] = 1.0
                rows.append(row)
                lhs_list.append(1.0)
                rhs_list.append(1.0)
                continue
            p_idx = svc_index[pred_id]
            # Cobertura do predecessor: omitir P implica omitir S, mesmo se P
            # for opcional. A desigualdade de datas sozinha relaxava esse caso.
            presence_row = np.zeros(total_vars, dtype=float)
            presence_row[num_cols + p_idx] = 1.0
            presence_row[num_cols + s_idx] = -1.0
            rows.append(presence_row)
            lhs_list.append(-np.inf)
            rhs_list.append(0.0)
            row = np.zeros(total_vars, dtype=float)
            for k, col in enumerate(columns):
                has_p = pred_id in col.service_ids
                has_s = svc.service_id in col.service_ids
                if has_p and has_s:
                    continue
                if has_p:
                    row[k] += float(col.day)
                if has_s:
                    row[k] -= float(col.day - 1)
            row[num_cols + p_idx] -= big_m_day
            row[num_cols + s_idx] -= big_m_day
            rows.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(1.0)

    # 4. Estoque global de materiais:
    for mat, avail_qty in catalog.materials_inventory.items():
        row = np.zeros(total_vars, dtype=float)
        has_usage = False
        for k, col in enumerate(columns):
            used = col.materials_used.get(mat, 0.0)
            if used > 0.0:
                row[k] = used
                has_usage = True
        if has_usage:
            rows.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(float(avail_qty))

    # 5. Reincidência exata (r_g = 1 se e somente se total de interrupções >= 2, sem cap artificial):
    for g_idx, g_key in enumerate(group_keys):
        row = np.zeros(total_vars, dtype=float)
        max_int_by_day: Dict[int, int] = {}
        for k, col in enumerate(columns):
            n_int = col.group_interruptions_count.get(g_key, 0)
            if n_int > 0:
                row[k] = float(n_int)
                max_int_by_day[col.day] = max(
                    max_int_by_day.get(col.day, 0), n_int
                )
        max_total_int = sum(max_int_by_day.values())
        if max_total_int >= 2:
            m_g = float(max_total_int - 1)
            row[num_cols + num_svcs + g_idx] = -m_g
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
    if res.x is None or res.status not in (0, 1):
        fallback = _verified_seed_fallback(
            network, all_services, catalog, (seed_b2, seed_b1, seed_b0), w,
            f"PLAN_KGP_{scenario_id}",
        )
        fallback_is_feasible = fallback is not None
        if fallback is None:
            fallback = replace(seed_b2, plan_id=f"PLAN_KGP_{scenario_id}", method_name="KGP")
            fallback, _ = _audit_plan_export(network, all_services, catalog, fallback)
            _reject_plan_certificate(fallback)
        fallback.solver_status = (
            "INFEASIBLE_ON_DISCRETE_COLUMNS" if res.status == 2
            else "INVALID_PLAN_REJECTED"
        )
        fallback.solver_time_sec = round(elapsed, 6)
        fallback.solver_objective = None
        fallback.solver_bound = None
        fallback.solver_gap = None
        fallback.is_optimal_proven = False
        if fallback_is_feasible:
            fallback.solver_objective = compute_exact_plan_objective(fallback, w, known_services)
            raw_bound = getattr(res, 'mip_dual_bound', None)
            if (res.status in (0, 1) and raw_bound is not None and math.isfinite(raw_bound)
                    and raw_bound <= fallback.solver_objective + 1e-4):
                fallback.solver_bound = round(float(raw_bound), 4)
                fallback.solver_gap = round(abs(fallback.solver_objective - fallback.solver_bound) /
                                            max(abs(fallback.solver_objective), 1e-10), 6)
            fallback.solver_status = ('FEASIBLE_FALLBACK_BOUNDED' if fallback.solver_bound is not None
                                      else 'FEASIBLE_FALLBACK_NO_BOUND')
        fallback.solver_time_sec = round(time.perf_counter() - t0, 6)
        fallback.metadata = {
            **fallback.metadata,
            "candidate_columns_count": num_cols,
            "solver_raw_status": int(res.status),
            "optimality_scope": (
                "Plano fallback factível verificado; limite conhecido nas colunas geradas quando disponível, sem prova de ótimo."
                if fallback_is_feasible else
                ("Inviabilidade restrita às colunas geradas; não prova inviabilidade operacional geral."
                 if res.status == 2 else "Solver interrompido/sem solução; fallback não factível, sem prova de inviabilidade.")
            ),
            "objective_weights": {
                "window_weight": w.window_weight, "consumer_hour_weight": w.consumer_hour_weight,
                "repeat_consumer_weight": w.repeat_consumer_weight, "physical_maneuver_weight": w.physical_maneuver_weight,
                "crew_hour_weight": w.crew_hour_weight, "optional_unscheduled_penalty": w.optional_unscheduled_penalty,
            },
            "fallback_verified_feasible": fallback_is_feasible,
            "missing_mandatory_service_ids": [
                s.service_id for s in known_services
                if s.mandatory and s.service_id not in fallback.completed_service_ids
            ],
            "omitted_optional_service_ids": [
                s.service_id for s in known_services
                if not s.mandatory and s.service_id not in fallback.completed_service_ids
            ],
        }
        return fallback

    x_sol = res.x
    chosen_cols = [columns[k] for k in range(num_cols) if x_sol[k] > 0.5]
    chosen_cols.sort(key=lambda col: col.day)

    final_windows: List[PlannedInterruptionWindow] = []
    completed_ids: List[str] = []

    for col in chosen_cols:
        for w_in in col.windows:
            new_win_id = f"KGP_{scenario_id}_W{len(final_windows) + 1:02d}"
            renamed_ivs = tuple(
                CrewScheduleInterval(
                    crew_id=iv.crew_id,
                    service_id=iv.service_id,
                    window_id=new_win_id,
                    day=iv.day,
                    travel_start_hour=iv.travel_start_hour,
                    prep_start_hour=iv.prep_start_hour,
                    exec_start_hour=iv.exec_start_hour,
                    exec_end_hour=iv.exec_end_hour,
                    recomp_end_hour=iv.recomp_end_hour,
                    travel_hours=iv.travel_hours,
                    x=iv.x,
                    y=iv.y,
                )
                for iv in w_in.crew_intervals
            )
            final_windows.append(
                PlannedInterruptionWindow(
                    window_id=new_win_id,
                    day=w_in.day,
                    zone_id=w_in.zone_id,
                    service_ids=w_in.service_ids,
                    start_hour=w_in.start_hour,
                    end_hour=w_in.end_hour,
                    work_start_hour=w_in.work_start_hour,
                    work_end_hour=w_in.work_end_hour,
                    maneuver_steps=w_in.maneuver_steps,
                    crew_intervals=renamed_ivs,
                    materials_used=dict(w_in.materials_used),
                    physical_maneuvers_count=w_in.physical_maneuvers_count,
                    interrupted_consumers=w_in.interrupted_consumers,
                    lifecycle_alerts=w_in.lifecycle_alerts,
                    inclusion_justifications=dict(
                        w_in.inclusion_justifications
                    ),
                )
            )
            for sid in w_in.service_ids:
                if sid not in completed_ids:
                    completed_ids.append(sid)

    unscheduled_ids: List[str] = []
    for svc in known_services:
        if svc.service_id not in completed_ids:
            unscheduled_ids.append(svc.service_id)
            excluded[svc.service_id] = (
                f"Serviço '{svc.service_id}' não pôde ser agendado sob as restrições ativas."
            )

    milp_obj_val = float(res.fun) if res.fun is not None else 0.0
    mip_gap = float(getattr(res, "mip_gap", 0.0) or 0.0)
    raw_bound = getattr(res, "mip_dual_bound", None)
    dual_bound = float(raw_bound) if raw_bound is not None and math.isfinite(raw_bound) else None
    missing_mandatory = [s.service_id for s in known_services
                         if s.mandatory and s.service_id in unscheduled_ids]
    omitted_optional = [s.service_id for s in known_services
                        if not s.mandatory and s.service_id in unscheduled_ids]

    plan_out = ExecutionPlan(
        plan_id=f"PLAN_KGP_{scenario_id}",
        method_name="KGP",
        scenario_id=scenario_id,
        decision_day=decision_day,
        windows=final_windows,
        completed_service_ids=tuple(completed_ids),
        unscheduled_service_ids=tuple(unscheduled_ids),
        exclusion_justifications=excluded,
        lifecycle_alerts=lc_alerts,
        solver_status="PENDING_VERIFICATION",
        solver_time_sec=round(elapsed, 6),
    )
    plan_out, feasible = _audit_plan_export(network, all_services, catalog, plan_out)
    if not feasible:
        rejected_verification = plan_out.metadata["independent_verification"]
        fallback = _verified_seed_fallback(
            network, all_services, catalog, (seed_b2, seed_b1, seed_b0), w,
            plan_out.plan_id,
        )
        if fallback is not None:
            # A certificate for the rejected MILP output must not migrate to
            # a different feasible plan. Its catalog result is diagnostic only.
            plan_out = fallback
            plan_out.solver_status = "FEASIBLE_FALLBACK_NO_BOUND"
        else:
            _reject_plan_certificate(plan_out)
        plan_out.metadata.update({
            "candidate_columns_count": num_cols,
            "solver_raw_status": int(res.status),
            "rejected_candidate_verification": rejected_verification,
            "rejected_solver_catalog_result": {
                "objective": round(milp_obj_val, 4),
                "bound": round(dual_bound, 4) if dual_bound is not None else None,
                "gap": round(max(0.0, mip_gap), 6),
            },
            "fallback_verified_feasible": fallback is not None,
            "mandatory_coverage_rule": "HARD_CONSTRAINT",
            "b1_seed_radius_km": b1_radius_km,
            "objective_weights": {
                "window_weight": w.window_weight, "consumer_hour_weight": w.consumer_hour_weight,
                "repeat_consumer_weight": w.repeat_consumer_weight, "physical_maneuver_weight": w.physical_maneuver_weight,
                "crew_hour_weight": w.crew_hour_weight, "optional_unscheduled_penalty": w.optional_unscheduled_penalty,
            },
            "missing_mandatory_service_ids": [
                s.service_id for s in known_services
                if s.mandatory and s.service_id not in plan_out.completed_service_ids
            ],
            "omitted_optional_service_ids": [
                s.service_id for s in known_services
                if not s.mandatory and s.service_id not in plan_out.completed_service_ids
            ],
        })
        if fallback is not None:
            plan_out.metadata["optimality_scope"] = (
                "Fallback factível auditado após rejeição do candidato MILP; "
                "resultado do catálogo rejeitado é apenas diagnóstico, sem bound/gap ou prova de ótimo."
            )
        plan_out.solver_time_sec = round(time.perf_counter() - t0, 6)
        return plan_out

    exact_reconstructed_obj = compute_exact_plan_objective(plan_out, w, known_services)
    plan_out.solver_objective = exact_reconstructed_obj
    objective_matches = abs(exact_reconstructed_obj - milp_obj_val) <= 1e-3
    bound_valid = dual_bound is not None and dual_bound <= exact_reconstructed_obj + 1e-4
    plan_out.solver_bound = round(dual_bound, 4) if bound_valid else None
    plan_out.solver_gap = (
        round(abs(exact_reconstructed_obj - dual_bound) / max(abs(exact_reconstructed_obj), 1e-10), 6)
        if bound_valid else None
    )
    plan_out.is_optimal_proven = bool(
        res.status == 0 and mip_gap <= 1e-4 and objective_matches and bound_valid
        and abs(exact_reconstructed_obj - dual_bound) <= 1e-4
    )
    plan_out.solver_status = (
        "OPTIMAL_ON_DISCRETE_COLUMNS" if plan_out.is_optimal_proven else
        ("FEASIBLE_BOUNDED" if bound_valid else "FEASIBLE_NO_BOUND")
    )
    if not objective_matches:
        plan_out.solver_status = "OBJECTIVE_INTEGRITY_FAILURE"
        plan_out.is_optimal_proven = False
        plan_out.solver_bound = None
        plan_out.solver_gap = None
    plan_out.metadata = {
        **plan_out.metadata,
        "candidate_columns_count": num_cols,
        "milp_column_objective": round(milp_obj_val, 4),
        "reconstructed_plan_objective": exact_reconstructed_obj,
        "objective_reconstruction_matches": objective_matches,
        "missing_mandatory_service_ids": missing_mandatory,
        "omitted_optional_service_ids": omitted_optional,
        "mandatory_coverage_rule": "HARD_CONSTRAINT",
        "optimality_scope": (
            "Ótimo global comprovado sobre o conjunto discreto de colunas "
            "diárias factíveis geradas (incluindo encadeamento diário e sementes B0/B1/B2)."
            if plan_out.is_optimal_proven else
            ("Plano factível auditado, mas objetivo reconstruído diverge do MILP; sem bound/gap ou prova de ótimo."
             if not objective_matches else
             "Melhor plano factível nas colunas geradas, com bound/gap quando disponíveis; sem prova de ótimo.")
        ),
        "b1_seed_radius_km": b1_radius_km,
        "candidate_generation_limits": {
            "max_package_size": 5,
            "max_windows_per_day": 5,
            "max_states_per_depth": 120,
            "window_start_rule": "Primeiro início viável por sequência, turno, precedência e recursos; não enumera inícios contínuos.",
            "crew_allocation_rule": "Catálogo completo e subcatálogos de uma equipe na primeira janela; escalonador construtivo.",
            "sequence_rule": "Expansão limitada, deduplicação e poda por custo; sementes B0/B1/B2 incluídas.",
        },
        "objective_weights": {
            "window_weight": w.window_weight,
            "consumer_hour_weight": w.consumer_hour_weight,
            "repeat_consumer_weight": w.repeat_consumer_weight,
            "physical_maneuver_weight": w.physical_maneuver_weight,
            "crew_hour_weight": w.crew_hour_weight,
            "optional_unscheduled_penalty": w.optional_unscheduled_penalty,
        },
    }
    plan_out.solver_time_sec = round(time.perf_counter() - t0, 6)
    return plan_out
