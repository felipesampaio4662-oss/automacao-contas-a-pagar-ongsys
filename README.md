# Automação de Contas a Pagar no Ongsys

Robô em Python que lê a planilha de pagamentos do financeiro, confere cada
lançamento e cadastra os títulos em **Contas a Pagar** no ERP Ongsys, com
auditoria campo a campo e travas que impedem qualquer ação financeira.

> Projeto de portfólio. Todos os nomes, CPF/CNPJ, contas, cartões e valores
> deste repositório são **fictícios**. Não há dados reais de fornecedores nem
> da organização onde a ferramenta foi usada. Projeto independente, sem
> vínculo com a empresa que desenvolve o Ongsys.

## O problema

O lançamento de contas a pagar era feito à mão, título por título: fornecedor,
datas, valor, documento, conta pagadora e o rateio entre centros de custo e
projetos. Cerca de 400 lançamentos por mês, uns 3 minutos e meio cada, com
risco de erro de digitação e sem conferência sistemática depois de salvar.

## A solução

O robô assume a **digitação**. A **decisão** continua com as pessoas.

1. **Planejar** — lê a planilha, aplica as regras (período, conta, exclusões,
   agrupamento de rateio) e gera um relatório de conferência. Não abre o ERP.
2. **Executar** — só começa depois de duas confirmações digitadas pelo
   operador (`SIM` e, com a fila completa na tela, `CONFIRMO`).
3. **Auditar** — reabre cada título criado e compara todos os campos com a
   planilha. Somente leitura.
4. **Revisar pendências** — o que o robô não resolveu com segurança aparece
   num painel para correção manual. Nada fica escondido.

Tudo roda por um painel em Tkinter ou por atalhos `.bat`, pensado para quem
não programa.

## Segurança

- **O robô nunca dá baixa, paga, envia remessa, envia para aprovação ou
  exclui.** Um disjuntor recusa qualquer clique ou campo ligado a essas ações e
  interrompe a execução inteira.
- **Avisos de exclusão nunca são confirmados**, só dispensados.
- **Nada é lançado duas vezes:** um controle local (checkpoint) bloqueia
  qualquer título que possa já existir; resultado incerto fica travado até uma
  auditoria confirmar.
- **Fornecedor errado é recusado:** nomes que divergem (sobrenomes em comum,
  siglas de iniciais diferentes) não são aceitos, mesmo com semelhança alta.
- **Só grava no endereço do ERP**, em conexão cifrada, e confere o domínio
  antes de cada salvamento.
- **Navegador sempre visível**; o login é manual e a senha nunca é guardada.
- **Recusas conhecidas do ERP** (ex.: período contábil fechado) viram falha
  explicada, que pode ser corrigida e relançada.
- **Rastreabilidade:** cópia do checkpoint antes de cada operação, histórico
  de quem executou e com qual planilha (impressão digital SHA-256), relatórios
  de conferência, resultado e auditoria, e captura de tela em toda falha.
- **Guarda temporária (LGPD):** capturas e relatórios antigos podem ser
  apagados por prazo, com confirmação digitada; a memória de lançamentos nunca.
- **Publicação segura:** `verificar_publicacao.py` procura CPF, CNPJ, e-mail,
  telefone, IDs e nomes reais (lidos na hora dos dados locais, sem gravá-los)
  antes de qualquer envio ao GitHub.

## Resultados (estimativa)

| | Manual | Com a automação |
|---|---|---|
| Tempo humano por mês (~400 títulos) | ~23 h | ~4 h (preparo, conferência e exceções) |
| Tempo do robô por título | — | ~75 s, já com a auditoria |
| Conferência campo a campo depois de salvar | não havia | 100% dos títulos |

Ganho estimado de cerca de **19 horas por mês**, sem custo de licença.

## Tecnologia

- Python 3.13, [Playwright](https://playwright.dev/python/) (Chromium),
  openpyxl, Tkinter
- **707 testes automatizados** com pytest, incluindo testes específicos das
  travas de segurança e regressões de cada falha encontrada no piloto

```
ongsys/
  regras.py        planejamento: período, cores, exclusões, agrupamento, rateio
  planilha.py      leitura da planilha
  navegador.py     preenchimento e leitura do ERP (Playwright)
  seletores.py     seletores da tela e disjuntor de ações proibidas
  auditoria.py     comparação campo a campo
  checkpoint.py    memória de lançamentos (anti-duplicidade)
  cli.py           comandos: planejar, executar, auditar, estado, limpar...
  painel.py        interface Tkinter
testes/            suíte pytest
docs/GUIA_DE_USO.md  guia detalhado de operação e das regras
```

## Como rodar os testes

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Para usar de verdade é preciso uma conta no Ongsys, copiar
`config.example.json` para `config.json` e ajustar contas, categorias e
apelidos de fornecedores. Veja o [guia de uso](docs/GUIA_DE_USO.md).

## Autor

Felipe Sampaio
