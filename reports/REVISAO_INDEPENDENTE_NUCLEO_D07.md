# Revisão independente do núcleo do KGP — 29/10/2026

Base examinada: `84340636c6d3877eacf44d4e19a1b802451057c1` e alterações da branch
`codex/d07-revisao-integral-20261029`, ainda sem commit final no momento desta revisão.
O relatório geral identifica o commit e o manifesto finais da entrega.

Revisão realizada por outro agente de IA, em contexto separado do implementador do
verificador. Este revisor também implementou as correções do otimizador; por isso
**esta revisão independente não abrange sua própria implementação de `optimizer.py`**.
Não constitui revisão por terceiro humano, homologação pela Cemig ou validação de campo.

## Escopo efetivamente inspecionado

- Contratos, leitor JSON estrito e fronteira de tipos em `contracts.py`.
- Validação estrutural e referências cruzadas em `validation.py`.
- Referência elétrica por fase, construção de etapas e cronologia global em `feasibility.py`.
- Bloqueio conservador de todas as sobreposições elétricas no mapa de conflitos dos comparadores.
- Consumo dos contadores independentes e apresentação do certificado em `metrics.py`.
- Regressões da reauditoria em `tests/test_verifier_audit.py` e novos contraexemplos em `tests/test_independent_review.py`.
- Leitura da melhoria local limitada de B2: troca de uma zona, pacotes/dias preservados,
  reconstrução das agendas e aceitação condicionada ao mesmo verificador e redução de J.

## Achados e fechamento observado

| Achado | Efeito reproduzido ou constatado | Ação do implementador | Verificação independente |
|---|---|---|---|
| R1 — Tratamento de contrato inválido ainda percorria campos inválidos | `windows=[None]`, `decision_day="0"` e carteira com `None` provocavam `AttributeError`/`TypeError` em vez de relatório de rejeição | Retorno fechado sem percorrer estruturas já rejeitadas | Três casos falharam antes e passaram após a correção |
| R2 — Conversão de enum era descartada pelo chamador | Chave com `current_state="CLOSED"` e etapa adulterada com `stage="EXECUTION"` causavam erro ao acessar `.value` | Objetos diretos exigem enums tipados; JSON continua decodificando seus valores válidos | Dois casos falharam antes e passaram após a correção |
| R3 — Inteiro não representável como float | `solver_time_sec=10**400` em JSON provocava `OverflowError` | Leitor converte a falha em rejeição `ValueError` de número não representável/finito | Caso falhou antes e passou após a correção |
| R4 — Métricas podiam promover uma alegação inconsistente a ótimo | Leitura identificou que status e gap bastavam mesmo com `is_optimal_proven=False` e objetivo 100/bound 0 | Métricas passaram a exigir flag, status qualificado e coerência objetivo/bound | Regressão independente confirma que a alegação inconsistente não se torna certificado; não se reivindica ciclo RED de pytest para esse caso |
| R5 — Consumidor inicialmente sem alimentação era contado como recomposto nas lacunas | Em duas janelas 8–8,7h e 8,8–9,5h, um consumidor inicialmente isolado produziu aprovação, 2,8 clientes-hora e dois reincidentes. O consumidor isolado nunca foi recomposto; sob horizonte contínuo seriam 2,9 clientes-hora e um reincidente | Escopo conservador: entradas com consumidor inicialmente sem alimentação são explicitamente rejeitadas; interrupção preexistente depende de outro horizonte de observação | Reprodução direta registrada nesta revisão; nova regressão exige rejeição explícita fora do escopo e passou |

Os seis casos de R1–R3 estão registrados com falha em
`validation_logs/independent_review_red.log`. O arquivo final de testes preserva as
expectativas corretas, sem depender dos scripts da auditoria anterior nem remover
os planos inválidos para obter aprovação.

R5 é redução explícita de escopo, e não implementação de um modelo de interrupções
preexistentes. Os experimentos admitidos começam com os consumidores alimentados.

## Evidência de verificação desta revisão

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_independent_review.py -q
# 8 passed in 3.54s

./.venv/Scripts/python.exe -m pytest tests/test_independent_review.py tests/test_verifier_audit.py -q
# 37 passed in 9.93s
```

O segundo comando verifica em conjunto os testes do implementador e os oito casos
adversariais desta revisão. O log está em `validation_logs/independent_review_final.log`.
A suíte integral, a rodada experimental, os documentos, o pacote e os hashes finais
pertencem à integração coordenada e devem ter sua própria evidência no relatório geral.
Estes dois comandos não substituem essa integração.

## Independência e limites das garantias

O verificador deixou de chamar o simulador do planejador para reconstruir as etapas,
os consumidores afetados e a união dos intervalos. A conectividade usa componentes
por conjuntos disjuntos (Union-Find), enquanto o planejador usa busca em largura.
A união de intervalos no verificador tem implementação separada. Os testes de injeção
de falha no simulador compartilhado passam a detectar o plano com impactos apagados.

Persistem contratos, dados, verificações cadastrais e hipóteses elétricas comuns.
O cálculo de transferência permanece uma aproximação radial por fase com a mesma
formulação física documentada; não é confronto com um solucionador elétrico externo.
Malhas, paralelismo de fontes, proteção, aterramento, LOTO e manobras individuais de
campo não recebem certificado operacional por esses testes.

A cronologia global recompõe os estados por fase em todas as fronteiras de etapas
representadas. Todas as janelas elétricas sobrepostas continuam bloqueadas, inclusive
em zonas disjuntas: a verificação não autoriza simultaneidade operacional com base
apenas na ausência de reenergização encontrada pelo teste de conectividade.
As durações e transições de manobra são agregadas por etapa do catálogo sintético.

A igualdade entre objetivo e bound e a flag exportada são condições de coerência do
relato do solver, não uma prova criptográfica ou reexecução independente do MILP pelo
verificador. O certificado divulgado permanece `OPTIMAL_ON_DISCRETE_COLUMNS`, restrito
ao catálogo gerado. O código e o experimento precisam estar associados aos seus hashes.

## Parecer de escopo

Os achados R1–R5 desta rodada foram encerrados por correção verificada ou bloqueio
explícito de escopo. A evidência sustenta a integração e a revisão interna do
demonstrador sintético sob as restrições declaradas. Não sustenta alegação de
validação operacional nem prontidão de submissão sem a conferência documental,
empresarial e de integração descrita no relatório geral.
