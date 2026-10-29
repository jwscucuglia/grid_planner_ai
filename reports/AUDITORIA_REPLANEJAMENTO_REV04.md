# Auditoria de replanejamento — REV04

Data: 29/10/2026. Escopo: datas de decisão, publicação do resultado bruto do KGP e bloqueio de opção elétrica não suportada.

B0, B1 e B2 usavam a disponibilidade do serviço como primeira data, sem limitá-la pela data de decisão. O gerador de colunas do KGP também aceitava dias passados do catálogo. A correção aplica `dia >= decision_day` aos comparadores, suas sementes e às colunas candidatas. Ordens opcionais vencidas continuam na carteira conhecida e são registradas como omitidas; sua penalidade permanece no objetivo.

Antes de publicar objetivo ou certificado, o KGP agora aplica o mesmo verificador independente ao objeto reconstruído e à sua exportação com leitura JSON estrita. O caminho de carteira vazia e os fallbacks usam essa fronteira. Um candidato rejeitado recebe `INVALID_PLAN_REJECTED`, sem objetivo factível, bound, gap ou prova de ótimo, com violações em metadata. Quando existe semente factível, o KGP prefere a melhor semente auditada; o resultado do catálogo rejeitado fica apenas como diagnóstico e não certifica esse fallback.

Os quatro métodos rejeitam `allow_dg_islanding=True` antes de qualquer simulação, com status `UNSUPPORTED_INPUT`, motivo explícito e carteira conhecida não executada. A política suportada permanece anti-ilhamento.

Evidências reproduzíveis:

- Decisão no dia 2 e catálogo com dias 1/2: todos os métodos executam a obrigatória `J` no dia 2.
- Equipes disponíveis somente no dia 1: nenhum método programa retroativamente; a obrigatória permanece pendente e o KGP não publica certificado.
- Obrigatória `J` com prazo 2 e opcional `O` com prazo 1: `J` no dia 2, `O` omitida; no pequeno caso documentado, KGP retorna objetivo e bound de **146,12**, gap zero, certificado limitado ao catálogo discreto gerado.
- Corrupção de manobras no candidato e rejeição da leitura JSON: bloqueiam o certificado; uma semente factível auditada pode ser devolvida sem bound/gap herdados.

TDD: a primeira execução dos casos de replanejamento/publicação apresentou 15 falhas em 16 testes; o bloqueio de ilhamento apresentou quatro falhas antes da implementação. Após a correção, `.venv/Scripts/python.exe -m pytest tests/test_rev04_replanning.py tests/test_optimizer_audit.py -q` passou **31 testes**. A suíte integral será executada sobre o núcleo consolidado da REV04; a tentativa com Python global foi interrompida após identificar ausência da dependência `networkx` nesse ambiente.

Esta auditoria não recalcula nem antecipa os resultados do benchmark consolidado.
