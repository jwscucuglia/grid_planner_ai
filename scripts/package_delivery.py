"""Package exact release bytes and verify every archived SHA-256 entry."""
from pathlib import Path
import datetime
import hashlib
import json
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from kgp import __version__
from kgp.integrity import canonical_text_sha256
CANDIDATURE_FILES = {
    'reports/AUDITORIA_DOCUMENTAL_D07.md', 'reports/AUDITORIA_INTEGRAL_D07_REV03.md',
    'reports/FONTES_OFICIAIS_SHA256_D07.json', 'reports/INVENTARIO_DRIVE_D07_20261029.csv',
    'reports/MATRIZ_ADERENCIA_OFICIAL_D07.md', 'reports/ORCAMENTO_TRANSPOSTO_CONFERENCIA.json',
    'reports/ORCAMENTO_VALIDACAO_ARITMETICA.json', 'reports/SINCRONIZACAO_ORCAMENTO_ANEXOS_D07.json',
    'reports/SINCRONIZACAO_DRIVE_D07_REV03.json', 'scripts/validate_budget.py',
    'reports/AUDITORIA_INTEGRAL_D07_REV04.md', 'reports/SINCRONIZACAO_DRIVE_D07_REV04.json',
    'reports/SINCRONIZACAO_INGESTAO01_DOCS.json',
    'reports/AUDITORIA_BANCA_CANDIDATURA_20261029.md',
    'reports/SINCRONIZACAO_BANCA_20261029.json', 'reports/FECHAMENTO_BANCA_20261029.md',
}
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

sources = []
for directory in ('src', 'tests', 'configs', 'data', 'reports', 'docs', 'scripts'):
    sources.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                   and p.suffix not in ('.pyc', '.pyo') and p != ROOT / 'reports/manifest_sha256.json')
sources += [ROOT / name for name in ('.gitignore', '.gitattributes', 'README.md', 'AGENTS.md', 'pyproject.toml', 'requirements.txt', 'requirements-reproducibility.txt')]
config = json.loads((ROOT / 'configs/protocol_and_seeds.json').read_text(encoding='utf-8'))
manifest = {'package_version': __version__, 'source_commit': config['source_commit'],
            'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'scope': 'Public technical release files; candidature and budget remain in the private Drive bundle. Excludes this manifest itself.',
            'files': {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(sources))
                      if p.relative_to(ROOT).as_posix() not in CANDIDATURE_FILES}}
manifest['canonical_text_sha256'] = {p.relative_to(ROOT).as_posix(): canonical_text_sha256(p)
    for p in sorted(set(sources)) if p.relative_to(ROOT).as_posix() in manifest['files']
    and canonical_text_sha256(p) is not None}
manifest['canonical_policy'] = 'Only UTF-8 text CRLF to LF; exact hashes remain mandatory for original archive bytes and binary files.'
target = ROOT / 'reports/manifest_sha256.json'
target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8', newline='\n')
archive = ROOT / ('dist/KGP_D07_PACOTE_AUDITADO_' + config.get('release_revision', 'REV04') + '_20261029.zip')
archive.parent.mkdir(exist_ok=True)
complete = dict(manifest)
complete['scope'] = 'Complete private Drive bundle, including candidature review; do not publish this bundle in the public repository.'
complete['files'] = {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(sources))}
complete['files']['reports/manifest_sha256.json'] = digest(target)
complete['canonical_text_sha256'] = {p.relative_to(ROOT).as_posix(): canonical_text_sha256(p)
    for p in sorted(set(sources)) if canonical_text_sha256(p) is not None}
complete['canonical_text_sha256']['reports/manifest_sha256.json'] = canonical_text_sha256(target)
complete_path = ROOT / 'dist/manifest_completo_sha256.json'
complete_path.write_text(json.dumps(complete, indent=2, ensure_ascii=False), encoding='utf-8', newline='\n')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
    for name in complete['files']:
        package.write(ROOT / name, name)
    package.write(complete_path, 'manifest_completo_sha256.json')
with zipfile.ZipFile(archive) as package:
    for name, expected in complete['files'].items():
        assert hashlib.sha256(package.read(name)).hexdigest() == expected, name
    assert json.loads(package.read('reports/manifest_sha256.json')) == manifest
    assert json.loads(package.read('manifest_completo_sha256.json')) == complete
print(json.dumps({'archive': str(archive), 'source_commit': config['source_commit'], 'files_hashed': len(manifest['files']),
                  'complete_private_bundle_hashes': len(complete['files']), 'archive_entries': len(complete['files']) + 1,
                  'archive_sha256': digest(archive)}, indent=2))
