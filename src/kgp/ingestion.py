"""Explicit CSV/JSON/GeoJSON boundary for external, documented KGP inputs.

Connections are identifiers, never inferred from geometry. All numbers carrying
dimensions are expressed in the manifest's declared units before conversion to
the existing km/kW/kV/ohm/hour contracts. This module never invokes a planner.

The v1 business domain accepts only explicit scope_status='ACTIVE'. Inactive or
cancelled codes have no defined contract here and are rejected, never interpreted
as an empty mandatory portfolio. Branches support equal nominal voltage at both
endpoints (relative tolerance 1e-9 for representation noise); transformer/converter
voltage transitions require a model unavailable in this importer. The public MV
reference is a same-level topology, not an MV/LV transformer simulation.

CSV respects the standard-library parser's field_size_limit (131072 characters
by default). The effective limit is recorded and a parser limit error is returned
as a structured INVALID_FILE diagnostic without changing global parser settings.
"""
from __future__ import annotations

import csv
from dataclasses import MISSING, dataclass, fields
from enum import Enum
import hashlib
import math
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from .contracts import (
    ConsumerUnit, CrewResource, ExecutionPlan, GridAsset, GridBranch, GridNetwork,
    GridNode, GridSwitch, IsolationZoneSpec, ResourceCatalog, ServiceOrder,
    _decode_typed, nonfinite_paths, operational_time_ticks, strict_json_loads,
)
from .validation import validate_problem_and_plan


@dataclass(frozen=True)
class IngestionIssue:
    code: str
    table: str
    row: int | None
    record_id: str | None
    message: str
    severity: str = "ERROR"


@dataclass(frozen=True)
class IngestionResult:
    network: GridNetwork | None
    services: tuple[ServiceOrder, ...]
    catalog: ResourceCatalog | None
    issues: tuple[IngestionIssue, ...]
    provenance: dict

    @property
    def is_valid(self) -> bool:
        return self.network is not None and self.catalog is not None and not any(
            item.severity == "ERROR" for item in self.issues)


_TABLE_TYPES = {
    "nodes": (GridNode, "node_id"), "branches": (GridBranch, "branch_id"),
    "switches": (GridSwitch, "switch_id"), "assets": (GridAsset, "asset_id"),
    "consumers": (ConsumerUnit, "consumer_id"),
    "isolation_zones": (IsolationZoneSpec, "zone_id"),
    "services": (ServiceOrder, "service_id"), "crews": (CrewResource, "crew_id"),
}
# Physical data and operational durations must not inherit synthetic defaults.
_EXPLICIT = {
    "nodes": {f.name for f in fields(GridNode)},
    "branches": {f.name for f in fields(GridBranch)},
    "switches": {f.name for f in fields(GridSwitch)},
    "assets": {f.name for f in fields(GridAsset)},
    "consumers": {"consumer_id", "node_id", "asset_id", "phase", "avg_demand_kw"},
    "isolation_zones": {"zone_id", "name", "feeder_id", "open_switches", "prep_duration_hours", "recomp_duration_hours"},
    "services": {"prep_hours", "recomp_hours", "x", "y", "required_crews_count", "required_materials",
                 "precedence_service_ids", "requires_deenergized", "compatible_zone_ids", "allow_simultaneous_in_zone",
                 "scope_status"},
    "crews": {f.name for f in fields(CrewResource)},
}
_UNIT_FACTORS = {
    "coordinates": {"km": 1., "m": .001}, "length": {"km": 1., "m": .001},
    "power": {"kW": 1., "W": .001}, "voltage": {"kV": 1., "V": .001},
    "impedance": {"ohm": 1.}, "duration": {"h": 1., "min": 1. / 60.},
}
_LINEAR_COORDINATE_SYSTEMS = {"CARTESIAN_KM", "CARTESIAN_M", "PROJECTED", "PROJETADO"}
_VOLTAGE_REL_TOLERANCE = 1e-9


