# RELATÓRIO TÉCNICO DE EXECUÇÃO — KAPTUS GRID PLANNER (KGP v0.2.0)
## Desafio 07 Inova Cemig.Lab Ciclo 5 | Demonstrador Pré-Inscrição Auditado

- **Data de execução (UTC):** `2026-10-28T21:37:34.691768+00:00`
- **Versão do pacote:** `0.2.0`
- **Ambiente Python:** `3.11.0` (`Windows-10-10.0.26300-SP0`)
- **Repositório:** `grid_planner_ai` (100% independente do Desafio 06 / GTA)
- **Modo de operação:** *Sidecar / Out-of-Band* sobre rede de referência multi-alimentador (13,8 kV, 3 alimentadores, 15 barras, 9 chaves NF/NA, 14 ativos em barras e trechos, 198 consumidores) e carteira sintética documentada.
- **Enquadramento TRL:** Suporte experimental de funções críticas em ambiente analítico/laboratorial de referência (**TRL 3**), sem alegar desempenho operacional em dados produtivos da Cemig.

---

## 1. Síntese Executiva e Verificação de Hipóteses

Todos os planos gerados pelos quatro métodos (`B0`, `B1`, `B2` e `KGP`) nas **4 instâncias de desenvolvimento (`DEV`)** e **8 instâncias retidas de avaliação cega (`EVAL`)** foram submetidos a round-trip de serialização JSON e auditados pelo verificador independente (`src/kgp/feasibility.py`), que reconstrói de forma autônoma a conectividade elétrica e confronta todos os campos exportados, cumprindo **100% da carteira obrigatória conhecida na data da decisão** com **zero violações** elétricas, temporais, de equipes ou de materiais.

### Tabela 1 — Desempenho Agregado nos Cenários Retidos (`EVAL`, $N = 8$ instâncias independentes, IC 95% Bootstrap)

