# REV04-P1 — portabilidade do histórico e passagem à revisão manual

A reauditoria recebida sobre `6eb2e4f0d9ad91d40e4e0b69e58af245c1b86c10` confirmou o fechamento de A1–A5 no recorte declarado, com 130 testes, 48 planos factíveis, carteira obrigatória completa e 12 objetivos KGP recalculados. O aval se refere ao avanço para revisão manual da candidatura; não certifica operação de campo, aprovação pela Cemig ou benefício operacional.

## Ajuste de portabilidade

Foi reproduzida a pendência em `reports/validation_scripts/rev03_audit/remaining_results.json`: o Git armazenava 8.979 bytes em LF, enquanto o ZIP original preservava 9.254 bytes com 275 CRLF. O arquivo histórico não é UTF-8; portanto, a conferência canônica corretamente não aceitava equivalência de finais de linha. As demais diferenças de texto UTF-8 já eram tratadas separadamente.

A regra específica `-text` em `.gitattributes`, seguida da reentrada dos bytes originais no índice, preserva esse histórico em qualquer configuração de finais de linha. O próprio arquivo de atributos também tem preservação exata. A conferência canônica não foi ampliada para codificações antigas, e não foi removida nenhuma verificação de integridade.

SHA-256 do histórico preservado, igual ao ZIP auditado:

`423e8e2217f3353069ca4395d9c11c3fe9d4a7e3316eecaff6ded23fe4d2c768`

A contraprova anterior com `core.autocrlf=false` e `core.eol=lf` falhou somente nesse histórico. Com a correção mínima aplicada, os 364 itens do manifesto auditado passaram: 112 correspondências exatas e 252 diferenças exclusivamente CRLF/LF de texto UTF-8. O histórico passou por bytes exatos.

O ensaio anterior feito no Windows utilizava a configuração local de exportação do Git, que podia reconstituir CRLF. O novo procedimento força a configuração LF e também confere a configuração CRLF. Não se apresenta esse ensaio Windows como execução de um sistema operacional Linux; a reauditoria recebida executou a suíte no Linux, e o ensaio de Git cobre o defeito de bytes identificado por ela.

Para repetir no checkout atualizado, com as dependências instaladas:

```text
git -c core.autocrlf=false -c core.eol=lf archive --format=zip --output=checkout-lf.zip HEAD
python -m zipfile -e checkout-lf.zip checkout-lf
```

Crie a pasta `checkout-lf/.audit_cache` e execute `python checkout-lf/scripts/validate_delivery.py`. O manifesto corrente inclui também este complemento e o arquivo de atributos; seu total pode superar os 364 itens do manifesto auditado original.

## Proveniência e revisão manual

Os 28 arquivos congelados em `configs/protocol_and_seeds.json`, incluindo núcleo e testes científicos, permanecem idênticos à fonte experimental `66e90ea783720ccd4dd1e9cda9eef288e0facb3d`. Entradas, planos, pesos e resultados não foram regenerados. Também foi corrigida uma frase com caracteres corrompidos no README e incluído o arquivo de atributos no empacotamento.

A suíte completa foi reexecutada após a correção: **130 aprovados em 188,82 s**, sem acrescentar testes à contagem. Log em `reports/validation_logs/rev04/portability_p1_pytest.log`. A cópia exportada em LF também teve os 48 planos e os 12 objetivos verificados novamente, sem falhas de integridade; a exportação CRLF passou pela mesma conferência de manifesto.

O pacote originalmente auditado permanece preservado com SHA-256 `c8ea40634d4e45f068c07345a5391e4dc500d740840d887ea1cc0c5568dce718`. O pacote complementar REV04-P1 tem manifesto próprio. Não se somam ensaios de portabilidade aos 130 testes da suíte.

A sequência de revisão é: respostas da candidatura e limites do escopo; evidências e ensaio dos slides; dados, entregas e critérios da PoC; campos empresariais e formulários aplicáveis; conferência humana antes da submissão. O guia vigente reúne esses materiais. Permanecem bloqueados ilhamento DG e catálogo contínuo de 24 h. Os ganhos descritivos nos oito casos sintéticos conhecidos e a perda de EVAL03 continuam com as mesmas ressalvas da REV04.

Este complemento não executa merge, deploy, assinatura ou submissão e não altera orçamento nem dados empresariais.
