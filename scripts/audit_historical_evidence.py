"""Read the preserved REV02 archive and compare it with its historical Git base."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / '.audit_cache/REV02_original.zip'
BASE = '84340636c6d3877eacf44d4e19a1b802451057c1'
with zipfile.ZipFile(ARCHIVE) as package:
    comparison = []
    for name in package.namelist():
        if name.endswith('/'):
            continue
        zipped = package.read(name)
        tracked = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)
        normalize = lambda raw: raw.replace(b'\r\n', b'\n') if Path(name).suffix != '.png' else raw
        comparison.append({'path': name, 'matches_historical_git_after_newline_normalization': normalize(zipped) == normalize(tracked)})
    manifest = json.loads(package.read('reports/manifest_sha256.json'))
    hashes = [{'path': name, 'matches': hashlib.sha256(package.read(name)).hexdigest() == digest}
              for name, digest in manifest['files'].items()]
result = {'archive_sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), 'base_commit': BASE,
          'git_matches': sum(r['matches_historical_git_after_newline_normalization'] for r in comparison),
          'git_total': len(comparison), 'manifest_matches': sum(r['matches'] for r in hashes),
          'manifest_total': len(hashes), 'file_comparison': comparison, 'hash_checks': hashes}
(ROOT / 'reports/HISTORICO_REV02_CONFERENCIA.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}, indent=2))
assert result['git_matches'] == result['git_total'] == 23
assert result['manifest_matches'] == result['manifest_total'] == 22
