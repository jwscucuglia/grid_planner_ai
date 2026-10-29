#!/usr/bin/env python
"""Import a KGP bundle and emit its machine-readable quality report."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kgp.ingestion import import_bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--report", type=Path, help="also write the quality report to this JSON file")
    args = parser.parse_args(argv)
    result = import_bundle(args.manifest)
    report = dict(is_valid=result.is_valid, issues=[asdict(i) for i in result.issues], provenance=result.provenance)
    text = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
