"""Independent-review witnesses for JSON depth and derived travel arithmetic."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from kgp.contracts import plan_from_json, strict_json_loads
from kgp.feasibility import _canonical_travel_hours, verify_plan_feasibility
from kgp.ingestion import import_bundle
from kgp.scheduling import compute_travel_hours
from test_ingestion import TABLES, modify_manifest, write_bundle
from test_material_conservation import METHODS, readback
from test_rev04_time import timed_case


def nested(depth):
    return '[' * depth + '0' + ']' * depth


@pytest.mark.parametrize('depth', [513, 1500, 5000])
def test_json_depth_has_specific_domain_error(depth):
    with pytest.raises(ValueError) as caught:
        strict_json_loads(nested(depth))
    assert getattr(caught.value, 'code', None) == 'JSON_DEPTH_EXCEEDED'
    assert '512' in str(caught.value)


@pytest.mark.parametrize('depth', [100, 400, 512])
def test_supported_json_depth_is_not_limited_by_recursive_finite_scan(depth):
    result = strict_json_loads(nested(depth))
    for _ in range(depth):
        assert len(result) == 1
        result = result[0]
    assert result == 0


def test_json_depth_ignores_brackets_and_escaped_quotes_in_strings():
    text = '[{\\\"' * 1500 + '\\"}]' * 1500
    assert strict_json_loads(json.dumps({'text': text})) == {'text': text}


def test_plan_reader_rejects_deep_metadata_without_traceback():
    with pytest.raises(ValueError) as caught:
        plan_from_json('{"metadata":{"probe":' + nested(1500) + '}}')
    assert getattr(caught.value, 'code', None) == 'JSON_DEPTH_EXCEEDED'


def deep_bundle(tmp_path, location):
    fmt = {'geojson': 'geojson', 'csv': 'csv'}.get(location, 'json')
    tables = deepcopy(TABLES)
    tables['services'][0]['target_assets'] = '__DEPTH_PROBE__'
    path = write_bundle(tmp_path, tables, fmt=fmt)
    target = path if location == 'manifest' else path.parent / f'services.{fmt}'
    if location == 'manifest':
        modify_manifest(path, lambda m: m['provenance'].update(probe='__DEPTH_PROBE__'))
    raw = target.read_bytes()
    marker = b'__DEPTH_PROBE__' if location == 'csv' else b'"__DEPTH_PROBE__"'
    assert raw.count(marker) == 1
    target.write_bytes(raw.replace(marker, nested(1500).encode('ascii')))
    return path


@pytest.mark.parametrize('location', ['manifest', 'json', 'geojson', 'csv'])
def test_deep_json_import_is_blocked_with_structured_diagnostic(tmp_path, location):
    result = import_bundle(deep_bundle(tmp_path, location))
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(i.code == 'JSON_DEPTH_EXCEEDED' for i in result.issues), result.issues


@pytest.mark.parametrize('location', ['manifest', 'json', 'geojson', 'csv'])
def test_cli_always_emits_json_for_depth_rejection(tmp_path, location):
    path = deep_bundle(tmp_path, location)
    cli = Path(__file__).resolve().parents[1] / 'scripts/import_kgp_bundle.py'
    run = subprocess.run([sys.executable, str(cli), str(path)], capture_output=True, text=True)
    assert run.returncode == 1
    assert 'Traceback' not in run.stderr
    report = json.loads(run.stdout)
    assert not report['is_valid']
    assert any(i['code'] == 'JSON_DEPTH_EXCEEDED' for i in report['issues'])


@pytest.mark.parametrize('helper', [compute_travel_hours, _canonical_travel_hours])
@pytest.mark.parametrize('distance,speed,expected', [
    (1e-6, 1e-7, 10.), (5e-324, 5e-324, 1.),
    (1e-6, 35., 0.), (1., 35., .0286), (0., 5e-324, 0.),
])
def test_travel_rounds_duration_after_division(helper, distance, speed, expected):
    assert helper(0., 0., distance, 0., speed) == expected


def travel_case(distance, speed, base=0.):
    network, jobs, catalog, _ = timed_case()
    jobs = [replace(jobs[0], x=distance)]
    crew = replace(catalog.crews['S1'], base_x=base, speed_kmh=speed)
    catalog.crews = {'S1': crew}
    return network, jobs, catalog


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('distance,speed,complete,travel', [
    (1e-6, 1e-7, False, None), (5e-324, 5e-324, True, 1.),
    (1e-6, 35., True, 0.), (1., 5e-324, False, None),
])
def test_all_engines_preserve_travel_time_and_mandatory_orders(method, distance, speed, complete, travel):
    network, jobs, catalog = travel_case(distance, speed)
    plan = readback(method(network, jobs, catalog, 'review_travel'))
    report = verify_plan_feasibility(network, jobs, catalog, plan)
    assert report.is_feasible is complete, report.violations
    assert bool(plan.completed_service_ids) is complete
    if complete:
        assert plan.windows[0].crew_intervals[0].travel_hours == travel
    else:
        assert plan.unscheduled_service_ids == ('S1',)
        assert not plan.is_optimal_proven
    json.dumps(asdict(report), allow_nan=False)


@pytest.mark.parametrize('distance,speed,base', [
    (1., 5e-324, 0.), (1e308, 1., 0.), (1e308, 1., -1e308),
])
def test_checker_reports_nonrepresentable_derived_travel_without_exception(distance, speed, base):
    network, jobs, catalog = travel_case(0., 35.)
    good = readback(METHODS[0](network, jobs, catalog, 'finite_plan'))
    assert verify_plan_feasibility(network, jobs, catalog, good).is_feasible
    changed_jobs = [replace(jobs[0], x=distance)]
    catalog.crews['S1'] = replace(catalog.crews['S1'], speed_kmh=speed, base_x=base)
    # The exported location is also changed so the missing canonical travel is
    # the target violation, rather than only a mismatching coordinate echo.
    good.windows = [replace(w, crew_intervals=tuple(replace(iv, x=distance) for iv in w.crew_intervals))
                    for w in good.windows]
    report = verify_plan_feasibility(network, changed_jobs, catalog, good)
    assert not report.is_feasible
    assert any('deslocamento' in v.lower() and 'represent' in v.lower() for v in report.violations)
    json.dumps(asdict(report), allow_nan=False)
