"""Contratos tipados de entrada, saída e rastreabilidade do KAPTUS GRID PLANNER (KGP).

Define as entidades de rede elétrica, ordens de serviço com referência temporal,
recursos/equipes, etapas de manobra, plano exportável, verificação de factibilidade
e métricas auditáveis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import json
import math
from typing import Any, Dict, List, Optional, Set, Tuple, Union, get_args, get_origin, get_type_hints


def nonfinite_paths(value: Any, path: str = "contract") -> List[str]:
    """Find non-finite numbers, including nested metadata and mutated objects."""
    if isinstance(value, float):
        return [] if math.isfinite(value) else [f"{path}: valor não finito"]
    if is_dataclass(value):
        return [e for f in fields(value) for e in nonfinite_paths(getattr(value, f.name), f"{path}.{f.name}")]
    if isinstance(value, dict):
        return [e for k, v in value.items() for e in nonfinite_paths(v, f"{path}.{k}")]
    if isinstance(value, (list, tuple, set)):
        return [e for i, v in enumerate(value) for e in nonfinite_paths(v, f"{path}[{i}]")]
    return []


class FiniteContract:
    """Dataclass construction boundary; verifier rechecks after any mutation."""

    def __post_init__(self) -> None:
        errors = nonfinite_paths(self)
        if errors:
            raise ValueError("; ".join(errors))


def _decode_typed(value: Any, expected: Any, path: str) -> Any:
    """Strict contract reader: no coercion of bools, strings or fractional days."""
    if expected is Any:
        errors = nonfinite_paths(value, path)
        if errors:
            raise ValueError("; ".join(errors))
        return value
    origin, args = get_origin(expected), get_args(expected)
    if origin is Union:
        for kind in args:
            try:
                return _decode_typed(value, kind, path)
            except (ValueError, TypeError):
                pass
        raise ValueError(f"{path}: tipo inválido")
    if expected is type(None):
        if value is not None:
            raise ValueError(f"{path}: esperado null")
        return None
    if isinstance(expected, type) and issubclass(expected, Enum):
        return expected(value)
    if isinstance(expected, type) and is_dataclass(expected):
        if is_dataclass(value):
            hints = get_type_hints(expected)
            for item in fields(expected):
                kind = hints[item.name]
                if isinstance(kind, type) and issubclass(kind, Enum) and not isinstance(getattr(value, item.name), kind):
                    raise ValueError(f"{path}.{item.name}: esperado enum {kind.__name__}")
        raw = {f.name: getattr(value, f.name) for f in fields(value)} if is_dataclass(value) else value
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: esperado objeto")
        known = {f.name for f in fields(expected)}
        if set(raw) - known:
            raise ValueError(f"{path}: campos desconhecidos {sorted(set(raw) - known)}")
        hints = get_type_hints(expected)
        try:
            return expected(**{k: _decode_typed(v, hints[k], f"{path}.{k}") for k, v in raw.items()})
        except TypeError as exc:
            raise ValueError(f"{path}: estrutura inválida: {exc}") from exc
    if origin in (list, tuple, set):
        if not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{path}: esperado sequência")
        if origin is tuple and args and args[-1] is not Ellipsis:
            if len(value) != len(args):
                raise ValueError(f"{path}: comprimento inválido")
            return tuple(_decode_typed(v, t, path) for v, t in zip(value, args))
        decoded = [_decode_typed(v, args[0], f"{path}[{i}]") for i, v in enumerate(value)]
        return origin(decoded)
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"{path}: esperado objeto")
        return {_decode_typed(k, args[0], path): _decode_typed(v, args[1], f"{path}.{k}") for k, v in value.items()}
    if expected is float:
        try:
            finite = type(value) in (int, float) and math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"{path}: esperado número finito")
        return float(value)
    if type(value) is not expected:
        raise ValueError(f"{path}: esperado {expected.__name__}")
    return value


def strict_json_loads(raw: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"JSON não finito: {value}")

    def unique_keys(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON com chave duplicada: {key}")
            result[key] = value
        return result

    data = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_keys)
    errors = nonfinite_paths(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data


def plan_from_json(raw: str) -> ExecutionPlan:
    """Read the actual exported JSON using the same typed contracts, strictly."""
    return _decode_typed(strict_json_loads(raw), ExecutionPlan, "plan")


class SwitchState(str, Enum):
    """Estado operacional de uma chave seccionadora ou religador."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"


