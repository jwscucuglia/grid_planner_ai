# KAPTUS GRID PLANNER — KGP 0.6.1 / REV06-P1

Demonstrador computacional do Desafio 07 Inova Cemig.Lab Ciclo 5. Investiga a programação conjunta de serviços em uma rede de referência e carteiras sintéticas. Projeto independente do D06/GTA; operação exclusivamente sidecar, sem telecomandos ou alteração de ordens em produção.

O núcleo representa conectividade por fase, isolamento de ativos nodais e de trechos, transferência radial aproximada, elegibilidade temporal, equipes no tempo, precedências, materiais e impactos por consumidor. B0, B1, B2 e KGP usam os mesmos dados e restrições e passam pelo mesmo verificador depois da leitura do JSON exportado.

A manutenção REV06-P1 fecha os três casos residuais de robustez apontados na revisão independente de `cf386439`: limite explícito de profundidade JSON, razão distância/velocidade antes do arredondamento temporal e diagnóstico de deslocamento derivado não representável. A evidência científica REV06 e seu pacote auditado permanecem preservados; o suplemento identifica separadamente a fonte da manutenção. A candidatura continua baseada no demonstrador REV06, com pequenos ajustes de precisão no roteiro e nos slides.

A [evidência REV06-P1](reports/MANUTENCAO_REV06_P1_20261030.md) registra **441 testes aprovados + 1 ignorado**, os 45 novos testes RED/GREEN e os **48 planos reproduzidos**, com as mesmas 12 entradas e métricas/status preservadas. `configs/delivery_revision.json` separa a entrega atual do protocolo científico original. Os testes de symlink mantêm a limitação de privilégio do Windows; a revisão externa executou todos os 397 casos da entrega anterior em outro ambiente.

## Escopo verificado

- Todas as janelas elétricas são seriais, inclusive em zonas disjuntas. Equipes podem executar serviços em paralelo dentro de uma janela. O bloqueio conservador evita supor independência elétrica por conjuntos estáticos de chaves/ativos.
- `feasibility.py` reconstrói conectividade por Union-Find por fase, etapas e impacto sem chamar o simulador do planejador, que usa BFS. A cronologia global verifica os alvos em todos os intervalos de eventos. Compartilha contratos e validações cadastrais; a transferência conserva hipóteses físicas comuns e simplificadas.
- Os contratos rejeitam não finitos; o JSON estrito rejeita `NaN`, infinito, chaves duplicadas e conversões silenciosas de tipos. O verificador revalida objetos e referências, inclusive após mutação.
- Carteira obrigatória é restrição rígida. Omissão opcional custa 50 nos pesos padrão. Objetivo, bound e gap descrevem o mesmo problema, inclusive no fallback factível ao limite de tempo.
- B2 tem um passe local de até 30 trocas de zona, com pacotes/dias preservados, reconstrução das rotas e aceitação somente de plano factível com menor objetivo padrão.
- `OPTIMAL_ON_DISCRETE_COLUMNS` limita o certificado ao catálogo efetivamente gerado. A expansão construtiva usa pacotes até 5 ordens, até 5 janelas/dia e 120 estados por profundidade, com inícios e alocações limitados; agendas factíveis das sementes B0/B1/B2 são acrescentadas fora desses filtros de tamanho. Inviabilidade desse catálogo não prova inviabilidade operacional geral.
- Materiais são conservados a partir dos requisitos canônicos dos serviços por somas e saldos racionais exatos. O verificador reconstrói esses totais independentemente, sem tolerância física de estoque. O MILP continua em precisão dupla; uma falha no recálculo final impede publicar certificado.
- O cálculo físico exige uma árvore energizada e uma fonte ativa por componente/fase no estado inicial e nos estados analisados. Ciclos, caminhos paralelos e fontes conectadas na mesma fase são bloqueados. Cadastro e conectividade preservam todas as arestas; a rejeição indica limite do modelo, sem concluir que a rede real seja inviável.

