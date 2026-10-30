"""Reject nonzero source numbers that disappear before canonical float use."""
from copy import deepcopy
from dataclasses import asdict
import json
import math

import pytest

from kgp.contracts import ExecutionPlan, plan_from_json, strict_json_loads
from kgp.ingestion import _convert, import_bundle
from test_ingestion import TABLES, modify_manifest, write_bundle


@pytest.mark.parametrize('literal', ['1e-400', '-1e-400', '0.01e-400', '1e-1000000'])
def test_strict_reader_rejects_nonzero_numeric_literal_rounded_to_zero(literal):
    with pytest.raises(ValueError, match='underflow'):
        strict_json_loads('{"M":' + literal + '}')


@pytest.mark.parametrize('literal,expected,sign', [
    ('0e-400', 0., 1.), ('-0e-400', -0., -1.), ('0.000e-400', 0., 1.),
    ('5e-324', 5e-324, 1.), ('-5e-324', -5e-324, -1.), ('1e-323', 1e-323, 1.),
])
def test_strict_reader_preserves_true_zero_and_representable_subnormal(literal, expected, sign):
    value = strict_json_loads('{"M":' + literal + '}')['M']
    assert type(value) is float
    assert value == expected
    assert math.copysign(1., value) == sign


def test_strict_reader_leaves_numeric_text_as_text():
    assert strict_json_loads('{"M":"1e-400"}') == {'M': '1e-400'}


@pytest.mark.parametrize('literal', ['1e-400', '-1e-400'])
def test_exported_plan_json_cannot_silently_replace_nonzero_numbers_by_zero(literal):
    plan = ExecutionPlan('underflow', 'CUSTOM', 'numeric_domain', 0,
                         metadata={'source_measurement': '__SOURCE_NUMBER__'})
    raw = json.dumps(asdict(plan), allow_nan=False).replace('"__SOURCE_NUMBER__"', literal)
    with pytest.raises(ValueError, match='underflow'):
        plan_from_json(raw)


@pytest.mark.parametrize('fmt', ['json', 'csv', 'geojson'])
@pytest.mark.parametrize('literal', ['1e-400', '-1e-400'])
def test_bundle_rejects_raw_material_underflow_before_any_planner(tmp_path, fmt, literal):
    tables = deepcopy(TABLES)
    tables['services'][0]['required_materials'] = {'M': '__SOURCE_NUMBER__'}
    path = write_bundle(tmp_path, tables, fmt=fmt)
    services_path = path.parent / f'services.{fmt}'
    raw = services_path.read_bytes()
    marker = b'""__SOURCE_NUMBER__""' if fmt == 'csv' else b'"__SOURCE_NUMBER__"'
    assert raw.count(marker) == 1
    services_path.write_bytes(raw.replace(marker, literal.encode('ascii')))
    result = import_bundle(path)
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(issue.table == 'services' and 'underflow' in issue.message for issue in result.issues)


@pytest.mark.parametrize('fmt', ['json', 'csv', 'geojson'])
@pytest.mark.parametrize('literal,expected', [('0e-400', 0.), ('-0e-400', -0.), ('5e-324', 5e-324)])
def test_bundle_keeps_zero_and_representable_small_material_quantities(tmp_path, fmt, literal, expected):
    tables = deepcopy(TABLES)
    tables['services'][0]['required_materials'] = {'M': '__SOURCE_NUMBER__'}
    path = write_bundle(tmp_path, tables, fmt=fmt)
    services_path = path.parent / f'services.{fmt}'
    raw = services_path.read_bytes()
    marker = b'""__SOURCE_NUMBER__""' if fmt == 'csv' else b'"__SOURCE_NUMBER__"'
    services_path.write_bytes(raw.replace(marker, literal.encode('ascii')))
    result = import_bundle(path)
    assert result.is_valid, result.issues
    assert result.services[0].required_materials == {'M': expected}


@pytest.mark.parametrize('field,dimension,factor', [
    ('x', 'coordinates', .001), ('length_km', 'length', .001),
    ('load_kw', 'power', .001), ('nominal_voltage_kv', 'voltage', .001),
    ('prep_hours', 'duration', 1. / 60.),
])
@pytest.mark.parametrize('value', [5e-324, -5e-324])
def test_unit_conversion_rejects_nonzero_input_rounded_to_zero(field, dimension, factor, value):
    with pytest.raises(ValueError, match='underflow'):
        _convert({field: value}, {dimension: factor})


@pytest.mark.parametrize('value', [5e-324, -5e-324, 0., -0.])
def test_unit_conversion_identity_preserves_representable_numbers(value):
    converted = _convert({'x': value}, {'coordinates': 1.})
    assert converted == {'x': value}
    assert math.copysign(1., converted['x']) == math.copysign(1., value)


@pytest.mark.parametrize('value', [0., -0., 1e-300])
def test_unit_conversion_allows_zero_and_nonzero_representable_result(value):
    converted = _convert({'x': value}, {'coordinates': .001})
    assert math.isfinite(converted['x'])
    assert (converted['x'] != 0.) is (value != 0.)


@pytest.mark.parametrize('fmt', ['json', 'csv', 'geojson'])
@pytest.mark.parametrize('value', [5e-324, -5e-324])
def test_importer_reports_unit_conversion_underflow_at_source_row(tmp_path, fmt, value):
    tables = deepcopy(TABLES)
    tables['nodes'][0]['x'] = value
    path = write_bundle(tmp_path, tables, fmt=fmt)
    modify_manifest(path, lambda m: m['units'].update(coordinates='m'))
    result = import_bundle(path)
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(issue.code == 'INVALID_VALUE' and issue.table == 'nodes' and issue.row == 1
               and issue.record_id == 'SRC' and 'underflow' in issue.message for issue in result.issues)
