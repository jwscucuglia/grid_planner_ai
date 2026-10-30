# Ingestão controlada GIS → KGP — incremento sobre REV04-P1

O usuário autorizou a abordagem de importação CSV/JSON discutida na revisão, sua integração ao núcleo e um ensaio com uma base GIS pública real. A Kaptus dispõe somente da documentação do desafio e da rede sintética; ainda não recebeu uma exportação da Cemig.

## Decisão e alternativas

Adotar arquivos com manifesto, mapeamento explícito de campos, unidades, validação e saída nos contratos internos existentes. JSON e CSV são a interface neutra; GeoJSON pode ser lido como registros com propriedades topológicas explícitas. Um adaptador de referência converterá um recorte de GIS público para essa interface. API corporativa depende de amostra/autorização e não é necessária para este incremento. Inferir conexões somente por proximidade não atende à correção elétrica exigida.

## Contrato do incremento

- `src/kgp/ingestion.py`: `import_bundle(manifest_path: str | Path) -> IngestionResult`.
- `IngestionResult`: `network: GridNetwork | None`, `services: tuple[ServiceOrder, ...]`, `catalog: ResourceCatalog | None`, `issues: tuple[IngestionIssue, ...]`, `provenance: dict`, propriedade `is_valid`.
- `IngestionIssue`: `code`, `table`, `row` (linha de dado, iniciando em 1), `record_id`, `message`, `severity` (`ERROR`/`WARNING`).
- Manifesto `schema_version='kgp.ingest.v1'`, metadados `network`, `catalog`, `decision_day`, `units`, `provenance`, `tables`.
- `tables`: nós (`nodes`), trechos (`branches`), chaves (`switches`), ativos (`assets`), consumidores (`consumers`), zonas (`isolation_zones`), serviços (`services`) e equipes (`crews`). Cada descritor contém `path`, `format` (`csv`, `json`, `geojson`) e `field_map` opcional, do nome canônico para o campo de origem. JSON é vetor de registros; GeoJSON é FeatureCollection com relações de conexão nos `properties`.
- Estoque e horizonte ficam em `catalog`; os metadados usam nomes dos contratos existentes. CSV codificado UTF-8/UTF-8 BOM; coleções em campos usam JSON. Os nomes de enumerações permanecem explícitos, sem presumir o significado de códigos externos.
- As unidades de coordenadas, comprimento, potência, tensão, impedância e duração devem ser declaradas. Conversões simples m/km, W/kW, V/kV e min/h são admitidas; graus geográficos não são distância linear. Transformação de CRS pertence ao adaptador GIS documentado, nunca ao cálculo de conectividade por proximidade.
- Campos críticos não podem herdar silenciosamente os padrões sintéticos das dataclasses: identificação, fases, conexões, fontes/estados, condições em serviço, parâmetros elétricos, demandas e durações aplicáveis precisam de valores explícitos. O adaptador pode produzir premissas de ensaio sintéticas claramente identificadas no manifesto e nos relatórios.
- Nenhuma linha inválida é descartada para produzir uma instância supostamente válida. Se houver erro, `is_valid=False` e nenhum trio completo de entradas será liberado para planejamento. Todas as ordens obrigatórias continuam rastreáveis.
- Caminhos contidos na pasta do manifesto; referências fora dela, JSON duplicado/não finito, IDs duplicados, referências órfãs, fases incompatíveis, unidades ambíguas e valores fora da resolução temporal são erros. Valores de coordenadas duplicadas não criam conectividade.
- Validar os contratos e referências existentes, sem modificar o significado da programação/otimização nem enfraquecer o verificador.
- O exemplo público fornece topologia/geometria real. Quando cargas, consumidores, recursos, chaves operacionais ou roteiro não estiverem no recorte, qualquer complemento será um overlay sintético explícito. Não representa modelo operacional da distribuidora, dados Cemig ou benefício real.
- O ensaio usa B0/B1/B2/KGP e o mesmo `feasibility.py`. Não exige que KGP ganhe; carteira obrigatória e factibilidade são condições de aceitação.

### Domínio explícito da importação v1

`scope_status` é obrigatório em cada serviço e seu único valor suportado é `ACTIVE`, como definido no núcleo existente. Códigos externos (`ATIVO`, por exemplo), inativos ou cancelados não possuem enumeração canônica neste incremento e bloqueiam a importação. O importador não converte esses valores nem permite que uma ordem obrigatória desapareça da carteira por status desconhecido. Uma futura representação de ordens inativas exige extensão explícita de contrato, sem remover ordens obrigatórias.

Trechos elétricos somente conectam barras do mesmo nível nominal de tensão. A comparação admite tolerância relativa de `1e-9`, sem tolerância absoluta, para ruído de representação numérica. Uma troca física de nível exige modelo explícito de transformador/conversor, que este incremento não suporta, inclusive entre alimentadores com identificadores distintos. Alimentadores sem trecho de interligação podem declarar níveis distintos. O recorte público atual representa a topologia de média tensão; não constitui simulação de transformação MT/BT.

O leitor CSV respeita `csv.field_size_limit()` da biblioteca padrão (131072 caracteres por campo no ambiente padrão), registra o limite efetivo na proveniência e converte erros do parser em diagnóstico estruturado `INVALID_FILE`. Campos no limite são aceitos; campos que excedem o limite bloqueiam a entrada com `is_valid=False`, sem exceção não tratada e sem liberar rede/catálogo para planejamento. O importador não altera a configuração global do parser.

## Proveniência e avaliação

Preservar SHA-256, fonte primária, licença, data e procedimento do recorte. Registrar origem, unidades, mapeamento, contagens, problemas e premissas. Manter os 130 testes e os resultados/arquivos REV04 como evidência histórica; novas verificações são adicionais e não transformam o estudo conhecido em avaliação cega. O núcleo e os 28 arquivos congelados não serão alterados para acomodar a importação.

## Documentação e apresentação

Nos mesmos IDs do Drive, esclarecer em ZING Q1/Q6, proposta e plano técnico o fluxo de ingestão, dados da PoC, transformação por identificadores/terminais, geração de agendas e MILP. Atualizar somente slide 3/notas para entrada → método → saída e decisão humana. Manter 10 slides, layout, cifras, resultados históricos, orçamento e campos empresariais.
