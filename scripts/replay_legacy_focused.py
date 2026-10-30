"""Replay the original focused audit with explicit API/contract adaptations.

The original is preserved. create=True injects a deliberately broken simulator
attribute which the new verifier must not call. Portfolio evidence is populated
for the old custom fixture. NaN rejection at construction is recorded as success.
"""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
original = root / 'reports/validation_scripts/historical/reproduce_focused.py'
text = original.read_text(encoding='utf-8')
text = text.replace("patch('kgp.feasibility.simulate_zone_intervention',bad_sim)",
                    "patch('kgp.feasibility.simulate_zone_intervention',bad_sim,create=True)")
text = text.replace("patch('kgp.feasibility.simulate_zone_intervention',bad_isolation_sim)",
                    "patch('kgp.feasibility.simulate_zone_intervention',bad_isolation_sim,create=True)")
text = text.replace("windows=[w1,w2])", "windows=[w1,w2],completed_service_ids=('S1','S3'))")
text = text.replace("replace(plan,windows=[w1])", "replace(plan,windows=[w1],completed_service_ids=('S1',))")
marker = text.index('# Malformed duration NaN')
nan_line = next(line for line in text[marker:].splitlines() if line.startswith('q=replace('))
text = text[:marker] + '''# New finite contract rejects NaN before a plan exists.
try:
 ''' + nan_line + '''
 r=verify_plan_feasibility(s.network,s.services,s.catalog,q)
 out['nan_execution_bounds']={'feasible':r.is_feasible,'violations':r.violations}
except ValueError as error:
 out['nan_execution_bounds']={'feasible':False,'rejected_at_contract':True,'reason':str(error)}
Path(__file__).with_name('root_results.json').write_text(json.dumps(out,indent=2,ensure_ascii=True),encoding='utf-8')
print(json.dumps(out,indent=2,ensure_ascii=True))
assert not out['tamper_exported_electrical_fields']['feasible']
assert not out['shared_simulator_outage_fault']['feasible']
assert out['shared_simulator_outage_fault']['consumer_hours']>0
assert not out['joint_switch_state_interaction']['feasible']
assert not out['nan_execution_bounds']['feasible']
'''
target = root / '.audit_cache/adapted/reproduce_focused.py'
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(text, encoding='utf-8')
subprocess.run([sys.executable, str(target), str(root)], cwd=root, check=True)
