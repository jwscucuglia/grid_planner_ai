# Modelos elétrico e de programação do KAPTUS GRID PLANNER

O demonstrador auditado REV04 usa uma rede sintética e agendas diárias candidatas. INGESTAO01 acrescenta leitura controlada de arquivos e um exemplo em topologia pública real com overlay sintético. Não transforma o estudo conhecido em validação operacional ou avaliação cega inédita. Os modelos abaixo distinguem funções implementadas e evolução a validar na PoC.

## 1. Do cadastro à conectividade

Os identificadores dos terminais e trechos determinam as arestas. Coordenadas não conectam circuitos por proximidade. Cada registro mantém origem, unidade, versão e vínculo ao instante representado; a PoC precisa também do instante em que a informação estava disponível ao decisor.

Para fase p e etapa s, G(p,s)=(V(p),E(p,s)) contém somente equipamentos em serviço, fases compatíveis e conexões condutoras segundo o estado das chaves. O nó v tem alimentação na fase p quando existe caminho a uma fonte ativa. O planejador calcula essa alcançabilidade por BFS; a auditoria reconstrói componentes por Union-Find a partir do plano exportado. As implementações distintas compartilham dados e hipóteses físicas.

O demonstrador exige conservadoramente ausência de fases alimentadas nos ativos de trabalho e, para ativos de trecho, em ambas as extremidades. Consumidores são associados à fase da ligação representada. Ligações reais multifásicas exigem representação e critério de interrupção acordados com a Cemig; o contrato atual de consumidor tem uma fase por registro. Não se deve duplicar uma UC por fase e somar clientes-hora como se fossem consumidores distintos.

O cenário aplica isolamento, eventual transferência, execução e recomposição. Horários e durações vêm da programação e dos parâmetros explícitos da intervenção. Todos os estágios de duração positiva devem ser admissíveis. Zonas e sequências são previamente validadas no recorte inicial; não há geração automática irrestrita de roteiros de campo.

## 2. Verificação elétrica e limites físicos

O código representa uma aproximação radial de carga e queda de tensão, com hipóteses documentadas; não resolve fluxo AC multifásico completo, proteção, aterramento ou liberação de trabalho. Ilhamento DG é bloqueado. O exemplo BDGD preserva arestas paralelas e conexões declaradas, mas não certifica o comportamento elétrico real desse alimentador.

Uma evolução de fluxo nodal poderá usar I=Y(s)V, combinada com relações de potência/carga, fontes e modelos dos equipamentos. Essa relação isolada não basta para resolver o problema. Modelo, parâmetros, ferramenta e critérios de aceite serão validados com a engenharia; OpenDSS é uma opção estudada, não uma integração entregue neste incremento.

## 3. Construção das alternativas de programação

Serviços conhecidos e liberados são cruzados com as zonas, prazos, materiais, precedências e recursos. O gerador enumera pacotes e constrói sequências diárias com intervalos de cada equipe, incluindo deslocamento, preparação, execução e recomposição. Cada coluna k representa uma agenda completa em um dia d(k), contendo serviços S(k), janelas, equipes, horários e consumo de materiais.

A expansão construtiva tem limites: até 5 serviços por pacote, até 5 janelas por dia e até 120 estados por profundidade, além de escolhas limitadas de horários e alocação. O catálogo também recebe agendas diárias factíveis das sementes B0/B1/B2; essas agendas adicionais não passam por todos os filtros de tamanho da expansão. O certificado refere-se ao conjunto efetivamente gerado, incluindo sementes. Não se anuncia um algoritmo de geração de colunas com pricing nem ótimo sobre todas as agendas contínuas.

## 4. MILP de seleção de agendas

Variáveis:

- y(k) binária: seleciona a agenda diária k.
- o(i) binária: omite serviço opcional i. Para obrigatórias, o(i)=0.
- r(g) binária: variável de custo de reincidência do grupo g. Grupos reúnem consumidores de mesmo nó/fase, que têm os mesmos intervalos nesse modelo.

Cobertura de cada serviço conhecido i:

`soma(k: i pertence a S(k)) y(k) + o(i) = 1`.

Para cada dia d:

`soma(k: d(k)=d) y(k) <= 1`.

Para cada material a, com uso m(a,k) e estoque M(a):

`soma(k) m(a,k)*y(k) <= M(a)`.

Uma sucessora selecionada exige seu predecessor selecionado. Se ambos estão na mesma coluna, o gerador verifica a sequência de execução. Quando estão em dias distintos, as restrições do MILP preservam a ordem dos dias. O verificador repete essa conferência sobre os horários exportados.

O número de interrupções N(g) é a soma das contagens diárias selecionadas. Para um limite U(g), a restrição implementada `N(g) <= 1+(U(g)-1)*r(g)` obriga r(g)=1 se houver duas ou mais interrupções. Com peso positivo na função objetivo, uma solução de custo mínimo não paga por r(g)=1 quando N(g)<=1. Se o peso é zero, o valor interno pode ser indiferente; o contador publicado sempre é recalculado do plano. A representação depende de recomposição positiva entre dias; catálogo contínuo 24h é explicitamente bloqueado.

Objetivo:

`min soma(k) c(k)*y(k) + w(R)*soma(g) n(g)*r(g) + p(O)*soma(i opcional) o(i)`.

O custo c(k) combina janelas, clientes-hora, manobras físicas e horas de equipes incluindo deslocamento. Clientes-hora são calculados pela duração da união dos intervalos sem alimentação de cada consumidor, sem duplicidade. Reincidência é separada de quantidade de janelas e de manobras.

Os pesos são prioridades explícitas de planejamento, não preços nem conversão automática de compensações regulatórias. Sua definição será acordada e congelada antes da avaliação reservada. Carteira obrigatória é uma restrição rígida; não pode ser comprada por penalidade finita.

SciPy/HiGHS resolve a seleção. Ótimo só é informado quando status, bound, gap e objetivo reconstruído são compatíveis e o plano passou pela leitura JSON estrita e pelo verificador. Caso contrário, é relatada solução factível com os limites conhecidos, ou a falha correspondente. O certificado vale somente nas colunas geradas.

## 5. Aceite da ingestão e da PoC

O importador gera GridNetwork, ServiceOrder e ResourceCatalog, mais diagnóstico e proveniência. Erros críticos não liberam instância para planejar e não eliminam ordens obrigatórias silenciosamente. A engenharia precisa conferir contagens, ligações, fases, fontes, estados e casos conhecidos antes de comparar benefícios. Sem histórico temporal suficiente, o resultado será rotulado estático/sintético.

O diferencial proposto está na combinação de conectividade pós-manobra, carteira heterogênea, programação temporal de equipes e impacto individual, com comparação competente e recálculo independente. BFS, Union-Find, grafos e MILP são técnicas estabelecidas. A contribuição e a utilidade serão avaliadas no conjunto, sem alegação de novidade isolada desses métodos.

Referências de implementação: `src/kgp/topology.py`, `eligibility.py`, `scheduling.py`, `optimizer.py`, `feasibility.py`, `metrics.py` e `ingestion.py`. As equações resumem a implementação e seus limites; a evolução na Cemig depende do contrato de dados e da validação física/operacional própria.
