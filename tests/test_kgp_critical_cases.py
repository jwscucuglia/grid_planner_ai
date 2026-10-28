"""Testes automatizados cobrindo todos os casos críticos de topologia, programação e verificação independente.

Inclui:
1. Casos críticos de topologia (radial simples, pontos próximos em alimentadores
   distintos, pontos distantes na mesma zona, chave NA de transferência, fonte
   removida, múltiplas fontes, ativo/fase ausente, desconexão parcial de fase,
   bloqueio de transferência por sobrecarga/parâmetros ausentes e sequência de
   manobras com impacto variável por etapa).
2. Casos críticos de programação e planos propositalmente inválidos para testar
   o verificador independente `feasibility.py` (serviço fora de isolamento, falta
   de competência, falta de material, sobreposição da mesma equipe, preparação/turno
   excedendo janela, violação de precedência, carteira incompleta, nota futura
   pós-decisão, alerta de ciclo de vida manutenção-substituição sem excluir ordem
   obrigatória, união de intervalos de clientes-hora sem duplicidade, carteira sem
   oportunidade de acoplamento e trade-off frequência vs. clientes-hora).
3. Verificação integral de B0, B1, B2 e KGP em todos os cenários DEV e EVAL.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List

import pytest

from kgp.baselines import (
    run_baseline_b0_individual,
    run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy,
)
from kgp.contracts import (
    ConsumerUnit,
    CrewResource,
    CrewScheduleInterval,
    ExecutionPlan,
    GridAsset,
    GridBranch,
    GridNetwork,
    GridNode,
    GridSwitch,
    IsolationZoneSpec,
    ManeuverStage,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
    ServiceType,
    SwitchState,
)
from kgp.eligibility import (
    check_service_zone_eligibility,
    detect_lifecycle_conflicts,
    filter_known_services,
)
from kgp.experiment import (
    DEV_SEEDS,
    EVAL_SEEDS,
    build_reference_network,
    evaluate_scenario_all_methods,
    generate_scenario_instance,
)
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics, merge_intervals
from kgp.optimizer import ObjectiveWeights, compute_exact_plan_objective, run_kgp_optimizer
from kgp.scheduling import build_window_schedule
from kgp.topology import (
    compute_energized_phases_by_node,
    evaluate_maneuver_step,
    simulate_zone_intervention,
    validate_asset_registration,
    verify_transfer_electrical_feasibility,
)


def test_radial_simple_isolation() -> None:
    """Caso topológico 1: radial simples sem transferência."""
    net = build_reference_network()
    steps, phys = simulate_zone_intervention(net, "Z_F1_TAIL_ONLY", 9.0, 11.0)
    assert phys == 2
    exec_step = [s for s in steps if s.stage == ManeuverStage.EXECUTION][0]
    assert "N_A1_5" in exec_step.deenergized_nodes
    assert "N_A1_4" in exec_step.energized_nodes
    assert "AST_A1_5A" in exec_step.deenergized_assets
    assert "AST_A1_4A" not in exec_step.deenergized_assets


def test_close_points_different_feeders_vs_distant_points_same_zone() -> None:
    """Casos topológicos 2 e 3: pontos próximos (200m) em alimentadores diferentes vs. pontos distantes (1,9 km) na mesma zona."""
    net = build_reference_network()
    scen = generate_scenario_instance(202601, "EVAL")
    s_f1_near = scen.services[0]  # AST_A1_2A (x=1.5, y=1.0)
    s_f1_far = scen.services[1]   # AST_A1_3B (x=3.45, y=0.98) -> dist ~ 1.95 km
    s_f2_near = scen.services[2]  # AST_A2_2A (x=1.5, y=1.20) -> dist = 0.20 km de s_f1_near!

    # S01 e S03 estão a apenas 200m, mas S03 (F2) não fica desenergizado na zona de F1:
    ok_f1_s01, _ = check_service_zone_eligibility(net, s_f1_near, "Z_F1_TRUNK_TRANSFER")
    ok_f1_s03, _ = check_service_zone_eligibility(net, s_f2_near, "Z_F1_TRUNK_TRANSFER")
    assert ok_f1_s01 is True
    assert ok_f1_s03 is False

    # S01 e S02 estão a 1,95 km de distância, mas ambos ficam isolados em Z_F1_TRUNK_TRANSFER:
    ok_f1_s02, _ = check_service_zone_eligibility(net, s_f1_far, "Z_F1_TRUNK_TRANSFER")
    assert ok_f1_s02 is True


def test_tie_switch_transfer_and_variable_stage_impact() -> None:
    """Caso topológico 4 e 8: chave NA mantém suprimento na execução e etapas têm impacto variável."""
    net = build_reference_network()
    steps, phys = simulate_zone_intervention(net, "Z_F1_TRUNK_TRANSFER", 9.0, 12.0)
    # 2 chaves NF abertas/fechadas + 1 chave NA fechada/aberta = 6 manobras físicas
    assert phys == 6
    assert len(steps) == 4

    iso_step = steps[0]
    tr_step = steps[1]
    exec_step = steps[2]

    # Na etapa 1 (ISOLATION, antes de fechar a chave NA SW_TIE_12), N_A1_4 fica sem tensão:
    assert "N_A1_4" in iso_step.deenergized_nodes
    assert "N_A1_2" in iso_step.deenergized_nodes
    assert "N_A1_3" in iso_step.deenergized_nodes

    # Nas etapas 2 (TRANSFER) e 3 (EXECUTION), SW_TIE_12 fecha e reenergiza N_A1_4 e N_A1_5 via SUB_02!
    assert "N_A1_4" in tr_step.energized_nodes
    assert "N_A1_4" in exec_step.energized_nodes
    assert "N_A1_5" in exec_step.energized_nodes
    # Enquanto N_A1_2 e N_A1_3 permanecem seguramente isolados entre SW_A1_1 e SW_A1_3:
    assert "N_A1_2" in exec_step.deenergized_nodes
    assert "N_A1_3" in exec_step.deenergized_nodes
    assert len(exec_step.interrupted_consumers) < len(iso_step.interrupted_consumers)


def test_removed_source_and_multiple_sources() -> None:
    """Casos topológicos 5 e 6: fonte removida e múltiplas fontes ativas."""
    net = build_reference_network()
    # Múltiplas fontes com SW_TIE_12 fechada:
    energized_multi, sources_multi, _ = compute_energized_phases_by_node(
        net, open_switches=set(), closed_switches={"SW_TIE_12"}
    )
    assert "SUB_01" in sources_multi["N_A1_3"]
    assert "SUB_02" in sources_multi["N_A1_3"]

    # Remove fonte SUB_01 (fora de serviço) sem fechar SW_TIE_12:
    net.nodes["SUB_01"] = replace(net.nodes["SUB_01"], in_service=False)
    energized_no_s1, _, notes = compute_energized_phases_by_node(
        net, open_switches=set(), closed_switches=set()
    )
    assert len(energized_no_s1["N_A1_2"]) == 0
    # E a GD em N_A1_5 é desconectada por anti-ilhamento:
    assert any("anti-ilhamento" in n for n in notes)


def test_missing_asset_or_phase_and_partial_phase_disconnection() -> None:
    """Caso topológico 7: ativo/fase ausente no cadastro e desconexão parcial de fase."""
    net = build_reference_network()
    # Ativo inexistente
    assert len(validate_asset_registration(net, "AST_INEXISTENTE")) > 0

    # Ativo exigindo fase C na barra N_A1_5 (que possui apenas fases A e B):
    net.assets["AST_BAD_PHASE"] = GridAsset(
        asset_id="AST_BAD_PHASE",
        node_id="N_A1_5",
        branch_id=None,
        feeder_id="F1",
        asset_type="TRANSFORMADOR",
        phases=("A", "B", "C"),
        in_service=True,
    )
    issues = validate_asset_registration(net, "AST_BAD_PHASE")
    assert any("requer fase(s) ['C'] ausente(s)" in msg for msg in issues)

    # Desconexão parcial de fase: trecho BR_A1_23 perdendo apenas a fase C
    net.branches["BR_A1_23"] = replace(net.branches["BR_A1_23"], phases=("A", "B"))
    step = evaluate_maneuver_step(
        net,
        step_order=1,
        stage=ManeuverStage.EXECUTION,
        start_hour=9.0,
        end_hour=11.0,
        open_switches=(),
        closed_switches=(),
    )
    # N_A1_3 ainda tem fases A e B energizadas -> NÃO pode constar como barra desenergizada!
    assert "N_A1_3" in step.energized_nodes
    assert "N_A1_3" not in step.deenergized_nodes
    assert "AST_A1_3A" not in step.deenergized_assets
    assert any("parcialmente energizada" in step.notes for _ in [1])


def test_transfer_disabled_when_params_missing_or_overloaded() -> None:
    """Verifica bloqueio de transferência quando parâmetros faltam ou há sobrecarga."""
    net = build_reference_network()
    net.isolation_zones["Z_INCOMPLETE"] = replace(
        net.isolation_zones["Z_F1_TRUNK_TRANSFER"],
        zone_id="Z_INCOMPLETE",
        transfer_params_complete=False,
    )
    step_inc = evaluate_maneuver_step(
        net,
        step_order=1,
        stage=ManeuverStage.TRANSFER,
        start_hour=9.0,
        end_hour=11.0,
        open_switches=("SW_A1_1", "SW_A1_3"),
        closed_switches=("SW_TIE_12",),
        zone=net.isolation_zones["Z_INCOMPLETE"],
    )
    assert step_inc.transfer_feasible is False

    # Sobrecarga térmica no trecho de interligação BR_TIE_12:
    net2 = build_reference_network()
    net2.branches["BR_TIE_12"] = replace(
        net2.branches["BR_TIE_12"], max_capacity_kw=50.0
    )
    step_ovl = evaluate_maneuver_step(
        net2,
        step_order=1,
        stage=ManeuverStage.TRANSFER,
        start_hour=9.0,
        end_hour=11.0,
        open_switches=("SW_A1_1", "SW_A1_3"),
        closed_switches=("SW_TIE_12",),
        zone=net2.isolation_zones["Z_F1_TRUNK_TRANSFER"],
    )
    assert step_ovl.transfer_feasible is False
    assert step_ovl.max_loading_pct > 100.0


def test_verifier_rejects_intentionally_invalid_plans() -> None:
    """Testa `feasibility.py` contra planos propositalmente inválidos em cada dimensão crítica."""
    scen = generate_scenario_instance(202601, "EVAL")
    valid_plan = run_baseline_b0_individual(
        scen.network, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )
    valid_rep = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, valid_plan
    )
    assert valid_rep.is_feasible is True

    # 1. Carteira obrigatória incompleta (remove uma janela):
    incomplete_plan = replace(valid_plan, windows=valid_plan.windows[:-1])
    rep_inc = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, incomplete_plan
    )
    assert rep_inc.is_feasible is False
    assert rep_inc.mandatory_portfolio_complete is False

    # 2. Serviço fora da zona de isolamento (aloca S01 de F1 numa zona de F3!):
    bad_zone_win = replace(valid_plan.windows[0], zone_id="Z_F3_TAIL_ONLY")
    bad_zone_plan = replace(
        valid_plan, windows=[bad_zone_win] + valid_plan.windows[1:]
    )
    rep_zone = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, bad_zone_plan
    )
    assert rep_zone.is_feasible is False
    assert any("permanece energizado" in v for v in rep_zone.violations)

    # 3. Falta de competência da equipe e falta de material:
    cat_no_mat = replace(
        scen.catalog,
        materials_inventory={"ISOLADOR_15KV": 1.0},
    )
    rep_mat = verify_plan_feasibility(
        scen.network, scen.services, cat_no_mat, valid_plan
    )
    assert rep_mat.is_feasible is False
    assert any("estoque de material" in v for v in rep_mat.violations)

    # 4. Sobreposição da mesma equipe no mesmo dia:
    w0 = valid_plan.windows[0]
    w1 = valid_plan.windows[1]
    iv0 = w0.crew_intervals[0]
    overlapping_iv1 = replace(
        w1.crew_intervals[0],
        crew_id=iv0.crew_id,
        day=iv0.day,
        travel_start_hour=iv0.exec_start_hour,
        prep_start_hour=iv0.exec_start_hour + 0.1,
        exec_start_hour=iv0.exec_start_hour + 0.3,
        exec_end_hour=iv0.exec_end_hour + 0.3,
        recomp_end_hour=iv0.recomp_end_hour + 0.3,
    )
    w1_overlap = replace(w1, day=iv0.day, crew_intervals=(overlapping_iv1,))
    overlap_plan = replace(
        valid_plan, windows=[w0, w1_overlap] + valid_plan.windows[2:]
    )
    rep_ovl = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, overlap_plan
    )
    assert rep_ovl.is_feasible is False
    assert any("Sobreposição de agenda" in v for v in rep_ovl.violations)

    # 5. Preparação ou turno que excede janela:
    iv_late = replace(
        iv0,
        exec_end_hour=17.5,
        recomp_end_hour=17.8,
    )
    w0_late = replace(w0, work_end_hour=17.5, end_hour=17.8, crew_intervals=(iv_late,))
    late_plan = replace(valid_plan, windows=[w0_late] + valid_plan.windows[1:])
    rep_late = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, late_plan
    )
    assert rep_late.is_feasible is False
    assert any("após o fim do turno" in v or "após o limite" in v for v in rep_late.violations)

    # 6. Violação de precedência:
    scen_pred = generate_scenario_instance(202603, "EVAL")
    plan_pred = run_baseline_b0_individual(
        scen_pred.network, scen_pred.services, scen_pred.catalog, scen_pred.scenario_id, 0
    )
    # Inverte os dias de S31_F3_PREP (predecessor) e S32_F3_TRAFO (dependente):
    inverted_wins: List[PlannedInterruptionWindow] = []
    for w in plan_pred.windows:
        if "S31_F3_PREP" in w.service_ids:
            new_ivs = tuple(replace(iv, day=4) for iv in w.crew_intervals)
            inverted_wins.append(replace(w, day=4, crew_intervals=new_ivs))
        elif "S32_F3_TRAFO" in w.service_ids:
            new_ivs = tuple(replace(iv, day=2) for iv in w.crew_intervals)
            inverted_wins.append(replace(w, day=2, crew_intervals=new_ivs))
        else:
            inverted_wins.append(w)
    bad_pred_plan = replace(plan_pred, windows=inverted_wins)
    rep_pred = verify_plan_feasibility(
        scen_pred.network, scen_pred.services, scen_pred.catalog, bad_pred_plan
    )
    assert rep_pred.is_feasible is False
    assert any("Precedência violada" in v for v in rep_pred.violations)

    # 7. Vazamento temporal (nota criada após a data de decisão incluída no plano):
    scen_leak = generate_scenario_instance(202606, "EVAL")
    future_svc = [s for s in scen_leak.services if s.created_at_day > 0][0]
    fake_win, _ = build_window_schedule(
        scen_leak.network,
        scen_leak.catalog,
        "Z_F2_DISTAL",
        3,
        "WIN_LEAK",
        [future_svc],
    )
    assert fake_win is not None
    base_plan_06 = run_baseline_b0_individual(
        scen_leak.network, scen_leak.services, scen_leak.catalog, scen_leak.scenario_id, 0
    )
    leaked_plan = replace(base_plan_06, windows=base_plan_06.windows + [fake_win])
    rep_leak = verify_plan_feasibility(
        scen_leak.network, scen_leak.services, scen_leak.catalog, leaked_plan
    )
    assert rep_leak.is_feasible is False
    assert any("Vazamento temporal" in v for v in rep_leak.violations)


def test_adversarial_1_tampered_exported_impact_and_steps_rejected() -> None:
    """Regressão adversarial 1: plano adulterado com maneuver_steps vazios ou impacto 0 é reprovado."""
    scen = generate_scenario_instance(202601, "EVAL")
    valid_plan = run_baseline_b0_individual(
        scen.network, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )
    w0_tampered = replace(
        valid_plan.windows[0],
        maneuver_steps=(),
        interrupted_consumers=(),
        physical_maneuvers_count=0,
    )
    tampered_plan = replace(
        valid_plan, windows=[w0_tampered] + valid_plan.windows[1:]
    )
    rep = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, tampered_plan
    )
    assert rep.is_feasible is False
    assert rep.isolation_verified is False
    assert any("Violação de integridade" in v for v in rep.violations)

    # E as métricas continuam refletindo os valores recalculados pelo verificador:
    metrics = compute_plan_metrics(
        scen.network, scen.services, tampered_plan, rep
    )
    assert metrics.is_feasible is False
    assert metrics.consumer_hours_total > 0.0
    assert metrics.physical_maneuvers_count > 0


def test_adversarial_2_branch_attached_asset_with_live_endpoint_rejected() -> None:
    """Regressão adversarial 2: ativo em trecho (branch_id) exige AMBAS as barras extremas desenergizadas."""
    scen = generate_scenario_instance(202601, "EVAL")
    net = scen.network

    # Em Z_F1_TRUNK_TRANSFER (abre SW_A1_1 em BR_A1_12 e SW_A1_3 em BR_A1_34):
    # - O trecho BR_A1_23 tem AMBAS as extremidades (N_A1_2 e N_A1_3) desenergizadas -> AST_BR_A1_23 elegível!
    steps, _ = simulate_zone_intervention(net, "Z_F1_TRUNK_TRANSFER", 9.0, 11.0)
    exec_step = [s for s in steps if s.stage == ManeuverStage.EXECUTION][0]
    assert "AST_BR_A1_23" in exec_step.deenergized_assets

    # - Já um ativo pendurado em BR_A1_12 (onde N_A1_1 continua energizada em 13,8 kV!) NÃO pode ser dado como isolado:
    net.assets["AST_LIVE_BRANCH_END"] = GridAsset(
        asset_id="AST_LIVE_BRANCH_END",
        node_id=None,
        branch_id="BR_A1_12",
        feeder_id="F1",
        asset_type="VAO_REDE_PODA",
        phases=("A", "B", "C"),
        in_service=True,
        x=1.1,
        y=1.0,
    )
    steps_boundary, _ = simulate_zone_intervention(
        net, "Z_F1_TRUNK_TRANSFER", 9.0, 11.0
    )
    exec_boundary = [
        s for s in steps_boundary if s.stage == ManeuverStage.EXECUTION
    ][0]
    assert "AST_LIVE_BRANCH_END" not in exec_boundary.deenergized_assets

    # E se um plano adulterado tentar executar serviço em AST_LIVE_BRANCH_END nessa zona, o verificador reprova:
    valid_plan = run_baseline_b0_individual(
        net, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )
    bad_services = list(scen.services)
    bad_services[0] = replace(
        bad_services[0], target_assets=("AST_LIVE_BRANCH_END",)
    )
    rep = verify_plan_feasibility(net, bad_services, scen.catalog, valid_plan)
    assert rep.is_feasible is False
    assert rep.isolation_verified is False
    assert any(
        "extremidade de trecho energizada" in v or "permanece energizado" in v
        for v in rep.violations
    )


def test_adversarial_3_overlapping_windows_same_zone_different_crews_rejected() -> None:
    """Regressão adversarial 3: duas janelas sobrepostas na mesma zona no mesmo dia (mesmo com equipes distintas) são reprovadas."""
    scen = generate_scenario_instance(202601, "EVAL")
    valid_plan = run_baseline_b0_individual(
        scen.network, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )
    w0 = valid_plan.windows[0]  # S01 em Z_F1_TRUNK_TRANSFER
    w1 = valid_plan.windows[1]  # S02 em Z_F1_TRUNK_TRANSFER

    # Força w0 e w1 para o mesmo dia (dia 2) com equipes distintas (EQ_PESADA_01 e EQ_MANUT_02),
    # porém sobrepondo as janelas [8.13, 10.88] e [9.50, 12.05] na mesma zona Z_F1_TRUNK_TRANSFER:
    iv0 = replace(w0.crew_intervals[0], crew_id="EQ_PESADA_01", day=2)
    w0_d2 = replace(w0, day=2, crew_intervals=(iv0,))

    iv1 = replace(
        w1.crew_intervals[0],
        crew_id="EQ_MANUT_02",
        day=2,
        travel_start_hour=9.20,
        prep_start_hour=9.50,
        exec_start_hour=9.80,
        exec_end_hour=11.80,
        recomp_end_hour=12.05,
    )
    steps1, _ = simulate_zone_intervention(
        scen.network, w0.zone_id, 9.80, 11.80
    )
    w1_d2_overlap = replace(
        w1,
        day=2,
        zone_id=w0.zone_id,
        start_hour=9.50,
        work_start_hour=9.80,
        work_end_hour=11.80,
        end_hour=12.05,
        maneuver_steps=steps1,
        crew_intervals=(iv1,),
    )
    bad_plan = replace(
        valid_plan, windows=[w0_d2, w1_d2_overlap] + valid_plan.windows[2:]
    )
    rep = verify_plan_feasibility(
        scen.network, scen.services, scen.catalog, bad_plan
    )
    assert rep.is_feasible is False
    assert any("Conflito entre janelas" in v for v in rep.violations)


def test_adversarial_4_multi_crew_service_disjoint_execution_rejected() -> None:
    """Regressão adversarial 4: serviço exigindo 2 equipes simultâneas não aceita intervalos disjuntos."""
    scen = generate_scenario_instance(202601, "EVAL")
    s_multi = replace(
        scen.services[0],
        required_crews_count=2,
        duration_hours=2.0,
    )
    services_multi = [s_multi] + scen.services[1:]

    # Constrói janela legítima com 2 equipes simultâneas no dia 2:
    win_simul, reason = build_window_schedule(
        scen.network,
        scen.catalog,
        "Z_F1_TRUNK_TRANSFER",
        2,
        "WIN_SIMUL_OK",
        [s_multi],
    )
    assert win_simul is not None, reason
    plan_ok = run_baseline_b0_individual(
        scen.network, services_multi, scen.catalog, "MULTI_OK", 0
    )
    rep_ok = verify_plan_feasibility(
        scen.network, services_multi, scen.catalog, plan_ok
    )
    assert rep_ok.is_feasible is True

    # Agora adultera os intervalos das 2 equipes de s_multi para ficarem disjuntos no tempo:
    w_target = [w for w in plan_ok.windows if s_multi.service_id in w.service_ids][0]
    other_wins = [w for w in plan_ok.windows if s_multi.service_id not in w.service_ids]
    iv_a, iv_b = w_target.crew_intervals[0], w_target.crew_intervals[1]
    iv_b_disjoint = replace(
        iv_b,
        exec_start_hour=iv_a.exec_end_hour + 0.20,
        exec_end_hour=iv_a.exec_end_hour + 2.20,
        recomp_end_hour=iv_a.exec_end_hour + 2.45,
    )
    steps_disj, _ = simulate_zone_intervention(
        scen.network,
        w_target.zone_id,
        iv_a.exec_start_hour,
        iv_b_disjoint.exec_end_hour,
    )
    w_disj = replace(
        w_target,
        work_end_hour=iv_b_disjoint.exec_end_hour,
        end_hour=iv_b_disjoint.recomp_end_hour,
        maneuver_steps=steps_disj,
        crew_intervals=(iv_a, iv_b_disjoint),
    )
    plan_disj = replace(plan_ok, windows=[w_disj] + other_wins)
    rep_disj = verify_plan_feasibility(
        scen.network, services_multi, scen.catalog, plan_disj
    )
    assert rep_disj.is_feasible is False
    assert any("Violação de simultaneidade de equipes" in v for v in rep_disj.violations)


def test_adversarial_5_phase_blind_transfer_rejected() -> None:
    """Regressão adversarial 5: carga na fase A não pode usar atalho exclusivo da fase B durante transferência."""
    nodes = {
        "SRC": GridNode("SRC", "Fonte", "F1", 0.0, 0.0, ("A", "B", "C"), True),
        "N1": GridNode("N1", "Barra 1", "F1", 1.0, 0.0, ("A", "B", "C"), False),
        "N2": GridNode("N2", "Barra 2", "F1", 2.0, 0.0, ("A", "B", "C"), False),
    }
    branches = {
        "BR_A1": GridBranch(
            "BR_A1", "SRC", "N1", "F1", ("A",), True, 0.01, 0.01, 100.0, 0.5
        ),
        "BR_A2": GridBranch(
            "BR_A2", "N1", "N2", "F1", ("A",), True, 0.01, 0.01, 100.0, 0.5
        ),
        # Atalho curto de alta capacidade, porém apenas na fase B:
        "BR_B_SHORTCUT": GridBranch(
            "BR_B_SHORTCUT", "SRC", "N2", "F1", ("B",), True, 0.01, 0.01, 5000.0, 0.5
        ),
    }
    switches = {
        "SW_TIE_A": GridSwitch(
            "SW_TIE_A", "BR_A2", SwitchState.OPEN, SwitchState.OPEN, True
        )
    }
    consumers = {
        "CU_PHASE_A_1000KW": ConsumerUnit(
            consumer_id="CU_PHASE_A_1000KW",
            node_id="N2",
            asset_id=None,
            phase="A",
            avg_demand_kw=1000.0,
        )
    }
    zone = IsolationZoneSpec(
        zone_id="Z_PHASE_TEST",
        name="Zona Teste Fase",
        feeder_id="F1",
        open_switches=(),
        tie_switches_closed=("SW_TIE_A",),
        requires_transfer_validation=True,
    )
    net = GridNetwork(
        network_id="NET_PHASE_TEST",
        version="1.0",
        valid_from_day=0,
        nodes=nodes,
        branches=branches,
        switches=switches,
        assets={},
        consumers=consumers,
        isolation_zones={"Z_PHASE_TEST": zone},
    )
    step = evaluate_maneuver_step(
        network=net,
        step_order=1,
        stage=ManeuverStage.TRANSFER,
        start_hour=8.0,
        end_hour=9.0,
        open_switches=(),
        closed_switches=("SW_TIE_A",),
        zone=zone,
    )
    assert step.transfer_feasible is False
    assert step.max_loading_pct == pytest.approx(1000.0)
    assert "Sobrecarga" in step.notes


def test_adversarial_6_spoofed_travel_coordinates_rejected() -> None:
    """Regressão adversarial 6: adulterar (iv.x, iv.y) para zerar deslocamento da base distante é reprovado."""
    scen = generate_scenario_instance(202601, "EVAL")
    # Move a base de EQ_PESADA_01 para (350.0, 350.0) km:
    far_crews = dict(scen.catalog.crews)
    c_pesada = far_crews["EQ_PESADA_01"]
    far_crews["EQ_PESADA_01"] = replace(c_pesada, base_x=350.0, base_y=350.0)
    far_cat = replace(scen.catalog, crews=far_crews)

    # Um plano adulterado que copia um intervalo com travel curto e falsifica iv.x=350.0, iv.y=350.0:
    valid_plan = run_baseline_b0_individual(
        scen.network, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )
    w0 = valid_plan.windows[0]
    iv0_spoofed = replace(w0.crew_intervals[0], x=350.0, y=350.0)
    w0_spoofed = replace(w0, crew_intervals=(iv0_spoofed,))
    spoofed_plan = replace(valid_plan, windows=[w0_spoofed] + valid_plan.windows[1:])

    rep = verify_plan_feasibility(
        scen.network, scen.services, far_cat, spoofed_plan
    )
    assert rep.is_feasible is False
    assert any(
        "Violação de coordenadas canônicas" in v or "Tempo de deslocamento inconsistente" in v
        for v in rep.violations
    )


def test_adversarial_7_non_maneuverable_switch_and_empty_target_assets_rejected() -> None:
    """Regressão adversarial 7: chave com is_maneuverable=False ou serviço com target_assets=() são reprovados."""
    scen = generate_scenario_instance(202601, "EVAL")
    valid_plan = run_baseline_b0_individual(
        scen.network, scen.services, scen.catalog, scen.scenario_id, scen.decision_day
    )

    # 7a. Chave SW_A1_1 marcada como não manobrável (is_maneuverable=False):
    net_locked = build_reference_network()
    net_locked.switches["SW_A1_1"] = replace(
        net_locked.switches["SW_A1_1"], is_maneuverable=False
    )
    elig, reason = check_service_zone_eligibility(
        net_locked, scen.services[0], "Z_F1_TRUNK_TRANSFER"
    )
    assert elig is False
    assert "não manobrável" in reason

    rep_locked = verify_plan_feasibility(
        net_locked, scen.services, scen.catalog, valid_plan
    )
    assert rep_locked.is_feasible is False
    assert any("não manobrável" in v for v in rep_locked.violations)

    # 7b. Serviço com target_assets vazio ():
    empty_svc = replace(scen.services[0], target_assets=())
    elig_empty, reason_empty = check_service_zone_eligibility(
        scen.network, empty_svc, "Z_F1_TRUNK_TRANSFER"
    )
    assert elig_empty is False
    assert "sem ativos de trabalho definidos" in reason_empty or "target_assets vazio" in reason_empty

    rep_empty = verify_plan_feasibility(
        scen.network, [empty_svc] + scen.services[1:], scen.catalog, valid_plan
    )
    assert rep_empty.is_feasible is False
    assert any("target_assets vazio" in v for v in rep_empty.violations)


def test_adversarial_8_optimizer_greedy_crew_allocation_parallel_pack() -> None:
    """Regressão adversarial 8: 2 serviços simultâneos com 1 equipe geral+especial e 1 equipe geral alocados em paralelo (custo 99,84 vs 209,04)."""
    net = build_reference_network()
    crews = {
        "C_GEN_SPEC": CrewResource(
            crew_id="C_GEN_SPEC",
            name="Equipe Geral + Especial",
            skills=("GERAL", "ESPECIAL"),
            available_days=(1,),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=1.5,
            base_y=1.0,
            speed_kmh=35.0,
        ),
        "C_GEN_ONLY": CrewResource(
            crew_id="C_GEN_ONLY",
            name="Equipe Geral",
            skills=("GERAL",),
            available_days=(1,),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=1.5,
            base_y=1.0,
            speed_kmh=35.0,
        ),
    }
    cat = ResourceCatalog(crews=crews, materials_inventory={})
    # S_GEN listado antes de S_SPEC para testar se a ordenação/backtracking evita o bloqueio guloso:
    s_gen = ServiceOrder(
        service_id="S_GEN",
        title="Serviço Geral",
        service_type=ServiceType.PREVENTIVA,
        target_assets=("AST_A1_5A",),
        mandatory=True,
        created_at_day=0,
        ready_at_day=1,
        deadline_day=1,
        duration_hours=2.0,
        required_skills=("GERAL",),
        required_crews_count=1,
        allow_simultaneous_in_zone=True,
        x=4.8,
        y=1.0,
    )
    s_spec = ServiceOrder(
        service_id="S_SPEC",
        title="Serviço Especial",
        service_type=ServiceType.TRANSFORMADOR,
        target_assets=("AST_A1_5B",),
        mandatory=True,
        created_at_day=0,
        ready_at_day=1,
        deadline_day=1,
        duration_hours=2.0,
        required_skills=("ESPECIAL",),
        required_crews_count=1,
        allow_simultaneous_in_zone=True,
        x=4.85,
        y=1.08,
    )
    win, reason = build_window_schedule(
        net, cat, "Z_F1_TAIL_ONLY", 1, "W_PAR", [s_gen, s_spec]
    )
    assert win is not None, reason
    # Execução em paralelo deve durar apenas 2,0h (e não 4,0h sequenciais):
    assert (win.work_end_hour - win.work_start_hour) == pytest.approx(2.0, abs=0.05)

    plan_kgp = run_kgp_optimizer(net, [s_gen, s_spec], cat, "ADV_8", 0)
    rep = verify_plan_feasibility(net, [s_gen, s_spec], cat, plan_kgp)
    assert rep.is_feasible is True
    assert len(plan_kgp.windows) == 1
    assert (
        plan_kgp.windows[0].work_end_hour - plan_kgp.windows[0].work_start_hour
    ) == pytest.approx(2.0, abs=0.05)


def test_adversarial_9_optimizer_shift_domain_three_windows_one_day() -> None:
    """Regressão adversarial 9: 3 zonas distintas, 3 serviços obrigatórios de 2h e 1 equipe em 1 único dia (8h às 16h30)."""
    net = build_reference_network()
    crews = {
        "C_SOLO": CrewResource(
            crew_id="C_SOLO",
            name="Equipe Única",
            skills=("TRAFO", "PODA", "REDE_AEREA", "CHAVE"),
            available_days=(1,),
            shift_start_hour=8.0,
            shift_end_hour=16.6,
            base_x=1.5,
            base_y=1.0,
            speed_kmh=40.0,
        )
    }
    cat = ResourceCatalog(crews=crews, materials_inventory={})
    services = [
        ServiceOrder(
            service_id="S_Z1",
            title="Serviço Zona 1",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_5A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=1.8,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=4.8,
            y=1.0,
        ),
        ServiceOrder(
            service_id="S_Z2",
            title="Serviço Zona 2",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A2_3A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=1.8,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=3.0,
            y=3.0,
        ),
        ServiceOrder(
            service_id="S_Z3",
            title="Serviço Zona 3",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A3_3A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=1.8,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=4.4,
            y=-1.5,
        ),
    ]
    plan_kgp = run_kgp_optimizer(net, services, cat, "ADV_9", 0)
    rep = verify_plan_feasibility(net, services, cat, plan_kgp)
    assert rep.is_feasible is True, rep.violations
    assert len(plan_kgp.unscheduled_service_ids) == 0
    assert len(plan_kgp.completed_service_ids) == 3
    assert len(plan_kgp.windows) == 3


def test_adversarial_10_optimizer_repeat_cap_and_exact_reconstruction_objective() -> None:
    """Regressão adversarial 10: 3 janelas no mesmo dia sem bloqueio de reincidência, penalidade binária exata em 3 dias e igualdade exata MILP == plano reconstruído."""
    net = build_reference_network()
    crews = {
        "C_FAST": CrewResource(
            crew_id="C_FAST",
            name="Equipe Rápida",
            skills=("TRAFO", "PODA", "REDE_AEREA", "CHAVE"),
            available_days=(1, 2, 3),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=4.8,
            base_y=1.0,
            speed_kmh=40.0,
        )
    }
    cat = ResourceCatalog(crews=crews, materials_inventory={})

    # 10a. 3 serviços curtos (0,5h) no MESMO dia (dia 1) em zonas/ativos que interrompem o mesmo consumidor:
    svcs_1day_3wins = [
        ServiceOrder(
            service_id="S_1D_A",
            title="Serviço 1 Dia 1",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_5A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=0.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=4.8,
            y=1.0,
        ),
        ServiceOrder(
            service_id="S_1D_B",
            title="Serviço 2 Dia 1",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_4A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=0.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=3.8,
            y=1.0,
        ),
        ServiceOrder(
            service_id="S_1D_C",
            title="Serviço 3 Dia 1",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_2A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=0.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=1.5,
            y=1.0,
        ),
    ]
    plan_1d = run_kgp_optimizer(net, svcs_1day_3wins, cat, "ADV_10_1D", 0)
    rep_1d = verify_plan_feasibility(net, svcs_1day_3wins, cat, plan_1d)
    assert rep_1d.is_feasible is True, rep_1d.violations
    assert len(plan_1d.completed_service_ids) == 3

    # 10b. 3 serviços em 3 dias distintos interrompendo o mesmo consumidor:
    svcs_3days = [
        ServiceOrder(
            service_id=f"S_REP_{d}",
            title=f"Serviço Dia {d}",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_5A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=d,
            deadline_day=d,
            duration_hours=0.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=4.8,
            y=1.0,
        )
        for d in (1, 2, 3)
    ]
    plan_3d = run_kgp_optimizer(net, svcs_3days, cat, "ADV_10_3D", 0)
    rep_3d = verify_plan_feasibility(net, svcs_3days, cat, plan_3d)
    assert rep_3d.is_feasible is True, rep_3d.violations
    exact_obj_3d = compute_exact_plan_objective(plan_3d, ObjectiveWeights())
    assert plan_3d.metadata["milp_column_objective"] == pytest.approx(
        exact_obj_3d, abs=1e-4
    )
    assert plan_3d.metadata["reconstructed_plan_objective"] == pytest.approx(
        exact_obj_3d, abs=1e-4
    )

    # 10c. 2 serviços próximos no mesmo dia (dia 1) em zonas distintas: KGP tem exatamente o mesmo objetivo na coluna MILP e no plano reconstruído, e não perde para B0:
    svcs_2near = [
        ServiceOrder(
            service_id="S_NEAR_1",
            title="Serviço Próximo 1",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A1_5A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=1.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=4.8,
            y=1.0,
        ),
        ServiceOrder(
            service_id="S_NEAR_2",
            title="Serviço Próximo 2",
            service_type=ServiceType.PREVENTIVA,
            target_assets=("AST_A2_3A",),
            mandatory=True,
            created_at_day=0,
            ready_at_day=1,
            deadline_day=1,
            duration_hours=1.5,
            required_skills=("TRAFO",),
            required_crews_count=1,
            allow_simultaneous_in_zone=False,
            x=3.0,
            y=3.0,
        ),
    ]
    plan_b0 = run_baseline_b0_individual(net, svcs_2near, cat, "ADV_10_NEAR", 0)
    plan_kgp = run_kgp_optimizer(net, svcs_2near, cat, "ADV_10_NEAR", 0)
    obj_b0 = compute_exact_plan_objective(plan_b0, ObjectiveWeights())
    obj_kgp = compute_exact_plan_objective(plan_kgp, ObjectiveWeights())
    assert obj_kgp <= obj_b0 + 1e-6
    assert plan_kgp.metadata["milp_column_objective"] == pytest.approx(
        obj_kgp, abs=1e-4
    )


def test_lifecycle_conflict_and_temporal_filtering() -> None:
    """Verifica alerta de conflito manutenção-substituição sem eliminar ordem obrigatória."""
    scen = generate_scenario_instance(202606, "EVAL")
    known, excluded = filter_known_services(scen.services, decision_day=0)
    assert "S64_FUTURE_LEAK_TEST" in excluded
    assert len(known) == 3

    alerts = detect_lifecycle_conflicts(known)
    assert len(alerts) == 1
    assert alerts[0].asset_id == "AST_A1_3B"
    assert alerts[0].maintenance_service_id == "S61_MAINT_TRAFO"
    assert alerts[0].replacement_service_id == "S62_REPL_TRAFO"

    res = evaluate_scenario_all_methods(scen)
    for m in ("B0", "B1", "B2", "KGP"):
        plan, feas, metrics = res[m]
        assert feas.is_feasible is True
        assert "S61_MAINT_TRAFO" in plan.completed_service_ids
        assert "S62_REPL_TRAFO" in plan.completed_service_ids
        assert metrics.lifecycle_alerts_count == 1


def test_consumer_hours_union_and_tradeoff_case() -> None:
    """Verifica união de intervalos sem duplicidade e caso em que agrupar reduz frequência mas aumenta clientes-hora."""
    # União de intervalos sobrepostos [8.0, 11.0] e [10.0, 12.0] = [8.0, 12.0] (4.0h, não 5.0h):
    merged = merge_intervals([(8.0, 11.0), (10.0, 12.0), (12.0, 12.5)])
    assert len(merged) == 1
    assert merged[0] == (8.0, 12.5)

    # Caso explícito de trade-off:
    # Serviço curto no tronco (1.0h em AST_A1_2A) + Serviço longo no fim de linha (3.5h em AST_A1_5B)
    # executados sequencialmente pela mesma equipe na zona radial completa Z_F1_FULL_RADIAL
    # vs. janelas separadas nas zonas mínimas (Z_F1_TRUNK_TRANSFER + Z_F1_TAIL_ONLY).
    net = build_reference_network()
    cat = generate_scenario_instance(202604, "EVAL").catalog
    s_trunk = ServiceOrder(
        service_id="S_SHORT_TRUNK",
        title="Inspeção rápida tronco",
        service_type=ServiceType.PREVENTIVA,
        target_assets=("AST_A1_2A",),
        mandatory=True,
        created_at_day=0,
        ready_at_day=1,
        deadline_day=2,
        duration_hours=1.0,
        required_skills=("TRAFO",),
        required_crews_count=1,
        allow_simultaneous_in_zone=False,
        x=1.5,
        y=1.0,
    )
    s_tail = ServiceOrder(
        service_id="S_LONG_TAIL",
        title="Reforma longa no fim do ramal",
        service_type=ServiceType.TRANSFORMADOR,
        target_assets=("AST_A1_5B",),
        mandatory=True,
        created_at_day=0,
        ready_at_day=1,
        deadline_day=2,
        duration_hours=3.5,
        required_skills=("TRAFO",),
        required_crews_count=1,
        allow_simultaneous_in_zone=False,
        x=4.85,
        y=1.08,
    )

    win_coupled, _ = build_window_schedule(
        net, cat, "Z_F1_FULL_RADIAL", 1, "W_COUPLED", [s_trunk, s_tail]
    )
    win_sep1, _ = build_window_schedule(
        net, cat, "Z_F1_TRUNK_TRANSFER", 1, "W_SEP1", [s_trunk]
    )
    win_sep2, _ = build_window_schedule(
        net, cat, "Z_F1_TAIL_ONLY", 2, "W_SEP2", [s_tail]
    )
    assert win_coupled is not None and win_sep1 is not None and win_sep2 is not None

    plan_coupled = ExecutionPlan(
        plan_id="P_COUPLED",
        method_name="COUPLED",
        scenario_id="TRADEOFF",
        decision_day=0,
        windows=[win_coupled],
        completed_service_ids=("S_SHORT_TRUNK", "S_LONG_TAIL"),
    )
    plan_sep = ExecutionPlan(
        plan_id="P_SEP",
        method_name="SEPARATE",
        scenario_id="TRADEOFF",
        decision_day=0,
        windows=[win_sep1, win_sep2],
        completed_service_ids=("S_SHORT_TRUNK", "S_LONG_TAIL"),
    )
    f_c = verify_plan_feasibility(net, [s_trunk, s_tail], cat, plan_coupled)
    f_s = verify_plan_feasibility(net, [s_trunk, s_tail], cat, plan_sep)
    m_c = compute_plan_metrics(net, [s_trunk, s_tail], plan_coupled, f_c)
    m_s = compute_plan_metrics(net, [s_trunk, s_tail], plan_sep, f_s)

    # O agrupamento em Z_F1_FULL_RADIAL reduz janelas (1 < 2), mas aumenta clientes-hora!
    assert m_c.interruption_windows_count == 1
    assert m_s.interruption_windows_count == 2
    assert m_c.consumer_hours_total > m_s.consumer_hours_total


def test_all_dev_and_eval_scenarios_feasible_and_complete() -> None:
    """Executa todos os cenários DEV e EVAL e comprova 100% de factibilidade independente."""
    for seed in DEV_SEEDS:
        sc = generate_scenario_instance(seed, "DEV")
        res = evaluate_scenario_all_methods(sc)
        for m in ("B0", "B1", "B2", "KGP"):
            _, feas, metrics = res[m]
            assert feas.is_feasible is True, f"Falha em {sc.scenario_id} método {m}: {feas.violations}"
            assert metrics.mandatory_completion_rate == 1.0

    for seed in EVAL_SEEDS:
        sc = generate_scenario_instance(seed, "EVAL")
        res = evaluate_scenario_all_methods(sc)
        for m in ("B0", "B1", "B2", "KGP"):
            _, feas, metrics = res[m]
            assert feas.is_feasible is True, f"Falha em {sc.scenario_id} método {m}: {feas.violations}"
            assert metrics.mandatory_completion_rate == 1.0

        # Em EVAL_05 (sem oportunidade de acoplamento), todos empatam com 3 janelas:
        if seed == 202605:
            assert res["B0"][2].interruption_windows_count == 3
            assert res["B1"][2].interruption_windows_count == 3
            assert res["B2"][2].interruption_windows_count == 3
            assert res["KGP"][2].interruption_windows_count == 3