class ServiceType(str, Enum):
    """Tipos de serviços da carteira heterogênea de distribuição."""

    PREVENTIVA = "PREVENTIVA"
    OBRA = "OBRA"
    PODA = "PODA"
    TRANSFORMADOR = "TRANSFORMADOR"
    TENSAO = "TENSAO"
    SUBSTITUICAO = "SUBSTITUICAO"


class ManeuverStage(str, Enum):
    """Etapas operacionais de uma intervenção programada."""

    ISOLATION = "ISOLATION"
    TRANSFER = "TRANSFER"
    EXECUTION = "EXECUTION"
    RECOMPOSITION = "RECOMPOSITION"


@dataclass(frozen=True)
class GridNode(FiniteContract):
    """Barra/nó da rede elétrica com faseamento, coordenadas e estado em serviço."""

    node_id: str
    name: str
    feeder_id: str
    x: float
    y: float
    phases: Tuple[str, ...] = ("A", "B", "C")
    is_source: bool = False
    in_service: bool = True
    nominal_voltage_kv: float = 13.8
    load_kw: float = 0.0
    distributed_gen_kw: float = 0.0


@dataclass(frozen=True)
class GridBranch(FiniteContract):
    """Trecho elétrico entre dois nós com parâmetros de impedância e limite térmico."""

    branch_id: str
    from_node: str
    to_node: str
    feeder_id: str
    phases: Tuple[str, ...] = ("A", "B", "C")
    in_service: bool = True
    r_ohm: float = 0.15
    x_ohm: float = 0.20
    max_capacity_kw: float = 4000.0
    length_km: float = 0.5


@dataclass(frozen=True)
class GridSwitch(FiniteContract):
    """Chave de manobra (NF ou NA) associada a um trecho da rede."""

    switch_id: str
    branch_id: str
    normal_state: SwitchState
    current_state: SwitchState
    is_maneuverable: bool = True
    maneuver_duration_hours: float = 0.15
    in_service: bool = True


@dataclass(frozen=True)
class GridAsset(FiniteContract):
    """Ativo de trabalho (estrutura, transformador, vão, regulador) vinculado à rede."""

    asset_id: str
    node_id: Optional[str]
    branch_id: Optional[str]
    feeder_id: str
    asset_type: str
    phases: Tuple[str, ...] = ("A", "B", "C")
    in_service: bool = True
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class ConsumerUnit(FiniteContract):
    """Unidade consumidora conectada a um nó/ativo e conjunto de fases."""

    consumer_id: str
    node_id: str
    asset_id: Optional[str]
    phase: str = "A"
    consumer_class: str = "RESIDENCIAL"
    criticality: int = 1
    avg_demand_kw: float = 2.5
    dic_limit_hours: Optional[float] = None
    fic_limit_count: Optional[int] = None


@dataclass(frozen=True)
class IsolationZoneSpec(FiniteContract):
    """Zona de isolamento predefinida e validada com manobras e transferência opcional."""

    zone_id: str
    name: str
    feeder_id: str
    open_switches: Tuple[str, ...]
    tie_switches_closed: Tuple[str, ...] = ()
    prep_duration_hours: float = 0.25
    recomp_duration_hours: float = 0.25
    requires_transfer_validation: bool = False
    transfer_params_complete: bool = True
    max_transfer_voltage_drop_pct: float = 5.0


