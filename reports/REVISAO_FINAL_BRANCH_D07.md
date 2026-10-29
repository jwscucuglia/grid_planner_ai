# Revisão final independente da branch D07

Data: 2026-10-29. Revisão por outro agente de software; não equivale a auditoria por terceiro humano.

Base solicitada: `84340636c6d3877eacf44d4e19a1b802451057c1`. Estado examinado: branch `codex/d07-revisao-integral-20261029`, posteriormente registrada em `2e6906d269f94b90da9c6afd344ed6e2d4cfe53f` durante a revisão. Não foram editados arquivos de produção nem testes versionados pelo revisor.

## Escopo e resultado

Leitura de AGENTS.md, plano `docs/superpowers/plans/2026-10-29-revisao-integral-d07.md`, otimizador, revalidação/experimento e interações com contratos, verificador, métricas e baselines. Revisão restrita ao demonstrador finito declarado, sem exigir modelagem operacional de campo fora do escopo.

Nenhum achado Critical nesta revisão. Dois achados Important requerem correção antes de declarar encerrada a revisão. Dois ajustes Minor de rastreabilidade/linguagem também são descritos. O experimento completo não foi executado pelo revisor; sua execução pertence ao coordenador.

## Important 1 — timeout sem incumbente do solver perde evidência do fallback factível

Local: `src/kgp/optimizer.py:735–763`, em particular atribuições de objetivo/bound/gap a `None`.

Reprodução pequena: `small_instance(mandatory=True)` de `test_optimizer_audit.py`, com retorno controlado do solver `SimpleNamespace(x=None, status=1, mip_dual_bound=0., mip_gap=None, fun=None)`. O fallback B2 passa pelo verificador, conclui a ordem e tem objetivo canônico `96.12`. Apesar disso, o plano exporta objetivo, bound e gap `None`. O texto de escopo ainda afirma inviabilidade, embora o evento tenha sido limite de execução.

Efeito: o caminho de timeout perde uma solução factível conhecida e um limite inferior conhecido no relato quantitativo, contrariando a regra de reportar melhor solução factível, bound, gap e tempo sem alegar ótimo. A correção já cobre timeout com incumbente do solver; falta o caminho sem incumbente do solver e com fallback factível.

Regressão esperada: preservar a distinção entre `status=2` realmente inviável e `status=1` interrompido. No caso reproduzido, recalcular J do fallback com carteira/pesos canônicos, preservar bound zero e recomputar gap `1.0`, sem prova de ótimo e sem texto de inviabilidade. Um fallback incompleto/inviável não deve receber objetivo factível.

## Important 2 — rodada sem comparação válida assina gráfico de rodada anterior

Local: `src/kgp/revalidation.py:142–160`.

Reprodução isolada: destino `.audit_cache/final_review/stale_artifact`; PNG preexistente com conteúdo marcador; uma instância EVAL com ordem mandatory exigindo competência inexistente. Resultado da execução: `0/4 feasible; 0 EVAL pairs`. O PNG antigo permanece byte a byte e aparece em `reports/manifest_sha256.json` da nova rodada.

Efeito: as tabelas excluem corretamente planos inválidos, mas a evidência visual pode continuar mostrando benefício de outra rodada e receber hash junto dos resultados atuais. O `glob` de todos os planos/verificações no diretório também pode incluir casos remanescentes de outra configuração.

Regressão esperada: rodada sem pares válidos deve gerar figura explícita de ausência de comparação ou retirar o artefato legado da evidência corrente. O manifesto deve enumerar somente artefatos efetivamente produzidos/admitidos nesta rodada. Preservar histórico separadamente é adequado; reutilizá-lo como figura atual não é.

## Minor 1 — raio B1 da semente não corresponde ao comparador avaliado

Local: `src/kgp/optimizer.py:567` e `src/kgp/experiment.py:900`.

O protocolo e comparador B1 usam raio `0.75`; a chamada interna do otimizador omite o argumento e usa default `1.25`. Um spy em caso pequeno observou `[0.75, 1.25]`, comparador seguido de semente. Isso não prova degradação dos resultados atuais, mas impede afirmar sem ressalva que as colunas contêm a sequência do B1 efetivamente comparado.

