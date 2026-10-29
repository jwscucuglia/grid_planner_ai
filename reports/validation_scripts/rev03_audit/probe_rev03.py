"""Independent tiny input probes; no repository mutation and no full-suite run."""
from dataclasses import replace
from pathlib import Path
import json
import sys

REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "src"))
from kgp.contracts import (ConsumerUnit, CrewResource, ExecutionPlan, GridAsset,
    GridBranch, GridNetwork, GridNode, GridSwitch, IsolationZoneSpec,
    ResourceCatalog, ServiceOrder, ServiceType, SwitchState)
from kgp.optimizer import run_kgp_optimizer, ObjectiveWeights, compute_exact_plan_objective
from kgp.baselines import (run_baseline_b0_individual, run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy)
from kgp.feasibility import verify_plan_feasibility
from kgp.metrics import compute_plan_metrics
from kgp.validation import validate_problem_and_plan

def instance(days=(1,), crew_days=None, mandatory=True):
    net = GridNetwork("independent_probe", "1", 0)
    net.nodes = {"S": GridNode("S", "source", "F", 0., 0., is_source=True),
                 "N": GridNode("N", "load", "F", 0., 0.)}
    net.branches = {"B": GridBranch("B", "S", "N", "F")}
    net.switches = {"SW": GridSwitch("SW", "B", SwitchState.CLOSED, SwitchState.CLOSED)}
    net.assets = {"A": GridAsset("A", "N", None, "F", "test")}
    net.consumers = {"C": ConsumerUnit("C", "N", "A")}
    net.isolation_zones = {"Z": IsolationZoneSpec("Z", "zone", "F", ("SW",),
        prep_duration_hours=.1, recomp_duration_hours=.1)}
    cat = ResourceCatalog({"T": CrewResource("T", "team", ("s",),
        days if crew_days is None else crew_days, prep_hours=.1)}, {}, days,
        max_window_duration_hours=1.)
    jobs = [ServiceOrder("J", "job", ServiceType.OBRA, ("A",), mandatory,
        0, days[0], days[-1], .5, ("s",), compatible_zone_ids=("Z",),
        prep_hours=.1, recomp_hours=.1)]
    return net, cat, jobs

def summary(net, cat, jobs, plan):
    f = verify_plan_feasibility(net, jobs, cat, plan)
    m = compute_plan_metrics(net, jobs, plan, f)
    return dict(feasible=f.is_feasible, violations=f.violations,
        status=plan.solver_status, optimal=plan.is_optimal_proven,
        metrics_optimal=m.is_optimal_proven,
        objective=plan.solver_objective, bound=plan.solver_bound, gap=plan.solver_gap,
        completed=list(plan.completed_service_ids), omitted=list(plan.unscheduled_service_ids),
        windows=[dict(day=w.day, start=w.start_hour, end=w.end_hour,
                      services=w.service_ids) for w in plan.windows], metadata=plan.metadata)

out = {}
for penalty in (0., 50., 250.):
    net, cat, jobs = instance(mandatory=False)
    w = ObjectiveWeights(optional_unscheduled_penalty=penalty)
    p = run_kgp_optimizer(net, jobs, cat, "optional", weights=w)
    out[f"optional_{penalty:g}"] = summary(net, cat, jobs, p)
    out[f"optional_{penalty:g}"]["recount"] = compute_exact_plan_objective(p, w, jobs)

net, cat, jobs = instance(days=(1, 2), crew_days=(1,))
empty = ExecutionPlan("empty", "audit", "past_days", 2, unscheduled_service_ids=("J",))
assert validate_problem_and_plan(net, jobs, cat, empty) == []
out["decision_day_2_past_crew_only"] = {
    method: summary(net, cat, jobs, fn(net, jobs, cat, "past_days", decision_day=2))
    for method, fn in (("B0", run_baseline_b0_individual),
        ("B1", run_baseline_b1_geographic), ("B2", run_baseline_b2_topological_greedy),
        ("KGP", run_kgp_optimizer))}

net, cat, jobs = instance(days=(1, 2))
out["decision_day_2_both_days_available"] = {
    method: summary(net, cat, jobs, fn(net, jobs, cat, "past_days_both", decision_day=2))
    for method, fn in (("B0", run_baseline_b0_individual),
        ("B1", run_baseline_b1_geographic), ("B2", run_baseline_b2_topological_greedy),
        ("KGP", run_kgp_optimizer))}

# A valid future plan exists (mandatory J on day 2, expired optional O omitted).
# Past bundling is cheaper, so the KGP historical choice is not just tie breaking.
net, cat, jobs = instance(days=(1, 2))
cat.max_window_duration_hours = 2.
jobs.append(replace(jobs[0], service_id="O", mandatory=False, deadline_day=1))
out["decision_day_2_past_bundle_beats_valid_future"] = {
    "KGP": summary(net, cat, jobs,
        run_kgp_optimizer(net, jobs, cat, "past_bundle", decision_day=2))}
future_cat = replace(cat, planning_days=(2,))
future_plan = run_kgp_optimizer(net, jobs, future_cat, "future_reference", decision_day=2)
out["decision_day_2_past_bundle_beats_valid_future"]["future_reference"] = summary(
    net, cat, jobs, future_plan)

Path(__file__).with_name("targeted_result.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
