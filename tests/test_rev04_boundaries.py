"""One operational tick is a real duration/conflict, never a tolerance waiver."""
from dataclasses import replace
from test_rev04_time import timed_case
from kgp.feasibility import verify_plan_feasibility


def test_one_tick_missing_service_duration_is_rejected():
    n,jobs,c,p=timed_case(.1)
    jobs=[replace(jobs[0],duration_hours=jobs[0].duration_hours+.0001),jobs[1]]
    assert not verify_plan_feasibility(n,jobs,c,p).is_feasible


def test_one_tick_window_overlap_is_rejected():
    n,jobs,c,p=timed_case(-.0001)
    assert not verify_plan_feasibility(n,jobs,c,p).is_feasible


def test_one_tick_falsified_declared_travel_is_rejected():
    n,jobs,c,p=timed_case(.1)
    w=p.windows[0]
    iv=replace(w.crew_intervals[0],travel_hours=.0001)
    p.windows[0]=replace(w,crew_intervals=(iv,))
    assert not verify_plan_feasibility(n,jobs,c,p).is_feasible
