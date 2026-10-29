"""Gerador de cenários, reavaliação sintética (DEV/EVAL conhecidos) e artefatos auditáveis do KGP.

Implementa:
- Rede elétrica de referência multi-alimentador com chaves NF/NA, fases, ativos em
  barras e trechos (`branch_id`) e zonas validadas;
- Geração reprodutível de cenários com separação estrita entre sementes de
  desenvolvimento (DEV) e avaliação conhecida (EVAL), sem calibração artificial para 1,7%;
- Execução comparativa de B0, B1, B2 e KGP submetidos ao mesmo verificador
  independente (`feasibility.py`) após round-trip de serialização JSON;
- Exportação de CSV, JSON, gráfico comparativo 16:9 em fundo branco, manifesto
  SHA-256 e relatório técnico completo (`reports/RELATORIO_EXECUCAO_KGP.md`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import datetime
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
from typing import Any, Dict, List, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .baselines import (
    run_baseline_b0_individual,
    run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy,
)
from .contracts import (
    ConsumerUnit,
    CrewResource,
    CrewScheduleInterval,
    ExecutionPlan,
    FeasibilityReport,
    GridAsset,
    GridBranch,
    GridNetwork,
    GridNode,
    GridSwitch,
    IsolationZoneSpec,
    LifecycleConflictAlert,
    ManeuverStage,
    ManeuverStepResult,
    PlanMetrics,
    PlannedInterruptionWindow,
    ResourceCatalog,
    ServiceOrder,
    ServiceType,
    SwitchState,
)
from .feasibility import verify_plan_feasibility
from .metrics import compute_plan_metrics
from .optimizer import (
    ObjectiveWeights,
    compute_exact_plan_objective,
    run_kgp_optimizer,
)


DEV_SEEDS: Tuple[int, ...] = (101, 102, 103, 104)
EVAL_SEEDS: Tuple[int, ...] = (
    202601,
    202602,
    202603,
    202604,
    202605,
    202606,
    202607,
    202608,
)


@dataclass
class ScenarioInstance:
    """Instância completa de cenário experimental (rede, carteira, recursos e metadados)."""

    scenario_id: str
    split: str
    seed: int
    regime_description: str
    decision_day: int
    network: GridNetwork
    services: List[ServiceOrder]
    catalog: ResourceCatalog


def roundtrip_plan_via_json(plan: ExecutionPlan) -> ExecutionPlan:
    """Verify the exported bytes through a strict typed JSON read."""
    from .contracts import plan_from_json
    return plan_from_json(json.dumps(asdict(plan), ensure_ascii=False, allow_nan=False))


def build_reference_network() -> GridNetwork:
    """Constrói a rede elétrica de referência multi-alimentador (13,8 kV) documentada.

    Topologia:
    - Alimentador F1 (SUB_01 -> N_A1_1 -> N_A1_2 -> N_A1_3 -> N_A1_4 -> N_A1_5)
      estendendo-se de x=0.0 a x=4.8 km ao longo de y=1.0 km.
    - Alimentador F2 (SUB_02 -> N_A2_1 -> N_A2_2 -> N_A2_3 -> N_A2_4)
      correndo próximo a F1 em x=1.5 km (y=1.20 km, a apenas 200m de N_A1_2!),
      e possuindo chave NA de interligação SW_TIE_12 entre N_A2_4 e N_A1_4.
    - Alimentador F3 (SUB_03 -> N_A3_1 -> N_A3_2 -> N_A3_3) em eixo sul (y=-1.5 km).
    """
    nodes: Dict[str, GridNode] = {
        "SUB_01": GridNode("SUB_01", "Subestação SE-1 (Fonte F1)", "F1", 0.0, 1.0, ("A", "B", "C"), True, True, 13.8, 0.0),
        "N_A1_1": GridNode("N_A1_1", "Barra Tronco F1-1", "F1", 0.8, 1.0, ("A", "B", "C"), False, True, 13.8, 120.0),
        "N_A1_2": GridNode("N_A1_2", "Barra Tronco F1-2", "F1", 1.5, 1.0, ("A", "B", "C"), False, True, 13.8, 180.0),
        "N_A1_3": GridNode("N_A1_3", "Barra Meio F1-3", "F1", 3.4, 1.0, ("A", "B", "C"), False, True, 13.8, 160.0),
        "N_A1_4": GridNode("N_A1_4", "Barra Jusante F1-4", "F1", 4.2, 1.0, ("A", "B", "C"), False, True, 13.8, 140.0),
        "N_A1_5": GridNode("N_A1_5", "Barra Fim F1-5 (Ramal AB)", "F1", 4.9, 1.1, ("A", "B"), False, True, 13.8, 90.0, distributed_gen_kw=45.0),
        "SUB_02": GridNode("SUB_02", "Subestação SE-2 (Fonte F2)", "F2", 0.0, 1.25, ("A", "B", "C"), True, True, 13.8, 0.0),
        "N_A2_1": GridNode("N_A2_1", "Barra Tronco F2-1", "F2", 0.8, 1.22, ("A", "B", "C"), False, True, 13.8, 130.0),
        "N_A2_2": GridNode("N_A2_2", "Barra Paralela F2-2", "F2", 1.5, 1.20, ("A", "B", "C"), False, True, 13.8, 170.0),
        "N_A2_3": GridNode("N_A2_3", "Barra Meio F2-3", "F2", 2.9, 1.80, ("A", "B", "C"), False, True, 13.8, 150.0),
        "N_A2_4": GridNode("N_A2_4", "Barra Interligação F2-4", "F2", 4.1, 1.60, ("A", "B", "C"), False, True, 13.8, 110.0),
        "SUB_03": GridNode("SUB_03", "Subestação SE-3 (Fonte F3)", "F3", 0.0, -1.5, ("A", "B", "C"), True, True, 13.8, 0.0),
        "N_A3_1": GridNode("N_A3_1", "Barra Tronco F3-1", "F3", 1.2, -1.5, ("A", "B", "C"), False, True, 13.8, 140.0),
        "N_A3_2": GridNode("N_A3_2", "Barra Meio F3-2", "F3", 2.8, -1.5, ("A", "B", "C"), False, True, 13.8, 160.0),
        "N_A3_3": GridNode("N_A3_3", "Barra Fim F3-3", "F3", 4.4, -1.5, ("A", "B", "C"), False, True, 13.8, 100.0),
    }

    branches: Dict[str, GridBranch] = {
        "BR_A1_01": GridBranch("BR_A1_01", "SUB_01", "N_A1_1", "F1", ("A", "B", "C"), True, 0.08, 0.12, 4500.0, 0.8),
        "BR_A1_12": GridBranch("BR_A1_12", "N_A1_1", "N_A1_2", "F1", ("A", "B", "C"), True, 0.10, 0.15, 4200.0, 0.7),
        "BR_A1_23": GridBranch("BR_A1_23", "N_A1_2", "N_A1_3", "F1", ("A", "B", "C"), True, 0.22, 0.28, 3800.0, 1.9),
        "BR_A1_34": GridBranch("BR_A1_34", "N_A1_3", "N_A1_4", "F1", ("A", "B", "C"), True, 0.12, 0.16, 3500.0, 0.8),
        "BR_A1_45": GridBranch("BR_A1_45", "N_A1_4", "N_A1_5", "F1", ("A", "B"), True, 0.18, 0.20, 2200.0, 0.7),
        "BR_A2_01": GridBranch("BR_A2_01", "SUB_02", "N_A2_1", "F2", ("A", "B", "C"), True, 0.08, 0.12, 4500.0, 0.8),
        "BR_A2_12": GridBranch("BR_A2_12", "N_A2_1", "N_A2_2", "F2", ("A", "B", "C"), True, 0.10, 0.14, 4200.0, 0.7),
        "BR_A2_23": GridBranch("BR_A2_23", "N_A2_2", "N_A2_3", "F2", ("A", "B", "C"), True, 0.18, 0.22, 3800.0, 1.5),
        "BR_A2_34": GridBranch("BR_A2_34", "N_A2_3", "N_A2_4", "F2", ("A", "B", "C"), True, 0.15, 0.19, 3600.0, 1.3),
        "BR_TIE_12": GridBranch("BR_TIE_12", "N_A2_4", "N_A1_4", "F1_F2", ("A", "B", "C"), True, 0.14, 0.18, 3200.0, 0.6),
        "BR_A3_01": GridBranch("BR_A3_01", "SUB_03", "N_A3_1", "F3", ("A", "B", "C"), True, 0.09, 0.13, 4200.0, 1.2),
        "BR_A3_12": GridBranch("BR_A3_12", "N_A3_1", "N_A3_2", "F3", ("A", "B", "C"), True, 0.16, 0.21, 3800.0, 1.6),
        "BR_A3_23": GridBranch("BR_A3_23", "N_A3_2", "N_A3_3", "F3", ("A", "B", "C"), True, 0.18, 0.22, 3200.0, 1.6),
    }

    switches: Dict[str, GridSwitch] = {
        "SW_A1_1": GridSwitch("SW_A1_1", "BR_A1_12", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A1_2": GridSwitch("SW_A1_2", "BR_A1_23", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A1_3": GridSwitch("SW_A1_3", "BR_A1_34", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A1_4": GridSwitch("SW_A1_4", "BR_A1_45", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A2_1": GridSwitch("SW_A2_1", "BR_A2_12", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A2_2": GridSwitch("SW_A2_2", "BR_A2_23", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_TIE_12": GridSwitch("SW_TIE_12", "BR_TIE_12", SwitchState.OPEN, SwitchState.OPEN, True, 0.15),
        "SW_A3_1": GridSwitch("SW_A3_1", "BR_A3_12", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
        "SW_A3_2": GridSwitch("SW_A3_2", "BR_A3_23", SwitchState.CLOSED, SwitchState.CLOSED, True, 0.15),
    }

    assets: Dict[str, GridAsset] = {
        "AST_A1_2A": GridAsset("AST_A1_2A", "N_A1_2", None, "F1", "ESTRUTURA_MT", ("A", "B", "C"), True, 1.5, 1.0),
        "AST_A1_2B": GridAsset("AST_A1_2B", "N_A1_2", None, "F1", "TRANSFORMADOR", ("A", "B", "C"), True, 1.55, 1.02),
        "AST_BR_A1_23": GridAsset("AST_BR_A1_23", None, "BR_A1_23", "F1", "VAO_REDE_PODA", ("A", "B", "C"), True, 2.45, 1.0),
        "AST_A1_3A": GridAsset("AST_A1_3A", "N_A1_3", None, "F1", "ESTRUTURA_MT", ("A", "B", "C"), True, 3.4, 1.0),
        "AST_A1_3B": GridAsset("AST_A1_3B", "N_A1_3", None, "F1", "TRANSFORMADOR", ("A", "B", "C"), True, 3.45, 0.98),
        "AST_A1_4A": GridAsset("AST_A1_4A", "N_A1_4", None, "F1", "REGULADOR_TENSAO", ("A", "B", "C"), True, 4.2, 1.0),
        "AST_A1_5A": GridAsset("AST_A1_5A", "N_A1_5", None, "F1", "VAO_REDE_PODA", ("A", "B"), True, 4.9, 1.1),
        "AST_A1_5B": GridAsset("AST_A1_5B", "N_A1_5", None, "F1", "TRANSFORMADOR", ("A", "B"), True, 4.85, 1.08),
        "AST_A2_2A": GridAsset("AST_A2_2A", "N_A2_2", None, "F2", "ESTRUTURA_MT", ("A", "B", "C"), True, 1.5, 1.20),
        "AST_A2_3A": GridAsset("AST_A2_3A", "N_A2_3", None, "F2", "VAO_REDE_PODA", ("A", "B", "C"), True, 2.9, 1.80),
        "AST_A2_4A": GridAsset("AST_A2_4A", "N_A2_4", None, "F2", "TRANSFORMADOR", ("A", "B", "C"), True, 4.1, 1.60),
        "AST_A3_2A": GridAsset("AST_A3_2A", "N_A3_2", None, "F3", "ESTRUTURA_MT", ("A", "B", "C"), True, 2.8, -1.5),
        "AST_A3_3A": GridAsset("AST_A3_3A", "N_A3_3", None, "F3", "TRANSFORMADOR", ("A", "B", "C"), True, 4.4, -1.5),
        "AST_A3_3B": GridAsset("AST_A3_3B", "N_A3_3", None, "F3", "VAO_REDE_PODA", ("A", "B", "C"), True, 4.3, -1.48),
    }

    consumers: Dict[str, ConsumerUnit] = {}
    node_consumer_counts = {
        "N_A1_1": 10,
        "N_A1_2": 22,
        "N_A1_3": 20,
        "N_A1_4": 18,
        "N_A1_5": 12,
        "N_A2_1": 12,
        "N_A2_2": 24,
        "N_A2_3": 18,
        "N_A2_4": 14,
        "N_A3_1": 12,
        "N_A3_2": 20,
        "N_A3_3": 16,
    }
    for n_id, count in node_consumer_counts.items():
        node_phases = nodes[n_id].phases
        for idx in range(count):
            ph = node_phases[idx % len(node_phases)]
            cid = f"CONS_{n_id}_{idx + 1:02d}"
            c_class = "COMERCIAL" if idx % 5 == 0 else "RESIDENCIAL"
            crit = 2 if idx == 0 else 1
            consumers[cid] = ConsumerUnit(
                consumer_id=cid,
                node_id=n_id,
                asset_id=None,
                phase=ph,
                consumer_class=c_class,
                criticality=crit,
                avg_demand_kw=3.2 if c_class == "COMERCIAL" else 1.8,
            )

    isolation_zones: Dict[str, IsolationZoneSpec] = {
        "Z_F1_TRUNK_TRANSFER": IsolationZoneSpec(
            zone_id="Z_F1_TRUNK_TRANSFER",
            name="Isolamento Tronco F1 (N_A1_2 e N_A1_3) com socorro NA para N_A1_4/N_A1_5",
            feeder_id="F1",
            open_switches=("SW_A1_1", "SW_A1_3"),
            tie_switches_closed=("SW_TIE_12",),
            prep_duration_hours=0.30,
            recomp_duration_hours=0.25,
            requires_transfer_validation=True,
            transfer_params_complete=True,
            max_transfer_voltage_drop_pct=5.0,
        ),
        "Z_F1_FULL_RADIAL": IsolationZoneSpec(
            zone_id="Z_F1_FULL_RADIAL",
            name="Desligamento Radial Completo F1 a jusante de SW_A1_1 (sem socorro)",
            feeder_id="F1",
            open_switches=("SW_A1_1",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
            requires_transfer_validation=False,
        ),
        "Z_F1_MID_SECTION": IsolationZoneSpec(
            zone_id="Z_F1_MID_SECTION",
            name="Isolamento Seção Média F1 (N_A1_3) com socorro NA para N_A1_4/N_A1_5",
            feeder_id="F1",
            open_switches=("SW_A1_2", "SW_A1_3"),
            tie_switches_closed=("SW_TIE_12",),
            prep_duration_hours=0.30,
            recomp_duration_hours=0.25,
            requires_transfer_validation=True,
            transfer_params_complete=True,
        ),
        "Z_F1_TAIL_ONLY": IsolationZoneSpec(
            zone_id="Z_F1_TAIL_ONLY",
            name="Isolamento Fim de Linha F1 (N_A1_5)",
            feeder_id="F1",
            open_switches=("SW_A1_4",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
        "Z_F1_DISTAL": IsolationZoneSpec(
            zone_id="Z_F1_DISTAL",
            name="Isolamento Seção Distal F1 (N_A1_4 e N_A1_5)",
            feeder_id="F1",
            open_switches=("SW_A1_3",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
        "Z_F2_TRUNK": IsolationZoneSpec(
            zone_id="Z_F2_TRUNK",
            name="Isolamento Tronco F2 (N_A2_2 a N_A2_4)",
            feeder_id="F2",
            open_switches=("SW_A2_1",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
        "Z_F2_DISTAL": IsolationZoneSpec(
            zone_id="Z_F2_DISTAL",
            name="Isolamento Seção Distal F2 (N_A2_3 e N_A2_4)",
            feeder_id="F2",
            open_switches=("SW_A2_2",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
        "Z_F3_MID_TAIL": IsolationZoneSpec(
            zone_id="Z_F3_MID_TAIL",
            name="Isolamento Tronco Médio/Fim F3 (N_A3_2 e N_A3_3)",
            feeder_id="F3",
            open_switches=("SW_A3_1",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
        "Z_F3_TAIL_ONLY": IsolationZoneSpec(
            zone_id="Z_F3_TAIL_ONLY",
            name="Isolamento Fim de Linha F3 (N_A3_3)",
            feeder_id="F3",
            open_switches=("SW_A3_2",),
            tie_switches_closed=(),
            prep_duration_hours=0.20,
            recomp_duration_hours=0.20,
        ),
    }

    return GridNetwork(
        network_id="CEMIG_D07_REF_MULTIFEEDER_13K8V",
        version="2026.10.28-rev2",
        valid_from_day=0,
        coordinate_system="CARTESIAN_KM",
        origin="Rede de Referência Sintética Multi-Alimentador 13,8 kV (3 alimentadores, 15 barras, 9 chaves NF/NA, 14 ativos em barras e trechos, 198 consumidores)",
        license="CC-BY-4.0 (Demonstrador Científico Independente KGP - Desafio 07)",
        changelog=[
            "2026-10-28 (rev1): Criação da rede multi-alimentador com interligação NA (SW_TIE_12) e ramal bifásico com GD.",
            "2026-10-28 (rev2): Inclusão de ativo vinculado a trecho (AST_BR_A1_23 em BR_A1_23) e validação conservadora de extremidades.",
        ],
        nodes=nodes,
        branches=branches,
        switches=switches,
        assets=assets,
        consumers=consumers,
        isolation_zones=isolation_zones,
        allow_dg_islanding=False,
    )


def _build_default_catalog() -> ResourceCatalog:
    crews = {
        "EQ_PESADA_01": CrewResource(
            crew_id="EQ_PESADA_01",
            name="Equipe Manutenção Pesada / Obras 01",
            skills=("MT_PESADA", "TRAFO", "MANUT_GERAL", "PODA"),
            available_days=(1, 2, 3, 4, 5),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=0.0,
            base_y=0.0,
            speed_kmh=35.0,
            prep_hours=0.20,
        ),
        "EQ_MANUT_02": CrewResource(
            crew_id="EQ_MANUT_02",
            name="Equipe Manutenção e Poda 02",
            skills=("MANUT_GERAL", "PODA", "TRAFO", "REGULADOR"),
            available_days=(2, 3, 4, 5),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=0.0,
            base_y=0.0,
            speed_kmh=35.0,
            prep_hours=0.20,
        ),
        "EQ_ESPEC_03": CrewResource(
            crew_id="EQ_ESPEC_03",
            name="Equipe Especializada Regulação / Obras 03",
            skills=("MT_PESADA", "REGULADOR", "MANUT_GERAL"),
            available_days=(1, 3, 4, 5),
            shift_start_hour=8.0,
            shift_end_hour=17.0,
            base_x=0.0,
            base_y=0.0,
            speed_kmh=35.0,
            prep_hours=0.20,
        ),
    }
    materials = {
        "ISOLADOR_15KV": 60.0,
        "CRUZETA_CONCRETO": 25.0,
        "CONECTOR_CUNHA": 120.0,
        "KIT_TRAFO": 12.0,
        "ELO_FUSIVEL": 80.0,
    }
    return ResourceCatalog(
        crews=crews,
        materials_inventory=materials,
        planning_days=(1, 2, 3, 4, 5),
        window_start_hour=8.0,
        window_end_hour=17.0,
        max_window_duration_hours=8.0,
    )


def generate_scenario_instance(seed: int, split: str) -> ScenarioInstance:
    """Gera uma instância de cenário reprodutível sem calibrar para 1,7%."""
    rng = random.Random(seed)
    network = build_reference_network()
    catalog = _build_default_catalog()
    decision_day = 0

    if seed == 202601:
        scenario_id = "EVAL_01_TOPO_DISTANT_AND_PARALLEL"
        desc = (
            "Serviços distantes (1,9 km) na mesma zona pós-transferência (Z_F1_TRUNK_TRANSFER) "
            "e serviço vizinho a 200m em alimentador distinto (F2), além de ganho de paralelismo no dia 2."
        )
        services = [
            ServiceOrder(
                service_id="S01_F1_NEAR",
                title="Substituição de isoladores na estrutura N_A1_2",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A1_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=3,
                duration_hours=2.2,
                required_skills=("MANUT_GERAL",),
                required_crews_count=1,
                required_materials={"ISOLADOR_15KV": 6.0},
                priority=1,
                x=1.5,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S02_F1_FAR",
                title="Reaperto e manutenção de transformador em N_A1_3 (1,9 km distante)",
                service_type=ServiceType.TRANSFORMADOR,
                target_assets=("AST_A1_3B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=3,
                duration_hours=2.0,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                priority=2,
                x=3.45,
                y=0.98,
            ),
            ServiceOrder(
                service_id="S03_F2_PARALLEL",
                title="Manutenção preventiva em N_A2_2 (a 200m de S01, porém no alimentador F2)",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A2_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=1.8,
                required_skills=("MANUT_GERAL",),
                required_crews_count=1,
                required_materials={"ISOLADOR_15KV": 3.0},
                priority=2,
                x=1.5,
                y=1.20,
            ),
            ServiceOrder(
                service_id="S04_F2_DISTAL",
                title="Poda preventiva em N_A2_3 (mesma zona tronco F2 a 1,5 km)",
                service_type=ServiceType.PODA,
                target_assets=("AST_A2_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=1.6,
                required_skills=("PODA",),
                required_crews_count=1,
                required_materials={"CONECTOR_CUNHA": 4.0},
                priority=3,
                x=2.9,
                y=1.80,
            ),
        ]
    elif seed == 202602:
        scenario_id = "EVAL_02_TIE_TRANSFER_AND_CREW_SYNC"
        desc = (
            "Três serviços em F1 com transferência via chave NA (SW_TIE_12) e sincronização "
            "de equipes entre dias para execução paralela vs. sequencial."
        )
        services = [
            ServiceOrder(
                service_id="S21_F1_STRUCT",
                title="Adequação de estrutura MT em N_A1_2",
                service_type=ServiceType.OBRA,
                target_assets=("AST_A1_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=3,
                duration_hours=2.5,
                required_skills=("MT_PESADA",),
                required_crews_count=1,
                required_materials={"CRUZETA_CONCRETO": 2.0},
                priority=1,
                x=1.5,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S22_F1_TRAFO",
                title="Adequação de transformador em N_A1_2",
                service_type=ServiceType.TRANSFORMADOR,
                target_assets=("AST_A1_2B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=3,
                duration_hours=2.2,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                priority=1,
                x=1.55,
                y=1.02,
            ),
            ServiceOrder(
                service_id="S23_F1_MID",
                title="Inspeção termográfica corretiva em N_A1_3 (1,9 km)",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A1_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=3,
                duration_hours=2.0,
                required_skills=("MANUT_GERAL",),
                required_crews_count=1,
                required_materials={"CONECTOR_CUNHA": 6.0},
                priority=2,
                x=3.4,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S24_F3_TAIL",
                title="Manutenção de transformador no fim do F3",
                service_type=ServiceType.TRANSFORMADOR,
                target_assets=("AST_A3_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=1.8,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                priority=3,
                x=4.4,
                y=-1.5,
            ),
        ]
    elif seed == 202603:
        scenario_id = "EVAL_03_PRECEDENCE_AND_RELEASE_DATES"
        desc = (
            "Serviços com liberação temporal defasada (ready_at=2) e precedência técnica; "
            "esperar pelo dia 2 permite acoplar os serviços em janela única sem reincidência."
        )
        services = [
            ServiceOrder(
                service_id="S31_F3_PREP",
                title="Substituição de estrutura base em N_A3_2",
                service_type=ServiceType.OBRA,
                target_assets=("AST_A3_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=2.0,
                required_skills=("MT_PESADA",),
                required_crews_count=1,
                required_materials={"CRUZETA_CONCRETO": 2.0},
                priority=1,
                x=2.8,
                y=-1.5,
            ),
            ServiceOrder(
                service_id="S32_F3_TRAFO",
                title="Reconexão e ajuste de transformador em N_A3_3 (liberado dia 2)",
                service_type=ServiceType.TRANSFORMADOR,
                target_assets=("AST_A3_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=4,
                duration_hours=2.0,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                precedence_service_ids=("S31_F3_PREP",),
                priority=2,
                x=4.4,
                y=-1.5,
            ),
            ServiceOrder(
                service_id="S33_F3_PODA",
                title="Poda de segurança no vão N_A3_3 (liberado dia 2)",
                service_type=ServiceType.PODA,
                target_assets=("AST_A3_3B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=4,
                duration_hours=1.8,
                required_skills=("PODA",),
                required_crews_count=1,
                required_materials={"ELO_FUSIVEL": 2.0},
                priority=2,
                x=4.3,
                y=-1.48,
            ),
            ServiceOrder(
                service_id="S34_F1_TAIL",
                title="Poda no ramal final de F1 (N_A1_5, liberado dia 2)",
                service_type=ServiceType.PODA,
                target_assets=("AST_A1_5A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=5,
                duration_hours=1.5,
                required_skills=("PODA",),
                required_crews_count=1,
                required_materials={"CONECTOR_CUNHA": 2.0},
                priority=3,
                x=4.9,
                y=1.1,
            ),
        ]
    elif seed == 202604:
        scenario_id = "EVAL_04_FREQ_VS_DURATION_TRADEOFF"
        desc = (
            "Trade-off entre frequência e clientes-hora: acoplar serviço longo do fim de linha "
            "na zona tronco sem transferência aumenta clientes-hora, enquanto usar transferência "
            "ou zonas dedicadas equilibra impacto."
        )
        services = [
            ServiceOrder(
                service_id="S41_F1_MID_A",
                title="Manutenção preventiva na estrutura N_A1_3",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A1_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=2.0,
                required_skills=("MANUT_GERAL",),
                required_crews_count=1,
                required_materials={"ISOLADOR_15KV": 3.0},
                priority=1,
                x=3.4,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S42_F1_TRUNK_B",
                title="Substituição de cruzeta em N_A1_2 (1,9 km de S41)",
                service_type=ServiceType.OBRA,
                target_assets=("AST_A1_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=4,
                duration_hours=2.2,
                required_skills=("MT_PESADA",),
                required_crews_count=1,
                required_materials={"CRUZETA_CONCRETO": 2.0},
                priority=2,
                x=1.5,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S43_F1_TAIL_LONG",
                title="Obra pesada no fim do ramal N_A1_5",
                service_type=ServiceType.OBRA,
                target_assets=("AST_A1_5B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=5,
                duration_hours=3.2,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 2.0},
                priority=2,
                x=4.85,
                y=1.08,
            ),
        ]
    elif seed == 202605:
        scenario_id = "EVAL_05_DISJOINT_NO_OPPORTUNITY"
        desc = (
            "Cenário de controle sem oportunidade de acoplamento (alimentadores distintos e "
            "janelas temporais estritas disjuntas): B0, B1, B2 e KGP devem empatar."
        )
        services = [
            ServiceOrder(
                service_id="S51_F1_TAIL",
                title="Serviço isolado no fim de F1 (prazo estrito dia 1)",
                service_type=ServiceType.PODA,
                target_assets=("AST_A1_5A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=1,
                duration_hours=2.0,
                required_skills=("PODA",),
                required_crews_count=1,
                required_materials={"CONECTOR_CUNHA": 2.0},
                priority=1,
                x=4.9,
                y=1.1,
            ),
            ServiceOrder(
                service_id="S52_F2_DISTAL",
                title="Serviço isolado em F2 (liberado e com prazo no dia 2)",
                service_type=ServiceType.TRANSFORMADOR,
                target_assets=("AST_A2_4A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=2,
                duration_hours=2.0,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                priority=1,
                x=4.1,
                y=1.60,
            ),
            ServiceOrder(
                service_id="S53_F3_TAIL",
                title="Serviço isolado em F3 (liberado e com prazo no dia 3)",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A3_3A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=3,
                deadline_day=3,
                duration_hours=2.0,
                required_skills=("MANUT_GERAL",),
                required_crews_count=1,
                required_materials={"ISOLADOR_15KV": 3.0},
                priority=1,
                x=4.4,
                y=-1.5,
            ),
        ]
    elif seed == 202606:
        scenario_id = "EVAL_06_LIFECYCLE_AND_TEMPORAL_FILTER"
        desc = (
            "Conflito manutenção-substituição no mesmo ativo (AST_A1_3B) preservando ordens "
            "obrigatórias com alerta + nota futura (created_at=2 > decision_day=0) filtrada."
        )
        services = [
            ServiceOrder(
                service_id="S61_MAINT_TRAFO",
                title="Manutenção preventiva periódica no transformador AST_A1_3B",
                service_type=ServiceType.PREVENTIVA,
                target_assets=("AST_A1_3B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=1.5,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 1.0},
                maintains_asset_ids=("AST_A1_3B",),
                priority=2,
                x=3.45,
                y=0.98,
            ),
            ServiceOrder(
                service_id="S62_REPL_TRAFO",
                title="Substituição por sobrecarga do mesmo transformador AST_A1_3B",
                service_type=ServiceType.SUBSTITUICAO,
                target_assets=("AST_A1_3B",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=2,
                deadline_day=4,
                duration_hours=2.2,
                required_skills=("TRAFO",),
                required_crews_count=1,
                required_materials={"KIT_TRAFO": 2.0},
                replaces_asset_ids=("AST_A1_3B",),
                priority=1,
                x=3.45,
                y=0.98,
            ),
            ServiceOrder(
                service_id="S63_F1_STRUCT",
                title="Manutenção de estrutura em N_A1_2 (mesma zona Z_F1_TRUNK_TRANSFER)",
                service_type=ServiceType.OBRA,
                target_assets=("AST_A1_2A",),
                mandatory=True,
                created_at_day=0,
                ready_at_day=1,
                deadline_day=4,
                duration_hours=1.8,
                required_skills=("MT_PESADA",),
                required_crews_count=1,
                required_materials={"CRUZETA_CONCRETO": 1.0},
                priority=1,
                x=1.5,
                y=1.0,
            ),
            ServiceOrder(
                service_id="S64_FUTURE_LEAK_TEST",
                title="Nota emergencial criada no dia 2 (após decisão no dia 0)",
                service_type=ServiceType.PODA,
                target_assets=("AST_A2_3A",),
                mandatory=True,
                created_at_day=2,
                ready_at_day=2,
                deadline_day=5,
                duration_hours=1.5,
                required_skills=("PODA",),
                required_crews_count=1,
                required_materials={"CONECTOR_CUNHA": 2.0},
                priority=1,
                x=2.9,
                y=1.80,
            ),
        ]
    else:
        is_dev = split == "DEV"
        scenario_id = (
            f"EVAL_{seed - 202600:02d}_STOCHASTIC_PORTFOLIO"
            if not is_dev
            else f"DEV_{seed}_STOCHASTIC_PORTFOLIO"
        )
        desc = (
            f"Carteira sintética estocástica ({split}, seed={seed}) gerada a partir de "
            "distribuição documentada de ativos, prazos e especialidades."
        )
        asset_pool = [
            ("AST_A1_2A", ServiceType.OBRA, ("MT_PESADA",), {"CRUZETA_CONCRETO": 1.0}),
            ("AST_A1_2B", ServiceType.TRANSFORMADOR, ("TRAFO",), {"KIT_TRAFO": 1.0}),
            ("AST_A1_3A", ServiceType.PREVENTIVA, ("MANUT_GERAL",), {"ISOLADOR_15KV": 3.0}),
            ("AST_A1_3B", ServiceType.TRANSFORMADOR, ("TRAFO",), {"KIT_TRAFO": 1.0}),
            ("AST_A1_4A", ServiceType.TENSAO, ("REGULADOR",), {"CONECTOR_CUNHA": 4.0}),
            ("AST_A1_5A", ServiceType.PODA, ("PODA",), {"ELO_FUSIVEL": 2.0}),
            ("AST_A2_2A", ServiceType.PREVENTIVA, ("MANUT_GERAL",), {"ISOLADOR_15KV": 3.0}),
            ("AST_A2_3A", ServiceType.PODA, ("PODA",), {"CONECTOR_CUNHA": 3.0}),
            ("AST_A2_4A", ServiceType.TRANSFORMADOR, ("TRAFO",), {"KIT_TRAFO": 1.0}),
            ("AST_A3_2A", ServiceType.OBRA, ("MT_PESADA",), {"CRUZETA_CONCRETO": 1.0}),
            ("AST_A3_3A", ServiceType.TRANSFORMADOR, ("TRAFO",), {"KIT_TRAFO": 1.0}),
            ("AST_A3_3B", ServiceType.PODA, ("PODA",), {"ELO_FUSIVEL": 2.0}),
        ]
        n_pick = 5 if not is_dev else 4
        chosen_items = rng.sample(asset_pool, k=n_pick)
        services = []
        for idx, (aid, stype, skills, mats) in enumerate(chosen_items):
            ast = network.assets[aid]
            r_day = rng.choice([1, 1, 2])
            d_day = rng.choice([3, 4, 5])
            dur = round(rng.uniform(1.4, 2.4), 1)
            prio = rng.choice([1, 2, 3])
            services.append(
                ServiceOrder(
                    service_id=f"S_{seed}_{idx + 1:02d}",
                    title=f"Serviço {stype.value} em {aid}",
                    service_type=stype,
                    target_assets=(aid,),
                    mandatory=True,
                    created_at_day=0,
                    ready_at_day=r_day,
                    deadline_day=max(r_day + 1, d_day),
                    duration_hours=dur,
                    required_skills=skills,
                    required_crews_count=1,
                    required_materials=mats,
                    priority=prio,
                    x=ast.x,
                    y=ast.y,
                )
            )

    return ScenarioInstance(
        scenario_id=scenario_id,
        split=split,
        seed=seed,
        regime_description=desc,
        decision_day=decision_day,
        network=network,
        services=services,
        catalog=catalog,
    )


def evaluate_scenario_all_methods(
    scenario: ScenarioInstance,
    b1_radius_km: float = 1.25,
) -> Dict[str, Tuple[ExecutionPlan, FeasibilityReport, PlanMetrics]]:
    """Executa B0, B1, B2 e KGP sobre o mesmo cenário e submete todos ao verificador independente após round-trip JSON."""
    plan_b0 = run_baseline_b0_individual(
        scenario.network,
        scenario.services,
        scenario.catalog,
        scenario.scenario_id,
        scenario.decision_day,
    )
    plan_b1 = run_baseline_b1_geographic(
        scenario.network,
        scenario.services,
        scenario.catalog,
        scenario.scenario_id,
        scenario.decision_day,
        radius_km=b1_radius_km,
    )
    plan_b2 = run_baseline_b2_topological_greedy(
        scenario.network,
        scenario.services,
        scenario.catalog,
        scenario.scenario_id,
        scenario.decision_day,
    )
    plan_kgp = run_kgp_optimizer(
        scenario.network,
        scenario.services,
        scenario.catalog,
        scenario.scenario_id,
        scenario.decision_day,
    )

    results: Dict[str, Tuple[ExecutionPlan, FeasibilityReport, PlanMetrics]] = {}
    for method, raw_plan in [
        ("B0", plan_b0),
        ("B1", plan_b1),
        ("B2", plan_b2),
        ("KGP", plan_kgp),
    ]:
        # Round-trip completo via JSON antes da verificação independente:
        exported_plan = roundtrip_plan_via_json(raw_plan)
        feas = verify_plan_feasibility(
            scenario.network,
            scenario.services,
            scenario.catalog,
            exported_plan,
        )
        metrics = compute_plan_metrics(
            scenario.network,
            scenario.services,
            exported_plan,
            feas,
        )
        results[method] = (exported_plan, feas, metrics)

    return results


def _bootstrap_ci_mean(
    values: Sequence[float],
    n_boot: int = 2000,
    seed: int = 42,
) -> Tuple[float, float, float]:
    """Calcula média e intervalo de confiança bootstrap 95% no nível das instâncias."""
    arr = np.array(values, dtype=float)
    if len(arr) == 0:
        return 0.0, 0.0, 0.0
    mean_val = float(np.mean(arr))
    if len(arr) == 1:
        return mean_val, mean_val, mean_val
    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot, dtype=float)
    n = len(arr)
    for b in range(n_boot):
        sample = rng.choice(arr, size=n, replace=True)
        boot_means[b] = np.mean(sample)
    lo = float(np.percentile(boot_means, 2.5))
    hi = float(np.percentile(boot_means, 97.5))
    return round(mean_val, 4), round(lo, 4), round(hi, 4)


def generate_comparative_figure(
    eval_rows: Sequence[Dict[str, Any]],
    output_path: Path,
) -> None:
    """Gera figura comparativa 16:9 em fundo branco de alta resolução (1920x1080)."""
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.labelsize": 11,
        }
    )

    methods = ["B0", "B1", "B2", "KGP"]
    colors = {
        "B0": "#64748B",
        "B1": "#D97706",
        "B2": "#2563EB",
        "KGP": "#059669",
    }

    eval_only = [
        r for r in eval_rows if r["split"] == "EVAL" and r["is_feasible"]
    ]
    scenarios = sorted(list({r["scenario_id"] for r in eval_only}))

    fig, axes = plt.subplots(2, 2, figsize=(16, 9), dpi=120)
    fig.suptitle(
        "KAPTUS GRID PLANNER (KGP — Desafio 07 Cemig): Reavaliação Sintética de Casos Conhecidos (EVAL)",
        fontsize=16,
        fontweight="bold",
        y=0.98,
    )

    ax1 = axes[0, 0]
    mean_wins = [
        np.mean([r["interruption_windows_count"] for r in eval_only if r["method_name"] == m])
        for m in methods
    ]
    mean_man = [
        np.mean([r["physical_maneuvers_count"] for r in eval_only if r["method_name"] == m])
        for m in methods
    ]
    x_pos = np.arange(len(methods))
    width = 0.36
    b1_bars = ax1.bar(
        x_pos - width / 2,
        mean_wins,
        width,
        label="Janelas de Interrupção",
        color=[colors[m] for m in methods],
        edgecolor="#1E293B",
    )
    b2_bars = ax1.bar(
        x_pos + width / 2,
        mean_man,
        width,
        label="Manobras Físicas de Chaves",
        color=[colors[m] for m in methods],
        alpha=0.45,
        edgecolor="#1E293B",
        hatch="//",
    )
    ax1.set_xticks(x_pos)
    ax1.set_xticklabels(methods, fontweight="bold")
    ax1.set_ylabel("Quantidade Média por Cenário")
    ax1.set_title("A) Janelas de Interrupção vs. Manobras Físicas (Média EVAL)")
    ax1.grid(axis="y", linestyle="--", alpha=0.4)
    ax1.legend(loc="upper right")
    for bar in b1_bars:
        h = bar.get_height()
        ax1.annotate(
            f"{h:.2f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
        )
    for bar in b2_bars:
        h = bar.get_height()
        ax1.annotate(
            f"{h:.2f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax2 = axes[0, 1]
    mean_ch = [
        np.mean([r["consumer_hours_total"] for r in eval_only if r["method_name"] == m])
        for m in methods
    ]
    bars_ch = ax2.bar(
        methods,
        mean_ch,
        color=[colors[m] for m in methods],
        edgecolor="#1E293B",
        width=0.52,
    )
    ax2.set_ylabel("Clientes-Hora (União Sem Duplicidade)")
    ax2.set_title("B) Impacto Médio em Clientes-Hora nos Consumidores (EVAL)")
    ax2.grid(axis="y", linestyle="--", alpha=0.4)
    for bar in bars_ch:
        h = bar.get_height()
        ax2.annotate(
            f"{h:.1f} ch",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    ax3 = axes[1, 0]
    short_scen = ["_".join(s.split("_")[:2]) for s in scenarios]
    sx = np.arange(len(scenarios))
    w_s = 0.20
    for idx_m, m in enumerate(methods):
        vals = [
            next(
                r["consumer_hours_total"]
                for r in eval_only
                if r["scenario_id"] == s and r["method_name"] == m
            )
            for s in scenarios
        ]
        ax3.bar(
            sx + (idx_m - 1.5) * w_s,
            vals,
            w_s,
            label=m,
            color=colors[m],
            edgecolor="#1E293B",
        )
    ax3.set_xticks(sx)
    ax3.set_xticklabels(short_scen, rotation=20, ha="right")
    ax3.set_ylabel("Clientes-Hora por Cenário")
    ax3.set_title("C) Clientes-Hora por Caso Sintético Conhecido (Ganhos, Empates e Controle)")
    ax3.grid(axis="y", linestyle="--", alpha=0.4)
    ax3.legend(ncol=4, loc="upper right")

    ax4 = axes[1, 1]
    mean_coupling = [
        100.0 * np.mean([r["coupling_rate"] for r in eval_only if r["method_name"] == m])
        for m in methods
    ]
    mean_repeat = [
        np.mean(
            [
                r["repeat_interrupted_consumers_count"]
                for r in eval_only
                if r["method_name"] == m
            ]
        )
        for m in methods
    ]
    ax4_twin = ax4.twinx()
    b_coup = ax4.bar(
        x_pos - width / 2,
        mean_coupling,
        width,
        label="Taxa de Acoplamento (%)",
        color="#0284C7",
        edgecolor="#1E293B",
    )
    b_rep = ax4_twin.bar(
        x_pos + width / 2,
        mean_repeat,
        width,
        label="Consumidores Reincidentes (2+ int.)",
        color="#DC2626",
        alpha=0.75,
        edgecolor="#1E293B",
    )
    ax4.set_xticks(x_pos)
    ax4.set_xticklabels(methods, fontweight="bold")
    ax4.set_ylabel("Janelas com 2+ Serviços (%)", color="#0284C7")
    ax4_twin.set_ylabel("Consumidores com 2+ Interrupções", color="#DC2626")
    ax4.set_ylim(0, 105)
    ax4.set_title("D) Taxa de Acoplamento (%) vs. Reincidência de Consumidores")
    ax4.grid(axis="y", linestyle="--", alpha=0.4)
    for bar in b_coup:
        h = bar.get_height()
        ax4.annotate(
            f"{h:.1f}%",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            color="#0369A1",
            fontweight="bold",
        )
    for bar in b_rep:
        h = bar.get_height()
        ax4_twin.annotate(
            f"{h:.1f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
            color="#B91C1C",
            fontweight="bold",
        )

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_full_experiment(base_dir: Path) -> Dict[str, Any]:
    """Frozen synthetic revalidation of already observed DEV/EVAL cases."""
    from .revalidation import run_revalidation
    return run_revalidation(base_dir)


if __name__ == "__main__":
    root_dir = Path(__file__).resolve().parents[2]
    run_full_experiment(root_dir)
