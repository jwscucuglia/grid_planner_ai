"""Frozen, explicitly non-blind synthetic revalidation and evidence exports."""
from dataclasses import asdict
from pathlib import Path
import csv
import datetime
import importlib.metadata
import json
import platform
import subprocess
import sys

from . import __version__
from .contracts import plan_from_json
from .feasibility import verify_plan_feasibility
from .metrics import compute_plan_metrics
from .optimizer import ObjectiveWeights, compute_exact_plan_objective


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def comparable_scenario_ids(rows):
    """Only the same completed, verified mandatory portfolio may earn benefit."""
    ids = {r['scenario_id'] for r in rows}
    return {sid for sid in ids if
            {r['method_name'] for r in rows if r['scenario_id'] == sid} == {'B0', 'B1', 'B2', 'KGP'}
            and all(r['is_feasible'] and r['mandatory_completion_rate'] == 1.0
                    for r in rows if r['scenario_id'] == sid)}


def run_revalidation(base_dir):
    from .experiment import (DEV_SEEDS, EVAL_SEEDS, generate_scenario_instance,
                             evaluate_scenario_all_methods, generate_comparative_figure,
                             compute_sha256, _bootstrap_ci_mean, build_reference_network)
    base_dir = Path(base_dir)
    source_root = Path(__file__).resolve().parents[2]
    reports = base_dir / 'reports'
    evidence = reports / 'revalidation_REV04_20261029'
    evidence.mkdir(parents=True, exist_ok=True)
    scenarios = [generate_scenario_instance(seed, split) for split, seeds in
                 [('DEV', DEV_SEEDS), ('EVAL', EVAL_SEEDS)] for seed in seeds]
    source_paths = sorted(list((source_root / 'src/kgp').glob('*.py')) +
                          list((source_root / 'tests').glob('*.py')) +
                          [source_root / 'pyproject.toml', source_root / 'requirements.txt'])
    source_hashes = {p.relative_to(source_root).as_posix(): compute_sha256(p) for p in source_paths}
    inputs = {}
    for sc in scenarios:
        target = evidence / 'inputs' / (sc.scenario_id + '.json')
        _write_json(target, asdict(sc))
        inputs[target.relative_to(base_dir).as_posix()] = compute_sha256(target)
    _write_json(base_dir / 'data/reference_network.json', asdict(build_reference_network()))
    try:
        source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source_root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        source_commit = 'UNAVAILABLE'
    weights = ObjectiveWeights()
    config = {
        'release_revision': 'REV04', 'evidence_directory': evidence.relative_to(base_dir).as_posix(),
        'project': 'KAPTUS GRID PLANNER — D07', 'package_version': __version__,
        'execution_timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source_commit': source_commit, 'source_sha256': source_hashes, 'input_sha256': inputs,
        'python_version': sys.version, 'platform': platform.platform(),
        'dependencies': {p: importlib.metadata.version(p) for p in ('numpy', 'scipy', 'matplotlib', 'networkx', 'pytest')},
        'dev_seeds': list(DEV_SEEDS), 'eval_seeds': list(EVAL_SEEDS),
        'evaluation_design': 'SYNTHETIC_REEVALUATION_OF_PREVIOUSLY_OBSERVED_CASES_NOT_BLIND',
        'calibrated_b1_radius_km_on_dev': 0.75,
        'b1_radius_origin': 'Valor DEV documentado na REV02, mantido sem nova seleção nesta rodada.',
        'objective_weights': asdict(weights), 'electrical_window_policy': 'SERIAL_WINDOWS_ONLY',
        'dg_policy': 'UNSUPPORTED_ISLANDING_REJECTED_BEFORE_PLANNING',
        'operational_resolution_hours': 0.0001,
        'interval_adjacency_rule': 'Integer tick equality only; any positive tick gap is a restoration',
        'stage_admissibility_rule': 'Every positive-duration stage must pass electrical checks',
        'transfer_policy': 'Required transfer failure rejects zone; optional failed transfer stays open and actual state is audited',
        'initial_supply_policy': 'All consumers initially supplied; preexisting outages rejected explicitly.',
        'b2_local_search': 'One pass, at most 30 alternative zones, packages/days fixed; rebuild all routes; accept only feasible lower default J.',
        'solver_catalog_limits': {'max_package_size': 5, 'max_windows_per_day': 5, 'max_states_per_depth': 120},
        'analysis_rule': 'Parear somente cenários com os 4 métodos factíveis e 100% da mesma carteira obrigatória; excluir falhas do benefício e preservá-las nas saídas.',
        'statistical_scope': 'Descrição dos casos desenhados na mesma rede; bootstrap empírico não é inferência sobre desempenho Cemig.',
        'freeze_rule': 'Código, objetivo, dados e regra de análise gravados antes de executar os métodos; hashes conferidos após execução.',
        'anti_calibration_statement': 'Sem ajuste para 1,7% ou benefício desejado. Sementes conhecidas mantidas; não é nova avaliação retida.',
    }
    config_path = base_dir / 'configs/protocol_and_seeds.json'
    _write_json(config_path, config)
    frozen_hash = compute_sha256(config_path)
    rows, details, paired = [], {}, []
    current_artifacts = []
    for sc in scenarios:
        print('RUN ' + sc.scenario_id, flush=True)
        outputs = evaluate_scenario_all_methods(sc, b1_radius_km=0.75)
        details[sc.scenario_id] = {'split': sc.split, 'seed': sc.seed, 'regime_description': sc.regime_description, 'methods': {}}
        for method, (plan, _, _) in outputs.items():
            plan_path = evidence / 'plans' / f'{sc.scenario_id}_{method}.json'
            _write_json(plan_path, asdict(plan))
            current_artifacts.append(plan_path)
            exported = plan_from_json(plan_path.read_text(encoding='utf-8'))
            feasible = verify_plan_feasibility(sc.network, sc.services, sc.catalog, exported)
            metrics = compute_plan_metrics(sc.network, sc.services, exported, feasible)
            verification_path = evidence / 'verification' / f'{sc.scenario_id}_{method}.json'
            _write_json(verification_path, asdict(feasible))
            current_artifacts.append(verification_path)
            row = {'scenario_id': sc.scenario_id, 'split': sc.split, 'seed': sc.seed, **asdict(metrics),
                   'violations_count': len(feasible.violations), 'warnings_count': len(feasible.warnings)}
            rows.append(row)
            details[sc.scenario_id]['methods'][method] = {'plan': asdict(exported), 'feasibility': asdict(feasible), 'metrics': asdict(metrics)}
        print('VERIFIED ' + sc.scenario_id + ' ' + str(sum(r['is_feasible'] for r in rows[-4:])) + '/4', flush=True)
    comparable = comparable_scenario_ids(rows)
    eval_rows = [r for r in rows if r['split'] == 'EVAL']
    for sc in scenarios:
        if sc.split != 'EVAL' or sc.scenario_id not in comparable:
            continue
        methods = details[sc.scenario_id]['methods']
        comp = {'scenario_id': sc.scenario_id, 'seed': sc.seed, 'regime_description': sc.regime_description}
        for key, metric in [('windows', 'interruption_windows_count'), ('consumer_hours', 'consumer_hours_total'),
                            ('repeat_consumers', 'repeat_interrupted_consumers_count'), ('physical_maneuvers', 'physical_maneuvers_count')]:
            values = {m: d['metrics'][metric] for m, d in methods.items()}
            values.update({'diff_KGP_vs_' + m: round(values['KGP'] - values[m], 4) for m in ('B0', 'B1', 'B2')})
            comp[key] = values
        values = {m: compute_exact_plan_objective(plan_from_json(json.dumps(methods[m]['plan'], allow_nan=False)), weights, sc.services)
                  for m in ('B2', 'KGP')}
        values['diff_KGP_vs_B2'] = round(values['KGP'] - values['B2'], 4)
        comp['combined_objective_J'] = values
        paired.append(comp)
    aggregate = {}
    for method in ('B0', 'B1', 'B2', 'KGP'):
        all_m = [r for r in eval_rows if r['method_name'] == method]
        sample = [r for r in all_m if r['scenario_id'] in comparable]
        stats = {'feasible_instances': sum(r['is_feasible'] for r in all_m), 'total_instances': len(all_m),
                 'comparable_instances': len(sample), 'mandatory_completion_rate_mean':
                 float(sum(r['mandatory_completion_rate'] for r in sample) / len(sample)) if sample else None,
                 'planning_time_sec_mean': sum(r['planning_time_sec'] for r in sample) / len(sample) if sample else None}
        for key, metric in [('interruption_windows', 'interruption_windows_count'), ('physical_maneuvers', 'physical_maneuvers_count'),
                            ('consumer_hours', 'consumer_hours_total'), ('repeat_consumers', 'repeat_interrupted_consumers_count'),
                            ('coupling_rate', 'coupling_rate')]:
            stats[key + '_mean_ci95'] = list(_bootstrap_ci_mean([r[metric] for r in sample])) if sample else [None, None, None]
        aggregate[method] = stats
    head = {}
    for title, key in [('by_windows', 'windows'), ('by_consumer_hours', 'consumer_hours'),
                       ('by_repeat_consumers', 'repeat_consumers'), ('by_physical_maneuvers', 'physical_maneuvers'),
                       ('by_combined_objective_J', 'combined_objective_J')]:
        delta = [c[key]['diff_KGP_vs_B2'] for c in paired]
        head[title] = {'wins': sum(d < -1e-4 for d in delta), 'ties': sum(abs(d) <= 1e-4 for d in delta), 'losses': sum(d > 1e-4 for d in delta)}
    summary = {'protocol_config': config, 'frozen_protocol_sha256': frozen_hash, 'eval_aggregated_metrics': aggregate,
               'paired_eval_comparisons': paired, 'kgp_vs_b2_head_to_head': head, 'scenarios_detail': details,
               'noncomparable_scenarios': sorted({r['scenario_id'] for r in rows} - comparable)}
    with (reports / 'kgp_evaluation_results.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _write_json(reports / 'kgp_evaluation_summary.json', summary)
    eligible_figure_rows = [r for r in rows if r['scenario_id'] in comparable]
    if any(r['split'] == 'EVAL' for r in eligible_figure_rows):
        generate_comparative_figure(eligible_figure_rows, reports / 'comparativo_kgp_desafio07.png')
    else:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(16, 9), dpi=120, facecolor='white')
        ax.axis('off')
        ax.text(0.5, 0.5, 'Sem comparações factíveis nesta rodada\nFalhas preservadas; sem benefício calculado.',
                ha='center', va='center', fontsize=22, transform=ax.transAxes)
        fig.savefig(reports / 'comparativo_kgp_desafio07.png', dpi=120, facecolor='white')
        plt.close(fig)
    _write_report(reports / 'RELATORIO_EXECUCAO_KGP.md', summary, rows)
    for p in source_paths:
        if source_hashes[p.relative_to(source_root).as_posix()] != compute_sha256(p):
            raise RuntimeError(f'Código alterado após congelamento: {p}')
    for relative, digest in inputs.items():
        if compute_sha256(base_dir / relative) != digest:
            raise RuntimeError('Entrada alterada após congelamento: ' + relative)
    if frozen_hash != compute_sha256(config_path):
        raise RuntimeError('Protocolo alterado durante a execução')
    tracked = sorted(source_paths + [base_dir / relative for relative in inputs] +
                     current_artifacts +
                     [config_path, base_dir / 'data/reference_network.json', reports / 'kgp_evaluation_results.csv',
                      reports / 'kgp_evaluation_summary.json', reports / 'RELATORIO_EXECUCAO_KGP.md',
                      reports / 'comparativo_kgp_desafio07.png', reports / 'pytest_execution.log'])
    manifest = {'package_version': __version__, 'source_commit': source_commit,
                'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'files': {p.relative_to(base_dir if p.is_relative_to(base_dir) else source_root).as_posix(): compute_sha256(p)
                          for p in tracked if p.exists()}}
    _write_json(reports / 'manifest_sha256.json', manifest)
    print(f'END {sum(r["is_feasible"] for r in rows)}/{len(rows)} feasible; {len(paired)} EVAL pairs', flush=True)
    return summary