class _InputError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _safe_path(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise _InputError("UNSAFE_PATH", "path must be a nonempty relative file path")
    path = Path(raw)
    if path.is_absolute() or path.drive:
        raise _InputError("UNSAFE_PATH", "table path must be relative to the manifest directory")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise _InputError("UNSAFE_PATH", "table path escapes the manifest directory")
    return resolved


def _records(path: Path, fmt: str) -> tuple[list, bytes]:
    content = path.read_bytes()
    raw = content.decode("utf-8-sig")
    if fmt == "csv":
        import io
        reader = csv.DictReader(io.StringIO(raw, newline=""))
        header = reader.fieldnames
        if not header or any(not key for key in header):
            raise _InputError("INVALID_FILE", "CSV requires nonempty column names")
        if len(set(header)) != len(header):
            raise _InputError("DUPLICATE_HEADER", "CSV contains duplicate column names")
        records = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in records):
            raise _InputError("INVALID_FILE", "CSV row width differs from its header")
        return records, content
    data = strict_json_loads(raw)
    if fmt == "json":
        if not isinstance(data, list):
            raise _InputError("INVALID_FILE", "JSON table must be an array of records")
        return data, content
    if fmt == "geojson":
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
            raise _InputError("INVALID_FILE", "GeoJSON table must be a FeatureCollection")
        records = []
        for feature in data["features"]:
            if not isinstance(feature, dict) or feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
                raise _InputError("INVALID_FILE", "each GeoJSON Feature needs record properties")
            records.append(feature["properties"])
        return records, content
    raise _InputError("INVALID_FORMAT", "format must be csv, json or geojson")


def _csv_value(raw: str, expected: Any) -> Any:
    origin = get_origin(expected)
    if origin is Union:
        if raw in ("", "null") and type(None) in get_args(expected):
            return None
        expected = next(t for t in get_args(expected) if t is not type(None))
    if expected is str or (isinstance(expected, type) and issubclass(expected, Enum)):
        return raw
    return strict_json_loads(raw)


def _dimension(field_name: str) -> str | None:
    if field_name in {"x", "y", "base_x", "base_y"}:
        return "coordinates"
    for suffix, dimension in [("_km", "length"), ("_kw", "power"), ("_kv", "voltage"),
                               ("_ohm", "impedance"), ("_hours", "duration"), ("_hour", "duration")]:
        if field_name.endswith(suffix):
            return dimension
    return None


def _convert(raw: dict, factors: dict) -> dict:
    converted = dict(raw)
    for name, value in raw.items():
        dimension = _dimension(name)
        if dimension is None or value is None:
            continue
        if type(value) not in (int, float):
            raise _InputError("INVALID_VALUE", f"{name}: expected a finite number, without implicit coercion")
        converted[name] = value * factors.get(dimension, 1.)
        if dimension == "duration":
            try:
                operational_time_ticks(converted[name], name)
            except ValueError as exc:
                raise _InputError("OFF_GRID_TIME", str(exc)) from exc
    errors = nonfinite_paths(converted)
    if errors:
        raise _InputError("INVALID_VALUE", "; ".join(errors))
    return converted


def _record_constraints(table: str, obj: Any) -> None:
    """Report local physical-domain errors at their original table/row boundary.

    The independent validator still checks the assembled problem below. These
    local checks preserve diagnostics before a bad record is withheld.
    """
    errors = []
    if table in {"nodes", "branches", "assets"}:
        if not obj.phases or len(set(obj.phases)) != len(obj.phases) or not set(obj.phases) <= {"A", "B", "C"}:
            errors.append("phases must be a nonempty, unique subset of A/B/C")
    if table == "nodes" and (obj.load_kw < 0 or obj.distributed_gen_kw < 0 or obj.nominal_voltage_kv <= 0):
        errors.append("node demands/generation must be nonnegative and voltage positive")
    if table == "branches" and (obj.max_capacity_kw <= 0 or min(obj.r_ohm, obj.x_ohm, obj.length_km) < 0):
        errors.append("branch capacity must be positive; impedance and length must be nonnegative")
    if table == "switches" and obj.maneuver_duration_hours < 0:
        errors.append("maneuver duration must be nonnegative")
    if table == "consumers" and (obj.avg_demand_kw < 0 or obj.phase not in {"A", "B", "C"}):
        errors.append("consumer demand must be nonnegative and phase A/B/C")
    if table == "services":
        if obj.scope_status != "ACTIVE":
            raise _InputError("INVALID_SCOPE_STATUS", "v1 accepts only explicit scope_status='ACTIVE'; external/inactive/cancelled codes have no supported mapping")
        if obj.duration_hours <= 0 or min(obj.prep_hours, obj.recomp_hours) < 0 or obj.required_crews_count < 1:
            errors.append("execution duration and crew count must be positive; preparation/recomposition nonnegative")
        if not obj.created_at_day <= obj.ready_at_day <= obj.deadline_day:
            errors.append("expected created_at_day <= ready_at_day <= deadline_day")
    if table == "crews" and (not 0 <= obj.shift_start_hour < obj.shift_end_hour <= 24 or obj.speed_kmh <= 0 or obj.prep_hours < 0):
        errors.append("crew shift, speed or preparation is outside the physical domain")
    if errors:
        raise _InputError("INVALID_VALUE", "; ".join(errors))


