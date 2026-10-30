"""Per-day objective columns require an explicit positive overnight gap."""
from dataclasses import replace
import pytest
from test_rev04_replanning import replanning_instance, METHODS
from kgp.feasibility import verify_plan_feasibility


@pytest.mark.parametrize('method',METHODS)
def test_catalog_without_positive_daily_gap_is_explicitly_unsupported(method):
    n,c,jobs=replanning_instance()
    c=replace(c,window_start_hour=0.,window_end_hour=24.,max_window_duration_hours=24.)
    p=method(n,jobs,c,'NO_GAP')
    assert p.solver_status=='UNSUPPORTED_INPUT'
    assert p.metadata['unsupported_option']=='continuous_24h_catalog'
    assert not p.is_optimal_proven
    assert not verify_plan_feasibility(n,jobs,c,p).is_feasible


def test_catalog_with_one_tick_overnight_gap_remains_supported():
    n,c,jobs=replanning_instance()
    c=replace(c,window_start_hour=0.,window_end_hour=23.9999)
    p=METHODS[-1](n,jobs,c,'GAP')
    assert verify_plan_feasibility(n,jobs,c,p).is_feasible