@dataclass
class GridNetwork(FiniteContract):
    """Grafo completo da rede de distribuição com metadados e rastreabilidade."""

    network_id: str
    version: str
    valid_from_day: int
    coordinate_system: str = "CARTESIAN_KM"
    origin: str = "Rede de Referência Sintética Multi-Alimentador KGP"
    license: str = "CC-BY-4.0 / Demonstrador Científico KGP"
    changelog: List[str] = field(default_factory=list)
    nodes: Dict[str, GridNode] = field(default_factory=dict)
    branches: Dict[str, GridBranch] = field(default_factory=dict)
    switches: Dict[str, GridSwitch] = field(default_factory=dict)
    assets: Dict[str, GridAsset] = field(default_factory=dict)
    consumers: Dict[str, ConsumerUnit] = field(default_factory=dict)
    isolation_zones: Dict[str, IsolationZoneSpec] = field(default_factory=dict)
    allow_dg_islanding: bool = False


@dataclass(frozen=True)
class ServiceOrder(FiniteContract):
    """Ordem/nota de serviço na carteira de manutenção e obras."""

    service_id: str
    title: str
    service_type: ServiceType
    target_assets: Tuple[str, ...]
    mandatory: bool
    created_at_day: int
    ready_at_day: int
    deadline_day: int
    duration_hours: float
    required_skills: Tuple[str, ...]
    required_crews_count: int = 1
    required_materials: Dict[str, float] = field(default_factory=dict)
    precedence_service_ids: Tuple[str, ...] = ()
    requires_deenergized: bool = True
    compatible_zone_ids: Tuple[str, ...] = ()
    allow_simultaneous_in_zone: bool = True
    prep_hours: float = 0.20
    recomp_hours: float = 0.15
    priority: int = 1
    replaces_asset_ids: Tuple[str, ...] = ()
    maintains_asset_ids: Tuple[str, ...] = ()
    scope_status: str = "ACTIVE"
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class CrewResource(FiniteContract):
    """Equipe operacional com especialidades, jornada diária e base geográfica."""

    crew_id: str
    name: str
    skills: Tuple[str, ...]
    available_days: Tuple[int, ...]
    shift_start_hour: float = 8.0
    shift_end_hour: float = 17.0
    base_x: float = 0.0
    base_y: float = 0.0
    speed_kmh: float = 35.0
    prep_hours: float = 0.20


@dataclass
class ResourceCatalog(FiniteContract):
    """Catálogo de recursos operacionais (equipes, estoque de materiais e horizonte)."""

    crews: Dict[str, CrewResource] = field(default_factory=dict)
    materials_inventory: Dict[str, float] = field(default_factory=dict)
    planning_days: Tuple[int, ...] = (1, 2, 3, 4, 5)
    window_start_hour: float = 8.0
    window_end_hour: float = 17.0
    max_window_duration_hours: float = 8.0


@dataclass(frozen=True)
class ManeuverStepResult(FiniteContract):
    """Resultado elétrico e topológico de uma etapa da intervenção."""

    step_order: int
    stage: ManeuverStage
    start_hour: float
    end_hour: float
    open_switches: Tuple[str, ...]
    closed_switches: Tuple[str, ...]
    energized_nodes: Tuple[str, ...]
    deenergized_nodes: Tuple[str, ...]
    deenergized_assets: Tuple[str, ...]
    interrupted_consumers: Tuple[str, ...]
    active_sources: Tuple[str, ...]
    transfer_feasible: bool = True
    max_loading_pct: float = 0.0
    estimated_voltage_drop_pct: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class CrewScheduleInterval(FiniteContract):
    """Alocação temporal contínua de uma equipe a um serviço dentro de uma janela."""

    crew_id: str
    service_id: str
    window_id: str
    day: int
    travel_start_hour: float
    prep_start_hour: float
    exec_start_hour: float
    exec_end_hour: float
    recomp_end_hour: float
    travel_hours: float
    x: float
    y: float


