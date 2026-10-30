# Ingestão GIS e integração ao núcleo KGP — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** importar arquivos de rede/carteira/recursos, preservar relações elétricas e demonstrar o fluxo integrado sobre GIS público real com premissas adicionais identificadas.

**Architecture:** um importador neutro produz os contratos existentes e relatório de qualidade. Um adaptador de referência prepara o dado público sem presumir o esquema da Cemig. O núcleo auditado conserva sua semântica e seu verificador.

**Tech Stack:** Python existente, CSV/JSON/GeoJSON com biblioteca padrão; ferramenta GIS auxiliar isolada se necessária para extrair o formato público.

**Spec:** `docs/superpowers/specs/2026-10-29-ingestao-gis-design.md`.

## Global Constraints

- Independência D06; sidecar sem telecomandos.
- Não modificar o núcleo/28 arquivos congelados nem forçar ganho.
- Preservar resultado REV04 e proveniência; dados reais e complementos sintéticos separados.
- JSON estrito, unidades e tempo explícitos; erro crítico impede planejamento.
- Orçamento e documentos empresariais privados fora Git; PR1 em rascunho, sem merge.

## Review Focus

- IDs órfãos/duplicados e fases incompatíveis precisam bloquear, não desaparecer.
- Campos ausentes não recebem padrões elétricos fictícios das dataclasses.
- Geometrias coincidentes não criam ligação; graus não viram quilômetros.
- Arquivos/manifesto não podem escapar da raiz nem aceitar JSON ambíguo/não finito.
- Reconciliação e recálculo devem preservar rede/carteira/recursos em ida e volta.

## Task 1 — Importador e CLI

**Files:** criar `src/kgp/ingestion.py`, `scripts/import_kgp_bundle.py`, `tests/test_ingestion.py`, fixtures mínimas.
**Consumes:** contratos e validador existentes; manifesto da spec.
**Produces:** `import_bundle(...) -> IngestionResult` e CLI com JSON de qualidade, código 0 no sucesso, não zero no bloqueio.

- [x] Escrever regressões e observar RED antes do módulo.
- [x] Implementar leitura, campos/unidades, conversão e validação.
- [x] Testar mapeamento CSV/JSON/GeoJSON e equivalência com instância conhecida.
- [x] Testar classes de erro do Review Focus e reportar linhas sem descarte.
- [x] Rodar testes de ingestão e revisar o diff.

## Task 2 — Referência GIS pública e integração

**Files:** dados mínimos com licença/proveniência em `data/gis_reference/`; adaptador/ensaio em `scripts/`; testes de integração e relatório em `reports/ingestion_20261029/`.
**Consumes:** fonte primária real e importador Task 1.
**Produces:** bundle reproduzível, relatório de reconciliação, quatro planos exportados e auditorias.

- [x] Identificar/baixar fonte primária pequena; conferir licença/hash/esquema.
- [x] Selecionar recorte determinístico e preservar IDs elétricos, fases e CRS.
- [x] Declarar em overlay cada parâmetro ou carteira sintética necessária.
- [x] Importar e comparar nós/arestas/conexões com referência independente do extrator.
- [x] Executar B0/B1/B2/KGP; exportar/ler/verificar 100% da carteira obrigatória sem exigir benefício.
- [x] Exercitar corrupção de conexão/fase/estado e comprovar bloqueio.

## Task 3 — Proposta, slide 3 e entrega

**Files:** mesmos Docs/Slides e PDFs do Drive; relatório técnico público sem informações financeiras.
**Consumes:** contratos e resultados realmente observados nas Tasks 1/2.
**Produces:** Q1/Q6 e plano de dados/metodologia claros; slide 3 entrada→algoritmo→saída, notas e PDFs conferidos.

- [x] Preparar texto/composição preservando núcleo histórico e limites.
- [x] Publicar correções delimitadas, reler e conferir PDFs afetados.
- [x] Rodar suíte completa, revisão independente e validação dos 48 planos conhecidos.
- [x] Atualizar manifestos, preservar pacotes anteriores e verificar ZIP/checkout/Drive.
- [x] Publicar na PR1 e conferir HEAD remoto, rascunho e principal preservada.
