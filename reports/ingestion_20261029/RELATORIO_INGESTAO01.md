# INGESTAO01 — GIS público e integração ao KGP

Fonte real: BDGD/ANEEL EFLUL 31/12/2025, CTMT 002. Os 354 PACs e 358 registros de ligação foram preservados, incluindo paralelas e estados normais das 43 chaves (33 fechadas, 10 abertas). A exportação CSV/GeoJSON foi conferida contra a GDB original e tem licença/proveniência próprias.

A referência de alcançabilidade por fase dos registros de origem coincide com BFS e Union-Find na configuração normal, no isolamento do ensaio e na restauração.

O importador produziu os contratos existentes e o mesmo núcleo executou B0/B1/B2/KGP. Os quatro planos foram exportados, relidos e aprovados pelo verificador, com 2/2 serviços obrigatórios em cada um.

**Limite:** topologia pública real com overlay sintético de cargas, consumidores, carteira, recursos, parâmetros ausentes, cenário de manobra e coordenadas de agenda. O regulador é somente ligação passante. O estado normal das chaves foi assumido como estado do ensaio; não é telemetria histórica.

A fonte apresenta 81 PACs com posições divergentes, até aproximadamente 64m; suas geometrias ficam restritas à visualização/QC. As posições de programação são sintéticas, sem interpretação de deslocamento real. Não há UC real, BT, dado Cemig, fluxo AC/proteção homologados ou estimativa de benefício operacional/financeiro.

Reprodução: `python scripts/prepare_gis_reference.py`; `python scripts/import_kgp_bundle.py data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json`; `python scripts/validate_gis_ingestion.py`. Consultar `summary.json`, `import_quality.json`, `plans/`, `verification/` e `data/gis_reference/eflul_2025_ctmt002/source/NOTICE.md`.
