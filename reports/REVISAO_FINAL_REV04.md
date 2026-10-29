# Revisão final independente de código — REV04

Data: 29/10/2026. Base: `f21690e8cd49f5174e1d03ad0b45bda397d003e3`; alvo: worktree REV04 anterior ao congelamento. Revisão computacional em uma passagem, sem alteração de `src/`, `tests/`, índice ou branch. Foram usadas as instruções de revisão recebida e solicitação de revisão; esta execução é o assento independente de revisão, sem delegação adicional.

**Parecer:** os cinco contraexemplos originais A1–A5 estão fechados nos caminhos examinados. Não encontrei regressão bloqueante introduzida pelo diff. O núcleo pode seguir para congelamento e reavaliação dos 48 planos no recorte publicado. Uma limitação temporal preexistente adicional foi reproduzida e fechada por redução explícita de escopo: catálogos contínuos de 24 h são recusados; exige-se ao menos um tick de recomposição entre dias. Este parecer não confirma números ainda não regenerados nem representa homologação humana ou operacional.

## Pontos sólidos observados

- `optimizer.py:531` audita o objeto bruto e o JSON relido. A carteira vazia, o retorno normal, o candidato rejeitado e as sementes usadas como fallback passam por essa fronteira antes de receber certificação. Um candidato MILP rejeitado não transfere seu bound ou prova ao fallback.
- `optimizer.py:1018` exige factibilidade já auditada, coerência do objetivo reconstruído, bound compatível e término apropriado do solver para declarar ótimo sobre as colunas discretas. Um incumbent com status 1 e gap zero permaneceu sem prova de ótimo.
- `planning_policy.py:8` recusa explicitamente `allow_dg_islanding=True` nos quatro métodos. O verificador e as entradas diretas de simulação também recusam a opção, evitando atribuir carregamento zero a uma fonte omitida. A mesma política recusa catálogos contínuos de 24 h antes dos quatro planejadores, e o verificador rejeita esse catálogo independentemente.
- A inviabilidade de uma etapa positiva impede elegibilidade/programação e invalida a auditoria. A transferência opcional abandonada mantém a tie aberta; a obrigatória malsucedida impede a intervenção. As implementações independentes continuam separadas.
- O contrato de ticks preserva a distinção entre adjacência e restauração de um tick. Os hashes canônicos limitam-se a CRLF→LF de texto UTF-8; o empacotamento ainda confere os bytes exatos de cada entrada do ZIP e os dois manifestos armazenados.

## Evidência executada nesta revisão

Com `.venv/Scripts/python.exe`, sem repetir a suíte integral em paralelo ao coordenador:

1. `-m pytest -q -p no:cacheprovider tests/test_rev04_replanning.py tests/test_manifest_portability.py`: **22 aprovados em 1,44 s**. Inclui decisão posterior ao início do catálogo, opcionais vencidas, rede inicialmente sem alimentação, candidato corrompido e falha na exportação JSON.
2. `.audit_cache/rev04/final_review/review_checks.py`: **12 grupos de contraprovas/controles aprovados**. Reutiliza apenas os construtores dos scripts originais, sem suas antigas asserções que exigiam defeitos. A1, A2 e as duas variantes de A4 foram executados nos quatro métodos. Foram exercitados retornos do solver sem incumbent nos status 1/2/4 e incumbent no status 1. As sementes factíveis preservaram J=96,12 e não receberam prova de ótimo.
3. O testemunho A5 original foi rejeitado no objeto e na leitura JSON. Controles contíguos e separados por 0,0001 h produziram, respectivamente, J=193,20 e J=205,20 tanto na reconstrução quanto nos contadores independentes, com 0 e 1 reincidentes.
4. O script real `package_delivery.py` foi executado em uma cópia mínima isolada dentro da pasta de evidências. As **11 entradas do manifesto completo** conferiram byte a byte no ZIP, inclusive o manifesto público. Após converter seis arquivos CRLF para LF, a conferência canônica passou e o modo exato recusou essas mesmas diferenças.

