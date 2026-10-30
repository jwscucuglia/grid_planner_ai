# Revisão independente da ingestão GIS — INGESTÃO01

Data: 29/10/2026. Base: `056f7d61d4eb4c327abb14a6af256a013f41ddac` (núcleo KGP 0.4 / REV04-P1). Esta revisão cobre somente o incremento de ingestão e a referência GIS pública; não substitui a auditoria anterior do núcleo.

**Conclusão: as três falhas reproduzidas foram corrigidas e suas contraprovas passaram. Não restaram achados abertos nesta revisão delimitada.**

## Escopo e procedimento

Foram lidos `AGENTS.md`, a especificação `docs/superpowers/specs/2026-10-29-ingestao-gis-design.md`, o importador, a CLI, o adaptador público e os testes correspondentes. O revisor não alterou implementação, testes, dados de origem ou índice Git. Os testemunhos adicionais foram produzidos em `.audit_cache/ingestion_review/`.

Verificações executadas:

- `.venv/Scripts/python.exe -m pytest tests/test_ingestion.py tests/test_gis_reference_integration.py -q`: **87 testes passaram** na rodada inicial; após as correções e regressões adicionais, **101 testes passaram em 38,75 s**.
- `.venv/Scripts/python.exe .audit_cache/ingestion_review/probes.py`: exemplos mínimos, planejamento B0/B1/B2/KGP, exportação/leitura do JSON e recálculo por `feasibility.py`.
- `.venv/Scripts/python.exe .audit_cache/ingestion_review/source_probe.py`: comparação independente com a GDB de origem, hashes, preservação do grafo, fases, estados de chave e alcance elétrico por fase.
- `.venv/Scripts/python.exe .audit_cache/ingestion_review/recheck.py`: os três exemplos inválidos passaram a produzir resultado bloqueado, diagnóstico JSON na CLI e código de saída 1. Os testemunhos de tensão e CSV foram atualizados apenas para fornecer `scope_status="ACTIVE"`, agora obrigatório, evitando que um campo ausente mascarasse a contraprova.
- `.venv/Scripts/python.exe .audit_cache/ingestion_review/artifacts_probe.py`: leitura dos quatro planos públicos já exportados, novo recálculo de factibilidade/métricas e conferência dos 21 hashes de artefatos do ensaio.

Não foi executada uma segunda suíte completa em paralelo com a validação conduzida pelo agente principal. O sucesso do importador significa conformidade da entrada com o modelo suportado; não garante que uma carteira arbitrária tenha solução factível.

## Três falhas reproduzidas, corrigidas e rechecadas

### R1 — Alta: código desconhecido de escopo elimina ordem obrigatória da avaliação

Testemunho mínimo: no bundle mínimo, manter `S1.mandatory=true` e alterar somente `S1.scope_status` para `"ATIVO"`. Antes da correção, `import_bundle` devolveu `is_valid=True`, sem problemas. B0, B1, B2 e KGP produziram planos sem serviços concluídos nem pendentes. O verificador independente informou plano factível e `total_mandatory_count=0`.

A origem é o domínio aberto de `scope_status` na ingestão: o núcleo considera somente `"ACTIVE"` como escopo ativo. Um código externo desconhecido entra como exclusão de carteira. Isso viola a exigência de não descartar ordens obrigatórias por interpretação implícita de códigos de origem.

Correção recomendada: validar os estados de escopo explicitamente suportados na fronteira de ingestão e bloquear valores desconhecidos, com tabela, linha e ID; conversão de códigos externos cabe ao adaptador documentado. A ordem original deve permanecer rastreável.

**Rechecagem:** `scope_status` passou a ser campo crítico explícito. A versão v1 aceita somente `"ACTIVE"`; códigos externos ou de inativação/cancelamento não possuem mapeamento suportado. O testemunho agora retorna `INVALID_SCOPE_STATUS`, tabela `services`, linha 1, ID `S1`, `is_valid=False`, sem rede ou catálogo liberados; a CLI produz JSON e código 1. A proveniência mantém a contagem original e o ID `S1`.

### R2 — Alta: ramo direto entre tensões nominais incompatíveis é aceito

Testemunho mínimo: manter o ramo `B1` entre `SRC` e `N1`, com `SRC.nominal_voltage_kv=13.8`, alterando somente `N1.nominal_voltage_kv` para `0.38`. Antes da correção, a importação permaneceu válida, sem problemas; todos os quatro métodos concluíram `S1` e o verificador informou factibilidade.

O contrato reduzido de `GridBranch` não representa conversão de tensão. Validar apenas positividade das tensões não impede que um erro de unidade ou de vínculo passe como linha condutora direta entre níveis diferentes.

Correção recomendada: bloquear incompatibilidade de tensão nas duas extremidades dos ramos suportados, identificando o ramo e sua linha. Eventual conversão de tensão exige um contrato e modelo próprios, fora deste incremento.

**Rechecagem:** o testemunho agora produz `UNSUPPORTED_VOLTAGE_TRANSITION`, tabela `branches`, linha 1, ID `B1`. Rede e catálogo são retidos; a CLI produz JSON e código 1. A comparação usa tolerância relativa declarada de `1e-9`, sem tolerância absoluta; os controles cobrem ruído de representação, mesma tensão entre alimentadores distintos e níveis diferentes em redes sem ramo de ligação.

### R3 — Média: falha do leitor CSV escapa da API e da saída JSON

Testemunho mínimo: no bundle CSV mínimo, usar um campo `nodes.name` de 131.073 caracteres, acima do limite padrão de campo da biblioteca CSV. Antes da correção, a API lançou `_csv.Error: field larger than field limit (131072)` sem devolver `IngestionResult`. A CLI terminou com código 1, mas a saída padrão não continha o relatório JSON previsto.

