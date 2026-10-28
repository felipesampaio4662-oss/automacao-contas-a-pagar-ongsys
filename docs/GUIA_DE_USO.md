# Guia de uso — Automação Ongsys de Contas a Pagar

Programa local que lê a planilha de fluxo, confere os lançamentos pendentes e
cadastra os títulos em Contas a Pagar no Ongsys.

**O programa nunca dá baixa, nunca paga, nunca envia remessa bancária e nunca
envia para aprovação.** Ele apenas cria o título.

## Painel visual (recomendado)

Dê dois cliques em **`painel.bat`** para abrir a interface visual. O painel não
substitui nem reimplementa a automação: ele chama os mesmos comandos Python dos
arquivos `.bat`, mostra as mensagens em uma área de acompanhamento e permite
responder às confirmações sem usar uma janela de terminal separada.

No painel:

- a área **Acompanhamento** pode ser aumentada ou reduzida arrastando a
  divisória horizontal; durante uma operação ela se expande automaticamente e
  depois retorna à altura escolhida;

1. selecione a planilha;
2. confira o período detectado;
3. escolha explicitamente a conta ou **Sem conta pagadora**;
4. use **Planejar** e abra o relatório de conferência;
5. use **Executar** somente depois da conferência;
6. digite `SIM` na primeira barreira e `CONFIRMO` quando a fila completa for
   exibida;
7. use **Auditar** antes de o time baixar ou pagar os títulos.

Os campos Fornecedor e Limite são opcionais e servem para pilotos menores. O
modo excepcional de duplicidade intencional continua disponível somente no
fluxo técnico já existente; ele não foi exposto no painel para evitar uso
acidental.

O painel funciona inteiramente neste computador, sem servidor web e sem serviço
externo adicional. Fechar a janela durante uma execução exige nova confirmação
e pode deixar o título corrente pendente de auditoria.

---

## O que fazer, na ordem

| Passo | Arquivo | O que acontece |
|---|---|---|
| 1 | `instalar.bat` | Prepara o programa. Só na primeira vez. |
| 2 | `login.bat` | Abre o navegador para você fazer o login à mão. |
| 3 | `painel.bat` | Abre a interface visual recomendada para as próximas etapas. |
| 4 | `planejar.bat` | Alternativa pelo terminal: só confere e gera relatórios. |
| 5 | `conferir_tela.bat` | Abre o formulário do Ongsys e verifica os campos. Não preenche nada. |
| 6 | `testar_fornecedor.bat` | Digita fornecedores no campo de busca e mostra as sugestões. Não clica em nenhuma nem salva. |
| 7 | `executar.bat` | Alternativa pelo terminal: cria ou atualiza títulos após confirmação. |
| 8 | `auditar.bat` | Reabre os títulos salvos e compara com a planilha. |

Extras: `situacao.bat` mostra o que já foi lançado; `testar.bat` roda os testes
internos do programa. `resolver_pendencia.bat` altera somente o checkpoint
local depois que uma tentativa sem ID foi conferida manualmente como não
existente; ele não abre o Ongsys nem lança nada.

Dê **dois cliques** nos arquivos. Não é preciso digitar comandos.

---

## Segurança

- Sua senha nunca é digitada, lida ou guardada pelo programa. O login é sempre
  manual, feito por você na tela do Ongsys.
- A sessão fica apenas neste computador, na pasta `perfil_ongsys`.
- Nenhum dado é enviado para fora da máquina.
- O modo padrão é sempre conferência. Para lançar é preciso digitar `CONFIRMO`.
- Há limite de títulos e de valor total por execução, ajustáveis no
  `config.json`.
- O navegador fica visível, para você acompanhar cada passo.

**Nunca coloque usuário, senha ou código de acesso no `config.json`.**

---

## Como o programa decide o que lançar

### Linhas consideradas

Só entram as linhas cuja célula do **Fornecedor** está com a cor de pendente
(`FFFFFF99` por padrão). As linhas com a cor de já lançado (`FFFFFFCC`) ficam de
fora e aparecem no relatório de exclusões.

Se a planilha não tiver nenhuma linha com a cor de pendente, o programa **para**
e mostra as cores que realmente encontrou. Ele não assume que tudo é pendente,
porque isso relançaria a planilha inteira.

### Linhas descartadas

Ficam de fora, sempre com o motivo no relatório:

- transferência entre contas, recarga de cartão, aplicação, resgate, rendimento;
- recebimento de recursos e outras entradas financeiras;
- retenções lançadas em separado (IRRF, PCC e as demais configuradas);
- linhas sem fornecedor, sem valor de saída ou sem data válida;
- valores iguais ou menores que zero.

