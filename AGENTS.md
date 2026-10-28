# AGENTS.md — KAPTUS GRID PLANNER (KGP) | DESAFIO 07 CEMIG

## 1. Objetivo e Isolamento
Desenvolver o demonstrador computacional reproduzível do **KAPTUS GRID PLANNER (KGP)** para o Desafio 07 do Inova Cemig.Lab Ciclo 5.
- Este repositório (`grid_planner_ai`) é 100% independente do Desafio 06 (GTA)[cite: 1]. Não importe código, dados ou resultados do D06[cite: 1].
- Operação estritamente *sidecar* (out-of-band): sem telecomandos e usando apenas rede de referência e carteira sintética documentada[cite: 1].

## 2. Regras Científicas Inegociáveis
1. **Não calibrar gerador para forçar 1,7%:** O comportamento dos cenários deve emergir de premissas documentadas (incluindo cenários com alto ganho de acoplamento, cenários de empate e cenários onde restrições impedem agrupar).
2. **Conectividade Pós-Manobra Real:** Não usar apenas busca estática "downstream"[cite: 1]. Calcular componentes conectados às fontes e barras efetivamente desenergizadas em cada etapa da intervenção (isolamento, transferência por chave NA/NF e recomposição)[cite: 1].
3. **Ocupação de Equipes no Tempo:** Não validar recursos apenas por soma agregada de homens-hora (HH)[cite: 1]. Verificar conflito de agenda de cada equipe no tempo, competências, preparação, sequência de execução, deslocamento e recomposição[cite: 1].
4. **Verificador Independente (`feasibility.py`):** Módulo separado do otimizador que recalcula carteira concluída, isolamento, recursos, janelas e impacto diretamente a partir do plano exportado. Todos os métodos (`B0`, `B1`, `B2` e `KGP`) devem passar pelo mesmo verificador e concluir 100% da carteira obrigatória sob as mesmas restrições[cite: 1].
5. **Métricas Exatas:**
   - Distinguir **janelas de interrupção** de **manobras físicas**[cite: 1].
   - Calcular **clientes-hora** pela soma, por consumidor, da duração da união dos intervalos sem alimentação (sem duplicar intervalos sobrepostos).
   - Declarar otimalidade apenas quando comprovada pelos limites do solver; caso contrário, relatar melhor solução factível, limite conhecido (bound), gap e tempo de execução[cite: 1].
   - Não calcular economia financeira fazendo regra de três sobre R$ 3,64 milhões.

   @"
# AGENTS.md — KAPTUS GRID PLANNER (KGP) | DESAFIO 07 CEMIG

## 1. Objetivo e Isolamento
Construir um demonstrador independente do GTA/D06 para investigar se a programação conjunta de serviços compatíveis reduz interrupções e impacto nos consumidores, cumprindo a mesma carteira, prazos e recursos.
- Repositório `grid_planner_ai` 100% independente do Desafio 06 (sem copiar código ou resultados do D06).
- Usar rede de referência e carteira sintética documentada.

## 2. Hipóteses Testáveis
- H1: A conectividade após as manobras encontra serviços compatíveis que uma seleção apenas geográfica não identifica.
- H2: Otimizar datas e pacotes melhora o equilíbrio entre frequência e duração das interrupções em relação a uma heurística topológica competente.
- H3: As recomendações continuam factíveis quando há limites de equipes, precedências e variação de duração.

## 3. Arquitetura Obrigatória de Arquivos
- `src/kgp/contracts.py`: Entidades tipadas (Rede, Serviço com `created_at`/`ready_at`/prazo/duração/competências/precedências, Recursos/Equipes, Consumidores e Saída do Plano).
- `src/kgp/topology.py`: Cálculo de componentes conectados às fontes e consumidores sem alimentação em cada etapa da manobra (isolamento, transferência e recomposição).
- `src/kgp/eligibility.py`: Elegibilidade de serviços na zona de isolamento, simultaneidade, precedências e alerta de conflitos manutenção-substituição (sem eliminar ordem obrigatória).
- `src/kgp/scheduling.py`: Sequenciamento temporal real de equipes (preparação, execução, recomposição, deslocamento), sem usar apenas horas-homem agregadas.
- `src/kgp/baselines.py`:
  - `B0` (Individual por prioridade e prazo);
  - `B1` (Geográfico por distância/raio submetido ao mesmo verificador elétrico e de recursos);
  - `B2` (Topológico guloso por compatibilidade elétrica, prioridade e prazo com melhoria local).
- `src/kgp/optimizer.py`: Otimizador combinatório (`KGP` via MILP/SciPy) selecionando pacotes, datas e alocação de recursos dentro das mesmas opções permitidas aos comparadores.
- `src/kgp/feasibility.py`: Verificador independente em módulo separado que recalcula carteira concluída, datas, recursos, materiais, isolamento e impacto a partir do plano exportado.
- `src/kgp/metrics.py`: Cálculo de janelas de interrupção, manobras físicas, taxa de acoplamento (janelas com 2+ serviços), reincidência (2+ interrupções por consumidor), clientes-hora (soma da união dos intervalos sem alimentação, sem duplicidade) e status/gap/tempo do solver.
- `src/kgp/experiment.py`: Gerador de cenários (sem calibrar artificialmente para 1,7%), separação de sementes de desenvolvimento e avaliação retida, geração de CSV/JSON, gráfico comparativo em fundo branco 16:9, manifesto SHA-256 e `reports/RELATORIO_EXECUCAO_KGP.md`.
- `tests/`: Testes cobrindo todos os casos críticos de topologia e programação, incluindo planos propositalmente inválidos para testar o verificador `feasibility.py`.
"@ | Set-Content -Path AGENTS.md -Encoding UTF8