A exceção de leitura CSV não integrava a fronteira de captura dos erros de arquivo. Um código de saída não zero sem JSON quebra o contrato de diagnóstico estruturado.

Correção recomendada: transformar `csv.Error` em problema de ingestão bloqueante e manter saída JSON e código 1 na CLI. Quando o arquivo não puder ser lido, registrar a tabela/arquivo; não atribuir uma linha inventada.

**Rechecagem:** a exceção passou a ser capturada. O testemunho devolve `INVALID_FILE` na tabela `nodes`, incluindo o limite de campo; `row` e `record_id` permanecem nulos para a falha de leitura do arquivo. A API retorna resultado inválido, a CLI produz JSON e código 1, e a ordem `S1` permanece rastreável na proveniência.

## Fonte pública e separação do overlay

A fonte verificada foi o ZIP BDGD ANEEL/EFLUL, referência 31/12/2025, SHA-256 `35a071a48728cd3bf5dbd9aa960026c9f6290f04c3d4abea25038eae476df168`, preservado no cache local. Os metadados de origem, licença e procedimento estão em `data/gis_reference/eflul_2025_ctmt002/source/`, com identificação da fonte primária e qualificação da licença do catálogo pai.

O revisor leu diretamente a GDB e comparou todos os atributos dos CSVs e todas as propriedades dos GeoJSONs exportados: CTMT (1), SSDMT (314), UNSEMT (43) e UNREMT (1). As comparações coincidiram; os 21 hashes de artefatos do manifesto local também coincidiram.

No bundle preparado e importado foram preservados **354 PACs e 358 arestas**, incluindo arestas paralelas, extremidades explícitas, IDs e fases. As 43 chaves mantiveram as posições normais declaradas: **33 fechadas e 10 abertas**. O alcance por fase do KGP coincidiu com a travessia independente das relações PAC da origem; **344 PACs** tinham alimentação em pelo menos uma fase no estado normal. Nenhuma conexão foi inferida por geometria ou proximidade.

O adaptador identifica explicitamente coordenadas de programação sintéticas, dois consumidores sintéticos, carteira, recursos, materiais, parâmetros elétricos ausentes, premissa de elementos em serviço e uso do estado normal como estado atual. O regulador é tratado apenas como conexão passante; geometria da origem fica preservada para exibição. O manifesto declara `operational_validation=false` e registra os limites do recorte, sem UC reais ou rede BT.

Essas verificações demonstram preservação de dados e integração computacional. O ensaio de B0/B1/B2/KGP sobre o overlay deve ser apresentado como verificação de factibilidade da carteira sintética. Ele não mede benefício operacional da distribuidora, não representa validação de campo e não altera os resultados históricos REV04.

Os quatro planos em `reports/ingestion_20261029/plans/` foram relidos pelo revisor e submetidos novamente ao mesmo verificador independente: **B0, B1, B2 e KGP factíveis, cada um com 2/2 ordens obrigatórias**. O recálculo coincidiu com os JSONs de verificação e métricas publicados. O resumo registra as nove comparações por fase/etapa entre referência de origem, BFS e Union-Find: normal, isolamento do ensaio e restauração. O gráfico foi inspecionado visualmente e mantém a qualificação de geometria para exibição e de overlay sintético.

## Fechamento

R1, R2 e R3 estão fechados pelas contraprovas e pelos controles de regressão. A revisão confirma o incremento no escopo declarado, com v1 restrita a carteira explicitamente ativa, níveis de tensão compatíveis por ramo, unidades suportadas e parâmetros operacionais explicitados. `is_valid` continua sendo um resultado de validação de entrada; a aprovação de um plano exige a execução de `feasibility.py` sobre o plano exportado.

As evidências da rodada inicial estão em `.audit_cache/ingestion_review/probe_results.json`; as rechecagens estão em `recheck_results.json`, `source_probe_results.json` e `artifacts_probe_results.json`. A suíte completa e o fechamento do pacote permanecem sob responsabilidade da validação principal.

Identidades SHA-256 da implementação rechecadas:

- `src/kgp/ingestion.py`: `f3b17053012a9bc3e4e4cba841174a1e16bb2f0e2c5949b407b4bed90e926497`.
- `scripts/import_kgp_bundle.py`: `59fede1e8127062103da8bdbe494ae0cf860483f6a98013ddbc46c0ea7876ab3`.
- `scripts/prepare_gis_reference.py`: `38dbe08a4a3fdd448f2fcea0c4a949c17e59cf17a9cd2d08b83f938651cdfc7d`.

## Complemento de revisão documental

Foram confrontados `docs/MODELO_MATEMATICO_KGP.md` e `docs/CONTRATO_INGESTAO_KGP.md` com a implementação final, sem novos testes do núcleo. A formulação descreve corretamente a desigualdade de reincidência, a indiferença da variável interna quando o peso é zero, o recálculo das métricas publicadas e o certificado restrito às agendas discretas geradas. A separação entre fonte pública, overlay sintético e evolução dependente da PoC é compatível com o adaptador e o validador.

Dois ajustes textuais foram solicitados e conferidos: os limites de 5 serviços/pacote, 5 janelas/dia e 120 estados por profundidade qualificam a expansão construtiva, com agendas sementes B0/B1/B2 adicionais; os hashes de arquivos no diagnóstico referem-se aos arquivos lidos e decodificados com sucesso. Com esses ajustes, não restaram incompatibilidades factuais identificadas nos dois documentos.
