# KAPTUS GRID PLANNER — KGP 0.3.0 / REV03

Demonstrador computacional do Desafio 07 Inova Cemig.Lab Ciclo 5. Investiga a programação conjunta de serviços em uma rede de referência e carteiras sintéticas. Projeto independente do D06/GTA; operação exclusivamente sidecar, sem telecomandos ou alteração de ordens em produção.

O núcleo representa conectividade por fase, isolamento de ativos nodais e de trechos, transferência radial aproximada, elegibilidade temporal, equipes no tempo, precedências, materiais e impactos por consumidor. B0, B1, B2 e KGP usam os mesmos dados e restrições e passam pelo mesmo verificador depois da leitura do JSON exportado.

## Escopo verificado

- Todas as janelas elétricas são seriais, inclusive em zonas disjuntas. Equipes podem executar serviços em paralelo dentro de uma janela. O bloqueio conservador evita supor independência elétrica por conjuntos estáticos de chaves/ativos.
- `feasibility.py` reconstrói conectividade por Union-Find por fase, etapas e impacto sem chamar o simulador do planejador, que usa BFS. A cronologia global verifica os alvos em todos os intervalos de eventos. Compartilha contratos e validações cadastrais; a transferência conserva hipóteses físicas comuns e simplificadas.
- Os contratos rejeitam não finitos; o JSON estrito rejeita `NaN`, infinito, chaves duplicadas e conversões silenciosas de tipos. O verificador revalida objetos e referências, inclusive após mutação.
- Carteira obrigatória é restrição rígida. Omissão opcional custa 50 nos pesos padrão. Objetivo, bound e gap descrevem o mesmo problema, inclusive no fallback factível ao limite de tempo.
- B2 tem um passe local de até 30 trocas de zona, com pacotes/dias preservados, reconstrução das rotas e aceitação somente de plano factível com menor objetivo padrão.
- `OPTIMAL_ON_DISCRETE_COLUMNS` limita o certificado às colunas geradas: pacotes até 5 ordens, até 5 janelas/dia e 120 estados por profundidade; inícios e alocações são construtivos. Inviabilidade desse catálogo não prova inviabilidade operacional geral.

## Reprodução no Windows

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -u -m kgp.experiment
```

As versões efetivamente usadas constam de `configs/protocol_and_seeds.json`. A execução congela código, entradas, pesos e análise antes de rodar os métodos e confere os hashes ao final. As sementes DEV/EVAL já eram conhecidas: esta rodada é reavaliação sintética, sem novo cegamento ou inferência sobre desempenho na Cemig. O raio B1 de 0,75 km foi mantido da REV02; não houve ajuste para obter benefício desejado.

## Evidências

- Relatório, CSV, JSON detalhado, figura branca 16:9 e manifesto SHA-256 em `reports/`.
- Entradas, planos efetivamente lidos e verificações em `reports/revalidation_20261029/`. Falhas e carteira incompleta são preservadas e excluídas das comparações de benefício.
- [Requisitos oficiais](reports/MATRIZ_ADERENCIA_OFICIAL_D07.md), [revisão documental](reports/AUDITORIA_DOCUMENTAL_D07.md), [otimizador](reports/AUDITORIA_OTIMIZADOR_D07.md), [revisão do núcleo](reports/REVISAO_INDEPENDENTE_NUCLEO_D07.md) e [revisão final](reports/REVISAO_FINAL_BRANCH_D07.md).
- `reports/history/REV02/` preserva resultados anteriores. O estudo separado `0.1.0-d07-scoped` continua histórico no Drive; os estudos não são somados.

## Limitações e prontidão

As manobras são representadas em etapas agregadas: abertura no início do isolamento, transferência no meio da preparação e restauração do estado inicial ao fim da recomposição. Não se certificam operações individuais de campo, proteção, aterramento ou LOTO. Consumidores inicialmente sem alimentação são rejeitados explicitamente por escopo. Viagens são euclidianas e as durações são sintéticas.

Janelas, manobras físicas, interrupções, reincidência e clientes-hora são métricas diferentes. Clientes-hora usam a união de intervalos de cada consumidor. Não se convertem estes resultados em economia financeira ou compensações regulatórias comprovadas.

TRL 3 é proposto somente para as funções demonstradas em laboratório. Integrações de leitura com Electric Office, GeoServiços e SAP, dados reais e critérios com os programadores dependem da Cemig. Revisões por agentes de IA separados não equivalem a auditoria por terceiro humano. A entrega se destina à revisão interna; submissão exige dados e comprovações empresariais, disponibilidade da equipe e assinaturas humanas indicadas na matriz.
