# KGP 0.6.1 / REV06-P1 — fechamento dos casos residuais

Manutenção de 30/10/2026 sobre a entrega auditada `cf386439`. A revisão recebida reproduziu 397 testes no seu ambiente, 781 hashes, 48 planos e 12 objetivos/bounds/gaps da REV06. O teste nativo de symlink explica a diferença para 396 aprovados + 1 ignorado no Windows. Esses números continuam identificando a entrega original; não são somados aos resultados da manutenção.

## Três casos de robustez

1. **JSON profundo.** Antes, 1.500 vetores aninhados escapavam como `RecursionError`. A leitura estrita agora limita a estrutura a 512 objetos/vetores antes da decodificação, ignora delimitadores em strings e produz `JSON_DEPTH_EXCEEDED`. A varredura de não finitos usa pilha explícita. Manifesto, JSON/GeoJSON, célula CSV, CLI e plano exportado foram cobertos. Controles de 100, 400 e 512 níveis continuam aceitos; 513, 1.500 e 5.000 são bloqueados.
2. **Distância pequena e velocidade extrema.** O limiar de `1e-6 km` foi removido no sequenciador e no recálculo independente. Ambos calculam distância/velocidade antes de arredondar para `0.0001 h`. O caso de 10 h de viagem + 1 h de trabalho não cabe no turno: os quatro métodos mantêm a obrigatória pendente e não publicam certificado. A razão entre dois subnormais preserva 1 h; a viagem minúscula com velocidade usual continua arredondando a zero.
3. **Tempo derivado não representável.** A rejeição de ticks do deslocamento é tratada somente no ponto desse cálculo no verificador. Um plano finito com viagem canônica infinita, tempo além da representação operacional ou diferença de coordenadas não representável recebe relatório finito e serializável de inviabilidade. Não há captura genérica de erros internos.

## Evidência e revisão

Os 45 novos testes produziram **28 falhas + 17 controles aprovados** no checkout isolado da versão auditada, e **45/45** na correção. A suíte consolidada passou com **441 aprovados + 1 ignorado** no Windows; o teste ignorado mantém a limitação de privilégio nativo de symlink, sem novo caso ignorado. Logs separados preservam a primeira rodada e o fechamento após os ajustes de empacotamento.

Um agente revisor com contexto novo repetiu os 45 testes e sondagens adicionais de objetos/planos de profundidade 400/512, rejeição numérica, ordem dos diagnósticos e caminhos de reprodução. Não reproduziu achado crítico/importante no núcleo. Encontrou um resumo de empacotamento que ainda imprimia o commit do protocolo antigo; o resumo agora usa a mesma fonte selecionada nos manifestos. A contraprova de empacotamento confirmou igualdade de commits e preservação do arquivo original. Essa revisão por agente não equivale a auditoria por terceiro humano.

## Mesmos casos conhecidos

Fonte científica da manutenção: `2bc16881e99e16a410831221a5d66d439d7ddd3a`. A execução congelou **37 hashes** de módulos/testes/metadados e confirmou **12 entradas idênticas**. Os **48/48 planos** foram gerados novamente, relidos e verificados com 100% da carteira obrigatória. Todas as métricas/status, exceto duração computacional, coincidiram; os oito resultados pareados também permaneceram iguais.

Persistem as médias descritivas de **15% menos janelas** e **16,9401% menos clientes-hora** frente a B2, incluindo a perda EVAL_03. Sem nova avaliação cega, ampliação de amostra, benefício operacional Cemig ou conversão dos percentuais em economia financeira. No GIS, cadastro e conectividade continuam separados do domínio físico radial: nenhuma aresta é removida para obter aceitação e nenhuma saída bloqueada ganha métricas de benefício.

## Reprodução e integridade

```text
python -m pytest -q
python reports/validation_scripts/rev06_p1/reproduce_maintenance.py --output .audit_cache/rev06_p1/reproduction-check
python scripts/validate_delivery.py
python scripts/package_delivery.py
```

O executor de reprodução escreve somente na pasta isolada solicitada. Os nomes históricos de diretórios usados por `run_revalidation` permanecem internos a essa pasta; fonte, hashes e metadados identificam a execução correta. `configs/protocol_and_seeds.json` e os planos publicados da REV06 ficam históricos e preservados. `configs/delivery_revision.json` identifica separadamente a manutenção e produz um novo ZIP datado, sem substituir o pacote recebido pelo revisor externo. O manifesto público exclui os documentos privados da candidatura e do orçamento.

## Precisão da apresentação

O roteiro e as notas do slide 3 passam a dizer **“suprimento em cada etapa elétrica representada”**. O rótulo T2 passa a **“Protocolo experimental KGP REV06”**. O slide 5 qualifica a comparação como realizada em casos sintéticos. Os títulos/referências científicas REV06 nos documentos continuam designando a evidência original, preservada. Os PDFs alterados foram exportados, inspecionados e conferidos por bytes após publicação no Drive. Compartilhamento, preenchimento empresarial, currículos, assinaturas e classificação contábil continuam sob revisão humana; nenhum formulário foi submetido.