@dataclass(frozen=True)
class LifecycleConflictAlert(FiniteContract):
    """Alerta de conflito entre manutenção e substituição futura no mesmo ativo."""

    maintenance_service_id: str
    replacement_service_id: str
    asset_id: str
    recommended_precedence: Tuple[str, str]
    explanation: str


@dataclass
class PlannedInterruptionWindow(FiniteContract):
    """Janela programada de intervenção associada a uma zona de isolamento e data."""

    window_id: str
    day: int
    zone_id: str
    service_ids: Tuple[str, ...]
    start_hour: float
    end_hour: float
    work_start_hour: float
    work_end_hour: float
    maneuver_steps: Tuple[ManeuverStepResult, ...]
    crew_intervals: Tuple[CrewScheduleInterval, ...]
    materials_used: Dict[str, float]
    physical_maneuvers_count: int
    interrupted_consumers: Tuple[str, ...]
    lifecycle_alerts: Tuple[LifecycleConflictAlert, ...] = ()
    inclusion_justifications: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionPlan(FiniteContract):
    """Plano de programação exportado por qualquer comparador (B0, B1, B2) ou KGP."""

    plan_id: str
    method_name: str
    scenario_id: str
    decision_day: int
    windows: List[PlannedInterruptionWindow] = field(default_factory=list)
    completed_service_ids: Tuple[str, ...] = ()
    unscheduled_service_ids: Tuple[str, ...] = ()
    exclusion_justifications: Dict[str, str] = field(default_factory=dict)
    lifecycle_alerts: List[LifecycleConflictAlert] = field(default_factory=list)
    unmodeled_constraints: Tuple[str, ...] = (
        "Ajustes finos de coordenação e seletividade de proteção em tempo real",
        "Aterramento temporário de campo e bloqueio mecânico (LOTO) pelo COD/Cemig",
        "Autorização operacional humana final do programador e despachante",
    )
    solver_status: str = "NOT_APPLICABLE"
    solver_objective: Optional[float] = None
    solver_bound: Optional[float] = None
    solver_gap: Optional[float] = None
    solver_time_sec: float = 0.0
    is_optimal_proven: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FeasibilityReport(FiniteContract):
    """Relatório de auditoria do verificador independente sobre um plano exportado."""

    plan_id: str
    method_name: str
    scenario_id: str
    is_feasible: bool
    mandatory_portfolio_complete: bool
    completed_mandatory_count: int
    total_mandatory_count: int
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recalculated_materials: Dict[str, float] = field(default_factory=dict)
    recalculated_consumer_hours: float = 0.0
    recalculated_windows_count: int = 0
    recalculated_physical_maneuvers: int = 0
    recalculated_repeat_consumers: int = 0
    recalculated_unique_consumers: int = 0
    recalculated_total_interruptions: int = 0
    recalculated_completed_services: Tuple[str, ...] = ()
    isolation_verified: bool = True


@dataclass
class PlanMetrics(FiniteContract):
    """Métricas exatas de avaliação de um plano de desligamentos programados."""

    plan_id: str
    method_name: str
    scenario_id: str
    is_feasible: bool
    mandatory_completion_rate: float
    interruption_windows_count: int
    physical_maneuvers_count: int
    coupled_windows_count: int
    coupling_rate: float
    avg_services_per_window: float
    total_services_scheduled: int
    unique_consumers_interrupted: int
    repeat_interrupted_consumers_count: int
    total_consumer_interruptions: int
    consumer_hours_total: float
    total_crew_work_hours: float
    total_crew_travel_hours: float
    deadline_violations_count: int
    lifecycle_alerts_count: int
    planning_time_sec: float
    solver_status: str
    solver_objective: Optional[float]
    solver_bound: Optional[float]
    solver_gap: Optional[float]
    is_optimal_proven: bool
