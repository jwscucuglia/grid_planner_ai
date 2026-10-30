"""Exact archive integrity plus explicit, bounded Git line-ending equivalence."""
from pathlib import Path
import hashlib

TEXT_EXTENSIONS = {'.py', '.json', '.csv', '.md', '.toml', '.txt', '.log', '.patch'}


def canonical_text_sha256(path: Path):
    """Canonicalize only UTF-8 text CRLF→LF; binary/UTF-16 stays byte-exact."""
    if path.suffix.lower() not in TEXT_EXTENSIONS and path.name != '.gitignore':
        return None
    raw = path.read_bytes()
    if b'\0' in raw:
        return None
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def verify_manifest(root: Path, manifest: dict, require_exact_bytes=False):
    root = root.resolve()
    canonical = manifest.get('canonical_text_sha256', {})
    exact, normalized, failures = 0, [], []
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append(name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            exact += 1
        elif (not require_exact_bytes and name in canonical and
              canonical_text_sha256(path) == canonical[name]):
            normalized.append(name)
        else:
            failures.append(name)
    return {'files_checked': len(manifest['files']), 'exact_matches': exact,
            'line_ending_only_matches': normalized, 'failures': failures}
