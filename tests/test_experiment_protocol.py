from kgp.revalidation import comparable_scenario_ids


def test_comparisons_require_all_four_feasible_complete_portfolios():
    def rows(sid):
        return [{'scenario_id': sid, 'method_name': name, 'is_feasible': True, 'mandatory_completion_rate': 1.0}
                for name in ('B0', 'B1', 'B2', 'KGP')]
    valid = rows('VALID')
    infeasible = rows('INFEASIBLE')
    infeasible[-1]['is_feasible'] = False
    incomplete = rows('INCOMPLETE')
    incomplete[-1]['mandatory_completion_rate'] = 0.5
    missing = rows('MISSING')[:-1]
    assert comparable_scenario_ids(valid + infeasible + incomplete + missing) == {'VALID'}
