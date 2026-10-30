"""Material quantities have no operational rounding grid or physical epsilon."""
from dataclasses import asdict, replace
from fractions import Fraction
import json
import math

import pytest

from kgp.baselines import (
    run_baseline_b0_individual, run_baseline_b1_geographic,
    run_baseline_b2_topological_greedy,
)
from kgp.contracts import plan_from_json
from kgp.feasibility import verify_plan_feasibility
from kgp.optimizer import run_kgp_optimizer
from kgp.scheduling import build_window_schedule
from test_rev04_time import timed_case


METHODS = [run_baseline_b0_individual, run_baseline_b1_geographic,
           run_baseline_b2_topological_greedy, run_kgp_optimizer]


def material_case(quantities, stock, *, separate_days=False):
    network, original, catalog, _ = timed_case()
    days = tuple(range(1, len(quantities) + 1)) if separate_days else (1,)
    catalog.planning_days = days
    crew = catalog.crews['S1']
    catalog.crews = {'S1': replace(crew, available_days=days)}
    catalog.materials_inventory = {'M': stock} if stock else {}
    services = [replace(original[0], service_id=f'S{i}', required_materials={'M': q},
                        ready_at_day=i if separate_days else 1,
                        deadline_day=i if separate_days else 1)
                for i, q in enumerate(quantities, 1)]
    return network, services, catalog


def readback(plan):
    return plan_from_json(json.dumps(asdict(plan), allow_nan=False))


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('quantity,stock', [(0.00004, 0.), (0.33333, 0.3333),
                                           (5e-324, 0.), (math.nextafter(1., math.inf), 1.)])
def test_positive_material_deficit_cannot_complete_mandatory_order(method, quantity, stock):
    network, services, catalog = material_case([quantity], stock)
    plan = readback(method(network, services, catalog, 'material_deficit'))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert not plan.completed_service_ids
    assert not report.is_feasible
    assert not plan.is_optimal_proven


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('quantity', [0.00004, 0.33333, 5e-324])
def test_exact_small_stock_is_usable_and_survives_json(method, quantity):
    network, services, catalog = material_case([quantity], quantity)
    plan = readback(method(network, services, catalog, 'material_exact_stock'))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert report.is_feasible, report.violations
    assert plan.completed_service_ids == ('S1',)
    assert plan.windows[0].materials_used == {'M': quantity}
    assert report.recalculated_materials == {'M': quantity}


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('stock,complete', [(0.99999, True), (0.99998, False)])
def test_stock_is_conserved_across_three_separate_days(method, stock, complete):
    network, services, catalog = material_case([0.33333] * 3, stock, separate_days=True)
    plan = readback(method(network, services, catalog, 'material_multiple_days'))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert report.is_feasible is complete, report.violations
    assert (len(plan.completed_service_ids) == 3) is complete
    if not complete:
        assert not plan.is_optimal_proven


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
def test_decimal_point_one_and_two_fit_point_three_stock(method):
    network, services, catalog = material_case([0.1, 0.2], 0.3)
    plan = readback(method(network, services, catalog, 'material_decimal_sum'))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert report.is_feasible, report.violations
    assert report.recalculated_materials == {'M': 0.3}


@pytest.mark.parametrize('method', METHODS, ids=['B0', 'B1', 'B2', 'KGP'])
@pytest.mark.parametrize('quantities,stock', [([1., 1e-28], 1.), ([5e-324, 5e-324], 5e-324),
                                            ([1e308, 1e308], 1.7e308)])
def test_sum_cannot_lose_tiny_parcels_or_overflow_into_a_valid_plan(method, quantities, stock):
    network, services, catalog = material_case(quantities, stock, separate_days=True)
    plan = readback(method(network, services, catalog, 'material_exact_accumulation'))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert not report.is_feasible
    assert len(plan.completed_service_ids) < 2
    assert not plan.is_optimal_proven


@pytest.mark.parametrize('quantity,echo', [(0.33333, 0.3333), (5e-324, 0.)])
def test_forged_material_echo_is_rejected_after_strict_json(quantity, echo):
    network, services, catalog = material_case([quantity], quantity)
    plan = run_baseline_b0_individual(network, services, catalog, 'material_echo')
    plan.windows[0] = replace(plan.windows[0], materials_used={'M': echo})
    report = verify_plan_feasibility(network, services, catalog, readback(plan))
    assert not report.is_feasible
    assert any('materia' in error.lower() for error in report.violations)


def test_window_echo_is_canonical_float_of_exact_sum_without_ulp_tolerance():
    network, services, catalog = material_case([0.1, 0.2], 0.3)
    window, error = build_window_schedule(network, catalog, 'Z', 1, 'W', services)
    assert window is not None, error
    assert window.materials_used == {'M': 0.3}
    plan = run_baseline_b0_individual(network, services, catalog, 'material_echo_sum')
    plan.windows = [replace(window, materials_used={'M': 0.30000000000000004})]
    report = verify_plan_feasibility(network, services, catalog, readback(plan))
    assert not report.is_feasible


@pytest.mark.parametrize('quantities,stock', [([1., 1e-28], 1.), ([1e308, 1e308], 1.7e308)])
def test_checker_independently_accumulates_exact_requirements_of_forged_plan(quantities, stock):
    network, original, catalog, plan = timed_case(0.1)
    services = [replace(s, required_materials={'M': q}) for s, q in zip(original, quantities)]
    catalog.materials_inventory = {'M': stock}
    plan.windows = [replace(w, materials_used={'M': q}) for w, q in zip(plan.windows, quantities)]
    report = verify_plan_feasibility(network, services, catalog, readback(plan))
    assert not report.is_feasible
    assert any('estoque' in error.lower() for error in report.violations)
    assert Fraction(report.recalculated_materials_exact['M']) > Fraction(str(stock))
    json.dumps(asdict(report), allow_nan=False)
    if quantities[0] == 1e308:
        assert 'M' not in report.recalculated_materials
        assert any('represent' in error.lower() for error in report.violations)


def test_overflowing_window_material_sum_is_rejected_without_crash():
    network, services, catalog = material_case([1e308, 1e308], 1.7e308)
    window, error = build_window_schedule(network, catalog, 'Z', 1, 'W', services)
    assert window is None
    assert 'material' in error.lower()


@pytest.mark.parametrize('quantity', [1e-100, 5e-324])
def test_optimizer_global_stock_row_limits_optional_jobs_on_different_days(quantity):
    network, services, catalog = material_case([quantity, quantity], quantity, separate_days=True)
    services = [replace(s, mandatory=False) for s in services]
    from kgp.optimizer import ObjectiveWeights
    plan = readback(run_kgp_optimizer(network, services, catalog, 'material_scaled_row',
                                     weights=ObjectiveWeights(optional_unscheduled_penalty=1000.)))
    report = verify_plan_feasibility(network, services, catalog, plan)
    assert report.is_feasible, report.violations
    assert len(plan.completed_service_ids) == 1
