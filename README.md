# Bot Financeiro

Controle de gastos pessoais por mensagem no Telegram. Você manda
`45,90 mercado` e o gasto entra categorizado num banco SQLite local.

Sem planilha para preencher, sem app para abrir, sem formulário. O registro
leva o mesmo tempo que mandar uma mensagem para alguém — que é justamente o
motivo de funcionar no longo prazo.

## Como funciona

```
Telegram  →  polling  →  parser  →  SQLite
                            ↓
                    categorização automática
                            ↓
                  botões de correção no chat
```

O bot busca as mensagens no Telegram por polling, extrai valor, descrição,
data e categoria do texto livre, e grava num arquivo `.db` local. Cada
lançamento volta como uma mensagem de confirmação com botões para corrigir
a categoria ou apagar.

Não há webhook, servidor ou URL pública envolvidos.

## Exemplos

| Mensagem | Interpretação |
|---|---|
| `45,90 mercado` | R$ 45,90 · Alimentação · hoje |
| `uber 23` | R$ 23,00 · Transporte · hoje |
| `gastei 80 no posto ontem` | R$ 80,00 · Transporte · ontem |
| `1.250,00 aluguel` | R$ 1.250,00 · Moradia · hoje |
| `paguei 350 de luz 05/08` | R$ 350,00 · Moradia · 05/08 |

O valor pode vir antes ou depois da descrição, com vírgula ou ponto decimal,
com ou sem `R$`. Palavras de ligação (`gastei`, `no`, `de`) são descartadas.

## Categorização

Duas camadas, nesta ordem:

1. **Regras aprendidas** — o que você já corrigiu manualmente
2. **Palavras-chave** — dicionário inicial com termos comuns

Quando você troca a categoria pelo botão, o bot guarda a associação na tabela
`regras`. Da próxima vez que a mesma palavra aparecer, ele acerta sozinho.
Quanto mais uso, menos correção.

## Comandos

| Comando | Função |
|---|---|
| `/hoje` | Gastos do dia |
| `/mes` | Resumo por categoria, com percentuais e média diária |
| `/ultimos` | Últimos 10 lançamentos |
| `/regras` | Associações aprendidas com suas correções |
| `/exportar` | Baixa tudo em CSV |

## Instalação

Requer Python 3.10 ou superior.

```bash
git clone https://github.com/SEU-USUARIO/bot-financeiro.git
cd bot-financeiro
pip install -r requirements.txt
```

### Configuração

1. Crie um bot com o [@BotFather](https://t.me/BotFather) e guarde o token
2. Descubra seu chat ID: mande uma mensagem ao bot e abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — o número está em
   `chat.id`
3. Copie o arquivo de exemplo e preencha:

```bash
cp .env.example .env
```

```
TELEGRAM_TOKEN=seu_token
CHAT_ID=seu_id
DB_PATH=financeiro.db
```

### Execução

```bash
python bot.py
```

O bot responde enquanto o processo estiver rodando. `Ctrl+C` para parar.

## Banco de dados

Arquivo SQLite criado automaticamente na primeira execução.

```sql
transacoes (id, data, valor, descricao, categoria, criado_em, raw)
regras     (palavra, categoria, usos)
```

A coluna `raw` guarda a mensagem original de cada lançamento, o que permite
reprocessar o histórico caso o parser mude.

Consulta direta:

```bash
sqlite3 financeiro.db "SELECT categoria, SUM(valor) FROM transacoes GROUP BY 1"
```

## Personalização

O dicionário `REGRAS_BASE`, no topo do `bot.py`, define as palavras-chave de
cada categoria. Adicionar os estabelecimentos que você frequenta — o mercado
do bairro, o posto, o delivery — reduz bastante a correção manual.

## Segurança

- Apenas o `CHAT_ID` configurado consegue usar o bot; mensagens de outros
  usuários são descartadas
- O `.env` e o arquivo `.db` estão no `.gitignore` e nunca são versionados
- Os dados ficam apenas na máquina onde o bot roda

## Limitações conhecidas

- O bot só responde enquanto o processo estiver ativo; gastos enviados com
  ele desligado são descartados na próxima execução
- Descrições que contêm números podem confundir a extração do valor
- Não há suporte a parcelamento ou lançamentos recorrentes

## Stack

Python · [python-telegram-bot](https://python-telegram-bot.org) · SQLite
