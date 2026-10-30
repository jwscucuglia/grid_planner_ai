"""Boundary regressions for explicit, fail-closed GIS bundle imports."""
from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


TABLES = {
    "nodes": [
        dict(node_id="SRC", name="Source", feeder_id="F1", x=0., y=0., phases=["A"],
             is_source=True, in_service=True, nominal_voltage_kv=13.8, load_kw=0., distributed_gen_kw=0.),
        dict(node_id="N1", name="Load", feeder_id="F1", x=1., y=0., phases=["A"],
             is_source=False, in_service=True, nominal_voltage_kv=13.8, load_kw=2., distributed_gen_kw=0.),
    ],
    "branches": [dict(branch_id="B1", from_node="SRC", to_node="N1", feeder_id="F1", phases=["A"],
                      in_service=True, r_ohm=.1, x_ohm=.1, max_capacity_kw=100., length_km=1.)],
    "switches": [dict(switch_id="SW1", branch_id="B1", normal_state="CLOSED", current_state="CLOSED",
                      is_maneuverable=True, maneuver_duration_hours=.1, in_service=True)],
    "assets": [dict(asset_id="A1", node_id="N1", branch_id=None, feeder_id="F1", asset_type="POLE",
                    phases=["A"], in_service=True, x=1., y=0.)],
    "consumers": [dict(consumer_id="C1", node_id="N1", asset_id="A1", phase="A", avg_demand_kw=2.)],
    "isolation_zones": [dict(zone_id="Z1", name="Zone", feeder_id="F1", open_switches=["SW1"],
                            prep_duration_hours=.1, recomp_duration_hours=.1)],
    "services": [dict(service_id="S1", title="Work", service_type="PREVENTIVA", target_assets=["A1"],
                      mandatory=True, created_at_day=0, ready_at_day=0, deadline_day=1, duration_hours=1.,
                      required_skills=["ELEC"], prep_hours=.1, recomp_hours=.1,
                      required_crews_count=1, required_materials={}, precedence_service_ids=[],
                      requires_deenergized=True, allow_simultaneous_in_zone=True,
                      scope_status="ACTIVE",
                      compatible_zone_ids=["Z1"], x=1., y=0.)],
    "crews": [dict(crew_id="E1", name="Crew", skills=["ELEC"], available_days=[1], shift_start_hour=8.,
                   shift_end_hour=17., base_x=1., base_y=0., speed_kmh=30., prep_hours=.1)],
}


def write_bundle(root: Path, tables=None, fmt="json") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    tables = deepcopy(TABLES if tables is None else tables)
    manifest = dict(
        schema_version="kgp.ingest.v1", decision_day=0,
        network=dict(network_id="GIS_TEST", version="1", valid_from_day=0,
                     coordinate_system="CARTESIAN_KM", origin="Test reference", license="CC0"),
        catalog=dict(materials_inventory={}, planning_days=[1], window_start_hour=8.,
                     window_end_hour=17., max_window_duration_hours=8.),
        units=dict(coordinates="km", length="km", power="kW", voltage="kV", impedance="ohm", duration="h"),
        provenance=dict(source="test fixture", synthetic_overlay="load and work assumptions"), tables={})
    for name, records in tables.items():
        target = root / f"{name}.{fmt}"
        if fmt == "csv":
            with target.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]) if records else ["id"])
                writer.writeheader()
                writer.writerows({k: json.dumps(v) if not isinstance(v, str) else v for k, v in row.items()}
                                 for row in records)
        else:
            body = records if fmt == "json" else dict(type="FeatureCollection", features=[
                dict(type="Feature", geometry=None, properties=row) for row in records])
            target.write_text(json.dumps(body, allow_nan=False), encoding="utf-8")
        manifest["tables"][name] = dict(path=target.name, format=fmt)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def importer(path):
    # This assertion is RED before implementation, rather than an import-time error.
    assert importlib.util.find_spec("kgp.ingestion") is not None, "GIS importer is not implemented"
    from kgp.ingestion import import_bundle
    return import_bundle(path)


