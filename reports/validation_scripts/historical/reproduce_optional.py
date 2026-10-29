import sys,json
from pathlib import Path
if len(sys.argv)<2:
    sys.argv.append('/workspace/scratch/1b1b52c852ec/grid_planner_ai_rev02_audit')
source=Path(__file__).with_name('reproduce_optimizer.py').read_text()
exec(source.split('out={}')[0])
net,cat,jobs=instance()
jobs=[replace(jobs[0],mandatory=False)]
p=run_kgp_optimizer(net,jobs,cat,'optional_omission')
b=run_baseline_b0_individual(net,jobs,cat,'optional_omission')
out={'KGP':summarize(net,cat,jobs,p),'KGP_metadata':p.metadata,'B0':summarize(net,cat,jobs,b)}
Path(__file__).with_name('optional_result.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
