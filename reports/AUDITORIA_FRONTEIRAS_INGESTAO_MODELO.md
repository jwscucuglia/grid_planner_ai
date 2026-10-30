# Revisão adversarial das fronteiras de ingestão e do modelo físico

Data: 29/10/2026. Base anterior reproduzida: `f49a8c6543717ff740f1f7c701e20d34e82e6e5a`. Código corrigido reavaliado no working tree, identificado pelos hashes dos módulos nos logs públicos.

Os relatos e scripts técnicos externos foram lidos antes da reprodução. Esta frente produziu runners e oráculos próprios, sem modificar implementação, dados da rede, carteiras históricas ou commits. Não utilizou código ou resultados do D06. É revisão por agente de IA; não representa homologação de engenharia, validação em campo ou auditoria por terceiro humano.

## Resultado

Foram confirmadas a perda de uma obrigação em CSV malformado, colisões implícitas de mapeamento e a insuficiência da aproximação radial para topologias energizadas com ciclos/paralelas ou múltiplas fontes na mesma fase. As correções passaram nas contraprovas independentes: **15/15 verificações de ingestão** e **81/81 verificações do domínio físico**. Um teste nativo de symlink não pôde ser executado por falta de privilégio no Windows; os dois caminhos de RuntimeError de resolução foram exercitados separadamente.

Não restou discrepância reproduzida aberta nesses casos após a correção. O uso de tempos agregados de zona foi esclarecido como premissa do modelo, sem prometer reconstrução de operações individuais de campo. A rede GIS foi preservada e continua útil como evidência de cadastro/conectividade; seu estado energizado com ciclos fica fora da análise física radial suportada.

Scripts, fixtures e logs RED/GREEN: [validation_scripts/banca_ingestion_model/](validation_scripts/banca_ingestion_model/README.md).

## 1. CSV malformado escondia uma obrigatória

O controle contém S1 e S2 obrigatórias. S2 tem alvos vazios e é intencionalmente impossível de programar; sua presença precisa resultar em carteira incompleta. Este caso não afirma que duas ordens programáveis se tornaram uma.

Com `title` na última coluna, uma aspa aberta antes do título de S1 e nunca fechada até o fim faz o leitor permissivo incorporar a linha física de S2 ao título de S1. A base f49 importava o arquivo com `is_valid=True`, nenhuma ocorrência de erro e apenas S1 na carteira/proveniência. B0, B1, B2 e KGP passavam pelo verificador com 1/1 obrigatória concluída. O CSV íntegro preservava 2 obrigatórias e os quatro métodos corretamente expunham a incompletude 1/2.

O leitor agora usa modo CSV estrito. O mesmo arquivo recebe `INVALID_FILE` e não libera rede/catálogo para planejamento. Um título multilinha legítimo, com aspas escapadas, continua preservando as duas ordens. O critério é sintaxe CSV válida; não se contam linhas físicas como se fossem registros.

## 2. Mapa efetivo precisa ser injetivo

Na base f49, remover a coluna `in_service` e declarar `field_map={"in_service":"is_source"}` fazia dois campos canônicos consumirem a mesma coluna: o alias explícito e o fallback de identidade de `is_source`. N1 passava silenciosamente a fora de serviço, deixando de estar alimentado. Um caso análogo `{"x":"y"}` com x ausente transformava a coordenada (1,0) em (0,0). Explicitar os dois aliases iguais já era rejeitado, evidenciando a lacuna na resolução dos fallbacks.

A correção verifica a unicidade do mapa completo canônico→origem, depois dos fallbacks. Ambos os casos implícitos agora são rejeitados com `INVALID_FIELD_MAP`. Renomear `in_service` para uma coluna `STATE` independente continua válido. Uma troca de eixos explicitamente mapeada em ambas as direções também continua válida; o programa não tenta adivinhar o significado empresarial de um mapa injetivo.

## 3. Ciclo de symlink precisa permanecer um erro estruturado