## Reprodução no Windows com Python 3.11

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-reproducibility.txt
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONUTF8 = '1'
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -u -m kgp.experiment
```

As versões efetivamente usadas constam de `configs/protocol_and_seeds.json`. A execução congela código, entradas, pesos e análise antes de rodar os métodos e confere os hashes ao final. As sementes DEV/EVAL já eram conhecidas: esta rodada é reavaliação sintética, sem novo cegamento ou inferência sobre desempenho na Cemig. O raio B1 de 0,75 km foi mantido da REV02; não houve ajuste para obter benefício desejado.

## Evidências

- Relatório, CSV, JSON detalhado, figura branca 16:9 e manifesto SHA-256 em `reports/`.
- Entradas, planos efetivamente lidos e verificações atuais em `reports/revalidation_REV06_20261029/`. Falhas e carteira incompleta são preservadas e excluídas das comparações de benefício.
- [Revisão complementar REV06](reports/AUDITORIA_FRONTEIRAS_INGESTAO_MODELO.md): CSV estrito, mapa efetivo de campos, caminhos e fronteira física.
- [Revisão adversarial e correção REV05](reports/AUDITORIA_ADVERSARIAL_CODIGO_BANCA.md), com contraprovas públicas reproduzíveis e limites da conclusão.
- [Achados e validação REV04](reports/AUDITORIA_A1_A5_REV04.md), [otimizador](reports/AUDITORIA_OTIMIZADOR_D07.md), [revisão do núcleo](reports/REVISAO_INDEPENDENTE_NUCLEO_D07.md) e [revisão final](reports/REVISAO_FINAL_BRANCH_D07.md).
- Matrizes oficiais/documentais, inventário do Drive e orçamento ficam no pacote restrito da candidatura no Drive do projeto. O relatório de execução também os referencia; não integram este repositório público.
- `reports/history/REV02/` a `reports/history/REV05/` preservam resultados anteriores. As entradas/saídas REV04 e REV05 continuam em seus diretórios próprios. O estudo separado `0.1.0-d07-scoped` continua histórico no Drive; os estudos não são somados.

## Limitações e prontidão

As manobras são representadas em etapas agregadas: abertura no início do isolamento, transferência no meio da preparação e restauração do estado inicial ao fim da recomposição. Não se certificam operações individuais de campo, proteção, aterramento ou LOTO. Consumidores inicialmente sem alimentação são rejeitados explicitamente por escopo. Viagens são euclidianas e as durações são sintéticas.

Janelas, manobras físicas, interrupções, reincidência e clientes-hora são métricas diferentes. Clientes-hora usam a união de intervalos de cada consumidor. Não se convertem estes resultados em economia financeira ou compensações regulatórias comprovadas.

TRL 3 é proposto somente para as funções demonstradas em laboratório. Integrações de leitura com Electric Office, GeoServiços e SAP, dados reais e critérios com os programadores dependem da Cemig. Revisões por agentes de IA separados não equivalem a auditoria por terceiro humano. A entrega se destina à revisão interna; submissão exige dados e comprovações empresariais, disponibilidade da equipe e assinaturas humanas indicadas na matriz.

## Fechamento da auditoria A1-A5

A REV04 rejeita qualquer etapa elétrica positiva inviável e ilhamento por DG não suportado. Transferência obrigatória malsucedida rejeita a intervenção; na opcional abandonada, a tie permanece aberta e o estado executado é auditado. Datas anteriores à decisão são excluídas; o certificado bruto do KGP exige auditoria antes/depois do JSON. Horários operacionais usam ticks de0,0001h (0,36s), adjacência exata e rejeição de valores fora da grade.

O manifesto preserva SHA-256 dos bytes e acrescenta SHA-256 canônico apenas para texto UTF-8 com CRLF/LF. `validate_delivery.py` identifica equivalência de finais de linha separadamente; binários e arquivos de origem continuam verificados por bytes exatos. As evidências REV03/REV04/REV05 são históricas; a rodada corrente está no diretório REV06 acima.

O catálogo exige intervalo diário positivo de recomposição; calendários contínuos de 0–24 h são explicitamente não suportados.

A correção de portabilidade REV04-P1 preserva os bytes do JSON histórico não UTF-8 por uma regra específica em `.gitattributes`. A validação canônica permanece restrita a UTF-8; o original histórico continua sujeito a SHA-256 exato. Consulte [a conferência de portabilidade](reports/PORTABILIDADE_REV04_P1.md). O núcleo, os testes científicos, as entradas e os resultados da REV04 são preservados.

## Incremento INGESTAO01 — histórico de arquivos externos e GIS público

O importador `kgp.ingestion.import_bundle` recebe CSV/JSON e GeoJSON com propriedades topológicas explícitas. Ele mapeia campos, converte unidades declaradas, valida referências/fases/estados e entrega os contratos existentes, com diagnóstico por registro e proveniência. Erros críticos bloqueiam o planejamento; `scope_status` é explícito e somente `ACTIVE` no contrato v1. Transições de tensão sem conversor suportado são bloqueadas. Consulte o [contrato de importação](docs/CONTRATO_INGESTAO_KGP.md) e a [formulação elétrica e de programação](docs/MODELO_MATEMATICO_KGP.md).

O ensaio adicional usa topologia GIS **real e pública**, BDGD/ANEEL EFLUL de 31/12/2025, CTMT 002: 354 PACs e 358 ligações, preservando paralelas. A fonte tem aviso/licença ODbL, extrator reproduzível e hashes em `data/gis_reference/eflul_2025_ctmt002/source/`. As 81 divergências de posição são registradas, sem reconstruir conexões por proximidade. O bundle de programação usa um **overlay sintético explícito** para cargas, consumidores, carteira, recursos, parâmetros ausentes e posições de agenda; não usa UCs reais nem dados Cemig.

```text
python scripts/prepare_gis_reference.py
python scripts/import_kgp_bundle.py data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json --report quality-public.json
python scripts/validate_gis_ingestion.py
```

As evidências ficam em [reports/ingestion_20261029/](reports/ingestion_20261029/). A comparação fonte→grafo e os quatro planos exportados passaram pelo mesmo verificador, com 2/2 obrigatórias por método. É uma verificação funcional de ingestão/integração, separada do estudo REV04 conhecido e da validação operacional futura na Cemig. O adaptador corporativo depende da amostra e do contrato de dados da PoC; o incremento não muda a evidência histórica 130/48 nem amplia os limites físicos do núcleo.

A suíte consolidada do incremento INGESTAO01 passou com **231 testes em 209,91 s**, incluindo os 130 anteriores, 94 de ingestão e 7 de integração. Esse é um registro histórico. A [revisão do incremento](reports/REVISAO_INGESTAO01.md) registra contraprovas e limites; não equivale a homologação operacional ou auditoria por terceiro humano.

## Checkpoint adversarial REV05 — histórico

A revisão de banca reproduziu A-CODE-01: arredondamentos de materiais permitiam falsa factibilidade. Também encontrou A-CODE-02: um literal numérico não zero podia desaparecer na leitura ou na conversão de unidades antes de formar o contrato. As duas correções têm RED/GREEN, 60 testes de materiais e 51 da fronteira numérica. A revisão separada reexecutou 58 sondagens originais, 105 adicionais de materiais e 143 da fronteira de entrada, todas com os resultados esperados. A suíte completa atual passou com **342 testes em 197,95 s** (231 anteriores + 60 + 51). Não restou achado reproduzido aberto nessa passagem finita; isso não é uma prova geral de correção.

Fonte do checkpoint: `f49a8c6543717ff740f1f7c701e20d34e82e6e5a`. A REV05 repetiu os casos conhecidos sem selecionar novas sementes, pesos ou raio. As saídas GIS em `reports/ingestion_REV05_20261029/` e `reports/ingestion_20261029/` ficam históricas sob a fronteira então permissiva. Não são reaproveitadas como aprovação física atual do multigrafo. Escala industrial será medida por ordens, equipes, dias, alternativas e tempo total na PoC.

O [modelo matemático](docs/MODELO_MATEMATICO_KGP.md) descreve os quatro motores e a conservação de materiais. A [matriz de contribuição incremental](docs/INOVACAO_E_VALIDACAO_INCREMENTAL_D07.md) distingue funções existentes da hipótese de combinação a validar no processo Cemig.

## Validação corrente REV06

Fonte congelada: `bdd6ab6be7593c906d80261c916eb9874676240a`. A suíte consolidada passou com **396 testes aprovados e 1 ignorado em 210,34 s**. O caso ignorado exige privilégio de criação de symlink neste Windows; os dois pontos de tratamento de erro de resolução foram testados separadamente. A revisão complementar conferiu 15 controles de ingestão e 81 do domínio físico, além de repetir 58 + 105 + 143 controles anteriores na fonte atual. Os testes GIS e duas fixtures de transferência foram adaptados ao domínio novo, preservando as verificações negativas e os controles válidos; não se afirma identidade byte a byte da suíte anterior.

Os **48/48 planos** das 12 entradas conhecidas foram relidos e verificados, com 100% da carteira obrigatória; **12 objetivos KGP** foram recalculados independentemente. Os 36 hashes de fonte e os 12 de entradas permaneceram congelados; entradas, sementes, pesos e raio B1 são os mesmos da REV04. Os oito resultados pareados permaneceram iguais: médias 15% menores em janelas e 16,9401% menores em clientes-hora frente a B2. Em EVAL_03 houve uma janela a menos e 33,5536 clientes-hora a mais. São descrições de casos conhecidos na mesma rede sintética, sem inferência de benefício operacional.

O ensaio atual em [ingestion_REV06_20261029](reports/ingestion_REV06_20261029/RELATORIO_INGESTAO01.md) preservou **354 PACs, 358 arestas e nove verificações de conectividade**. Os quatro métodos retornam `UNSUPPORTED_INPUT` porque há ciclos energizados fora do cálculo radial. As duas obrigatórias permanecem não programadas; nenhum benefício é calculado sobre a saída bloqueada. A rede real não foi declarada inviável. Os quatro planos aprovados no modelo anterior são históricos.

Os tempos de preparação/recomposição da zona são agregados autoritativos, a validar externamente. O tempo individual por chave é informativo e não monta uma sequência de operadores. O demonstrador não certifica execução de campo. Consulte [REV06_RELEASE.json](reports/REV06_RELEASE.json) e os logs em `reports/validation_logs/rev06/`.
