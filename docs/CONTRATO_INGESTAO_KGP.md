# Contrato de importação KGP — INGESTAO01 / integridade REV06

## Interface e responsabilidade

`kgp.ingestion.import_bundle(manifest_path)` lê um pacote de arquivos e retorna `IngestionResult` com `network`, `services`, `catalog`, `issues`, `provenance` e `is_valid`. As saídas válidas são os contratos existentes `GridNetwork`, `ServiceOrder` e `ResourceCatalog`. O importador não chama o otimizador e não confirma a factibilidade de uma carteira; isso cabe aos planejadores e ao verificador sobre o plano exportado.

Erros impedem a liberação de rede/catálogo para planejamento; os IDs dos registros e das ordens continuam rastreáveis no diagnóstico. Nenhuma obrigatória pode desaparecer por descarte de linha ou código de status desconhecido.

## Manifesto e arquivos

Um `manifest.json` declara `schema_version: kgp.ingest.v1`, `decision_day`, metadados `network`, parâmetros `catalog`, `units`, `provenance` e `tables`. As oito tabelas são `nodes`, `branches`, `switches`, `assets`, `consumers`, `isolation_zones`, `services`, `crews`. Estoque e horizonte ficam em `catalog`. Uma tabela vazia precisa ser representada explicitamente, respeitando as condições de um estudo válido.

Cada descritor tem `path` relativo, `format` (`csv`, `json` ou `geojson`) e, opcionalmente, `field_map`. O mapeamento tem direção **campo canônico → coluna da origem**. Os arquivos devem ficar dentro da pasta do manifesto, inclusive após resolver links. Metadados de licença, proveniência e validade não equivalem a autorizações inventadas; o fornecedor precisa disponibilizar a amostra/autorização real para a PoC.

- CSV: UTF-8 ou UTF-8 BOM, cabeçalhos únicos. Arrays e mapas dentro de células são JSON; números/booleanos também seguem os tipos explícitos esperados. O limite de tamanho de campo é aplicado com diagnóstico estruturado.
- JSON: vetor de registros; leitura estrita, sem chaves duplicadas ou não finitos. Até 512 objetos/vetores aninhados; profundidade maior produz `JSON_DEPTH_EXCEEDED` antes da decodificação. O limite também vale para manifesto, propriedades GeoJSON, células JSON do CSV e plano exportado; delimitadores em strings não contam como estrutura.
- GeoJSON: FeatureCollection com relações elétricas explícitas em `properties`. Geometrias são preservadas na origem, mas não criam nós/arestas por cruzamento ou proximidade.

O exemplo mínimo está em `tests/fixtures/ingestion/minimal/manifest.json`. O exemplo público completo está em `data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json`.

A leitura CSV é estrita: uma aspa não fechada ou um erro do parser bloqueia a tabela com `INVALID_FILE`, em vez de promover uma carteira possivelmente truncada. Multilinhas corretamente delimitadas continuam permitidas. O mapa efetivo resolve primeiro todos os aliases e identidades canônicas, incluindo campos opcionais; duas propriedades não podem consumir a mesma coluna. Colisões implícitas e explícitas geram `INVALID_FIELD_MAP`. Caminhos com ciclo de link simbólico produzem diagnóstico, sem liberar instância.

## Dimensões e dados críticos

`units` declara seis dimensões: `coordinates` (m/km), `length` (m/km), `power` (W/kW), `voltage` (V/kV), `impedance` (ohm), `duration` (min/h). Os valores dos campos, mesmo com sufixos canônicos como `_kw` ou `_hours`, estão nas unidades declaradas **antes** da conversão. A saída usa km/kW/kV/ohm/h; velocidade `speed_kmh` permanece explicitamente km/h. Datas relativas são dias inteiros, não horas ou timestamps convertidos automaticamente.

O sistema de coordenadas deve declarar uma base linear (`CARTESIAN_KM`, `CARTESIAN_M`, `PROJECTED` ou `PROJETADO`). Um código EPSG isolado não é presumido linear. O adaptador de origem transforma o CRS quando necessário e registra os CRS reais na proveniência; graus geográficos não são usados como distância.

Identificação, fases, ligações, fontes, condições em serviço, estados das chaves, parâmetros elétricos, demandas e tempos críticos exigem valores explícitos; não herdam valores sintéticos das dataclasses. Papéis dos campos adicionais devem ser mapeados conscientemente. Campos externos não mapeados geram aviso; códigos de enumeração desconhecidos bloqueiam em vez de serem adivinhados.

## Domínio v1