def modify_manifest(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def assert_blocked(result, code=None, table=None, row=None, record_id=None):
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(i.severity == "ERROR" and (code is None or i.code == code)
               and (table is None or i.table == table) and (row is None or i.row == row)
               and (record_id is None or i.record_id == record_id) for i in result.issues), result.issues


@pytest.mark.parametrize("fmt", ["json", "csv", "geojson"])
def test_imports_explicit_connections_and_preserves_portfolio(tmp_path, fmt):
    result = importer(write_bundle(tmp_path, fmt=fmt))
    assert result.is_valid, result.issues
    assert result.network.branches["B1"].from_node == "SRC"
    assert result.network.nodes["N1"].load_kw == 2.
    assert result.network.nodes["N1"].phases == ("A",)
    assert result.services[0].service_id == "S1"
    assert result.catalog.crews["E1"].available_days == (1,)
    assert result.provenance["counts"]["nodes"] == 2
    assert len(result.provenance["files"]["nodes"]["sha256"]) == 64


def test_maps_external_names_without_guessing_enum_codes(tmp_path):
    tables = deepcopy(TABLES)
    for row in tables["nodes"]:
        row["PAC"] = row.pop("node_id")
    path = write_bundle(tmp_path, tables)
    modify_manifest(path, lambda m: m["tables"]["nodes"].update(field_map={"node_id": "PAC"}))
    result = importer(path)
    assert result.is_valid, result.issues
    assert set(result.network.nodes) == {"SRC", "N1"}
    tables["switches"][0]["current_state"] = "NF"
    assert_blocked(importer(write_bundle(tmp_path / "coded", tables)), "INVALID_VALUE", "switches", 1, "SW1")


def test_converts_declared_units_for_every_coordinate_and_operational_time(tmp_path):
    tables = deepcopy(TABLES)
    for name, rows in tables.items():
        for row in rows:
            for key, value in list(row.items()):
                if key in {"x", "y", "base_x", "base_y", "length_km"} or key.endswith("_kw") or key.endswith("_kv"):
                    row[key] = value * 1000
                elif key.endswith("_hours") or key.endswith("_hour"):
                    row[key] = value * 60
    path = write_bundle(tmp_path, tables)
    def change(m):
        m["units"].update(coordinates="m", length="m", power="W", voltage="V", duration="min")
        m["network"]["coordinate_system"] = "PROJECTED"
        for key in ["window_start_hour", "window_end_hour", "max_window_duration_hours"]:
            m["catalog"][key] *= 60
    modify_manifest(path, change)
    result = importer(path)
    assert result.is_valid, result.issues
    assert result.network.coordinate_system == "CARTESIAN_KM"
    assert result.network.nodes["N1"].x == 1.
    assert result.network.nodes["N1"].nominal_voltage_kv == 13.8
    assert result.network.branches["B1"].length_km == 1.
    assert result.services[0].duration_hours == 1.
    assert result.catalog.window_start_hour == 8.


@pytest.mark.parametrize("table,field", [
    ("nodes", "phases"), ("nodes", "is_source"), ("nodes", "in_service"),
    ("nodes", "load_kw"), ("nodes", "nominal_voltage_kv"), ("nodes", "distributed_gen_kw"),
    ("branches", "phases"), ("branches", "r_ohm"), ("branches", "x_ohm"),
    ("branches", "max_capacity_kw"), ("branches", "in_service"), ("branches", "length_km"),
    ("switches", "current_state"), ("switches", "maneuver_duration_hours"),
    ("assets", "phases"), ("consumers", "phase"), ("consumers", "avg_demand_kw"),
    ("services", "duration_hours"), ("services", "prep_hours"),
    ("services", "required_crews_count"), ("services", "required_materials"),
    ("services", "requires_deenergized"), ("services", "allow_simultaneous_in_zone"),
    ("services", "scope_status"),
    ("crews", "shift_start_hour"), ("crews", "base_x"),
])
def test_critical_fields_cannot_fall_back_to_synthetic_contract_defaults(tmp_path, table, field):
    tables = deepcopy(TABLES)
    del tables[table][0][field]
    assert_blocked(importer(write_bundle(tmp_path, tables)), "MISSING_FIELD", table, 1)


@pytest.mark.parametrize("table,field,value,code", [
    ("branches", "to_node", "MISSING", "ORPHAN_REFERENCE"),
    ("assets", "node_id", "MISSING", "ORPHAN_REFERENCE"),
    ("services", "target_assets", ["MISSING"], "ORPHAN_REFERENCE"),
    ("services", "precedence_service_ids", ["MISSING"], "ORPHAN_REFERENCE"),
    ("branches", "phases", ["B"], "INCOMPATIBLE_PHASES"),
    ("assets", "phases", ["B"], "INCOMPATIBLE_PHASES"),
    ("consumers", "phase", "B", "INCOMPATIBLE_PHASES"),
    ("services", "duration_hours", 1.00001, "OFF_GRID_TIME"),
    ("crews", "shift_start_hour", True, "INVALID_VALUE"),
    ("switches", "current_state", "OPEN", "INVALID_STRUCTURE"),
])
def test_reports_structured_errors_without_returning_plannable_inputs(tmp_path, table, field, value, code):
    tables = deepcopy(TABLES)
    tables[table][0][field] = value
    assert_blocked(importer(write_bundle(tmp_path, tables)), code)


@pytest.mark.parametrize("table", ["nodes", "branches", "services", "crews"])
def test_duplicate_ids_block_instead_of_overwriting_rows(tmp_path, table):
    tables = deepcopy(TABLES)
    tables[table].append(deepcopy(tables[table][0]))
    assert_blocked(importer(write_bundle(tmp_path, tables)), "DUPLICATE_ID", table, len(tables[table]))


@pytest.mark.parametrize("unit,value", [("coordinates", "degrees"), ("length", "feet"), ("power", "MW"), ("impedance", "ohm/km")])
def test_unsupported_or_geographic_units_block(tmp_path, unit, value):
    path = write_bundle(tmp_path)
    modify_manifest(path, lambda m: m["units"].update({unit: value}))
    assert_blocked(importer(path), "INVALID_UNITS")


def test_geographic_crs_cannot_claim_linear_coordinates(tmp_path):
    path = write_bundle(tmp_path)
    modify_manifest(path, lambda m: m["network"].update(coordinate_system="EPSG:4326"))
    assert_blocked(importer(path), "INVALID_CRS")


@pytest.mark.parametrize("bad_json", ['[{"node_id":"SRC","node_id":"OTHER"}]', '[{"x":NaN}]', '[{"x":1e999}]'])
def test_strict_json_rejects_duplicates_and_nonfinite_numbers(tmp_path, bad_json):
    path = write_bundle(tmp_path)
    (tmp_path / "nodes.json").write_text(bad_json, encoding="utf-8")
    assert_blocked(importer(path), "INVALID_FILE", "nodes")


def test_duplicate_csv_headers_and_extra_columns_block(tmp_path):
    path = write_bundle(tmp_path, fmt="csv")
    (tmp_path / "nodes.csv").write_text("node_id,node_id\nSRC,OTHER\n", encoding="utf-8")
    assert_blocked(importer(path), "DUPLICATE_HEADER", "nodes")


def test_csv_nested_json_is_strict(tmp_path):
    path = write_bundle(tmp_path, fmt="csv")
    text = (tmp_path / "services.csv").read_text(encoding="utf-8-sig")
    text = text.replace("1.0", "NaN", 1)
    (tmp_path / "services.csv").write_text(text, encoding="utf-8")
    assert_blocked(importer(path))


def test_paths_cannot_escape_manifest_directory(tmp_path):
    path = write_bundle(tmp_path / "bundle")
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    modify_manifest(path, lambda m: m["tables"]["nodes"].update(path="../outside.json"))
    assert_blocked(importer(path), "UNSAFE_PATH", "nodes")


def test_absolute_paths_are_blocked_even_when_inside_bundle(tmp_path):
    path = write_bundle(tmp_path)
    modify_manifest(path, lambda m: m["tables"]["nodes"].update(path=str(tmp_path / "nodes.json")))
    assert_blocked(importer(path), "UNSAFE_PATH", "nodes")


def test_unknown_units_are_not_silently_ignored(tmp_path):
    path = write_bundle(tmp_path)
    modify_manifest(path, lambda m: m["units"].update(current="A"))
    assert_blocked(importer(path), "INVALID_UNITS")


@pytest.mark.parametrize("field", ["from_node", "to_node"])
def test_branch_phases_must_exist_at_both_endpoints(tmp_path, field):
    tables = deepcopy(TABLES)
    bad_id = tables["branches"][0][field]
    next(row for row in tables["nodes"] if row["node_id"] == bad_id)["phases"] = ["B"]
    assert_blocked(importer(write_bundle(tmp_path, tables)), "INCOMPATIBLE_PHASES", "branches", 1, "B1")


def test_consumer_asset_cannot_be_attached_to_another_node(tmp_path):
    tables = deepcopy(TABLES)
    tables["consumers"][0]["node_id"] = "SRC"
    assert_blocked(importer(write_bundle(tmp_path, tables)), "INCONSISTENT_ATTACHMENT", "consumers", 1, "C1")


def test_asset_node_and_branch_references_must_agree(tmp_path):
    tables = deepcopy(TABLES)
    extra = deepcopy(tables["nodes"][1])
    extra["node_id"] = "ISOLATED"
    tables["nodes"].append(extra)
    tables["assets"][0].update(node_id="ISOLATED", branch_id="B1")
    assert_blocked(importer(write_bundle(tmp_path, tables)), "INCONSISTENT_ATTACHMENT", "assets", 1, "A1")


@pytest.mark.parametrize("table,field,value,record_id", [
    ("branches", "max_capacity_kw", -1., "B1"), ("branches", "r_ohm", -1., "B1"),
    ("nodes", "load_kw", -1., "SRC"), ("nodes", "nominal_voltage_kv", 0., "SRC"),
    ("nodes", "phases", [], "SRC"), ("switches", "maneuver_duration_hours", -.1, "SW1"),
    ("services", "duration_hours", 0., "S1"), ("services", "required_crews_count", 0, "S1"),
    ("crews", "speed_kmh", 0., "E1"), ("crews", "shift_end_hour", 7., "E1"),
    ("consumers", "avg_demand_kw", -1., "C1"),
])
def test_invalid_physical_value_has_its_source_row(tmp_path, table, field, value, record_id):
    tables = deepcopy(TABLES)
    tables[table][0][field] = value
    assert_blocked(importer(write_bundle(tmp_path, tables)), "INVALID_VALUE", table, 1, record_id)


def test_a_network_without_an_active_source_is_blocked(tmp_path):
    tables = deepcopy(TABLES)
    tables["nodes"][0]["is_source"] = False
    assert_blocked(importer(write_bundle(tmp_path, tables)), "MISSING_SOURCE", "nodes")


def test_csv_required_material_dicts_and_optional_nulls_survive(tmp_path):
    tables = deepcopy(TABLES)
    tables["services"][0]["required_materials"] = {"FUSE": 2.}
    path = write_bundle(tmp_path, tables, fmt="csv")
    modify_manifest(path, lambda m: m["catalog"].update(materials_inventory={"FUSE": 3.}))
    result = importer(path)
    assert result.is_valid, result.issues
    assert result.services[0].required_materials == {"FUSE": 2.}
    assert result.network.assets["A1"].branch_id is None


def test_strict_manifest_json_duplicates_are_blocked(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"kgp.ingest.v1","schema_version":"other"}', encoding="utf-8")
    assert_blocked(importer(path), "INVALID_MANIFEST", "manifest")


@pytest.mark.parametrize("status", ["ATIVO", "active", "INACTIVE", "CANCELLED", ""])
def test_unknown_scope_status_cannot_remove_a_mandatory_service(tmp_path, status):
    tables = deepcopy(TABLES)
    tables["services"][0]["scope_status"] = status
    result = importer(write_bundle(tmp_path, tables))
    assert_blocked(result, "INVALID_SCOPE_STATUS", "services", 1, "S1")
    assert result.provenance["record_ids"]["services"] == ["S1"]
    assert result.provenance["counts"]["services"] == 1


@pytest.mark.parametrize("cross_feeder", [False, True])
def test_branch_voltage_transition_requires_an_explicit_supported_converter(tmp_path, cross_feeder):
    tables = deepcopy(TABLES)
    tables["nodes"][1]["nominal_voltage_kv"] = .38
    if cross_feeder:
        tables["nodes"][1]["feeder_id"] = "F2"
    assert_blocked(importer(write_bundle(tmp_path, tables)), "UNSUPPORTED_VOLTAGE_TRANSITION", "branches", 1, "B1")


def test_same_voltage_across_different_feeder_ids_is_supported(tmp_path):
    tables = deepcopy(TABLES)
    tables["nodes"][1]["feeder_id"] = "F2"
    result = importer(write_bundle(tmp_path, tables))
    assert result.is_valid, result.issues


def test_separate_feeders_can_have_different_voltage_levels_without_a_connecting_branch(tmp_path):
    tables = deepcopy(TABLES)
    extra = deepcopy(tables["nodes"][0])
    extra.update(node_id="SRC_F2", feeder_id="F2", nominal_voltage_kv=.38)
    tables["nodes"].append(extra)
    result = importer(write_bundle(tmp_path, tables))
    assert result.is_valid, result.issues
    assert result.network.nodes["SRC_F2"].nominal_voltage_kv == .38


def test_voltage_comparison_allows_only_tiny_representation_noise(tmp_path):
    tables = deepcopy(TABLES)
    tables["nodes"][1]["nominal_voltage_kv"] = 13.8 + 1e-12
    result = importer(write_bundle(tmp_path, tables))
    assert result.is_valid, result.issues


def write_large_csv_field(path, size):
    target = path.parent / "nodes.csv"
    with target.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    records[0]["name"] = "X" * size
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def test_oversized_csv_field_returns_diagnostics_instead_of_raising(tmp_path):
    path = write_bundle(tmp_path, fmt="csv")
    write_large_csv_field(path, csv.field_size_limit() + 1)
    result = importer(path)
    assert_blocked(result, "INVALID_FILE", "nodes")
    assert any(str(csv.field_size_limit()) in issue.message for issue in result.issues if issue.table == "nodes")


def test_csv_field_exactly_at_the_declared_parser_limit_is_supported(tmp_path):
    path = write_bundle(tmp_path, fmt="csv")
    write_large_csv_field(path, csv.field_size_limit())
    result = importer(path)
    assert result.is_valid, result.issues
    assert len(result.network.nodes["SRC"].name) == csv.field_size_limit()


def test_cli_handles_oversized_csv_without_traceback(tmp_path):
    path = write_bundle(tmp_path, fmt="csv")
    write_large_csv_field(path, csv.field_size_limit() + 1)
    script = Path(__file__).resolve().parents[1] / "scripts" / "import_kgp_bundle.py"
    run = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
    assert run.returncode == 1
    report = json.loads(run.stdout)
    assert report["is_valid"] is False
    assert any(issue["code"] == "INVALID_FILE" and issue["table"] == "nodes" for issue in report["issues"])
    assert "Traceback" not in run.stderr


def test_import_is_equivalent_to_an_existing_complete_known_instance(tmp_path):
    from kgp.experiment import generate_scenario_instance
    known = generate_scenario_instance(202601, "DEV")
    network = asdict(known.network)
    catalog = asdict(known.catalog)
    tables = {key: list(network.pop(key).values()) for key in TABLES if key not in {"services", "crews"}}
    tables["services"] = [asdict(s) for s in known.services]
    tables["crews"] = list(catalog.pop("crews").values())
    path = write_bundle(tmp_path, tables)
    modify_manifest(path, lambda m: m.update(network=network, catalog=catalog, decision_day=known.decision_day))
    result = importer(path)
    assert result.is_valid, result.issues
    assert asdict(result.network) == asdict(known.network)
    assert result.services == tuple(known.services)
    assert asdict(result.catalog) == asdict(known.catalog)


def test_coincident_coordinates_do_not_connect_distinct_nodes(tmp_path):
    tables = deepcopy(TABLES)
    tables["nodes"][1]["x"] = 0.
    tables["branches"] = []
    tables["switches"] = []
    tables["isolation_zones"] = []
    tables["services"][0]["compatible_zone_ids"] = []
    result = importer(write_bundle(tmp_path, tables))
    assert result.is_valid, result.issues
    from kgp.topology import compute_energized_phases_by_node
    energized, _, _ = compute_energized_phases_by_node(result.network, set(), set())
    assert not energized["N1"]


def test_all_bad_rows_remain_counted_and_services_traceable(tmp_path):
    tables = deepcopy(TABLES)
    tables["nodes"][0].pop("is_source")
    tables["nodes"][1].pop("load_kw")
    result = importer(write_bundle(tmp_path, tables))
    assert_blocked(result)
    assert {issue.row for issue in result.issues if issue.table == "nodes"} >= {1, 2}
    assert result.provenance["counts"]["nodes"] == 2
    assert result.provenance["record_ids"]["services"] == ["S1"]


def test_existing_planner_and_independent_verifier_accept_imported_contracts(tmp_path):
    result = importer(write_bundle(tmp_path))
    assert result.is_valid, result.issues
    from kgp.baselines import run_baseline_b0_individual
    from kgp.contracts import plan_from_json
    from kgp.feasibility import verify_plan_feasibility
    plan = run_baseline_b0_individual(result.network, result.services, result.catalog, "INGEST", 0)
    exported = plan_from_json(json.dumps(asdict(plan), allow_nan=False))
    audit = verify_plan_feasibility(result.network, result.services, result.catalog, exported)
    assert audit.is_feasible, audit.violations
    assert audit.completed_mandatory_count == audit.total_mandatory_count == 1


def test_cli_reports_quality_and_returns_nonzero_for_blocked_bundle(tmp_path):
    path = write_bundle(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "import_kgp_bundle.py"
    assert script.is_file(), "GIS import CLI is not implemented"
    run = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["is_valid"] is True
    modify_manifest(path, lambda m: m["units"].update(duration="seconds"))
    run = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
    assert run.returncode != 0
    assert json.loads(run.stdout)["is_valid"] is False
