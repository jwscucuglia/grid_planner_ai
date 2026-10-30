# Validação técnica KGP 0.3.0 / REV03

Base84340636c6d3877eacf44d4e19a1b802451057c1. Fonte final5adf593ad9168e61a4b4ceea62346db7402a3d4c. Branch `codex/d07-revisao-integral-20261029`; sem merge ou submissão.

**74 testes aprovados em81,54s;48/48 planos factíveis** depois da leitura dos JSONs exportados, com100% da carteira obrigatória. A reprodução independente da rodada pelo script histórico igualou48/48 métricas/status, excluído tempo. Entradas e planos congelados foram relidos, e os12 objetivos KGP foram confrontados diretamente com contadores do verificador e horários de equipe.

| Requisito | Evidência e comportamento |
|---|---|
| Cronologia elétrica conjunta | Referência por fase em todos os limites de etapa; qualquer sobreposição de janelas elétricas é rejeitada, inclusive em zonas disjuntas; equipes paralelas dentro da mesma janela permitidas |
| Isolamento e impacto | Union-Find independente da BFS do planejador; etapas/consumidores/intervalos próprios; não chama o simulador do planejador; confere os campos evidenciais exportados |
| Recursos no tempo | Agenda, competências, viagens canônicas, preparação, execução, recomposição e precedências; velocidade positiva usada sem piso silencioso |
| Contratos e leitura | Valores finitos, tipos estritos, cronologia/referências, IDs e estrutura; JSON rejeita não finitos, chaves duplicadas e coerção silenciosa |
| Objetivo e solver | Obrigatórias rígidas; opcionais50 nos pesos padrão; predecessores presentes; objetivo/bound/gap coerentes inclusive fallback com bound0; status restrito às colunas geradas |
| Comparadores | Mesmos dados/restrições/verificador; B2 com melhoria local limitada; mesmo raio B1 no comparador e na semente |
| Evidência adversa | Não usa falhas/incompletude como benefício; figura sem comparação em rodada inviável; manifesto corrente exclui planos legados |

Nos8 EVAL conhecidos: janelas médias B2=2,5 e KGP=2,125 (**−15%**); clientes-hora B2=255,6620 e KGP=212,3526 (**−16,9%**). Janelas3 ganhos/5 empates/0 perdas; clientes-hora5/2/1. Em EVAL03 há+33,5536 clientes-hora em troca de uma janela a menos. Resultados sintéticos, sem validação operacional ou economia comprovada na Cemig.

Uma enumeração adicional de228 combinações em4 pequenos catálogos reproduziu os4 melhores valores e bounds, sem usar as restrições matriciais do MILP para decidir factibilidade. Esses casos não são somados aos48 planos do estudo principal.

Limites: sem novo cegamento; casos desenhados numa rede comum; etapas elétricas agregadas; transferência radial aproximada; sem LOTO/aterramento/proteção de campo; interrupções preexistentes rejeitadas. Certificado apenas nas colunas com pacotes até5 ordens,5 janelas/dia e120 estados por profundidade, inícios/alocações construtivos. Revisões por agentes separados não equivalem a auditoria humana ou homologação.

Os relatórios de revisão preservam os achados iniciais e os respectivos fechamentos RED→GREEN. Logs e scripts acompanham o repositório; o orçamento e a revisão documental da candidatura ficam no Drive restrito.

A igualdade48/48 refere-se a duas execuções da REV03. A comparação com a REV02 está separada em `COMPARACAO_REV02_REV03.json`: a melhoria local de B2 e as correções podem alterar métricas entre versões, além do status descritivo de B2. Não se afirma igualdade entre os dois estudos/versionamentos.

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m kgp.experiment
./.venv/Scripts/python.exe scripts/validate_delivery.py
./.venv/Scripts/python.exe scripts/validate_small_catalog.py
```
