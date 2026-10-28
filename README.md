# KAPTUS GRID PLANNER (KGP) — Desafio 07 Inova Cemig.Lab Ciclo 5

Demonstrador computacional independente e reproduzível para programação conjunta de serviços de manutenção e obras na rede elétrica de distribuição sob restrições topológicas pós-manobra, temporais e de recursos operacionais.

- **Isolamento de Escopo:** Este repositório (`grid_planner_ai`) é 100% independente do Desafio 06 (GTA). Não compartilha nem importa código, dados ou resultados do D06.
- **Modo de Operação:** *Sidecar / Out-of-Band* — sem telecomandos, operando sobre rede elétrica de referência multi-alimentador (`13,8 kV`) e carteira sintética documentada.

---

## 1. Arquitetura de Módulos (`src/kgp/`)

| Módulo | Responsabilidade Técnica |
| :--- | :--- |
| `src/kgp/contracts.py` | Entidades tipadas de entrada e saída: `GridNetwork`, `GridNode`, `GridBranch`, `GridSwitch`, `GridAsset`, `ConsumerUnit`, `IsolationZoneSpec`, `ServiceOrder` (com `created_at_day`, `ready_at_day`, `deadline_day`, competências, materiais e precedências), `CrewResource`, `ExecutionPlan`, `FeasibilityReport` e `PlanMetrics`. |
| `src/kgp/topology.py` | Cálculo de conectividade elétrica pós-manobra por fase (`A`, `B`, `C`), componentes conectados às fontes ativas, barras e ativos efetivamente desenergizados por etapa (`ISOLATION`, `TRANSFER`, `EXECUTION`, `RECOMPOSITION`), anti-ilhamento de GD e validação elétrica de transferência por chave NA (carregamento térmico e queda de tensão). |
| `src/kgp/eligibility.py` | Filtro temporal anti-vazamento na data da decisão (`created_at_day <= decision_day`), elegibilidade de ativos na zona de isolamento pós-manobra, precedências e detecção de conflitos de ciclo de vida (`manutenção vs. substituição`) sem eliminar ordens obrigatórias. |
| `src/kgp/scheduling.py` | Sequenciamento contínuo no tempo de cada equipe operacional (deslocamento euclidiano, preparação, execução, recomposição, competências, turno e restrições de simultaneidade), fazendo a duração da janela emergir do *makespan* real das equipes e manobras. |
| `src/kgp/baselines.py` | Comparadores competentes submetidos às mesmas restrições: `B0` (Individual por prioridade e prazo), `B1` (Geográfico por raio em km submetido ao verificador elétrico e de recursos) e `B2` (Topológico guloso por compatibilidade elétrica, prioridade e prazo). |
| `src/kgp/optimizer.py` | Otimizador combinatório `KGP` (MILP via `scipy.optimize.milp` / HiGHS) selecionando pacotes, zonas, datas e alocação de equipes com reporte de status, objetivo, limite dual (*bound*), *gap* e tempo computacional. |
| `src/kgp/feasibility.py` | Verificador independente em módulo separado que recalcula do zero a carteira obrigatória concluída, datas, isolamento elétrico, agenda das equipes, precedências, estoque de materiais e impacto a partir do plano exportado. |
| `src/kgp/metrics.py` | Apuração exata de janelas de interrupção vs. manobras físicas de chaves, taxa de acoplamento (`2+` serviços/janela), reincidência por consumidor (respeitando recomposição) e clientes-hora pela soma da união disjunta dos intervalos sem alimentação. |
| `src/kgp/experiment.py` | Geração reprodutível dos cenários de desenvolvimento (`DEV`) e avaliação cega retida (`EVAL`), geração de CSV/JSON, figura comparativa `16:9` em fundo branco, manifesto `SHA-256` e relatório técnico (`reports/RELATORIO_EXECUCAO_KGP.md`). |

---

## 2. Instruções de Reprodução

```powershell
# 1. Instalar dependências no ambiente virtual local (.venv)
.\.venv\Scripts\pip.exe install -r requirements.txt

# 2. Executar a bateria completa de testes automatizados (topologia, programação e planos inválidos)
.\.venv\Scripts\pytest.exe -v

# 3. Executar a avaliação experimental completa (DEV + EVAL) e gerar artefatos auditados
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m kgp.experiment
```

---

## 3. Artefatos Gerados

- `configs/protocol_and_seeds.json`: Configuração congelada do protocolo, pesos da função objetivo e sementes separadas (`DEV` e `EVAL`).
- `data/reference_network.json`: Rede elétrica de referência multi-alimentador exportada com origem, licença e metadados.
- `reports/kgp_evaluation_results.csv`: Tabela completa de métricas por cenário e método (`B0`, `B1`, `B2`, `KGP`).
- `reports/kgp_evaluation_summary.json`: Resultados detalhados, intervalos de confiança Bootstrap 95%, comparações pareadas e planos completos auditados pelo verificador independente.
- `reports/comparativo_kgp_desafio07.png`: Painel visual comparativo `16:9` (`1920x1080`) em fundo branco.
- `reports/RELATORIO_EXECUCAO_KGP.md`: Relatório técnico-científico detalhado com análise das hipóteses `H1`, `H2` e `H3`, ganhos, empates e limitações.
- `reports/manifest_sha256.json`: Manifesto criptográfico `SHA-256` de todos os arquivos de código, dados, configurações e relatórios.