Correção sugerida: passar as sementes já produzidas ou propagar o raio usado ao KGP; alternativamente, documentar explicitamente a diferença e retirar a garantia de inclusão exata do comparador avaliado.

## Minor 2 — texto do escopo afirma ótimo em resultado limitado

Local: `src/kgp/optimizer.py:879`.

`metadata.optimality_scope` usa incondicionalmente “Ótimo global comprovado” depois de aceitar tanto status ótimo quanto incumbente em limite. Os campos `solver_status` e `is_optimal_proven` estão corretos no teste de limite, mas a frase dos metadados contradiz esses campos.

Correção sugerida: definir o espaço de validade do bound/certificado sem declarar ótimo quando o status não o prova. O fallback de timeout também deve evitar a frase de inviabilidade apontada no primeiro achado.

## Evidência executada

Comando, no ambiente `.venv` do projeto:

```powershell
./.venv/Scripts/python.exe -m pytest -q tests/test_optimizer_audit.py tests/test_experiment_protocol.py tests/test_verifier_audit.py tests/test_independent_review.py tests/test_baseline_local.py
```

Resultado observado: **51 passed in 11.03s**, código de saída 0.

Reproduções adicionais persistidas em `validation_scripts/reproduce_final_findings.py`, executadas com código de saída 0:

```text
TIMEOUT_FALLBACK: feasible=True; objective=None; bound=None; gap=None; true_objective=96.12
STALE_FIGURE: comparable=0; old_figure_unchanged=True; figure_in_new_manifest=True
B1_RADIUS_COMPARATOR_THEN_SEED: [0.75, 1.25]
```

O script usa monkeypatch de estado de término do solver e de uma única instância/semente para exercitar caminhos específicos, sem executar o experimento completo nem modificar fontes. Os resultados principais de optional penalty 50, mandatory rígido, predecessor opcional, JSON estrito, isolamento global conservador, impacto independente e casos de alteração adversarial passaram nas regressões existentes. Aprovação final depende das correções dos achados Important e de sua verificação posterior.

## Fechamento pelo implementador — fonte 91d98ef

Os quatro achados acima descrevem o estado observado antes da correção. Foram fechados no commit `91d98ef324e43a229efdfe49a8c804f3d8685d60`, com regressões que falharam antes e passaram depois. O raio divergente e o texto contraditório de ótimo foram tratados como importantes para a fidelidade das alegações, além dos dois casos originalmente graduados Important.

- Timeout sem incumbente: fallback verificado com J96,12, bound0, gap1 e status `FEASIBLE_FALLBACK_BOUNDED`; nenhum texto de inviabilidade ou certificado de ótimo.
- Rodada inviável: figura substituída por aviso sem comparação; planos/verificações legados excluídos do manifesto corrente.
- Raio B1: spy agora observa `[0.75, 0.75]` no comparador e na semente.
- Incumbente em limite: o texto de escopo informa ausência de prova de ótimo.

Verificação pós-correção: os13 testes de otimizador/protocolo passaram em3,31s; suíte completa74/74 em83,78s; replay de achados em `validation_logs/final_findings_after.log`. O replay mostra `old_figure_unchanged=False`: a figura atual, com ausência de comparação, permanece corretamente incluída no manifesto. A rodada final produziu48/48 planos factíveis e foi reproduzida integralmente com igualdade de48/48 métricas/status, excluído tempo. Este fechamento foi registrado pelo implementador após a revisão; não é nova revisão independente ou homologação humana.

No fechamento adicional, a árvore `5adf593ad9168e61a4b4ceea62346db7402a3d4c` removeu o piso silencioso de5km/h no deslocamento do planejador e verificador. O teste canônico existente ganhou asserções10km/2km/h=5h, que falharam antes e passaram após a correção. A suíte completa foi reexecutada:74/74 em81,54s. Os dados experimentais usam velocidades superiores ao antigo piso; a rodada foi novamente congelada/reexecutada. Esta ampliação e seus logs constam do parecer integral, sem transformar o fechamento do implementador em revisão humana independente.