- Carteira normalizada com `scope_status` explícito e somente `ACTIVE`. Códigos corporativos, cancelamentos e exclusões precisam de extensão/mapeamento futuro aprovado; não se inventa uma enumeração de inativos.
- Fases A/B/C e fontes explícitas. Ao menos uma fonte ativa deve existir. Consumidor tem uma fase no contrato atual; importação de UC multifásica exige uma evolução com critério de contagem acordado.
- Uma aresta conecta nós do mesmo nível nominal (tolerância relativa `1e-9` para ruído de representação). Mudança de tensão sem modelo de conversor é bloqueada. O exemplo público inclui somente MV; o regulador passante não certifica regulação ou tap.
- Conexões são identificadores. Arestas paralelas e nós sem alimentação permanecem no cadastro; não são colapsados para facilitar os resultados.
- Cadastro e conectividade admitem multigrafos. A análise física radial exige, no estado inicial e nos estados avaliados, árvore energizada por fase e exatamente uma fonte ativa por componente. Ciclos, paralelas condutoras e múltiplas fontes conectadas ficam fora do domínio e bloqueiam o planejamento/certificado. Componentes desenergizados e fontes em componentes ou fases distintas não criam esse impedimento. Preservar um grafo não equivale a validar seu fluxo.
- Tempo operacional respeita `0.0001 h` e o domínio já auditado: DG ilhada e catálogo diário contínuo 24h sem recomposição positiva bloqueados.
- Deslocamento usa a razão distância euclidiana/velocidade antes do arredondamento para `0.0001 h`; distância pequena não é zerada antecipadamente. Duração derivada não representável não cabe em um turno finito e impede certificação, com relatório de inviabilidade serializável no verificador. Isso não valida velocidades irreais como premissa operacional.
- Números decimais são normalizados para floats binários finitos, com arredondamento usual nessa precisão; o contrato não oferece precisão decimal arbitrária. Subnormais representáveis, como `5e-324`, são preservados. Literais não zero que virariam zero, como `1e-400` ou `-1e-400`, são bloqueados na leitura JSON, inclusive em células CSV e propriedades GeoJSON. Zero verdadeiro, mesmo com sinal ou expoente, continua válido onde o campo permite zero.
- Conversões de unidades também bloqueiam valor não zero cujo produto viraria zero, com diagnóstico de campo/linha. Os saldos exatos de materiais usam os valores decimais canônicos dos floats já admitidos; não recuperam algarismos além da precisão da entrada normalizada. A rejeição evita apagar um requisito antes de planejar.

Cada erro tem código, severidade, tabela, linha de dado (iniciando em 1), ID quando disponível e mensagem. O diagnóstico inclui hashes dos arquivos lidos e decodificados com sucesso, unidades, mapeamento, contagens e IDs. Um lote estruturalmente válido ainda pode produzir uma carteira operacionalmente inviável; o verificador comum continua obrigatório.

## Tempos agregados de manobra

`IsolationZoneSpec.prep_duration_hours` e `recomp_duration_hours` são as durações autoritativas usadas na agenda e no recálculo. São agregados do cenário que precisam de validação externa antes de representar uma intervenção real. `GridSwitch.maneuver_duration_hours` é informativo neste contrato: não gera nem impõe a sequência de campo. Assim, alterar só esse campo não alonga uma janela. O demonstrador não certifica duração individual, quantidade de operadores, operações simultâneas ou liberação de trabalho; a PoC deve validar os agregados e a sequência com a engenharia.

## Reprodução

```text
python scripts/import_kgp_bundle.py tests/fixtures/ingestion/minimal/manifest.json --report quality.json
python scripts/prepare_gis_reference.py
python scripts/import_kgp_bundle.py data/gis_reference/eflul_2025_ctmt002/bundle/manifest.json --report quality-public.json
python scripts/validate_gis_ingestion.py
```

A CLI escreve diagnóstico JSON e retorna 0 quando a entrada é válida, 1 no bloqueio. Não altera bases corporativas ou faz telecomandos.

## GIS de referência e trabalho da PoC

O recorte oficial ANEEL/EFLUL preserva a conectividade por PAC, fases, arestas e geometria publicada. O overlay do ensaio identifica separadamente consumidores, cargas, carteira, equipes, materiais, parâmetros ausentes, roteiro e posições sintéticas de agenda. A fonte contém 81 PACs com posições conflitantes; suas coordenadas servem apenas para exibição/QC. Não há apuração de deslocamento, benefício financeiro ou operação real.

Na PoC, o M1 deve entregar: recorte e interligações acordados, mapa de campos e unidades, vínculo entre ordens e ativos, estados e roteiros validados, diagnóstico, snapshot e grafo confrontados com casos de referência da engenharia. O adaptador corporativo dependerá da amostra e das interfaces autorizadas. Histórico temporal insuficiente implica avaliação estática/sintética identificada, sem reconstrução contrafactual presumida.

O esquema permite começar o desenvolvimento e testar a integração agora; não anuncia um leitor universal de qualquer GIS, uma API Cemig pronta ou fluxo AC completo. A formulação e os limites estão em `MODELO_MATEMATICO_KGP.md`.

Na REV06, a topologia pública CTMT002 continua integralmente importada (354 PACs/358 arestas), mas seus caminhos energizados com ciclos ficam bloqueados para o modelo físico. As nove verificações de alcançabilidade continuam válidas. Os quatro planos aprovados no modelo anterior são registros históricos; não comprovam admissibilidade física do multigrafo e não são reutilizados como benefício atual.