Na base f49, RuntimeError de `Path.resolve` escapava para o chamador tanto no caminho do manifesto quanto no de uma tabela. A correção converte essa condição localmente em `SYMLINK_LOOP`, mantendo a rede e o catálogo indisponíveis. A captura foi revisada no limite de resolução de caminho; não é um `except RuntimeError` genérico em torno de toda a importação.

A criação real de um symlink autorreferente foi negada neste Windows com WinError 1314. Essa execução nativa está registrada como **skip**, sem transformá-la em sucesso. Para conferir o tratamento do erro, a reprodução substituiu apenas `Path.resolve` nos dois pontos com a mesma classe de exceção e verificou o diagnóstico estruturado. Isso não substitui uma repetição nativa em ambiente que permita criar o link.

## 4. Domínio físico: multigrafo por fase, com uma fonte por componente

O testemunho adiciona a uma carga energizada dois trechos em paralelo: resistência 1 ohm/capacidade 1000 kW e resistência 0,01 ohm/capacidade 1 kW. O alvo do serviço permanece em outro trecho, corretamente isolado, evitando confundir domínio de fluxo com isolamento do trabalho.

Na base f49, os mesmos registros em ordem alta-capacidade primeiro eram aceitos pelo cálculo do planejador e do verificador, com carregamento máximo 10%. A ordem inversa era rejeitada com 10000%. O BFS escolhia a primeira aresta como árvore de fluxo, deixando a outra sem fluxo atribuído. A concordância entre as duas implementações não demonstrava validade física dessa escolha. Os quatro métodos aceitavam o primeiro caso; KGP também publicava certificado no catálogo gerado.

Um triângulo energizado e uma árvore com duas fontes conectadas na mesma fase também eram aceitos. A segunda situação tem rank de ciclo zero, mostrando por que detectar somente ciclos é insuficiente: não há modelo validado de partilha entre fontes nessa aproximação.

O critério aplicado e reavaliado é:

1. Formar separadamente os estados efetivos das fases A/B/C, respeitando fases dos terminais, equipamentos em serviço e estados das chaves.
2. Manter cada `branch_id` como aresta distinta. Duas paralelas contam duas arestas; um self-loop conta como ciclo. Não se colapsam registros.
3. Para cada componente conectado que contenha fonte ativa, exigir `E = V - 1` e exatamente uma fonte naquela fase. Componentes sem fonte ficam fora do cálculo físico de carga/tensão.
4. Aplicar a fronteira no estado inicial e nos estados efetivos de intervenção; não basta avaliar uma seleção downstream estática.

O oráculo da revisão percorre os registros brutos e conta V/E por componente/fase. O planejador usa BFS e contagem de arestas; o verificador implementa uma verificação Union-Find separada, sem chamar o guard do planejador. O diff foi revisado, e a chamada existe no estado inicial, no cálculo físico das etapas e em snapshots sem zona.

Os quatro motores agora rejeitam os estados iniciais fora desse domínio, sem certificado KGP. Não se afirma que toda rede multifonte ou malhada seja fisicamente inviável; o modelo entregue não resolve esses estados. Quando o fechamento de uma tie criaria malha durante uma intervenção inicialmente radial, a transferência obrigatória é rejeitada; a opcional é abandonada e o plano só é aceito com a tie aberta em todas as etapas executadas.

Controles aceitos: árvore radial; paralela fora de serviço; ciclo sem fonte e sem consumidores inicialmente interrompidos; fontes em componentes separados; fontes em fases diferentes; paralelas que pertencem a fases distintas. A árvore radial de capacidade insuficiente continua rejeitada por sobrecarga, preservando a restrição física dentro do domínio suportado. Self-loop já é rejeitado pelo contrato estrutural de trechos; o oráculo também o identifica como ciclo.

## 5. Significado do ensaio GIS após a fronteira

O ensaio preserva **354 nós e 358 trechos**. O oráculo independente encontra, nos componentes conectados à fonte:

| Fase | V | E | Rank `E−V+1` |
|---|---:|---:|---:|
| A | 344 | 348 | 5 |
| B | 338 | 342 | 5 |
| C | 338 | 342 | 5 |