### Agrupamento

Linhas com o mesmo fornecedor, documento, vencimento, data de efetivação e conta
formam **um título com várias linhas de rateio**. Quatro linhas da mesma nota
viram um título com quatro rateios, não quatro títulos.

Centros de custo e categorias diferentes continuam como linhas separadas.

### Títulos bloqueados

Um título bloqueado aparece no relatório mas **não é lançado**. Bloqueiam:

- datas de emissão diferentes dentro do mesmo título;
- linha sem centro de custo;
- linha sem categoria detalhada;
- soma dos rateios diferente do valor do título;
- percentuais que não fecham 100%.

---

## Regras de preenchimento

### Código de despesa (Subprojeto) — a regra mais importante

O código da planilha precisa ser **exatamente** o código selecionado no Ongsys.

O programa compara o código inteiro, bloco a bloco. Uma opção com código
parecido é recusada: `2.2.1.05` **não** aceita `12.2.1.05 — Estacionamento` nem
`2.2.1.050`. Foi exatamente esse tipo de aproximação que trocou "Copa e Cozinha"
por "Estacionamento" em lançamentos anteriores.

Se o código não existir no Ongsys, o campo fica **vazio** e a pendência é
registrada. O programa nunca troca por outro código.

Depois de salvar, o título é reaberto e o código gravado é conferido de novo.

### Fornecedor

O programa procura pelo nome completo, sem acentos e pelo primeiro nome. Ele
compara o nome esperado com todos os resultados e **só aceita** um candidato
suficientemente parecido. Nunca escolhe o primeiro da lista.

Se dois candidatos empatarem, o título é pulado por ambiguidade. Se o cadastro
do Ongsys usar outro nome, informe a correspondência no `config.json`:

```json
"apelidos_fornecedores": {
  "NOME NA PLANILHA": "NOME EXATO NO ONGSYS"
}
```

### Tipo de despesa

Vale o **Título categoria** da planilha, não o ramo do fornecedor:

1. contém `SERVICOS_TOMADOS_DE_TERCEIROS` → Contratação de Serviço(s);
2. tributos e encargos → Pagamento de Impostos;
3. fornecedores de varejo configurados → Compra de Produto(s);
4. demais casos → Outros.

### Rateio

Cada linha é preenchida por inteiro **antes** de a próxima ser criada, na ordem
categoria → centro de custo → subprojeto → atividade → valor → percentual.

Os percentuais são calculados com precisão decimal e a última linha recebe o
ajuste para fechar exatamente 100%. Antes de salvar, o programa relê os valores
na tela e confere a soma; se não conciliar, não salva.

### Atividade

Se a planilha trouxer atividade, ela é selecionada. Se não trouxer, o campo fica
vazio — isso não impede o lançamento. Se a atividade informada não existir, o
campo fica vazio e a pendência é registrada.

### Documento repetido

Antes de abrir o formulário, o programa pesquisa no Ongsys por fornecedor,
documento, valor e datas. A consulta começa pelo **documento exato** na barra
Buscar da tabela; assim, um resultado realmente vazio significa título novo e
não exige confirmação manual. O filtro com lupa não é usado nessa etapa porque
também apresenta fornecedores inativos:

- **já existe igual** → não lança;
- **parecido** → para e pergunta;
- **documento repetido em outro título** → aplica variação.

Se o campo Buscar, o período ou a tabela falharem tecnicamente, a verificação
continua inconclusiva e o programa pede uma decisão. A ausência de resultado e
uma falha técnica nunca são tratadas como a mesma coisa.

Em cartões a variação preferida é `DD.MM_AA` (por exemplo `01.07_26`). Nos demais
casos usa `.1`, `.2`, `.3`. Depois da troca, o fornecedor é selecionado de novo,
porque o Ongsys costuma limpar esse campo.

---

## Relatórios

Ficam na pasta `relatorios`, com data e hora no nome:

- `pre_execucao_*.csv` — um título por linha, com datas, valores, rateios,
  centros de custo, códigos, atividades, bloqueios e alertas;
- `exclusoes_*.csv` — cada linha descartada e o motivo;
- `conferencia_*.xlsx` — os dois anteriores em um único arquivo do Excel;
- `resultado_*.csv` — o que foi criado, com ID do Ongsys e documento final;
- `auditoria_*.csv` — esperado × salvo, campo a campo.

Em caso de falha, uma imagem da tela é salva em `evidencias`, apenas neste
computador.

