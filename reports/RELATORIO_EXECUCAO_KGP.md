# RELATÓRIO TÉCNICO DE EXECUÇÃO — KAPTUS GRID PLANNER (KGP)
## Desafio 07 Inova Cemig.Lab Ciclo 5 | Demonstrador Pré-Inscrição Auditado

- **Data de execução (UTC):** `2026-10-28T20:20:55.528653+00:00`
- **Ambiente Python:** `3.11.0` (`Windows-10-10.0.26300-SP0`)
- **Repositório:** `grid_planner_ai` (100% independente do Desafio 06 / GTA)
- **Modo de operação:** *Sidecar / Out-of-Band* sobre rede de referência multi-alimentador (13,8 kV, 3 alimentadores, 15 barras, 9 chaves NF/NA, 198 consumidores) e carteira sintética documentada.
- **Enquadramento TRL:** Suporte experimental de funções críticas em ambiente analítico/laboratorial de referência (**TRL 3**), sem alegar desempenho operacional em dados produtivos da Cemig.

---

## 1. Síntese Executiva e Verificação de Hipóteses

Todos os planos gerados pelos quatro métodos (`B0`, `B1`, `B2` e `KGP`) nas **4 instâncias de desenvolvimento (`DEV`)** e **8 instâncias retidas de avaliação cega (`EVAL`)** foram auditados pelo verificador independente (`src/kgp/feasibility.py`), cumprindo **100% da carteira obrigatória conhecida na data da decisão** com **zero violações** elétricas, temporais, de equipes ou de materiais.

### Tabela 1 — Desempenho Agregado nos Cenários Retidos (`EVAL`, $N = 8$ instâncias independentes, IC 95% Bootstrap)

