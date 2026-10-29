# Contraprovas públicas — auditoria de código REV05

Fixtures inteiramente sintéticas. Os scripts dependem somente da biblioteca padrão, do código `src/kgp` deste checkout e de suas dependências normais (SciPy/NumPy). Não importam módulos de `tests`, não chamam pytest e não acessam Drive, bases corporativas ou dados do D06.

Da raiz do repositório:

```powershell
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/material_probe.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/adversarial_matrix.py
.\.venv\Scripts\python.exe reports/validation_scripts/banca_rev05/materials_exact_reaudit.py
```

`material_probe.py` reproduz o testemunho mínimo original. Os outros dois scripts retornam código 0 quando todas as expectativas passam, 1 quando uma expectativa falha. Todos escrevem fixtures, planos e resultados novos em `.audit_cache/banca_audit/public_reproduction/`, sem sobrescrever os logs públicos preservados nesta pasta. Os oráculos próprios usam contagem de ticks e aritmética decimal com 2.000 algarismos; não debitam o campo de apresentação `materials_used`.

Os resultados preservados são:

- `baseline_RED_e4f9dd_matrix.json`: primeira execução contra HEAD `e4f9dd78e9ef0065a87f40b31889bfcb07da4ade`, antes da correção. Registra hashes dos módulos, 12 classes/58 sondagens, 50 expectativas cumpridas e 8 violações na conservação de materiais, nos quatro motores.
- `baseline_RED_e4f9dd_material.json`: testemunho importado com demanda M=0,00004 e estoque zero; os quatro métodos foram incorretamente aceitos. O KGP publicou prova de ótimo.
- `final_GREEN_matrix.json`: reexecução pública após a correção no working tree, 58/58 expectativas cumpridas.
- `final_GREEN_materials_exact.json`: extensão da mesma classe de materiais, 105/105 expectativas cumpridas; contém hashes de todos os módulos observados, inclusive o novo `materials.py`.
- `final_GREEN_material_probe.json`: o testemunho original deixa de completar a obrigatória nos quatro métodos; todos são rejeitados pelo verificador, sem certificado KGP.

As duas evidências RED foram preservadas da execução inicial, cujos scripts usavam fixtures dos testes do mesmo repositório. As fixtures explícitas equivalentes estão agora em `audit_support.py`, permitindo rodar o pacote público sem depender desses testes. Os logs GREEN foram gerados pelos scripts públicos já usando esse suporte próprio. O HEAD continuava igual durante a revisão; os hashes identificam os módulos corrigidos no working tree. O relatório técnico é `reports/AUDITORIA_ADVERSARIAL_CODIGO_BANCA.md`.

As 105 verificações adicionais abrangem demandas `1 + 1e-28`, `0.1 + 0.2`, subnormais, terços decimais, planos entre janelas/dias, consumo forjado, echo inválido e overflow. Dois extremos expõem a precisão limitada do MILP em double: o controle final exato rejeita o candidato e devolve `FEASIBLE_FALLBACK_NO_BOUND`, sem transportar certificado. As contagens são verificações direcionadas sobre casos pequenos, sem inferência estatística e sem homologação operacional.