Os hashes de nós, trechos e chaves são iguais nas reproduções anterior e posterior desta revisão. Não foram retiradas paralelas, alteradas conexões ou escolhida uma árvore artificial para obter uma carteira factível.

A afirmação defensável é preservação cadastral e conectividade por fase de um multigrafo GIS público. O estado energizado está fora do domínio físico radial: os quatro métodos devem ser classificados como entrada não suportada, sem plano factível ou benefício operacional certificado desse ensaio. Os planos anteriores permanecem históricos sob suas hipóteses anteriores. Esta frente não repetiu o ensaio GIS completo nem a rodada de 48 planos; a avaliação sintética radial e suas novas execuções são evidências separadas.

## 6. Dois tempos diferentes: descrição por chave e agregado de zona

Os quatro métodos mantêm o mesmo comportamento observado na base f49: uma chave descrita com 2 h não altera a agenda se a zona tem preparação/recomposição de 0,1 h. A janela continua 8h–8,1h, trabalho até 9,1h e fim 9,2h. Alterando os agregados da zona para 2 h, a janela vira 8h–10h, trabalho até 11h e fim 13h.

Foi adotado o contrato agregado já implementado: tempos de zona são entradas autoritativas previamente validadas no cenário; duração individual da chave é informativa. Os docstrings de `GridSwitch` e `IsolationZoneSpec` agora explicitam isso. A revisão não deduziu mínimo por soma ou máximo das chaves, pois isso exigiria hipóteses adicionais de sequência, operadores e concorrência.

Não se infere que o agregado de 0,1 h seja executável em campo quando uma descrição individual indica 2 h. Validação operacional dos agregados e das operações individuais permanece responsabilidade externa ao demonstrador. A contagem de duas manobras físicas também não prova uma duração de campo.

## Evidências e limites

- [RED da importação na base f49](validation_scripts/banca_ingestion_model/baseline_RED_f49_import.json): 19 sondagens, 9 falhas esperadas na reprodução, 10 controles satisfeitos.
- [GREEN da importação](validation_scripts/banca_ingestion_model/final_GREEN_import.json): 15/15; um skip nativo de symlink. A diferença de quatro sondagens ocorre porque o CSV rejeitado não chega mais aos planejadores.
- [RED do domínio físico](validation_scripts/banca_ingestion_model/baseline_RED_f49_model.json): 17 casos/81 sondagens, 25 falhas e 56 controles satisfeitos.
- [GREEN do domínio físico](validation_scripts/banca_ingestion_model/final_GREEN_model.json): 17 casos/81 sondagens passaram, incluindo os dois casos de fechamento de tie que criaria malha.

As três baterias públicas da revisão anterior também foram reexecutadas **após** a estabilização conjunta dos guards físicos e da ingestão, sem substituir seus logs históricos 0.5:

- [Matriz de regressão REV06](validation_scripts/banca_ingestion_model/regression_matrix_REV06.json): **58/58**.
- [Materiais exatos REV06](validation_scripts/banca_ingestion_model/regression_materials_REV06.json): **105/105**.
- [Fronteira numérica REV06](validation_scripts/banca_ingestion_model/regression_underflow_REV06.json): **143/143**.

A [conferência de identidade de fonte](validation_scripts/banca_ingestion_model/verification_summary_REV06.json) compara os hashes registrados nos cinco logs GREEN/regressão com todos os módulos atuais. Eles coincidiram. Entre os módulos relevantes: `ingestion.py` = `3f11f9a0…b86aca16`, `topology.py` = `b08ec30b…5671cd3`, `feasibility.py` = `2c2672ab…364416` e `contracts.py` = `a86d9775…28381cc`; os hashes completos constam da conferência. Os arquivos em `banca_rev05` permanecem evidência histórica de sua própria fonte, não são reetiquetados como REV06.

Não foi executado fullpytest ou full48 por esta frente. As sondagens são pequenas e dirigidas; não estimam taxa de erro nem demonstram correção universal, desempenho em grande escala, fluxo AC, proteção, validade dos parâmetros sintéticos ou segurança de campo. Os resultados públicos contêm hashes da fonte e dados técnicos sintéticos, sem material financeiro ou corporativo privado.