def _write_report(path, summary, rows):
    cfg = summary['protocol_config']
    lines = ['# RELATÓRIO DE EXECUÇÃO — KGP ' + cfg['package_version'] + ' / REV04', '',
             'Reavaliação sintética de casos conhecidos — Desafio 07 Inova Cemig.Lab Ciclo 5.', '',
             f'Execução UTC: {cfg["execution_timestamp"]}. Código: `{cfg["source_commit"]}`; SHA-256 por arquivo no protocolo.',
             f'{sum(r["is_feasible"] for r in rows)}/{len(rows)} planos factíveis após leitura do JSON exportado, '
             f'{len(summary["paired_eval_comparisons"])} comparações EVAL admitidas. Carteira obrigatória e factibilidade são condições do benefício.', '',
             '## Resultados descritivos — 8 casos EVAL conhecidos, uma rede de referência', '',
             '| Método | Factíveis | Janelas médias | Manobras médias | Acoplamento | Reincidentes médios | Clientes-hora médios | Tempo médio (s) |',
             '|---|---|---|---|---|---|---|---|']
    for name, a in summary['eval_aggregated_metrics'].items():
        mean = lambda key: 'n/a' if a[key + '_mean_ci95'][0] is None else f'{a[key + "_mean_ci95"][0]:.4f}'
        t = a['planning_time_sec_mean']
        lines.append(f'| {name} | {a["feasible_instances"]}/{a["total_instances"]} | {mean("interruption_windows")} | '
                     f'{mean("physical_maneuvers")} | {mean("coupling_rate")} | {mean("repeat_consumers")} | '
                     f'{mean("consumer_hours")} | {t:.4f} |' if t is not None else f'| {name} | sem comparação factível |')
    lines += ['', '## Balanço KGP × B2 — valores menores são melhores', '',
              '| Métrica | Ganhos | Empates | Perdas |', '|---|---|---|---|']
    for metric, count in summary['kgp_vs_b2_head_to_head'].items():
        lines.append(f'| {metric} | {count["wins"]} | {count["ties"]} | {count["losses"]} |')
    lines += ['', '## Resultados por caso', '',
              '| Caso | Janelas B0/B1/B2/KGP | Clientes-hora B0/B1/B2/KGP | Δ KGP−B2 clientes-hora |', '|---|---|---|---|']
    for comp in summary['paired_eval_comparisons']:
        ws = '/'.join(str(comp['windows'][m]) for m in ('B0', 'B1', 'B2', 'KGP'))
        ch = '/'.join(f'{comp["consumer_hours"][m]:.2f}' for m in ('B0', 'B1', 'B2', 'KGP'))
        lines.append(f'| {comp["scenario_id"]} | {ws} | {ch} | {comp["consumer_hours"]["diff_KGP_vs_B2"]:+.4f} |')
    lines += ['', '## Interpretação e limites', '',
              '- H1/H2/H3 recebem evidência apenas nos casos representados. As entradas DEV/EVAL já foram observadas; não há novo cegamento nem amostra independente de redes Cemig.',
              '- O gerador, as sementes e o raio B1 de 0,75 km foram mantidos. Código, entradas, pesos e análise foram congelados antes de executar esta rodada. O protocolo contém os hashes e as versões.',
              '- O bootstrap empírico no JSON descreve reamostragens destes oito casos desenhados na mesma rede. Não mede validade externa nem demonstra benefício operacional ou economia na Cemig.',
              '- O verificador reconstrói conectividade por Union-Find por fase, etapas e intervalos de consumidores sem chamar o simulador do planejador (que usa BFS). Compartilha contratos e validadores cadastrais; a aproximação radial de transferência é implementada separadamente sob hipóteses comuns. Revisão por outro agente não equivale a auditoria por terceiro humano.',
              '- Todas as janelas elétricas são seriais, inclusive zonas disjuntas. A linha temporal global verifica alvos durante transferência/execução/recomposição; sobreposições continuam bloqueadas. Equipes podem atuar em paralelo dentro da mesma janela.',
              '- Preparação/recomposição elétricas são etapas agregadas: abertura no início, transferência no meio da preparação e restauração do estado inicial ao final. Não se certifica sequência individual de campo, proteção, LOTO ou aterramento. Consumidores inicialmente sem alimentação são rejeitados por escopo.',
              '- Janelas, manobras físicas, interrupções por consumidor e reincidência são métricas diferentes. Clientes-hora são a soma por consumidor da união de intervalos, sem duplicidade; contadores provêm do recálculo independente.',
              '- B2 inclui um passe local de até 30 trocas de zona, preservando pacotes e dias, reconstruindo as rotas completas e aceitando somente menor objetivo padrão e plano factível. Não é ótimo local de todo espaço.',
              '- OPTIMAL_ON_DISCRETE_COLUMNS refere-se somente às colunas geradas: pacotes até 5 ordens, até 5 janelas/dia, 120 estados por profundidade, inícios construtivos e alocações limitadas. Inviabilidade desse catálogo não prova inviabilidade operacional geral. Objetivo, bound, gap, tempo e limites constam dos planos.',
              '- Carteira obrigatória é restrição rígida; omissão opcional custa 50 por ordem nos pesos padrão. Nenhuma incompletude recebe pontuação de benefício. Bound/gap são saídas do solver, sem verificação formal de certificado externo.',
              '- TRL 3 é proposta restrita às funções demonstradas em laboratório; a plataforma não foi validada operacionalmente. Integrações Electric Office/GeoServiços/SAP e validação dos programadores dependem da Cemig, em leitura/sidecar e sem telecomandos.',
              '- Estudos históricos 0.1.0-d07-scoped e REV02 são preservados separadamente e não são somados a esta rodada.', '',
              '- REV04/A1: toda etapa elétrica com duração positiva deve ser admissível, incluindo isolamento e recomposição. A conferência dos campos de uma etapa inviável não equivale à sua aprovação.',
              '- REV04/A2: ilhamento por geração distribuída é explicitamente não suportado e bloqueado antes de planejar/auditar. A rede de referência mantém anti-ilhamento; não se anuncia fluxo de DG sem seus limites.',
              '- REV04/A3/A4: dias anteriores à decisão são excluídos; certificado exige auditoria do plano bruto e após JSON. Transferência obrigatória malsucedida rejeita a intervenção; na opcional abandonada, a chave permanece aberta e esse estado efetivamente executado é auditado.',
              '- REV04/A5: grade operacional de 0,0001 h (0,36 s), fronteiras/união em ticks inteiros e adjacência exata. Horários fora da grade são rejeitados; a tolerância de representação binária não apaga intervalos ou reduz requisitos de duração/agenda.',
              '## Artefatos e reprodução', '',
              '`python -m pytest -q`; `python -m kgp.experiment`. Entradas, planos lidos e verificações estão em `reports/revalidation_REV04_20261029/`; CSV, JSON, figura branca 16:9 e manifesto SHA-256 estão em `reports/`.',
              'Consulte também AUDITORIA_DOCUMENTAL_D07.md, MATRIZ_ADERENCIA_OFICIAL_D07.md, AUDITORIA_OTIMIZADOR_D07.md e REVISAO_INDEPENDENTE_NUCLEO_D07.md.', '']
    path.write_text('\n'.join(lines), encoding='utf-8')
