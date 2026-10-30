"""Run against an unchanged checkout; writes only next to this harness."""
import json, pathlib, sys, subprocess, importlib.metadata, hashlib
ROOT = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / 'src'))
from kgp.experiment import run_full_experiment
new = run_full_experiment(ROOT / 'reproduction')
old = json.loads((REPO / 'reports/kgp_evaluation_summary.json').read_text())
rows = []
assert set(old['scenarios_detail']) == set(new['scenarios_detail'])
for sid, original in old['scenarios_detail'].items():
    reproduced = new['scenarios_detail'][sid]
    assert set(original['methods']) == set(reproduced['methods'])
    for method, a in original['methods'].items():
        b = reproduced['methods'][method]
        x, y = dict(a['metrics']), dict(b['metrics'])
        x.pop('planning_time_sec', None); y.pop('planning_time_sec', None)
        differences = {k: [x.get(k), y.get(k)] for k in x.keys() | y.keys() if x.get(k) != y.get(k)}
        rows.append({'scenario': sid, 'method': method, 'same_metrics_excluding_runtime': not differences, 'differences': differences})
(ROOT / 'reproduction_comparison.json').write_text(json.dumps(rows, indent=2))
env = {'python': sys.version, 'versions': {p: importlib.metadata.version(p) for p in ['numpy','scipy','matplotlib']},
       'commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=REPO, text=True).strip(),
       'source_status': subprocess.check_output(['git','status','--porcelain'], cwd=REPO, text=True)}
(ROOT / 'environment.json').write_text(json.dumps(env, indent=2))
print(f"Metrics/status reproduced: {sum(r['same_metrics_excluding_runtime'] for r in rows)}/{len(rows)}; source unchanged: {not env['source_status']}")
assert len(rows) == 48 and all(r['same_metrics_excluding_runtime'] for r in rows)
