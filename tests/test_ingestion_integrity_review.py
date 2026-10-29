"""Adversarial regressions for CSV scope, effective mappings and path cycles."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

import kgp.ingestion as ingestion
from test_ingestion import TABLES, modify_manifest, write_bundle


def csv_two_orders(tmp_path, *, malformed=False, first_title='Work'):
    tables = deepcopy(TABLES)
    tables['services'][0]['title'] = first_title
    tables['services'].append(dict(tables['services'][0], service_id='S2', title='Second',
                                   target_assets=[], required_skills=[], compatible_zone_ids=[]))
    path = write_bundle(tmp_path, tables, fmt='csv')
    columns = [key for key in tables['services'][0] if key != 'title'] + ['title']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows({key: value if isinstance(value, str) else json.dumps(value)
                      for key, value in row.items()} for row in tables['services'])
    raw = stream.getvalue()
    if malformed:
        assert raw.count(',Work\r\n') == 1
        raw = raw.replace(',Work\r\n', ',"Work\r\n')
    (path.parent / 'services.csv').write_bytes(raw.encode('utf-8'))
    return path


def test_unterminated_csv_quote_cannot_absorb_a_mandatory_order(tmp_path):
    result = ingestion.import_bundle(csv_two_orders(tmp_path, malformed=True))
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(issue.code == 'INVALID_FILE' and issue.table == 'services'
               and 'unexpected end of data' in issue.message for issue in result.issues)


@pytest.mark.parametrize('title', ['Work', 'Work on line one\nline two, with "quoted" text'])
def test_strict_csv_retains_both_orders_and_valid_multiline_titles(tmp_path, title):
    result = ingestion.import_bundle(csv_two_orders(tmp_path, first_title=title))
    assert result.is_valid, result.issues
    assert [(s.service_id, s.mandatory) for s in result.services] == [('S1', True), ('S2', True)]
    assert result.provenance['counts']['services'] == 2
    assert result.provenance['record_ids']['services'] == ['S1', 'S2']
    assert result.services[0].title == title


def test_cli_returns_structured_csv_failure_without_traceback(tmp_path):
    path = csv_two_orders(tmp_path, malformed=True)
    script = Path(__file__).resolve().parents[1] / 'scripts/import_kgp_bundle.py'
    run = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
    assert run.returncode == 1
    report = json.loads(run.stdout)
    assert not report['is_valid']
    assert any(issue['code'] == 'INVALID_FILE' and issue['table'] == 'services' for issue in report['issues'])
    assert 'Traceback' not in run.stderr


@pytest.mark.parametrize('fmt', ['json', 'csv', 'geojson'])
@pytest.mark.parametrize('mapping,removed', [
    ({'in_service': 'is_source'}, 'in_service'),
    ({'x': 'y'}, 'x'),
    ({'in_service': 'is_source', 'is_source': 'is_source'}, 'in_service'),
])
def test_effective_field_mapping_cannot_reuse_a_source_column(tmp_path, fmt, mapping, removed):
    tables = deepcopy(TABLES)
    for row in tables['nodes']:
        row.pop(removed)
    path = write_bundle(tmp_path, tables, fmt=fmt)
    modify_manifest(path, lambda m: m['tables']['nodes'].update(field_map=mapping))
    result = ingestion.import_bundle(path)
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    assert any(issue.code == 'INVALID_FIELD_MAP' and issue.table == 'nodes' for issue in result.issues)


def test_mapping_uniqueness_also_covers_optional_fields_absent_from_rows(tmp_path):
    path = write_bundle(tmp_path)
    modify_manifest(path, lambda m: m['tables']['consumers'].update(field_map={'dic_limit_hours': 'fic_limit_count'}))
    result = ingestion.import_bundle(path)
    assert not result.is_valid
    assert any(issue.code == 'INVALID_FIELD_MAP' and issue.table == 'consumers' for issue in result.issues)


@pytest.mark.parametrize('fmt', ['json', 'csv', 'geojson'])
@pytest.mark.parametrize('rename', ['state', 'coordinates'])
def test_distinct_effective_source_names_allow_legitimate_canonical_renames(tmp_path, fmt, rename):
    tables = deepcopy(TABLES)
    if rename == 'state':
        mapping = {'in_service': 'is_source', 'is_source': 'SOURCE_FLAG'}
        for row in tables['nodes']:
            row['SOURCE_FLAG'] = row.pop('is_source')
            row['is_source'] = row.pop('in_service')
    else:
        mapping = {'x': 'y', 'y': 'x'}
        for row in tables['nodes']:
            row['x'], row['y'] = row['y'], row['x']
    path = write_bundle(tmp_path, tables, fmt=fmt)
    modify_manifest(path, lambda m: m['tables']['nodes'].update(field_map=mapping))
    result = ingestion.import_bundle(path)
    assert result.is_valid, result.issues
    for original in TABLES['nodes']:
        node = result.network.nodes[original['node_id']]
        assert (node.is_source, node.in_service, node.x, node.y) == (
            original['is_source'], original['in_service'], original['x'], original['y'])


@pytest.mark.parametrize('target', ['manifest', 'table'])
def test_path_resolve_symlink_runtime_error_becomes_a_specific_diagnostic(tmp_path, monkeypatch, target):
    path = write_bundle(tmp_path)
    loop_path = path if target == 'manifest' else path.parent / 'nodes.json'
    actual_resolve = Path.resolve

    def resolve_with_loop(candidate, *args, **kwargs):
        if candidate == loop_path:
            raise RuntimeError(f"Symlink loop from '{candidate}'")
        return actual_resolve(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', resolve_with_loop)
    result = ingestion.import_bundle(path)
    assert not result.is_valid
    assert result.network is None and result.catalog is None
    expected_table = 'manifest' if target == 'manifest' else 'nodes'
    assert any(issue.code == 'SYMLINK_LOOP' and issue.table == expected_table
               and 'cycle' in issue.message.lower() for issue in result.issues)


def test_unrelated_runtime_errors_are_not_masked_as_path_diagnostics(tmp_path, monkeypatch):
    path = write_bundle(tmp_path)

    def broken_record_reader(*args, **kwargs):
        raise RuntimeError('unrelated internal parser failure')

    monkeypatch.setattr(ingestion, '_records', broken_record_reader)
    with pytest.raises(RuntimeError, match='unrelated internal parser failure'):
        ingestion.import_bundle(path)


def test_cli_real_symlink_cycle_keeps_quality_json_when_supported(tmp_path):
    path = write_bundle(tmp_path)
    loop = tmp_path / 'loop.json'
    try:
        loop.symlink_to(loop.name)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'OS does not permit this symlink fixture: {exc}')
    modify_manifest(path, lambda m: m['tables']['nodes'].update(path='loop.json'))
    script = Path(__file__).resolve().parents[1] / 'scripts/import_kgp_bundle.py'
    run = subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)
    assert run.returncode == 1
    report = json.loads(run.stdout)
    assert not report['is_valid']
    assert any(issue['table'] == 'nodes' and issue['severity'] == 'ERROR' for issue in report['issues'])
    assert 'Traceback' not in run.stderr
