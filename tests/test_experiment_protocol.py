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


def test_failed_round_cannot_publish_previous_figure_or_stale_plan(tmp_path, monkeypatch):
    from dataclasses import replace
    import json
    import kgp.experiment as experiment
    from kgp.revalidation import run_revalidation
    scenario = experiment.generate_scenario_instance(202601, 'EVAL')
    scenario.services = [replace(s, required_skills=('UNAVAILABLE',)) for s in scenario.services]
    monkeypatch.setattr(experiment, 'DEV_SEEDS', ())
    monkeypatch.setattr(experiment, 'EVAL_SEEDS', (202601,))
    monkeypatch.setattr(experiment, 'generate_scenario_instance', lambda *args: scenario)
    reports = tmp_path / 'reports'
    evidence = reports / 'revalidation_20261029/plans'
    evidence.mkdir(parents=True)
    old = reports / 'comparativo_kgp_desafio07.png'
    old.write_bytes(b'OLD_SUCCESSFUL_FIGURE')
    (evidence / 'STALE.json').write_text('{}')
    result = run_revalidation(tmp_path)
    assert not result['paired_eval_comparisons']
    assert old.read_bytes().startswith(b'\x89PNG')
    manifest = json.loads((reports / 'manifest_sha256.json').read_text(encoding='utf-8'))
    assert not any('STALE' in name for name in manifest['files'])
