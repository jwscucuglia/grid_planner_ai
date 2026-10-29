# KGP 0.4.0 / REV04 — contraprovas A1–A5

Revisão sobre f21690e8cd49f5174e1d03ad0b45bda397d003e3, na mesma branch/PR1. A principal continua na REV02. Os achados da auditoria recebida foram tecnicamente procedentes nos casos reproduzidos; a sua existência não invalida retroativamente as48 métricas da REV03.

| Achado | Falha reproduzida | Fechamento e controle |
|---|---|---|
| A1 | Isolamento positivo marcado inviável a180% era aprovado | Toda etapa positiva é conferida na elegibilidade, escalonador e auditoria; inclui isolamento/transferência/execução/recomposição e casos sem fechamento de NA. Controles admissíveis e etapas de duração zero preservados |
| A2 | DG admitida na conectividade era omitida do fluxo | `allow_dg_islanding=True` explicitamente não suportado: quatro métodos devolvem `UNSUPPORTED_INPUT`, sem certificado. Elegibilidade/escalonador/simulador/verificador também recusam. Não se implementou despacho/limites de DG nesta rodada |
| A3 | Planos brutos usavam datas anteriores à decisão e declaravam ótimo | Dias passados excluídos dos comparadores/sementes/colunas; KGP audita objeto e JSON antes de reportar certificado. Ordem opcional vencida permanece omitida com penalidade correta; casoJdia2/Ovencida custa146,12 |
| A4 | Fallback sem transferência era auditado com a tie fechada | Obrigatória falha explicitamente, sem fallback silencioso; opcional malsucedida mantém a tie aberta e esse estado efetivamente executado é reconstruído de forma independente |
| A5 | Gap18ms e tolerâncias diferentes alteravam reincidência/J | Grade operacional1e-4h(0,36s) na leitura/validação; uniões independentes em ticks inteiros, adjacência exata e requisitos/agenda/etapas exatos. Witness fora da grade rejeitado; gap1tick é recomposição real |

As regressões foram observadas falhando antes das respectivas correções. As74 anteriores foram preservadas; a suíte consolidada passou com **125 testes em168,45s** no `.venv` da entrega. Testemunhos adicionais de1tick cobrem falsificação de etapa, duração/preparação/recomposição insuficientes, conflito de janela e deslocamento declarado incorreto. O caso subnormal que se converte em zero ticks não satisfaz uma duração estritamente positiva.

A revisão elétrica/temporal separada executou2.085 verificações:85 de casos/controles e1.000 amostras de intervalos confrontadas em cada uma das duas implementações com uma terceira referência de ocupação bruta de ticks. Nenhum bloqueador adicional foi observado nesse escopo. Essa revisão não equivale a homologação humana ou operacional.

Regra de midpoint: se a preparação tem número ímpar de ticks, o isolamento recebe `floor(prep/2)` e a transferência recebe o restante. Não se criam fronteiras de meia resolução ou arredondamento silencioso de um plano exportado.

O manifesto mantém hashes exatos para os bytes de origem/ZIP, acrescidos de hashes canônicos apenas CRLF→LF para textos UTF-8. A conferência de checkout distingue equivalência de finais de linha e alteração real; binários/UTF-16 não recebem essa tolerância.

Os novos resultados da rodada congelada e seus limites constam de `RELATORIO_EXECUCAO_KGP.md`, `kgp_evaluation_summary.json` e `manifest_sha256.json`. As entradas e planos REV03 foram preservados como histórico; não se forçou a manutenção dos percentuais conhecidos. Manobras continuam agregadas e o fluxo é uma aproximação radial; não há operação de campo, telecomandos ou garantia de benefício Cemig sem PoC.