| Método | Planos Factíveis | Carteira Obrigatória | Janelas de Interrupção (Média [IC 95%]) | Manobras Físicas (Média [IC 95%]) | Taxa de Acoplamento (Média [IC 95%]) | Consumidores Reincidentes (Média [IC 95%]) | Clientes-Hora União (Média [IC 95%]) | Tempo Médio (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B0** | 8/8 | 100.0% | 3.88 [3.38, 4.38] | 10.25 [8.74, 11.75] | 0.0% [0.0%, 0.0%] | 37.75 [20.23, 57.76] | 318.71 [239.15, 394.40] | 0.0389s |
| **B1** | 8/8 | 100.0% | 3.25 [2.75, 3.62] | 8.50 [7.25, 9.75] | 20.8% [9.4%, 32.3%] | 35.00 [18.50, 53.50] | 280.74 [212.42, 347.10] | 0.0345s |
| **B2** | 8/8 | 100.0% | 2.50 [1.88, 3.12] | 7.50 [6.50, 8.26] | 49.0% [27.1%, 71.9%] | 13.00 [2.00, 31.00] | 256.89 [196.41, 316.79] | 0.0352s |
| **KGP** | 8/8 | 100.0% | 2.12 [1.62, 2.62] | 6.25 [4.75, 7.75] | 58.3% [33.3%, 81.2%] | 0.00 [0.00, 0.00] | 212.72 [169.65, 254.35] | 3.3000s |

### Tabela 1B — Balanço Pareado `KGP` vs. `B2` por Métrica Individual (`EVAL`, $N = 8$)

| Métrica Avaliada | Vitórias `KGP` (< `B2`) | Empates (`KGP` = `B2`) | Perdas `KGP` (> `B2`) | Observação de Trade-Off |
| :--- | :---: | :---: | :---: | :--- |
| **Janelas de Interrupção** | 3 | 5 | 0 | Zero perdas em frequência de desligamentos |
| **Clientes-Hora (União)** | 6 | 1 | 1 | Em `EVAL_03`, `KGP` aguarda o dia 2 para reduzir 1 janela e zerar 16 reincidentes, com trade-off de +33,55 ch |
| **Consumidores Reincidentes** | 3 | 5 | 0 | Elimina toda a reincidência (0,00 vs. 13.00 média em `B2`) |
| **Objetivo Combinado ($J$)** | 7 | 1 | 0 | `KGP` vence ou empata em 100% das instâncias na função objetivo ponderada |

### Avaliação das Hipóteses Científicas
- **H1 (Conectividade pós-manobra vs. seleção geográfica):** Confirmada. O raio de `B1` calibrado nas sementes `DEV` (`0.75 km`) agrupa apenas serviços geograficamente próximos; em cenários onde ativos distantes (`1,9 km > raio B1 = 0.75 km`) pertencem à mesma zona isolada após manobra de transferência por chave NA (`SW_TIE_12`), e ativos próximos (`0,20 km`) pertencem a alimentadores distintos (`F1` vs. `F2`), `B1` não captura o acoplamento distante e tem o agrupamento inter-alimentador barrado pelo verificador elétrico, enquanto `B2` e `KGP` identificam a compatibilidade real pós-manobra.
- **H2 (Otimização conjunta vs. heurística topológica gulosa `B2`):** Confirmada com registro transparente de ganhos, empates e trade-offs por métrica (Tabela 1B). Quando há múltiplas datas elegíveis com disponibilidade distinta de equipes ou escolha entre acoplar serviço longo em zona tronco vs. zona distal, o otimizador `KGP` reduz janelas (3 vitórias, 5 empates, 0 perdas) e clientes-hora (6 vitórias, 1 empate, 1 perda em `EVAL_03` onde prioriza eliminar 1 janela e 16 consumidores reincidentes).
- **H3 (Factibilidade sob limites de equipes, precedências e variação de duração):** Confirmada. A duração de cada janela emerge da sequência real de execução das equipes (paralela quando há múltiplas equipes habilitadas disponíveis; sequencial quando há compartilhamento de equipe, precedência ou restrição de não-simultaneidade).

---

## 2. Resultados Pareados por Cenário Retido (`EVAL`)

| Cenário (`EVAL`) | Janelas (`B0 / B1 / B2 / KGP`) | Clientes-Hora (`B0 / B1 / B2 / KGP`) | Reincidentes (`B0 / B1 / B2 / KGP`) | Diferença `KGP - B2` (Janelas / Clientes-Hora) | Status Solver `KGP` (Gap) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `EVAL_01_TOPO_DISTANT_AND_PARALLEL` | 4 / 4 / 2 / **2** | 429.9 / 429.9 / 253.0 / **243.2** | 82 / 82 / 0 / **0** | +0 jan / -9.80 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_02_TIE_TRANSFER_AND_CREW_SYNC` | 4 / 3 / 2 / **2** | 486.7 / 299.5 / 256.1 / **167.8** | 72 / 50 / 0 / **0** | +0 jan / -88.32 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_03_PRECEDENCE_AND_RELEASE_DATES` | 4 / 3 / 3 / **2** | 182.8 / 147.6 / 147.6 / **181.2** | 16 / 16 / 16 / **0** | -1 jan / +33.55 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_04_FREQ_VS_DURATION_TRADEOFF` | 3 / 3 / 2 / **1** | 285.9 / 285.9 / 382.1 / **265.7** | 50 / 50 / 72 / **0** | -1 jan / -116.41 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_05_DISJOINT_NO_OPPORTUNITY` | 3 / 3 / 3 / **3** | 144.0 / 144.0 / 144.0 / **144.0** | 0 / 0 / 0 / **0** | +0 jan / +0.00 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_06_LIFECYCLE_AND_TEMPORAL_FILTER` | 3 / 2 / 1 / **1** | 263.4 / 217.9 / 183.2 / **120.1** | 50 / 50 / 0 / **0** | +0 jan / -63.13 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_07_STOCHASTIC_PORTFOLIO` | 5 / 4 / 3 / **3** | 405.2 / 404.4 / 372.3 / **291.2** | 16 / 16 / 0 / **0** | +0 jan / -81.10 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |
| `EVAL_08_STOCHASTIC_PORTFOLIO` | 5 / 4 / 4 / **3** | 351.8 / 316.6 / 316.6 / **288.5** | 16 / 16 / 16 / **0** | -1 jan / -28.13 ch | `OPTIMAL_ON_DISCRETE_COLUMNS` (0.0000%) |

---

## 3. Distinção Metodológica e Limitações Declaradas

1. **Janelas de Interrupção vs. Manobras Físicas:** Cada janela de interrupção requer abertura e posterior fechamento de chaves seccionadoras NF e, quando há transferência de carga, fechamento e abertura de chave NA ($2 \times N_{\text{chaves}}$ operações físicas). O relatório contabiliza ambas separadamente.
2. **Clientes-Hora sem Duplicidade:** Calculados pela soma, por consumidor, da duração da união disjunta dos intervalos sem alimentação em todas as etapas da manobra.
3. **Escopo de Otimalidade (`OPTIMAL_ON_DISCRETE_COLUMNS`):** A otimalidade comprovada pelo solver MILP (HiGHS) refere-se ao espaço discreto de colunas candidatas factíveis geradas a partir das zonas de isolamento, dias, inícios de turno e pacotes elegíveis.
4. **Não Extrapolação Financeira Direta:** Os resultados em rede de referência não são convertidos por regra de três sobre o montante de R\$ 3,64 milhões do Anexo 2, pois a apuração regulatória de compensações DIC/FIC/DMIC exige histórico individual por unidade consumidora, limites mensais/trimestrais/anuais do PRODIST Módulo 8 e faturamento real.
5. **Restrições Operacionais de Campo Não Modeladas:** Seletividade fina de proteção em tempo real, instalação física de aterramento temporário, bloqueio mecânico (LOTO) e autorização final do COD permanecem sob responsabilidade operacional humana da Cemig.
