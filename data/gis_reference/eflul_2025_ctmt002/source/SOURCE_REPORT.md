Este é um recorte de cadastro GIS **real e público** de distribuição. Ele permite ensaiar ingestão e conectividade; não constitui validação operacional da EFLUL ou da Cemig. Consumidores, rede BT e ordens de serviço não integram o recorte. Qualquer ensaio de programação que o utilize deve declarar seus próprios dados sintéticos de carga, consumidores, recursos, carteira e parâmetros ausentes.

Fonte e reprodução:

- ANEEL BDGD, distribuidora EFLUL (código 86), referência 31/12/2025; arquivo `Eflul_86_2025-12-31_V11_20260823-1830.gdb.zip`.
- Download oficial: <https://www.arcgis.com/sharing/rest/content/items/04a736e95f114c32a56206957a492cba/data>.
- Tamanho do ZIP: 3.174.969 bytes; SHA-256: `35a071a48728cd3bf5dbd9aa960026c9f6290f04c3d4abea25038eae476df168`.
- Aquisição em 29/10/2026. O pacote integral permanece apenas em `.audit_cache/ingestion_gis/`; ele contém outras entidades não redistribuídas neste recorte.
- Seleção reproduzível: `CTMT.COD_ID='002'` e todos os registros `SSDMT`, `UNSEMT`, `UNREMT` com `CTMT='002'`. É o menor dos quatro alimentadores da base pelo número de segmentos MV.
- Licença e atribuição: `NOTICE.md`, `LICENSE_ODBL_1_0.txt`, `catalog_license_metadata.json` e `arcgis_item_metadata.json`.

| Entidade | Registros | Conteúdo |
|---|---:|---|
| CTMT | 1 | Alimentador USA 06, fonte `PAC_INI=S01B01A02P0000002087` |
| SSDMT | 314 | Segmentos MV; fases ABC em 308 e A em 6 |
| UNSEMT | 43 | Chaves MV, 33 normalmente fechadas e 10 abertas |
| UNREMT | 1 | Regulador, preservado como registro de origem |
| PACs distintos | 354 | Todas as extremidades declaradas das 358 arestas |

Os CSVs das quatro entidades preservam os campos originais. Campos identificadores são texto: preserve zeros à esquerda. Os GeoJSONs preservam propriedades e geometria da origem convertida para EPSG:4326; CTMT não tem geometria. O esquema dessas quatro entidades está em `exported_layer_schema.json`. `mv_connectivity.csv` combina as três entidades elétricas sem colapsar arestas paralelas; `edge_id` é `entidade:COD_ID`.

A conectividade deriva exclusivamente de `PAC_1` e `PAC_2`. O estado normal considera `P_N_OPE=F` fechado e `A` aberto. O regulador é uma aresta passante **apenas para conectividade**, sem representar regulação de tensão. Permanecem três pares de segmentos paralelos; não há conexão inventada por proximidade, corte de ciclos ou fechamento automático de chaves. O estado normal tem 348 arestas fechadas, 344 PACs no componente da fonte e 10 extremidades isoladas de chaves abertas. Não foi criada continuação para alimentadores externos ao recorte.

O CRS original é **SIRGAS 2000, EPSG:4674**. `mv_nodes_display.csv` usa referências explícitas `SSDMT.PN_CON_1/2 -> PONNOT.COD_ID` e pontos dos dispositivos para reunir posições de cada PAC. Quando há conflito, registra a primeira referência em ordem lexicográfica como representante e conserva todas as alternativas no relatório de qualidade. A projeção auxiliar é **SIRGAS 2000 / UTM 22S, EPSG:31982**, em metros, sempre com ordem longitude/latitude. Há posições para os 354 PACs e nenhuma referência PN_CON faltante, porém **81 PACs têm mais de uma posição na origem**, com divergência máxima de **63,987 m**. Essas coordenadas servem para exibição, sem homologação para deslocamento ou tempo de viagem. O relatório não corrige ligações a partir delas.

O Manual BDGD Rev5 identifica `TEN_NOM='49'` como código de **13.800 V** (p. 182), `P_N_OPE=A/F` como aberta/fechada (p. 202), `COMP` em metros e `SEGCON.CNOM/CMAX` em amperes. Valores codificados não devem ser tratados como unidades físicas diretamente. O manual está identificado em `source.json`; parâmetros de SEGCON não foram incorporados ao modelo reduzido.

Para reproduzir a extração a partir da raiz do repositório, use um leitor auxiliar isolado e execute:

```powershell
.venv/Scripts/python.exe -m pip install --target .audit_cache/ingestion_gis/tools --no-deps pyogrio==0.13.0 shapely==2.2.0 pyproj==3.7.2 certifi
.venv/Scripts/python.exe data/gis_reference/eflul_2025_ctmt002/source/extract_reference.py --output data/gis_reference/eflul_2025_ctmt002/source
```

O script exige que numpy e networkx já estejam no ambiente científico do projeto. As bibliotecas auxiliares ficam no cache e não alteram as dependências do KGP. A identidade do ZIP é verificada antes de ler a GDB. `manifest_sha256.json` registra a integridade dos artefatos locais.
