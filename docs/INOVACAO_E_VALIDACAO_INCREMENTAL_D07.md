# Contribuição proposta e teste de valor incremental — D07

Consulta a fontes primárias em 29/10/2026. Esta matriz compara funções documentadas; não é uma avaliação completa de fornecedores, patentes ou configurações instaladas na Cemig. Ausência de uma função nesta seleção de documentos não demonstra ausência no produto.

| Capacidade | Referência existente | Contribuição que o KGP pretende testar | Evidência ainda necessária |
|---|---|---|---|
| Identificar elementos de isolamento | A Esri documenta traçado de isolamento em Utility Network, sujeito a configuração e requisitos de topologia. | Usar o estado elétrico por etapa como condição para reunir ordens da carteira. | Conferência da rede e das intervenções pela engenharia; comparação com traçados e processo disponíveis. |
| Programar ordens e técnicos | A Oracle documenta integração de Maintenance com Field Service para recomendar alocações por habilidades, disponibilidade e restrições. | Conciliar a agenda de serviços heterogêneos com a mesma intervenção elétrica e seus consumidores afetados. | Mapa das funções/licenças disponíveis, interfaces autorizadas e casos em que a combinação agrega utilidade ou reduz esforço. |
| Análise elétrica | O OpenDSS documenta fluxo de potência e estudos de falta, entre outras análises. | Usar verificações elétricas adequadas ao recorte de programação; a aproximação atual não substitui estudos completos. | Modelo físico e critérios aceitos pela engenharia; integração com ferramenta acordada, caso necessária. |
| Plano e impacto auditáveis | O demonstrador KGP exporta JSON e recalcula carteira, cronologia, materiais e impacto em módulo separado. | Tornar cada recomendação revisável e medir o valor da decisão completa, com perdas, exclusões e custos de revisão. | Replay temporal válido, novas instâncias retidas e comparação com processo existente sob a mesma carteira e recursos. |

Fontes: [Esri — isolamento](https://doc.esri.com/en/arcgis-pro/latest/help/data/utility-network/isolation-trace.html), [Oracle — programação e alocação de manutenção](https://docs.oracle.com/en/cloud/saas/readiness/scm/26c/maint26c/26C-maintenance-wn-f46396.htm) e [EPRI — capacidades do OpenDSS](https://opendss.epri.com/AboutOpenDSSCapabilities.html).

BFS, Union-Find, grafos e MILP são técnicas estabelecidas. A hipótese de inovação é a combinação aplicada ao fluxo do D07: estados elétricos por etapa, carteira heterogênea, agenda temporal de equipes e impacto individual rastreável. Não se afirma exclusividade, superioridade industrial demonstrada ou que o comparador B2 represente todas as capacidades do mercado.

Na PoC, o gate inicial deve registrar a configuração existente, os dados autorizados, o volume de ordens/equipes/dias, o orçamento computacional e o critério de utilidade. Medir o tempo total de candidatos, solver, verificação e revisão humana; apresentar também falhas, casos sem agrupamento e pioras. Horas liberadas indicam capacidade disponível; benefício financeiro exige custos evitáveis e custo incremental medidos. Sem esses insumos, o efeito econômico permanece não estimável.