Saídas detalhadas: `.audit_cache/rev04/final_review/checks.json`. Após a correção pontual do limite de 24 h, a contraprova adicional foi reexecutada: os quatro métodos recusaram o catálogo e o verificador o rejeitou; um controle com gap de um tick permaneceu factível e recebeu certificado coerente. A suíte integral é responsabilidade da execução consolidada do coordenador e não é apresentada como execução independente desta revisão. A rodada final dos 48 planos e o pacote definitivo são verificações posteriores a este parecer.

## Achado adicional preexistente, fechado: adjacência através da meia-noite

**Prioridade média, fechado por restrição explícita do catálogo; não é regressão deste diff.** Em `optimizer.py:789`, o MILP soma contagens de interrupção por dia. Essa soma considerava duas interrupções quando uma janela terminava às 24 h e outra começava às 0 h do dia seguinte, embora a união global não contivesse restauração positiva.

Caso mínimo reproduzido: uma fonte, um consumidor, uma zona, preparação/recomposição zero, catálogo/turno 0–24 h, dois serviços obrigatórios de 24 h, um no dia 1 e outro no dia 2. O plano é factível; o verificador conta zero reincidentes. Objetivo correto reconstruído: **266,80**. Objetivo MILP: **278,80**. A diferença é exatamente a penalidade de reincidência, 12.

A primeira execução REV04 retornou corretamente `OBJECTIVE_INTEGRITY_FAILURE`, `is_optimal_proven=False`, bound/gap nulos e o objetivo reconstruído 266,80. Não havia falso certificado. A soma diária já está presente em `f21690e`. Essa evidência anterior está preservada em `.audit_cache/rev04/final_review/cross_midnight_before_guard.json`.

O coordenador adotou a alternativa de recusa explícita, sem ampliar o MILP nesta revisão. Foi lida a guarda em `planning_policy.py` e a validação independente em `validation.py`: a diferença entre fim e início diário deve ser estritamente menor que 24 h em ticks. A contraprova foi reexecutada nesta revisão e B0/B1/B2/KGP retornaram `UNSUPPORTED_INPUT`, sem janelas ou certificado; o verificador também rejeitou o catálogo. Um catálogo 0–23,9999 h com serviços ajustados a esse limite passou: um reincidente, J=278,7997, objetivo reconstruído coerente e ótimo sobre as colunas. Assim, um gap positivo de um tick continua suportado.

Reprodutor atualizado e resultado: `.audit_cache/rev04/final_review/cross_midnight_probe.py` e `.json`. A restrição preserva a aditividade entre dias do modelo implementado. Os 48 casos conhecidos usam janelas 8–17 h e permanecem dentro desse escopo. Suportar catálogos sem recomposição diária positiva exigirá outra formulação; não é capacidade anunciada nesta entrega.

## Comportamentos considerados e não julgados

- Formação, despacho e estabilidade de ilhas DG: fora do modelo adotado; a rejeição explícita satisfaz a alternativa autorizada para A2.
- Fluxo AC, proteção, LOTO, segurança de campo e homologação: fora do demonstrador radial agregado e desta revisão.
- Completude de otimização contínua e escala industrial: fora do catálogo discreto limitado, cujas limitações são declaradas.
- Catálogo contínuo 24 h: explicitamente excluído após a contraprova acima; exige-se gap diário de recomposição de pelo menos um tick.
- Regeneração final de resultados, sincronização de documentos/Drive e bytes do pacote definitivo: etapas posteriores, não substituídas pela cópia mínima usada para testar o empacotador.

**Conclusão de integração:** apto para avançar com a REV04 no recorte documentado e manter a PR como rascunho, sem merge nesta etapa. Nenhum achado crítico novo nem bloqueador aberto nesta passagem; o caso adicional de meia-noite foi fechado mediante recusa explícita do catálogo não suportado, preservando o controle com gap positivo mínimo.
