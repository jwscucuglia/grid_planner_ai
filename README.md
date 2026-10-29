# KAPTUS GRID PLANNER — KGP 0.4.0 / REV04

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
- Entradas, planos efetivamente lidos e verificações em `reports/revalidation_REV04_20261029/`. Falhas e carteira incompleta são preservadas e excluídas das comparações de benefício.
- [Achados e validação REV04](reports/AUDITORIA_A1_A5_REV04.md), [otimizador](reports/AUDITORIA_OTIMIZADOR_D07.md), [revisão do núcleo](reports/REVISAO_INDEPENDENTE_NUCLEO_D07.md) e [revisão final](reports/REVISAO_FINAL_BRANCH_D07.md).
- Matrizes oficiais/documentais, inventário do Drive e orçamento ficam no pacote restrito da candidatura no Drive do projeto. O relatório de execução também os referencia; não integram este repositório público.
- `reports/history/REV02/` preserva resultados anteriores. O estudo separado `0.1.0-d07-scoped` continua histórico no Drive; os estudos não são somados.

## Limitações e prontidão

As manobras são representadas em etapas agregadas: abertura no início do isolamento, transferência no meio da preparação e restauração do estado inicial ao fim da recomposição. Não se certificam operações individuais de campo, proteção, aterramento ou LOTO. Consumidores inicialmente sem alimentação são rejeitados explicitamente por escopo. Viagens são euclidianas e as durações são sintéticas.

Janelas, manobras físicas, interrupções, reincidência e clientes-hora são métricas diferentes. Clientes-hora usam a união de intervalos de cada consumidor. Não se convertem estes resultados em economia financeira ou compensações regulatórias comprovadas.

TRL 3 é proposto somente para as funções demonstradas em laboratório. Integrações de leitura com Electric Office, GeoServiços e SAP, dados reais e critérios com os programadores dependem da Cemig. Revisões por agentes de IA separados não equivalem a auditoria por terceiro humano. A entrega se destina à revisão interna; submissão exige dados e comprovações empresariais, disponibilidade da equipe e assinaturas humanas indicadas na matriz.

## Fechamento da auditoria A1-A5

A REV04 rejeita qualquer etapa elétrica positiva inviável e ilhamento por DG não suportado. Transferência obrigatória malsucedida rejeita a intervenção; na opcional abandonada, a tie permanece aberta e o estado executado é auditado. Datas anteriores à decisão são excluídas; o certificado bruto do KGP exige auditoria antes/depois do JSON. Horários operacionais usam ticks de0,0001h (0,36s), adjacência exata e rejeição de valores fora da grade.

O manifesto preserva SHA-256 dos bytes e acrescenta SHA-256 canônico apenas para texto UTF-8 com CRLF/LF. `validate_delivery.py` identifica equivalência de finais de linha separadamente; binários e arquivos de origem continuam verificados por bytes exatos. As evidências REV03 em `reports/revalidation_20261029/` e `reports/history/REV03/` são históricas; a rodada corrente está no diretório REV04 acima.

O catálogo exige intervalo diário positivo de recomposição; calendários contínuos de 0–24 h são explicitamente não suportados.

A correção de portabilidade REV04-P1 preserva os bytes do JSON histórico não UTF-8 por uma regra específica em `.gitattributes`. A validação canônica permanece restrita a UTF-8; o original histórico continua sujeito a SHA-256 exato. Consulte [a conferência de portabilidade](reports/PORTABILIDADE_REV04_P1.md). O núcleo, os testes científicos, as entradas e os resultados da REV04 são preservados.

## Incremento INGESTAO01 — arquivos externos e GIS público

O importador `kgp.ingestion.import_bundle` recebe CSV/JSON e GeoJSON com propriedades topológicas explícitas. Ele mapeia campos, converte unidades declaradas, valida referências/fases/estados e entrega os contratos existentes, com diagnóstico por registro e proveniência. Erros críticos bloqueiam o planejamento; `scope_status` é explícito e somente `ACTIVE` no contrato v1. Transições de tensão sem conversor suportado são bloqueadas. Consulte o [contrato de importação](docs/CONTRATO_INGESTAO_KGP.md) e a [formulação elétrica e de programação](docs/MODELO_MATEMATICO_KGP.md).

O ensaio adicional usa topologia GIS **real e pública**, BDGD/ANEEL EFLUL de 31/12/2025, CTMT 002: 354 PACs e 358 ligações, preservando paralelas. A fonte tem aviso/licença ODbL, extrator reproduzível e hashes em `data/gis_reference/eflul_2025_ctmt002/source/`. As 81 divergências de posição são registradas, sem reconstruir conexões por proximidade. O bundle de programação usa um **overlay sintético explícito** para cargas, consumidores, carteira, recursos, parâmetros ausentes e posições de agenda; não usa UCs reais nem dados Cemig.

```text
python scripts/prepare_gis_reference.py
python scripts/import_kgp_bundle.py data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json --report quality-public.json
python scripts/validate_gis_ingestion.py
```

As evidências ficam em [reports/ingestion_20261029/](reports/ingestion_20261029/). A comparação fonte→grafo e os quatro planos exportados passaram pelo mesmo verificador, com 2/2 obrigatórias por método. É uma verificação funcional de ingestão/integração, separada do estudo REV04 conhecido e da validação operacional futura na Cemig. O adaptador corporativo depende da amostra e do contrato de dados da PoC; o incremento não muda a evidência histórica 130/48 nem amplia os limites físicos do núcleo.

A suíte consolidada passou com **231 testes em 209,91 s**, incluindo os 130 anteriores, 94 de ingestão e 7 de integração. A [revisão do incremento](reports/REVISAO_INGESTAO01.md) registra contraprovas e limites; não equivale a homologação operacional ou auditoria por terceiro humano.
