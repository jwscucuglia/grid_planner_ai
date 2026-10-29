# Reauditoria D/E — objetivo e certificado do KGP

Base: `84340636c6d3877eacf44d4e19a1b802451057c1`. Revisão: 29/10/2026.

## Regra vigente

A conclusão de toda ordem obrigatória conhecida e ativa é restrição rígida do MILP.
Não pode ser trocada por redução de custo, mesmo quando o custo operacional excede
1000000 unidades de objetivo; não há conversão para economia financeira.
O campo legado `ObjectiveWeights.unscheduled_penalty=1000000` permanece apenas para
compatibilidade de API e não relaxa essa restrição. A omissão de ordem opcional custa
`ObjectiveWeights.optional_unscheduled_penalty=50` por ordem, configurável e explicitada
nos metadados. Predecessores opcionais devem ser concluídos quando seus sucessores
forem executados. A restrição de presença complementa a de cronologia.

`compute_exact_plan_objective(plan, weights, services)` usa a carteira canônica para
classificar omissões. Chamadas sem carteira são mantidas para planos sem omissões
declaradas; omissões exigem `services`. Carteira obrigatória incompleta não recebe
objetivo factível. O verificador é a autoridade sobre a factibilidade operacional
modelada e deve ser executado antes de comparar benefícios.

## Defeitos comprovados e regressões

| Caso | Base 0.2.0 | Comportamento corrigido | Evidência |
|---|---|---|---|
| Omissão opcional | Objetivo exportado 1000000, bound 50, gap 0 | Objetivo = bound = 50, gap 0; carteira obrigatória completa; certificado limitado ao catálogo | `test_optional_omission_objective_bound_and_status_describe_same_problem` |
| Penalidade opcional configurável | Custo 50 fixo no modelo e divergente no recálculo | Penalidades 0, 50 e 250 confrontadas com enumeração servir/omitir de uma instância de uma ordem | `test_tiny_optional_problem_matches_exhaustive_serve_or_omit_reference` (3 casos) |
| Obrigatória cara | Omitia ordem factível quando o custo superava 1000000 | Obrigatória sempre concluída, custo > 1000000 aceito | `test_mandatory_completion_cannot_be_exchanged_for_finite_penalty` |
| Obrigatória inviável no catálogo | Retornava relaxação com carteira incompleta como solução parcial pontuada | `INFEASIBLE_ON_DISCRETE_COLUMNS`, sem objetivo/bound/gap factíveis; carteira faltante preservada | `test_infeasible_mandatory_portfolio_has_no_feasible_objective_or_certificate` |
| Predecessor opcional omitido | Executava sucessor obrigatório sem predecessor | Conclusão do predecessor exigida | `test_optional_predecessor_cannot_be_omitted_when_mandatory_successor_is_executed` |
| Incumbente ao limite de tempo | Descartava incumbente e trocava bound zero pelo objetivo | Preserva incumbente, bound 0, gap 1 e `FEASIBLE_BOUNDED`; sem certificado | `test_time_limit_preserves_incumbent_and_zero_dual_bound` |
| Recálculo sem carteira com omissão | Aplicava penalidade de obrigatória a qualquer ID | Rejeita recálculo ambíguo e exige `services` | `test_recount_requires_canonical_orders_when_omissions_exist` |

As sete primeiras execuções de regressão falharam antes da correção em
`validation_logs/optimizer_red.log`. A regressão de predecessor falhou em
`validation_logs/optimizer_precedence_red.log` antes de adicionar a restrição de presença.
Após as correções, nove casos passaram em `validation_logs/optimizer_green.log`.
Os caches são evidência de trabalho local; o pacote final deve preservar os testes
e logs de execução versionados definidos pelo relatório geral.

Comandos executados:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_optimizer_audit.py -q
./.venv/Scripts/python.exe -m pytest tests/test_kgp_critical_cases.py -q -k 'optimizer or reconstruction or all_methods'
./.venv/Scripts/python.exe .audit_cache/reauditoria/D07_REAUDITORIA_REV02/optimizer/reproduce_optional.py .
./.venv/Scripts/python.exe .audit_cache/reauditoria/D07_REAUDITORIA_REV02/optimizer/reproduce_optimizer.py .
```

Os três testes históricos selecionados passaram. O script opcional histórico agora
observa objetivo/bound 50 e `OPTIMAL_ON_DISCRETE_COLUMNS`; seu campo `documented_J`
continua sendo uma fórmula histórica que não inclui a penalidade opcional, portanto
não representa o objetivo vigente. O script histórico de otimização preservou, entre
outros, objetivo/bound 300,36 no caso de reincidência e 99,84 na alocação especializada.

## Alcance do certificado

`OPTIMAL_ON_DISCRETE_COLUMNS` certifica o melhor valor sobre as colunas geradas,
com seus limites e dados; não é prova de ótimo da programação operacional contínua.
Os limites exportados são: pacotes de até cinco ordens, até cinco janelas/dia,
expansão das 120 melhores situações por profundidade, deduplicação e poda por custo.
Incluem-se sementes B0/B1/B2. Para cada sequência tenta-se o primeiro início viável,
considerando turnos, precedências, deslocamento e liberação da zona; não se enumeram
todos os inícios contínuos. A alocação usa o escalonador construtivo e variantes com
catálogo completo e subcatálogos de uma equipe na primeira janela; não enumera todas
as combinações de equipes e sequências operacionais possíveis.

Inviabilidade desse catálogo não demonstra inviabilidade operacional geral. O bound
zero é um bound válido e permanece zero. Na eventual divergência entre objetivo de
colunas e objetivo reconstruído, o plano é marcado `OBJECTIVE_INTEGRITY_FAILURE`, sem
certificado, bound ou gap divulgado como comparável.

## Complemento após a revisão final

A suíte final contém11 casos de otimizador. Além dos nove acima, cobre timeout sem incumbente com fallback factível/bound0 e propagação do mesmo raio B1 entre comparador e semente. O teste de incumbente em limite também exige texto sem alegação de ótimo. O fallback só recebe J quando passa pelo mesmo verificador; um limite conhecido é preservado quando consistente, e o gap é recalculado para o plano entregue. Tempo de fallback está incluído no tempo total. Fonte final: `91d98ef324e43a229efdfe49a8c804f3d8685d60`.

`scripts/validate_small_catalog.py` enumerou228 combinações em quatro pequenos catálogos:14/14/14/10 colunas,64/64/64/36 combinações. Factibilidade foi decidida pelo verificador, sem usar as restrições matriciais do MILP; J foi calculado diretamente dos contadores e horários. Os quatro melhores objetivos e bounds coincidiram. Esta verificação é adicional e separada do estudo principal de48 planos.
