# Auditoria adversarial de código — KAPTUS GRID PLANNER

Data: 29/10/2026. Base inicial: `e4f9dd78e9ef0065a87f40b31889bfcb07da4ade` (REV04/INGESTAO01). Reavaliação REV05 realizada sobre a correção no working tree, com hashes dos módulos registrados nos resultados públicos vinculados abaixo.

Esta é uma revisão técnica por agente de IA com contraprovas executáveis. Não representa auditoria por terceiro humano, autorização operacional, homologação de engenharia ou validação em campo. Não utiliza código, dados ou resultados do D06.

## Conclusão desta passagem

Foi reproduzido **um achado científico de falsa factibilidade**, relativo à conservação de materiais, com efeito nos quatro motores e no verificador comum. No estado inicial examinado, o KGP também publicava prova de ótimo sobre esse plano incorretamente aceito. **O achado foi corrigido e a correção passou pela reavaliação adversarial independente desta passagem. Não há achado reproduzido aberto no alcance abaixo.**

A passagem própria foi limitada a **12 classes estratégicas, 58 sondagens pequenas, com controles positivos**. Resultado inicial: 50 sondagens passaram; as oito falhas correspondem a duas variantes do mesmo achado nos quatro métodos. Na reexecução após a correção, **58/58 passaram**. Uma extensão da mesma classe de materiais acrescentou **105/105 verificações com resultado esperado**. Isso não é uma estimativa de taxa de defeitos nem uma prova geral de correção. A suíte completa não foi executada por esta revisão.

## A-CODE-01 — Quantidade positiva de material desaparece por arredondamento

**Estado: corrigido e reavaliado na REV05; evidência RED original preservada.** O restante desta seção descreve o defeito observado antes da correção.

O domínio admite quantidades finitas não negativas em `required_materials` e `materials_inventory`, sem impor resolução de quatro casas decimais. O próprio contrato de tempo exclui quantidades de materiais da restrição de grade operacional. O importador aceita o seguinte exemplo mínimo, com uma ordem obrigatória, uma equipe, uma zona e um consumidor:

```python
required_materials = {"M": 0.00004}
materials_inventory = {}  # disponibilidade de M = 0
```

Após o planejamento, a exportação JSON estrita e a releitura, ocorre:

| Método | Obrigatória concluída | Material exportado/recalculado | `is_feasible` | Certificado |
|---|---:|---:|---|---|
| B0 | 1/1 | M = 0 | `True` | Heurístico |
| B1 | 1/1 | M = 0 | `True` | Heurístico |
| B2 | 1/1 | M = 0 | `True` | Heurístico |
| KGP | 1/1 | M = 0 | `True` | `OPTIMAL_ON_DISCRETE_COLUMNS`, `is_optimal_proven=True` |

A segunda variante tem demanda `0.33333` e estoque `0.3333`. Os quatro métodos também são aceitos, com consumo recalculado de `0.3333`, embora a demanda canônica exceda o estoque.

A causa foi rastreada a arredondamentos intermediários a quatro casas em `scheduling.py`, `baselines.py`, `optimizer.py` e `feasibility.py`, combinados com tolerâncias absolutas de integridade e estoque em `validation.py` e `feasibility.py`. Assim, a independência da reconstrução de conectividade não protege esta restrição: construção e verificação compartilham a perda de quantidade. A ausência da chave no estoque deve significar disponibilidade zero, sem tornar dispensável uma quantidade positiva.

O certificado do solver continua restrito às colunas geradas; o problema aqui é que as colunas e o plano são classificados como factíveis contra a carteira canônica quando não conservam material. Não foi reproduzida perda de carteira, clientes-hora ou isolamento causada por esse caso.

Reprodução, a partir da raiz do repositório:

```powershell
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/material_probe.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/adversarial_matrix.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/materials_exact_reaudit.py
```

Os scripts autossuficientes, fixtures públicas e instruções estão em [validation_scripts/banca_rev05/](validation_scripts/banca_rev05/README.md). Não dependem de pytest ou módulos de testes. Escrevem novas reproduções em `.audit_cache/banca_audit/public_reproduction/`; os logs públicos preservados não são sobrescritos. Os resultados iniciais estão em [baseline_RED_e4f9dd_matrix.json](validation_scripts/banca_rev05/baseline_RED_e4f9dd_matrix.json) e [baseline_RED_e4f9dd_material.json](validation_scripts/banca_rev05/baseline_RED_e4f9dd_material.json), com proveniência da base original e hashes dos módulos. Não integram a evidência histórica de 48 planos REV04.

## Correção e reavaliação independente REV05

O código corrigido mantém os saldos privados como racionais exatos dos valores decimais `str` das quantidades de entrada. `materials_used` é somente o echo float canônico de apresentação; não é usado para debitar o estoque. O verificador e a validação cadastral recompõem os requisitos diretamente das ordens, sem chamar o helper de saldos dos planejadores. A leitura JSON estrita e o verificador continuam como barreiras antes de publicar certificado.

O mesmo bundle mínimo com demanda positiva e estoque zero agora deixa a obrigatória sem programação nos quatro métodos. Todos são rejeitados pelo verificador; o KGP informa `INFEASIBLE_ON_DISCRETE_COLUMNS`, sem objetivo factível, bound, gap ou prova de ótimo. A variante `0.33333` versus `0.3333` também é rejeitada. Controles com estoque exatamente suficiente, inclusive subnormal, continuam utilizáveis. Não se reduziu o domínio a uma grade de materiais.

Evidências finais públicas:

- [Matriz original após a correção](validation_scripts/banca_rev05/final_GREEN_matrix.json): **58/58** na reprodução pública.
- [Testemunho mínimo após a correção](validation_scripts/banca_rev05/final_GREEN_material_probe.json): quatro rejeições corretas, sem certificado indevido.
- [Extensão de materiais exatos](validation_scripts/banca_rev05/final_GREEN_materials_exact.json): **105/105**, com hashes dos módulos corrigidos e resultados por caso.

As 105 verificações usam um oráculo separado com aritmética decimal de 2.000 algarismos: 84 combinações dos quatro motores, três disposições (pacote, janelas separadas e dias separados) e sete carteiras; seis planos completos forjados com déficit; 12 adulterações de echo; um agregado de janela fora da representação float finita; dois extremos de precisão do MILP. As carteiras incluem `1 + 1e-28`, `0.1 + 0.2`, subnormais, somas decimais próximas do estoque e overflow. Os relatórios de overflow permanecem serializáveis com `allow_nan=False`; o total racional fica disponível mesmo quando não cabe no mapa float.

O MILP continua usando double. Nos dois casos extremos — uma obrigatória consumindo todo o estoque e uma opcional acrescentando `1e-28` ou `5e-324` — o candidato numérico não conserva o estoque exato. A barreira final o rejeita e retorna um plano realmente factível com `FEASIBLE_FALLBACK_NO_BOUND`, sem transportar prova de ótimo ou bound do candidato rejeitado. Isso é uma limitação numérica observada e contida; não é certificado exato do MILP em aritmética racional.

## Alcance e controles

| Classe | Sondagens | Resultado inicial |
|---|---:|---|
| 01. Carteira mínima válida e impacto independente nos quatro motores | 4 | 4 passaram |
| 02. Conservação de material em duas insuficiências e um controle | 12 | 4 passaram; 8 reproduziram A-CODE-01 |
| 03. Obrigatória omitida e lista de conclusão adulterada | 2 | 2 rejeições esperadas |
| 04. Equipes: execução, preparação e recomposição com déficit de um tick; número, competência e turno | 6 | 6 rejeições esperadas |
| 05. Conhecimento na decisão, liberação, prazo, calendário e horizonte | 5 | 5 rejeições esperadas |
| 06. Precedência ordenada, ordem reversa/ciclo e predecessor opcional omitido | 3 | Controle aceito e 2 rejeições esperadas |
| 07. Conectividade após manobra: segunda fonte e ativo de trecho com extremidade alimentada | 2 | 2 rejeições esperadas |
| 08. Adjacência exata e sobreposição de um tick entre janelas com equipes distintas | 2 | Controle aceito e rejeição esperada |
| 09. Evidência exportada adulterada: consumidores, manobras, conectividade e relógio de etapa | 4 | 4 rejeições esperadas |
| 10. União de intervalos por enumeração independente de ticks: adjacência e gaps positivos | 3 | 3 passaram |
| 11. Objetivo, bound e certificado do KGP contra as duas alternativas enumeradas de uma ordem opcional, em cinco penalidades | 5 | 5 passaram |
| 12. Ingestão nos três formatos e bloqueios de status, ativo órfão, fase, tensão, caminho, duplicidade e tempo fora da grade | 10 | 10 passaram |

A contagem de clientes-hora da classe 01 foi calculada diretamente pela duração conhecida da única interrupção: `0.1 + 1 + 0.1 = 1.2` cliente-hora, com duas manobras físicas. A classe 10 enumerou o conjunto de ticks cobertos e suas componentes, sem chamar as funções de união do produto. A classe 11 enumerou servir/omitir: servir custa `90 + 1.2 + 2.5*2 + 0.6*1.2 = 96.92`, comparado a cada penalidade de omissão. A classe 02 usou aritmética decimal sobre os requisitos e o estoque canônicos como oráculo de conservação.

Adicionalmente, foram executadas regressões direcionadas já existentes:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_rev04_boundaries.py tests/test_rev04_electrical.py tests/test_independent_review.py tests/test_optimizer_audit.py
```

Resultado observado na inspeção inicial: **27 testes passaram em 4,45 s**, código de saída 0. Esses testes não cobriam A-CODE-01; a aceitação posterior da correção se apoia nas contraprovas próprias reexecutadas e nos resultados públicos acima.

## Limites da conclusão

Esta passagem examinou `contracts`, `validation`, `ingestion`, `topology`, `eligibility`, `scheduling`, `baselines`, `optimizer`, `feasibility`, `metrics`, o novo `materials` e as evidências técnicas públicas relacionadas. Exercitou B0/B1/B2/KGP, contratos importados e planos após JSON; não repetiu a experiência completa de 48 planos, nem o ensaio GIS completo nesta execução.

As entradas de contraprova respeitam o domínio admitido e os tempos de 0,0001 h. Não se exigiu ilhamento DG, calendário contínuo de 24 horas, fluxo AC/proteção, transformação de tensão, consumidor multifásico ou simultaneidade elétrica entre janelas. A aproximação radial, etapas agregadas de manobra, deslocamentos euclidianos, limites construtivos de colunas e validade dos parâmetros sintéticos permanecem limites declarados. A aprovação dos controles não demonstra segurança de campo nem benefício real na Cemig.

Não restou achado reproduzido aberto nesta passagem. O fechamento refere-se à correção de A-CODE-01 e aos casos efetivamente executados; não substitui a rodada consolidada do estudo, a avaliação de desempenho em instâncias maiores ou a validação por engenharia humana.
