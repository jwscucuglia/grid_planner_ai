# Contraprovas independentes — fronteiras de ingestão e modelo

Os relatos técnicos externos `import_contract/REPORT.md`, seus dois scripts e `model_boundary/REPORT.md`/`repro_model_boundary.py` foram lidos antes da reprodução. Os runners desta pasta foram escritos separadamente, com fixtures sintéticas explícitas e oráculo de componentes por fase sobre os registros brutos. Não dependem de pytest, módulos de testes, NetworkX, Drive ou dados do D06.

Executar na raiz do checkout com as dependências normais do projeto:

```powershell
.\.venv\Scripts\python.exe reports/validation_scripts/banca_ingestion_model/import_contract_reaudit.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_ingestion_model/model_domain_reaudit.py
```

Os scripts retornam 0 quando todas as expectativas passam e 1 quando alguma contraprova falha. Escrevem somente sob `.audit_cache/banca_audit/ingestion_model/`, sem substituir os logs públicos desta pasta. `--output-name` escolhe uma subpasta. `--source-root` permite carregar um snapshot de código diferente sem trocar o checkout.

## Evidência preservada

- `baseline_RED_f49_import.json`: código original `f49a8c6543717ff740f1f7c701e20d34e82e6e5a`, 19 sondagens, 9 expectativas violadas, 10 controles satisfeitos. As quatro execuções do CSV malformado ocorrem apenas porque a versão antiga libera a carteira incorretamente reduzida a uma obrigatória.
- `final_GREEN_import.json`: código corrigido no working tree, 15/15 verificações passaram; uma tentativa de criar symlink nativo foi pulada por WinError 1314. Os quatro planejadores não são chamados após a rejeição correta do CSV, por isso não há as quatro sondagens adicionais da versão RED. Os cenários de RuntimeError em resolução do manifesto e de uma tabela foram exercitados com substituição controlada de `Path.resolve`, sem mascarar o restante do importador.
- `baseline_RED_f49_model.json`: 17 casos/81 sondagens, 25 expectativas violadas e 56 controles satisfeitos na fronteira física anterior.
- `final_GREEN_model.json`: os mesmos 17 casos/81 sondagens passaram no código corrigido. Incluem fechamento de tie que criaria malha: transferência opcional abandonada com tie aberta e transferência obrigatória rejeitada.
- `regression_matrix_REV06.json`, `regression_materials_REV06.json` e `regression_underflow_REV06.json`: reexecuções dos runners públicos de `banca_rev05` contra a fonte REV06 estabilizada, com 58/58, 105/105 e 143/143. Os logs 0.5 em `banca_rev05` são preservados.
- `verification_summary_REV06.json`: comprova que os hashes dos 16 módulos nos cinco logs GREEN/regressão coincidem com a fonte atual observada pela revisão.

Cada log guarda hashes dos módulos carregados. A referência GIS guarda também hashes dos arquivos de nós/trechos/chaves, contagem 354/358 e componentes por fase. Nenhuma aresta é removida para produzir radialidade. Os logs RED usam bytes de código extraídos do Git, com LF, enquanto o working tree pode usar CRLF; isso é identificado separadamente da mudança lógica.

## Reproduzir a base RED sem trocar o checkout

O commit precisa estar disponível no Git local. O comando abaixo extrai somente o código KGP para uma pasta de auditoria:

```powershell
$auditBaseline = '.audit_cache/banca_audit/ingestion_model/f49_snapshot'
New-Item -ItemType Directory -Path $auditBaseline -Force | Out-Null
git archive --format=zip --output=.audit_cache/banca_audit/ingestion_model/f49_src.zip f49a8c6543717ff740f1f7c701e20d34e82e6e5a src/kgp
Expand-Archive -LiteralPath '.audit_cache/banca_audit/ingestion_model/f49_src.zip' -DestinationPath $auditBaseline -Force
.\.venv\Scripts\python.exe reports/validation_scripts/banca_ingestion_model/import_contract_reaudit.py --source-root "$auditBaseline/src" --output-name import_RED_reproduction
.\.venv\Scripts\python.exe reports/validation_scripts/banca_ingestion_model/model_domain_reaudit.py --source-root "$auditBaseline/src" --output-name model_RED_reproduction
```

Nessa base, código de saída 1 é esperado. A fonte atual e os logs públicos não são alterados.

## Limites

O oráculo físico verifica o domínio de árvores com uma fonte por componente/fase, incluindo multiplicidade de arestas. Não resolve fluxo AC. A conservação topológica não certifica as capacidades ou tensões reais do alimentador público. Tempos de zona são agregados autoritativos do cenário; duração individual de chave é informativa e não gera sequência de operadores. Não se realizou fullpytest nem a rodada de 48 planos nesta frente. A revisão não é homologação operacional ou auditoria por terceiro humano.
