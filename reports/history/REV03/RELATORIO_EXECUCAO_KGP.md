# RELATÓRIO DE EXECUÇÃO — KGP 0.3.0 / REV03

Reavaliação sintética de casos conhecidos — Desafio 07 Inova Cemig.Lab Ciclo 5.

Execução UTC: 2026-10-29T12:46:49.486371+00:00. Código: `5adf593ad9168e61a4b4ceea62346db7402a3d4c`; SHA-256 por arquivo no protocolo.
48/48 planos factíveis após leitura do JSON exportado, 8 comparações EVAL admitidas. Carteira obrigatória e factibilidade são condições do benefício.

## Resultados descritivos — 8 casos EVAL conhecidos, uma rede de referência

| Método | Factíveis | Janelas médias | Manobras médias | Acoplamento | Reincidentes médios | Clientes-hora médios | Tempo médio (s) |
|---|---|---|---|---|---|---|---|
| B0 | 8/8 | 3.8750 | 10.2500 | 0.0000 | 37.7500 | 318.7125 | 0.0481 |
| B1 | 8/8 | 3.2500 | 8.5000 | 0.2083 | 35.0000 | 280.7424 | 0.0394 |
| B2 | 8/8 | 2.5000 | 7.5000 | 0.4896 | 13.0000 | 255.6620 | 0.3537 |
| KGP | 8/8 | 2.1250 | 6.2500 | 0.5833 | 0.0000 | 212.3526 | 3.7578 |

## Balanço KGP × B2 — valores menores são melhores

| Métrica | Ganhos | Empates | Perdas |
|---|---|---|---|
| by_windows | 3 | 5 | 0 |
| by_consumer_hours | 5 | 2 | 1 |
| by_repeat_consumers | 3 | 5 | 0 |
| by_physical_maneuvers | 3 | 5 | 0 |
| by_combined_objective_J | 7 | 1 | 0 |

## Resultados por caso

| Caso | Janelas B0/B1/B2/KGP | Clientes-hora B0/B1/B2/KGP | Δ KGP−B2 clientes-hora |
|---|---|---|---|
| EVAL_01_TOPO_DISTANT_AND_PARALLEL | 4/4/2/2 | 429.90/429.90/243.20/243.20 | +0.0000 |
| EVAL_02_TIE_TRANSFER_AND_CREW_SYNC | 4/3/2/2 | 486.70/299.50/256.12/167.80 | -88.3218 |
| EVAL_03_PRECEDENCE_AND_RELEASE_DATES | 4/3/3/2 | 182.80/147.65/147.65/181.20 | +33.5536 |
| EVAL_04_FREQ_VS_DURATION_TRADEOFF | 3/3/2/1 | 285.90/285.90/382.13/265.72 | -116.4144 |
| EVAL_05_DISJOINT_NO_OPPORTUNITY | 3/3/3/3 | 144.00/144.00/144.00/144.00 | +0.0000 |
| EVAL_06_LIFECYCLE_AND_TEMPORAL_FILTER | 3/2/1/1 | 263.40/217.90/183.24/120.11 | -63.1344 |
| EVAL_07_STOCHASTIC_PORTFOLIO | 5/4/3/3 | 405.20/404.45/372.31/289.80 | -82.5120 |
| EVAL_08_STOCHASTIC_PORTFOLIO | 5/4/4/3 | 351.80/316.65/316.65/287.00 | -29.6464 |

## Interpretação e limites

- H1/H2/H3 recebem evidência apenas nos casos representados. As entradas DEV/EVAL já foram observadas; não há novo cegamento nem amostra independente de redes Cemig.
- O gerador, as sementes e o raio B1 de 0,75 km foram mantidos. Código, entradas, pesos e análise foram congelados antes de executar esta rodada. O protocolo contém os hashes e as versões.
- O bootstrap empírico no JSON descreve reamostragens destes oito casos desenhados na mesma rede. Não mede validade externa nem demonstra benefício operacional ou economia na Cemig.
- O verificador reconstrói conectividade por Union-Find por fase, etapas e intervalos de consumidores sem chamar o simulador do planejador (que usa BFS). Compartilha contratos e validadores cadastrais; a aproximação radial de transferência é implementada separadamente sob hipóteses comuns. Revisão por outro agente não equivale a auditoria por terceiro humano.
- Todas as janelas elétricas são seriais, inclusive zonas disjuntas. A linha temporal global verifica alvos durante transferência/execução/recomposição; sobreposições continuam bloqueadas. Equipes podem atuar em paralelo dentro da mesma janela.
- Preparação/recomposição elétricas são etapas agregadas: abertura no início, transferência no meio da preparação e restauração do estado inicial ao final. Não se certifica sequência individual de campo, proteção, LOTO ou aterramento. Consumidores inicialmente sem alimentação são rejeitados por escopo.
- Janelas, manobras físicas, interrupções por consumidor e reincidência são métricas diferentes. Clientes-hora são a soma por consumidor da união de intervalos, sem duplicidade; contadores provêm do recálculo independente.
- B2 inclui um passe local de até 30 trocas de zona, preservando pacotes e dias, reconstruindo as rotas completas e aceitando somente menor objetivo padrão e plano factível. Não é ótimo local de todo espaço.
- OPTIMAL_ON_DISCRETE_COLUMNS refere-se somente às colunas geradas: pacotes até 5 ordens, até 5 janelas/dia, 120 estados por profundidade, inícios construtivos e alocações limitadas. Inviabilidade desse catálogo não prova inviabilidade operacional geral. Objetivo, bound, gap, tempo e limites constam dos planos.
- Carteira obrigatória é restrição rígida; omissão opcional custa 50 por ordem nos pesos padrão. Nenhuma incompletude recebe pontuação de benefício. Bound/gap são saídas do solver, sem verificação formal de certificado externo.
- TRL 3 é proposta restrita às funções demonstradas em laboratório; a plataforma não foi validada operacionalmente. Integrações Electric Office/GeoServiços/SAP e validação dos programadores dependem da Cemig, em leitura/sidecar e sem telecomandos.
- Estudos históricos 0.1.0-d07-scoped e REV02 são preservados separadamente e não são somados a esta rodada.

## Artefatos e reprodução

`python -m pytest -q`; `python -m kgp.experiment`. Entradas, planos lidos e verificações estão em `reports/revalidation_20261029/`; CSV, JSON, figura branca 16:9 e manifesto SHA-256 estão em `reports/`.
Consulte também AUDITORIA_DOCUMENTAL_D07.md, MATRIZ_ADERENCIA_OFICIAL_D07.md, AUDITORIA_OTIMIZADOR_D07.md e REVISAO_INDEPENDENTE_NUCLEO_D07.md.