def import_bundle(manifest_path: str | Path) -> IngestionResult:
    """Read a self-contained bundle, returning diagnostics instead of partial success.

    Any ERROR withholds both network and catalog. Services that decoded correctly
    remain inspectable, and every source service ID/count is retained in provenance.
    JSON keys and CSV headers are unique; external enum codes require an upstream
    adapter. ``field_map`` maps canonical field names to exact source column names.
    """
    issues: list[IngestionIssue] = []
    provenance: dict = {"counts": {}, "record_ids": {}, "files": {}, "field_maps": {},
                        "limits": {"csv_field_size_characters": csv.field_size_limit(),
                                   "voltage_relative_tolerance": _VOLTAGE_REL_TOLERANCE,
                                   "voltage_absolute_tolerance_kv": 0.}}

    def issue(code, table, message, row=None, record_id=None, severity="ERROR"):
        issues.append(IngestionIssue(code, table, row, record_id, message, severity))

    def result(network=None, services=(), catalog=None):
        blocked = any(item.severity == "ERROR" for item in issues)
        return IngestionResult(None if blocked else network, tuple(services),
                               None if blocked else catalog, tuple(issues), provenance)

    try:
        path = Path(manifest_path).resolve()
        content = path.read_bytes()
        manifest = strict_json_loads(content.decode("utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        issue("INVALID_MANIFEST", "manifest", str(exc))
        return result()
    provenance["manifest_sha256"] = hashlib.sha256(content).hexdigest()
    provenance["schema_version"] = manifest.get("schema_version")
    for name in ("network", "catalog", "decision_day", "units", "provenance", "tables"):
        if name not in manifest:
            issue("MISSING_FIELD", "manifest", f"missing {name}")
    if manifest.get("schema_version") != "kgp.ingest.v1":
        issue("INVALID_SCHEMA", "manifest", "schema_version must be kgp.ingest.v1")
    if type(manifest.get("decision_day")) is not int:
        issue("INVALID_VALUE", "manifest", "decision_day must be an integer")
    source = manifest.get("provenance", {})
    if not isinstance(source, dict):
        issue("INVALID_VALUE", "manifest", "provenance must be an object")
    provenance["source"] = source
    provenance["decision_day"] = manifest.get("decision_day")
    factors = {}
    units = manifest.get("units", {})
    provenance["units"] = units
    if not isinstance(units, dict):
        issue("INVALID_UNITS", "manifest", "units must be an object")
        units = {}
    if units.keys() - _UNIT_FACTORS.keys():
        issue("INVALID_UNITS", "manifest", f"unknown unit dimensions: {sorted(units.keys() - _UNIT_FACTORS.keys())}")
    for name, allowed in _UNIT_FACTORS.items():
        value = units.get(name)
        if not isinstance(value, str) or value not in allowed:
            issue("INVALID_UNITS", "manifest", f"{name}: declare one of {list(allowed)}")
        else:
            factors[name] = allowed[value]
    descriptors = manifest.get("tables", {})
    if not isinstance(descriptors, dict):
        issue("INVALID_VALUE", "manifest", "tables must be an object")
        descriptors = {}
    for unknown in descriptors.keys() - _TABLE_TYPES.keys():
        issue("UNKNOWN_TABLE", unknown, "unknown table cannot be silently ignored")
    objects: dict[str, dict] = {name: {} for name in _TABLE_TYPES}
    locations: dict[str, dict] = {name: {} for name in _TABLE_TYPES}
    for table, (kind, id_field) in _TABLE_TYPES.items():
        descriptor = descriptors.get(table)
        if not isinstance(descriptor, dict):
            issue("MISSING_TABLE", table, "table descriptor is required (use an empty array for an empty table)")
            continue
        mapping = descriptor.get("field_map", {})
        hints = get_type_hints(kind)
        if (not isinstance(mapping, dict) or any(k not in hints or not isinstance(v, str) or not v for k, v in mapping.items())
                or len(set(mapping.values())) != len(mapping)):
            issue("INVALID_FIELD_MAP", table, "field_map must map unique canonical names to unique source names")
            continue
        provenance["field_maps"][table] = mapping
        try:
            table_path = _safe_path(path.parent, descriptor.get("path"))
            rows, raw = _records(table_path, descriptor.get("format"))
        except (OSError, ValueError, TypeError, UnicodeError, csv.Error) as exc:
            message = (f"CSV parser maximum field length is {csv.field_size_limit()} characters: {exc}"
                       if isinstance(exc, csv.Error) else str(exc))
            issue(getattr(exc, "code", "INVALID_FILE"), table, message)
            continue
        provenance["files"][table] = {"path": descriptor["path"], "format": descriptor.get("format"),
                                      "sha256": hashlib.sha256(raw).hexdigest()}
        provenance["counts"][table] = len(rows)
        provenance["record_ids"][table] = []
        required = {f.name for f in fields(kind) if f.default is MISSING and f.default_factory is MISSING} | _EXPLICIT[table]
        seen = set()
        for row_number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                issue("INVALID_RECORD", table, "record must be an object", row_number)
                continue
            record_id = row.get(mapping.get(id_field, id_field))
            record_id = record_id if isinstance(record_id, str) else None
            provenance["record_ids"][table].append(record_id)
            if record_id is not None and record_id in seen:
                issue("DUPLICATE_ID", table, "duplicate record ID; no row was overwritten", row_number, record_id)
                continue
            if record_id is not None:
                seen.add(record_id)
            values = {name: row[mapping.get(name, name)] for name in hints if mapping.get(name, name) in row}
            missing = sorted(required - values.keys())
            if missing:
                issue("MISSING_FIELD", table, f"explicit critical fields required: {', '.join(missing)}", row_number, record_id)
                continue
            extra = set(row) - {mapping.get(name, name) for name in hints}
            if extra:
                issue("UNMAPPED_FIELDS", table, f"source fields not used: {sorted(extra)}", row_number, record_id, "WARNING")
            try:
                if descriptor.get("format") == "csv":
                    values = {key: _csv_value(value, hints[key]) for key, value in values.items()}
                if not record_id or not record_id.strip():
                    raise _InputError("INVALID_VALUE", "record ID must be a nonempty string")
                converted = _convert(values, factors)
                obj = _decode_typed(converted, kind, f"{table}[{row_number}]")
                _record_constraints(table, obj)
                # Transfer assumptions are applicable only to zones actually using ties.
                if table == "isolation_zones" and obj.tie_switches_closed:
                    transfer_fields = {"requires_transfer_validation", "transfer_params_complete", "max_transfer_voltage_drop_pct"}
                    if transfer_fields - values.keys():
                        raise _InputError("MISSING_FIELD", f"explicit transfer fields required: {sorted(transfer_fields - values.keys())}")
                objects[table][record_id] = obj
                locations[table][record_id] = row_number
            except (ValueError, TypeError, OverflowError) as exc:
                issue(getattr(exc, "code", "INVALID_VALUE"), table, str(exc), row_number, record_id)

    def reference(table, item, field, ids, target_table):
        for target in ids:
            if target is not None and target not in objects[target_table]:
                issue("ORPHAN_REFERENCE", table, f"{field} references missing {target_table} ID {target}",
                      locations[table][item], item)

    def phases(table, item, actual, allowed):
        if not set(actual) <= set(allowed):
            issue("INCOMPATIBLE_PHASES", table, "phases are absent from the explicitly referenced endpoint/asset",
                  locations[table][item], item)

    for bid, branch in objects["branches"].items():
        reference("branches", bid, "endpoints", [branch.from_node, branch.to_node], "nodes")
        for nid in (branch.from_node, branch.to_node):
            if nid in objects["nodes"]:
                phases("branches", bid, branch.phases, objects["nodes"][nid].phases)
        if branch.from_node in objects["nodes"] and branch.to_node in objects["nodes"]:
            from_kv = objects["nodes"][branch.from_node].nominal_voltage_kv
            to_kv = objects["nodes"][branch.to_node].nominal_voltage_kv
            if not math.isclose(from_kv, to_kv, rel_tol=_VOLTAGE_REL_TOLERANCE, abs_tol=0.):
                issue("UNSUPPORTED_VOLTAGE_TRANSITION", "branches",
                      f"branch connects {from_kv} kV to {to_kv} kV without a supported transformer/converter model",
                      locations["branches"][bid], bid)
    for sid, switch in objects["switches"].items():
        reference("switches", sid, "branch_id", [switch.branch_id], "branches")
    for aid, asset in objects["assets"].items():
        reference("assets", aid, "node_id", [asset.node_id], "nodes")
        reference("assets", aid, "branch_id", [asset.branch_id], "branches")
        for target, table in [(asset.node_id, "nodes"), (asset.branch_id, "branches")]:
            if target in objects[table]:
                phases("assets", aid, asset.phases, objects[table][target].phases)
        if asset.node_id is not None and asset.branch_id in objects["branches"]:
            branch = objects["branches"][asset.branch_id]
            if asset.node_id not in {branch.from_node, branch.to_node}:
                issue("INCONSISTENT_ATTACHMENT", "assets", "asset node is not an endpoint of its branch", locations["assets"][aid], aid)
    for cid, consumer in objects["consumers"].items():
        reference("consumers", cid, "node_id", [consumer.node_id], "nodes")
        reference("consumers", cid, "asset_id", [consumer.asset_id], "assets")
        if consumer.node_id in objects["nodes"]:
            phases("consumers", cid, [consumer.phase], objects["nodes"][consumer.node_id].phases)
        if consumer.asset_id in objects["assets"]:
            asset = objects["assets"][consumer.asset_id]
            phases("consumers", cid, [consumer.phase], asset.phases)
            endpoints = {asset.node_id} if asset.node_id is not None else set()
            if asset.branch_id in objects["branches"]:
                branch = objects["branches"][asset.branch_id]
                endpoints.update((branch.from_node, branch.to_node))
            if consumer.node_id not in endpoints:
                issue("INCONSISTENT_ATTACHMENT", "consumers", "consumer node disagrees with its attached asset", locations["consumers"][cid], cid)
    for zid, zone in objects["isolation_zones"].items():
        reference("isolation_zones", zid, "switches", zone.open_switches + zone.tie_switches_closed, "switches")
    for sid, service in objects["services"].items():
        reference("services", sid, "assets", service.target_assets + service.replaces_asset_ids + service.maintains_asset_ids, "assets")
        reference("services", sid, "precedence_service_ids", service.precedence_service_ids, "services")
        reference("services", sid, "compatible_zone_ids", service.compatible_zone_ids, "isolation_zones")
    services = tuple(objects["services"].values())
    if not any(node.is_source and node.in_service for node in objects["nodes"].values()):
        issue("MISSING_SOURCE", "nodes", "network requires at least one explicitly active source")
    network = catalog = None
    for label, kind in [("network", GridNetwork), ("catalog", ResourceCatalog)]:
        metadata = manifest.get(label)
        if not isinstance(metadata, dict):
            issue("INVALID_VALUE", label, "metadata must be an object")
            continue
        reserved = (set(_TABLE_TYPES) - {"services", "crews"}) if label == "network" else {"crews"}
        if reserved & metadata.keys():
            issue("INVALID_STRUCTURE", label, "table data must be supplied via tables, not metadata")
            continue
        required = ({"network_id", "version", "valid_from_day", "coordinate_system", "origin", "license"} if label == "network"
                    else {"materials_inventory", "planning_days", "window_start_hour", "window_end_hour", "max_window_duration_hours"})
        if required - metadata.keys():
            issue("MISSING_FIELD", label, f"explicit metadata required: {sorted(required - metadata.keys())}")
            continue
        try:
            values = _convert(metadata, factors)
            if label == "network":
                provenance["source_coordinate_system"] = metadata["coordinate_system"]
                if metadata["coordinate_system"] not in _LINEAR_COORDINATE_SYSTEMS:
                    raise _InputError("INVALID_CRS", "declare CARTESIAN_KM/CARTESIAN_M or explicitly PROJECTED coordinates; transform geographic CRS upstream")
                values["coordinate_system"] = "CARTESIAN_KM"
                values.update({key: objects[key] for key in reserved})
                network = _decode_typed(values, kind, label)
            else:
                values["crews"] = objects["crews"]
                catalog = _decode_typed(values, kind, label)
        except (ValueError, TypeError, OverflowError) as exc:
            issue(getattr(exc, "code", "INVALID_VALUE"), label, str(exc))
    # Use the same contract/reference validator as the independent plan audit.
    # An empty plan is only a structural validation envelope, never a solution.
    if network is not None and catalog is not None and type(manifest.get("decision_day")) is int:
        envelope = ExecutionPlan(plan_id="INGESTION_VALIDATION", method_name="INGESTION", scenario_id=network.network_id,
                                 decision_day=manifest["decision_day"], unscheduled_service_ids=tuple(
                                     s.service_id for s in services if s.scope_status == "ACTIVE" and s.created_at_day <= manifest["decision_day"]))
        for message in validate_problem_and_plan(network, services, catalog, envelope):
            issue("INVALID_STRUCTURE", "problem", message)
    return result(network, services, catalog)
