# Auditoria temporal REV04 — A5

O contraexemplo preservado em `.audit_cache/rev04/original/contracts_science/near_adjacent_plan.json` usava duas interrupções separadas por 0,000005 h (18 ms). As métricas uniam o intervalo por tolerância de 0,00001 h; o verificador contava a recomposição intermediária. O objetivo divergente era 193,20 contra 205,20.

## Contrato de precisão

- A resolução operacional é **0,0001 h = 0,36 s**, já usada pelo escalonador. Horários de janela, etapas, intervalos de equipe, deslocamentos, durações operacionais e limites de turno/janela devem estar nessa grade.
- Os construtores continuam aceitando números finitos. A leitura tipada de JSON e a fronteira do verificador rejeitam tempos fora da grade antes dos cálculos. O plano recebido não é alterado nem arredondado silenciosamente.
- Admitem-se até quatro ULPs de erro de representação binária, limitados a 1/16 tick. Isso acomoda produtos decimais e somas como `0.1 + 0.2`, sem tolerância física para fechar intervalos de fornecimento.
- Coordenadas, materiais, metadados e estatísticas do solver preservam sua precisão; a grade é aplicada por lista explícita de campos operacionais.
- Intervalos são semiabertos `[início, fim)`. Sobreposição e adjacência exata pertencem à mesma interrupção; **qualquer lacuna positiva de um tick** cria outra interrupção. Etapas de duração zero não contribuem clientes-hora.
- A divisão da preparação para transferência usa `floor(preparation_ticks / 2)` para isolamento; o tick ímpar pertence à transferência. O tempo total de preparação é preservado.
- Métricas e verificador mantêm implementações próprias da união de intervalos, com comparação em ticks inteiros e sem compartilhar o algoritmo de união.
- A evidência elétrica exportada deve ter os mesmos ticks da reconstrução independente. Durações mínimas, limites de janela/turno, precedências, preparação, recomposição, deslocamentos e conflitos de agenda são comparados em ticks, sem franquia de um tick. A validação estrutural também usa ticks para cronologia e duração estritamente positiva.

## Regressões e reprodução

`tests/test_rev04_time.py` cobre o contraexemplo de 18 ms, adulteração de tempos de janela/etapa/equipe, entradas operacionais fora da grade, precisão de dados não operacionais, representação binária, adjacência e lacuna de um tick, etapas sem preparação/recomposição, fatores de duração 0,85/1,00/1,15 nas sementes DEV/EVAL e preparação de transferência com um tick ímpar. Também rejeita adulteração elétrica de um tick que voltava a mudar a frequência em 12 pontos do objetivo, preparação/execução/recomposição um tick abaixo do necessário e duração subnormal positiva que representa zero ticks.

O primeiro ciclo TDD falhou como esperado em 10 casos e aprovou 5; após a correção de contratos/métricas, os 15 casos passaram. A regressão adicional do ponto médio ímpar falhou na construção anterior (`8.9999–9.0000` em vez de `8.9999–8.9999` para isolamento). A validação integrada final está registrada junto à revisão REV04.

Após integração das comparações exatas no verificador e da reconstrução das etapas, `.venv/Scripts/python.exe -m pytest tests/test_rev04_time.py -q` aprovou **21 testes em 2,73 s**. `git diff --check` terminou com código zero. A suíte completa ficou sob coordenação da revisão integrada para não disputar o congelamento de fontes; nenhuma execução parcial é declarada como aprovação da suíte.

A tentativa inicial da suíte pelo `python` do sistema encontrou `PackageNotFoundError: networkx` em `test_failed_round_cannot_publish_previous_figure_or_stale_plan`. O mesmo arquivo passou integralmente (2 testes) com `.venv/Scripts/python.exe`, que contém as dependências declaradas do projeto. A verificação final usa esse ambiente virtual.

Preparação e recomposição iguais a zero permanecem válidas. Para dois serviços de uma hora e um consumidor, janelas contíguas têm uma interrupção e objetivo 193,20; separadas por um tick, têm duas interrupções e objetivo 205,20. Nos dois casos o impacto é exatamente 2 clientes-hora, também recalculado pelo verificador independente.
