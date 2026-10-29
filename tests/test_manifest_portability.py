import hashlib
from kgp.integrity import canonical_text_sha256, verify_manifest


def test_git_lf_checkout_can_match_exact_crlf_archive_manifest(tmp_path):
    p=tmp_path/'source.py'
    original=b'first\r\nsecond\r\n'
    p.write_bytes(original)
    m={'files':{'source.py':hashlib.sha256(original).hexdigest()},
       'canonical_text_sha256':{'source.py':canonical_text_sha256(p)}}
    p.write_bytes(original.replace(b'\r\n',b'\n'))
    assert verify_manifest(tmp_path,m)['line_ending_only_matches']==['source.py']
    assert verify_manifest(tmp_path,m,require_exact_bytes=True)['failures']==['source.py']
    p.write_bytes(b'changed\nsecond\n')
    assert verify_manifest(tmp_path,m)['failures']==['source.py']


def test_binary_files_never_use_text_normalization(tmp_path):
    p=tmp_path/'figure.png'
    p.write_bytes(b'\x89PNG\r\n')
    assert canonical_text_sha256(p) is None
    m={'files':{'figure.png':hashlib.sha256(p.read_bytes()).hexdigest()},
       'canonical_text_sha256':{'figure.png':'untrusted'}}
    p.write_bytes(b'\x89PNG\n')
    assert verify_manifest(tmp_path,m)['failures']==['figure.png']