Depois de um salvamento confirmado, o programa tenta capturar o ID entre os
registros mais recentes, ordenando a lista por ID. Isso evita depender apenas
do índice textual do Ongsys, que pode demorar para reconhecer um documento
recém-criado. O candidato ainda precisa conferir em documento, valor, emissão e
vencimento; se não houver correspondência única, o ID fica pendente para a
auditoria em vez de ser inferido.

---

## Retomar depois de uma falha

O arquivo `checkpoint.json` guarda a situação de cada título: `PLANEJADO`,
`EM_EXECUCAO`, `SALVO_NAO_AUDITADO`, `CONCLUIDO`, `FALHA` e outros.

Um título que possa já existir no Ongsys **nunca** é recriado automaticamente.
Ele volta como pendência para você conferir com `auditar.bat`. Só entra como
`CONCLUIDO` depois de ser reaberto e conferido sem divergência.

Se a planilha mudar, o programa percebe pela impressão digital dos dados e avisa
antes de lançar.

Use `situacao.bat` para ver a situação atual.

---

## Quando o programa para tudo

Por segurança, a execução inteira é interrompida se:

- a sessão do Ongsys expirar;
- campos essenciais da tela sumirem (o site mudou);
- a conta pagadora não estiver disponível;
- os totais não conciliarem;
- aparecer CAPTCHA ou bloqueio de segurança.

---

## Configuração

Edite `config.json` (criado automaticamente a partir de `config.example.json`).

Campos mais usados:

| Campo | Para que serve |
|---|---|
| `contas_pagadoras` | Lista de contas. A conta é escolhida a cada execução. |
| `contas_cartao` | Números das contas que são cartão de crédito mas não têm a palavra CARTAO no nome (hoje `"65"` = PREPAGO - 0003 e `"66"` = PRE PAGO - 0004). O nome em `contas_pagadoras` deve ser igual ao do Ongsys. |
| `cores_pendentes` | Cor das linhas a lançar. |
| `cores_ja_lancadas` | Cor das linhas já tratadas. |
| `apelidos_fornecedores` | Nome da planilha → nome no Ongsys. |
| `fornecedores_varejo` | Viram "Compra de Produto(s)". |
| `ignorar_fornecedores` | Movimentações que não são conta a pagar. |
| `limite_titulos_por_execucao` | Máximo de títulos por rodada. |
| `limite_valor_total` | Valor total máximo por rodada. |
| `alerta_valor_titulo` | Acima disso, o título recebe aviso destacado. |
| `vencimento_igual_efetivacao` | Fornecedores em que vencimento segue a efetivação. |

Se a planilha tiver colunas com outros nomes, o programa tenta reconhecê-las
sozinho. Quando não conseguir, ele diz quais faltam e você pode indicar a coluna
pela letra.

---

## Se algo der errado

1. Rode `testar.bat`. Se os testes passarem, o problema está na tela do Ongsys
   ou nos dados, não nas regras.
2. Rode `conferir_tela.bat` para ver se os campos mudaram de nome.
3. Abra o último `resultado_*.csv` e leia a coluna `mensagem`.
4. Rode `situacao.bat` para ver se algum título ficou sem auditoria.

Guarde os relatórios e as imagens da pasta `evidencias` ao pedir ajuda. Nenhum
deles contém senha.

## Publicação no GitHub (LGPD)

O repositório guarda **só o programa**. Ficam apenas neste computador, e o
`.gitignore` impede que subam:

- `config.json` (contas, apelidos de fornecedores) — no GitHub vai só o
  `config.example.json`, com contas e números fictícios;
- `checkpoint.json` e cópias, `backups/`, `relatorios/`, `evidencias/`,
  `perfil_ongsys/` (sessão do Ongsys) e qualquer planilha, imagem ou documento;
- `HANDOVER.md`, `VERSAO_PILOTO.txt` e `PENDENCIAS_*.md`, que citam
  fornecedores e títulos reais.

Os testes usam nomes, CPF/CNPJ e IDs **fictícios** (CNPJ de exemplo tem os
dígitos repetidos, como `11.111.111/0001-11`).

Antes de qualquer envio, rode `verificar_publicacao.bat`. Ele lê os nomes reais
de fornecedores dos dados locais (sem gravá-los em lugar nenhum) e avisa se
encontrar nome real, pedaço de nome, CPF, CNPJ, e-mail, telefone ou ID de
título real em algum arquivo que iria para o GitHub.

Opcional: `instalar_ganchos_git.bat` faz o Git rodar essa conferência sozinho
a cada `git commit` e `git push`, recusando o envio se ela achar algo.