| Método | Planos Factíveis | Carteira Obrigatória | Janelas de Interrupção (Média [IC 95%]) | Manobras Físicas (Média [IC 95%]) | Taxa de Acoplamento (Média [IC 95%]) | Consumidores Reincidentes (Média [IC 95%]) | Clientes-Hora União (Média [IC 95%]) | Tempo Médio (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B0** | 8/8 | 100.0% | 3.88 [3.38, 4.38] | 10.25 [8.74, 11.75] | 0.0% [0.0%, 0.0%] | 37.75 [20.23, 57.76] | 314.99 [237.22, 391.67] | 0.0301s |
| **B1** | 8/8 | 100.0% | 3.25 [2.75, 3.62] | 8.50 [7.25, 9.75] | 20.8% [9.4%, 32.3%] | 16.50 [4.00, 31.00] | 268.72 [208.09, 324.36] | 0.0253s |
| **B2** | 8/8 | 100.0% | 2.50 [1.88, 3.12] | 7.50 [6.50, 8.26] | 49.0% [27.1%, 71.9%] | 13.00 [2.00, 31.00] | 269.62 [202.71, 330.27] | 0.0241s |
| **KGP** | 8/8 | 100.0% | 2.12 [1.62, 2.62] | 6.25 [4.75, 7.75] | 58.3% [33.3%, 81.2%] | 0.00 [0.00, 0.00] | 211.53 [168.91, 252.91] | 0.3120s |

### Avaliação das Hipóteses Científicas
- **H1 (Conectividade pós-manobra vs. seleção geográfica):** Confirmada. Em cenários onde ativos distantes (`1,9 km > raio B1 = 1,25 km`) pertencem à mesma zona isolada após manobra de transferência por chave NA (`SW_TIE_12`), e ativos próximos (`0,20 km`) pertencem a alimentadores distintos (`F1` vs. `F2`), `B1` não captura o acoplamento distante e tem o agrupamento inter-alimentador barrado pelo verificador elétrico, enquanto `B2` e `KGP` identificam a compatibilidade real pós-manobra.
- **H2 (Otimização conjunta vs. heurística topológica gulosa `B2`):** Confirmada com registro transparente de ganhos e empates. Quando há múltiplas datas elegíveis com disponibilidade distinta de equipes ou escolha entre acoplar serviço longo em zona tronco vs. zona distal, o otimizador `KGP` reduz clientes-hora e/ou janelas em relação ao `B2`, mantendo empate exato nos cenários onde `B2` já atinge a solução ótima.
- **H3 (Factibilidade sob limites de equipes, precedências e variação de duração):** Confirmada. A duração de cada janela emerge da sequência real de execução das equipes (paralela quando há múltiplas equipes habilitadas disponíveis; sequencial quando há compartilhamento de equipe, precedência ou restrição de não-simultaneidade).

---

## 2. Resultados Pareados por Cenário Retido (`EVAL`)

| Cenário (`EVAL`) | Janelas (`B0 / B1 / B2 / KGP`) | Clientes-Hora (`B0 / B1 / B2 / KGP`) | Reincidentes (`B0 / B1 / B2 / KGP`) | Diferença `KGP - B2` (Janelas / Clientes-Hora) | Status Solver `KGP` (Gap) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `EVAL_01_TOPO_DISTANT_AND_PARALLEL` | 4 / 4 / 2 / **2** | 429.9 / 376.6 / 354.8 / **243.2** | 82 / 0 / 0 / **0** | +0 jan / -111.64 ch | `OPTIMAL` (0.0000%) |
| `EVAL_02_TIE_TRANSFER_AND_CREW_SYNC` | 4 / 3 / 2 / **2** | 486.7 / 287.1 / 256.1 / **167.8** | 72 / 0 / 0 / **0** | +0 jan / -88.32 ch | `OPTIMAL` (0.0000%) |
| `EVAL_03_PRECEDENCE_AND_RELEASE_DATES` | 4 / 3 / 3 / **2** | 182.8 / 147.6 / 147.6 / **181.2** | 16 / 16 / 16 / **0** | -1 jan / +33.55 ch | `OPTIMAL` (0.0000%) |
| `EVAL_04_FREQ_VS_DURATION_TRADEOFF` | 3 / 3 / 2 / **1** | 285.9 / 285.8 / 382.1 / **259.2** | 50 / 50 / 72 / **0** | -1 jan / -122.93 ch | `OPTIMAL` (0.0000%) |
| `EVAL_05_DISJOINT_NO_OPPORTUNITY` | 3 / 3 / 3 / **3** | 144.0 / 144.0 / 144.0 / **144.0** | 0 / 0 / 0 / **0** | +0 jan / +0.00 ch | `OPTIMAL` (0.0000%) |
| `EVAL_06_LIFECYCLE_AND_TEMPORAL_FILTER` | 3 / 2 / 1 / **1** | 263.4 / 217.9 / 183.2 / **120.0** | 50 / 50 / 0 / **0** | +0 jan / -63.24 ch | `OPTIMAL` (0.0000%) |
| `EVAL_07_STOCHASTIC_PORTFOLIO` | 5 / 4 / 3 / **3** | 375.4 / 374.0 / 372.3 / **289.8** | 16 / 0 / 0 / **0** | +0 jan / -82.51 ch | `OPTIMAL` (0.0000%) |
| `EVAL_08_STOCHASTIC_PORTFOLIO` | 5 / 4 / 4 / **3** | 351.8 / 316.6 / 316.6 / **287.0** | 16 / 16 / 16 / **0** | -1 jan / -29.65 ch | `OPTIMAL` (0.0000%) |

---

## 3. Distinção Metodológica e Limitações Declaradas

1. **Janelas de Interrupção vs. Manobras Físicas:** Cada janela de interrupção requer abertura e posterior fechamento de chaves seccionadoras NF e, quando há transferência de carga, fechamento e abertura de chave NA ($2 \times N_{\text{chaves}}$ operações físicas). O relatório contabiliza ambas separadamente.
2. **Clientes-Hora sem Duplicidade:** Calculados pela soma, por consumidor, da duração da união disjunta dos intervalos sem alimentação em todas as etapas da manobra.
3. **Não Extrapolação Financeira Direta:** Os resultados em rede de referência não são convertidos por regra de três sobre o montante de R\$ 3,64 milhões do Anexo 2, pois a apuração regulatória de compensações DIC/FIC/DMIC exige histórico individual por unidade consumidora, limites mensais/trimestrais/anuais do PRODIST Módulo 8 e faturamento real.
4. **Restrições Operacionais de Campo Não Modeladas:** Seletividade fina de proteção em tempo real, instalação física de aterramento temporário, bloqueio mecânico (LOTO) e autorização final do COD permanecem sob responsabilidade operacional humana da Cemig.
