# Contraprovas públicas — auditoria de código REV05

Fixtures inteiramente sintéticas. Os scripts dependem somente da biblioteca padrão, do código `src/kgp` deste checkout e de suas dependências normais (SciPy/NumPy). Não importam módulos de `tests`, não chamam pytest e não acessam Drive, bases corporativas ou dados do D06.

Da raiz do repositório:

```powershell
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/material_probe.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/adversarial_matrix.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/materials_exact_reaudit.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/numeric_underflow_reaudit.py
```

`material_probe.py` reproduz o testemunho mínimo original. Os outros três runners retornam código 0 quando todas as expectativas passam, 1 quando uma expectativa falha. Todos escrevem fixtures, planos e resultados novos em `.audit_cache/banca_audit/public_reproduction/`, sem sobrescrever os logs públicos preservados nesta pasta. Os oráculos próprios usam contagem de ticks e aritmética decimal; não debitam o campo de apresentação `materials_used`. O runner de materiais usa precisão decimal de 2.000 algarismos; o de underflow lê diretamente os tokens dos bytes com `parse_float=Decimal`.

Os resultados preservados são:

- `baseline_RED_e4f9dd_matrix.json`: primeira execução contra HEAD `e4f9dd78e9ef0065a87f40b31889bfcb07da4ade`, antes da correção. Registra hashes dos módulos, 12 classes/58 sondagens, 50 expectativas cumpridas e 8 violações na conservação de materiais, nos quatro motores.
- `baseline_RED_e4f9dd_material.json`: testemunho importado com demanda M=0,00004 e estoque zero; os quatro métodos foram incorretamente aceitos. O KGP publicou prova de ótimo.
- `final_GREEN_matrix.json`: reexecução pública após a correção no working tree, 58/58 expectativas cumpridas.
- `final_GREEN_materials_exact.json`: extensão da mesma classe de materiais, 105/105 expectativas cumpridas; contém hashes de todos os módulos observados, inclusive o novo `materials.py`.
- `final_GREEN_material_probe.json`: o testemunho original deixa de completar a obrigatória nos quatro métodos; todos são rejeitados pelo verificador, sem certificado KGP.
- `baseline_RED_underflow_pre_parser.json`: investigação A-CODE-02 após o conserto de saldos, antes do guard de leitura/conversão. Contém os bytes identificados por SHA-256, valores decimais independentes e perda de `±1e-400` para zero nos formatos JSON/CSV. Base dos módulos: `e2a72e993ec13cac5304fe04d9e3800578e17a80` (hash de `contracts.py` inclui CRLF; o blob Git usa LF).
- `final_GREEN_numeric_underflow.json`: 143/143 verificações do leitor estrito, JSON/CSV/GeoJSON, conversão de unidades e controles executados nos quatro métodos. Guarda os hashes do código após A-CODE-01/A-CODE-02.
- `pre_parser_GREEN_matrix.json` e `pre_parser_GREEN_materials_exact.json`: cópias da reavaliação intermediária anterior ao guard numérico. As evidências `final_GREEN_matrix.json` e `final_GREEN_materials_exact.json` foram reexecutadas após o parser corrigido e continuam 58/58 e 105/105.

As duas evidências RED de A-CODE-01 foram preservadas da execução inicial, cujos scripts usavam fixtures dos testes do mesmo repositório. As fixtures explícitas equivalentes estão agora em `audit_support.py`, permitindo rodar o pacote público sem depender desses testes. A evidência RED de A-CODE-02 usou esse suporte público e oráculo decimal sobre os bytes. Os logs GREEN foram gerados pelos scripts públicos. Os hashes identificam os módulos observados em cada etapa; o repositório avançou entre a base inicial e o conserto do parser. O relatório técnico é `reports/AUDITORIA_ADVERSARIAL_CODIGO_BANCA.md`.

As 105 verificações adicionais abrangem demandas `1 + 1e-28`, `0.1 + 0.2`, subnormais, terços decimais, planos entre janelas/dias, consumo forjado, echo inválido e overflow. Dois extremos expõem a precisão limitada do MILP em double: o controle final exato rejeita o candidato e devolve `FEASIBLE_FALLBACK_NO_BOUND`, sem transportar certificado. As contagens são verificações direcionadas sobre casos pequenos, sem inferência estatística e sem homologação operacional.
