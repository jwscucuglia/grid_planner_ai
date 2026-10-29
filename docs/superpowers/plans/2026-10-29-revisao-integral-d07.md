# Revisão integral D07 — plano de implementação

> Execução autorizada no repasse do usuário: corrigir, testar, documentar e preparar PR sem merge ou submissão. Usar revisão sistemática, TDD e revisão final independente.

**Objetivo:** fechar os achados A–E da reauditoria de 8434063 e reconciliar a candidatura com evidências reproduzidas.
**Arquitetura:** manter os módulos existentes; uma referência elétrica no verificador sem chamar o simulador do planejador. Restringir o catálogo a janelas elétricas seriais, com execução paralela de equipes dentro de uma janela.
**Tecnologias:** Python, SciPy MILP, pytest, Google Drive conectado.
**Especificação:** repasse integral fornecido pelo usuário e AGENTS.md.

## Restrições globais
- D07 independente; sidecar, rede de referência e carteira sintética; sem telecomandos.
- Sem calibrar para benefício desejado; mesmos dados e limites para B0/B1/B2/KGP.
- Preservar IDs/históricos Drive e alterações anteriores; não mergear, assinar, publicar nem submeter.
- Otimalidade apenas nas colunas discretas efetivamente geradas; resultados conhecidos não são nova avaliação cega.

## Foco de revisão
- Transferência em zona disjunta reenergizando outro serviço: rejeitar toda sobreposição elétrica até suportar composição global.
- Adulteração de campos e falha comum no simulador: recálculo BFS por fase e comparação completa dos campos evidenciais.
- NaN, infinito, ordem temporal inválida, referências e IDs duplicados: rejeitar antes de cálculo.
- Ordens opcionais omitidas e mandatory caro/inviável: objetivo/bound/gap coerentes sem relaxar carteira obrigatória.
- Evidência documental desatualizada: versionar a reavaliação sem somar estudos anteriores.

## Tarefas
- [x] Acessos, inventário recursivo, base limpa e branch isolada; baseline 20 testes aprovados.
- [x] 1. Regressões em tests/test_verifier_audit.py: contraexemplo global, adulterações por campo, falha compartilhada, não-finitos e cronologia. Rodar antes e registrar falhas.
- [x] 2. Corrigir feasibility.py: reconstrução independente de etapas e impacto, integridade completa; validar contratos; mapear todas zonas como conflitantes em baselines.py. Rodar regressões e suíte.
- [x] 3. Corrigir optimizer.py e tests/test_optimizer_audit.py: mesma penalidade opcional, cobertura obrigatória rígida, comparação pequena com enumeração; relatar catálogo.
- [x] 4. Corrigir fronteiras JSON e experiment.py: JSON estrito, entradas/planos/verificações exportados, protocolo congelado antes execução, classificação reavaliação sintética e perdas por métrica; executar 48 planos e manifestos.
- [x] 5. Conferir edital/modelos/orçamento; produzir matrizes em reports; reconciliar documentos vigentes, slides e PDFs no Drive após resultados finais.
- [x] 6. Revisão independente; regressões de achados adicionais; suíte final e hashes; commits rastreáveis, push branch e PR se houver acesso.

## Registro de decisões e verificações
- 2026-10-29: branch em checkout limpo atende isolamento autorizado; base 84340636c6d3877eacf44d4e19a1b802451057c1.
- Restrição conservadora: nenhuma janela elétrica simultânea, mesmo em zonas disjuntas. Evita assumir independência elétrica por interseção estática; custa oportunidades de paralelismo entre janelas, explicitamente fora do escopo demonstrado.
- Reavaliação das mesmas sementes DEV/EVAL conhecidas; preservar rótulos para rastreio, retirar alegação de cegamento/independência estatística.
- Núcleo final5adf593:74 testes/81,54s;48 planos factíveis;12 objetivos recalculados;228 combinações enumeradas em4 catálogos;48/48 métricas/status reproduzidos sem tempo.
- Revisões adicionais encontraram contratos malformados, relato de ótimo, outages preexistentes, fallback sem incumbent, figura legada e raio B1. Todos corrigidos ou bloqueados explicitamente, com evidências no pacote. Deslocamento abaixo de5km/h também corrigido por referência física10/2=5h.
- Repositório confirmado público: dados da candidatura, orçamento, inventário e matrizes permanecem no Drive/pacote restrito. Código, testes e evidências experimentais integram a PR.
- Documentos e pitch atualizados nos próprios IDs,9 PDFs conferidos por hash e65 páginas inspecionadas. Orçamento é estimativa; classificação de apoio/h é pendência contábil/Cemig. Dados empresariais serão preenchidos pelo usuário.
- PR para revisão interna criada: https://github.com/jwscucuglia/grid_planner_ai/pull/1 (draft). Conector recusou criação por permissão da integração; acesso Git local autorizado criou a PR. Principal preservada em8434063; sem merge, deploy ou submissão.
