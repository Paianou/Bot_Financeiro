"""
Bot Financeiro - Telegram + SQLite

Rode com:  python bot.py
Configure o arquivo .env antes (veja .env.example).
"""

import os
import re
import csv
import sqlite3
import logging
import unicodedata
from io import BytesIO
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "financeiro.db")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

CATEGORIAS = [
    "Alimentacao", "Transporte", "Moradia", "Saude",
    "Lazer", "Compras", "Educacao", "Outros",
]

# Palavras-chave iniciais. O bot aprende mais conforme voce corrige.
REGRAS_BASE = {
    "Alimentacao": ["mercado", "supermercado", "padaria", "ifood", "restaurante",
                    "lanche", "almoco", "jantar", "cafe", "pizza", "feira",
                    "acougue", "hortifruti", "rappi", "hamburguer", "sorvete"],
    "Transporte": ["uber", "99", "gasolina", "posto", "combustivel", "onibus",
                   "metro", "estacionamento", "pedagio", "taxi", "etanol",
                   "mecanico", "oleo", "pneu"],
    "Moradia": ["aluguel", "condominio", "luz", "agua", "gas", "internet",
                "energia", "iptu", "faxina", "diarista", "movel", "reforma"],
    "Saude": ["farmacia", "remedio", "medico", "dentista", "academia",
              "consulta", "exame", "plano", "psicologo", "terapia"],
    "Lazer": ["cinema", "bar", "cerveja", "show", "netflix", "spotify",
              "viagem", "jogo", "steam", "hotel", "balada", "teatro"],
    "Compras": ["roupa", "tenis", "amazon", "shopee", "mercadolivre",
                "presente", "eletronico", "celular", "shopping", "aliexpress"],
    "Educacao": ["curso", "livro", "faculdade", "escola", "material",
                 "mensalidade", "apostila", "udemy"],
}

# Palavras ignoradas ao montar a descricao
RUIDO = {"gastei", "paguei", "comprei", "no", "na", "em", "de", "do", "da",
         "com", "por", "pra", "para", "um", "uma", "o", "a", "reais", "conto",
         "contos", "pila", "hoje", "ontem", "anteontem"}


# --------------------------------------------------------------------
# Banco de dados
# --------------------------------------------------------------------

def conectar():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def criar_tabelas():
    with conectar() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transacoes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                data        TEXT NOT NULL,
                valor       REAL NOT NULL,
                descricao   TEXT NOT NULL,
                categoria   TEXT NOT NULL,
                criado_em   TEXT NOT NULL,
                raw         TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS regras (
                palavra   TEXT PRIMARY KEY,
                categoria TEXT NOT NULL,
                usos      INTEGER DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data ON transacoes(data)")
    log.info("Banco pronto: %s", os.path.abspath(DB_PATH))


def inserir(t: dict) -> int:
    with conectar() as conn:
        cur = conn.execute(
            "INSERT INTO transacoes (data, valor, descricao, categoria, criado_em, raw)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (t["data"].isoformat(), t["valor"], t["descricao"], t["categoria"],
             datetime.now().isoformat(timespec="seconds"), t["raw"]),
        )
        return cur.lastrowid


def buscar(tid: int):
    with conectar() as conn:
        return conn.execute("SELECT * FROM transacoes WHERE id = ?", (tid,)).fetchone()


def mudar_categoria(tid: int, categoria: str):
    with conectar() as conn:
        conn.execute("UPDATE transacoes SET categoria = ? WHERE id = ?", (categoria, tid))


def remover(tid: int):
    with conectar() as conn:
        conn.execute("DELETE FROM transacoes WHERE id = ?", (tid,))


def aprender(descricao: str, categoria: str):
    """Guarda a correcao para acertar da proxima vez."""
    palavra = primeira_palavra_util(descricao)
    if not palavra:
        return
    with conectar() as conn:
        conn.execute("""
            INSERT INTO regras (palavra, categoria) VALUES (?, ?)
            ON CONFLICT(palavra) DO UPDATE SET
                categoria = excluded.categoria,
                usos = usos + 1
        """, (palavra, categoria))
    log.info("Aprendido: %s -> %s", palavra, categoria)


def regras_aprendidas() -> dict:
    with conectar() as conn:
        return {r["palavra"]: r["categoria"]
                for r in conn.execute("SELECT palavra, categoria FROM regras")}


# --------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------

def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def primeira_palavra_util(texto: str) -> str:
    for p in normalizar(texto).split():
        if len(p) > 2 and p not in RUIDO and not p.isdigit():
            return p
    return ""


VALOR_RE = re.compile(
    r"(?:^|\s)"
    r"(\d{1,3}(?:\.\d{3})+,\d{2}"      # 1.250,00
    r"|\d+,\d{2}"                      # 45,90
    r"|\d+\.\d{2}"                     # 45.90
    r"|\d+)"                           # 45
    r"(?=\s|$)"
)


def extrair_data(texto: str):
    """Le referencias relativas e datas curtas. Devolve (data, texto_limpo)."""
    n = normalizar(texto)
    hoje = date.today()

    if "anteontem" in n:
        return hoje - timedelta(days=2), re.sub(r"anteontem", " ", texto, flags=re.I)
    if "ontem" in n:
        return hoje - timedelta(days=1), re.sub(r"ontem", " ", texto, flags=re.I)
    if "hoje" in n:
        return hoje, re.sub(r"hoje", " ", texto, flags=re.I)

    m = re.search(r"(?:^|\s)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?=\s|$)", texto)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = int(m.group(3) or hoje.year)
        if ano < 100:
            ano += 2000
        try:
            d = date(ano, mes, dia)
            if d <= hoje + timedelta(days=1):
                return d, texto.replace(m.group(0), " ")
        except ValueError:
            pass

    return hoje, texto


def categorizar(descricao: str) -> str:
    d = normalizar(descricao)
    palavras = d.split()

    aprendidas = regras_aprendidas()
    for p in palavras:
        if p in aprendidas:
            return aprendidas[p]

    for cat, chaves in REGRAS_BASE.items():
        for chave in chaves:
            if chave in palavras or (len(chave) > 4 and chave in d):
                return cat
    return "Outros"


def interpretar(texto: str):
    """Extrai valor, descricao, categoria e data. Devolve None se nao achar valor."""
    data, resto = extrair_data(texto)

    limpo = re.sub(r"r\$\s*", " ", resto, flags=re.I)
    limpo = re.sub(r"\s+", " ", limpo).strip()

    m = VALOR_RE.search(limpo)
    if not m:
        return None

    bruto = m.group(1)
    if "," in bruto:
        valor = float(bruto.replace(".", "").replace(",", "."))
    else:
        valor = float(bruto)

    if valor <= 0 or valor > 1_000_000:
        return None

    descricao = (limpo[:m.start()] + " " + limpo[m.end():])
    descricao = re.sub(r"\s+", " ", descricao).strip()
    descricao = " ".join(p for p in descricao.split()
                         if normalizar(p) not in RUIDO)

    if not descricao:
        return None

    return {
        "valor": valor,
        "descricao": descricao,
        "categoria": categorizar(descricao),
        "data": data,
        "raw": texto,
    }


# --------------------------------------------------------------------
# Formatacao
# --------------------------------------------------------------------

def moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def teclado_confirmacao(tid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Trocar categoria", callback_data=f"menu:{tid}"),
        InlineKeyboardButton("Apagar", callback_data=f"del:{tid}"),
    ]])


def teclado_categorias(tid: int) -> InlineKeyboardMarkup:
    linhas = []
    for i in range(0, len(CATEGORIAS), 2):
        linha = [InlineKeyboardButton(CATEGORIAS[i], callback_data=f"cat:{tid}:{i}")]
        if i + 1 < len(CATEGORIAS):
            linha.append(InlineKeyboardButton(CATEGORIAS[i + 1],
                                              callback_data=f"cat:{tid}:{i+1}"))
        linhas.append(linha)
    return InlineKeyboardMarkup(linhas)


# --------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Bot Financeiro</b>\n\n"
        "Mande um gasto assim:\n"
        "<code>45,90 mercado</code>\n"
        "<code>gastei 80 no posto ontem</code>\n"
        "<code>1.250,00 aluguel 05/08</code>\n\n"
        "<b>Comandos</b>\n"
        "/hoje - gastos de hoje\n"
        "/mes - resumo do mes por categoria\n"
        "/ultimos - ultimos 10 lancamentos\n"
        "/exportar - baixar tudo em CSV\n"
        "/regras - categorias aprendidas",
        parse_mode=ParseMode.HTML,
    )


async def cmd_hoje(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hoje = date.today().isoformat()
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT valor, descricao, categoria FROM transacoes"
            " WHERE data = ? ORDER BY id", (hoje,)
        ).fetchall()

    if not linhas:
        await update.message.reply_text("Nenhum gasto hoje.")
        return

    total = sum(r["valor"] for r in linhas)
    corpo = "\n".join(f"· {moeda(r['valor'])} — {r['descricao']}" for r in linhas)
    await update.message.reply_text(
        f"<b>Hoje</b>\n{corpo}\n\nTotal: <b>{moeda(total)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_mes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mes = date.today().strftime("%Y-%m")
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT categoria, SUM(valor) AS total, COUNT(*) AS qtd"
            " FROM transacoes WHERE data LIKE ?"
            " GROUP BY categoria ORDER BY total DESC", (f"{mes}%",)
        ).fetchall()

    if not linhas:
        await update.message.reply_text("Nenhum gasto neste mes.")
        return

    total = sum(r["total"] for r in linhas)
    corpo = "\n".join(
        f"· {r['categoria']}: {moeda(r['total'])} "
        f"({r['total']/total*100:.0f}% · {r['qtd']}x)"
        for r in linhas
    )
    dias = date.today().day
    await update.message.reply_text(
        f"<b>{date.today().strftime('%m/%Y')}</b>\n{corpo}\n\n"
        f"Total: <b>{moeda(total)}</b>\n"
        f"Media diaria: {moeda(total/dias)}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_ultimos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT id, data, valor, descricao, categoria FROM transacoes"
            " ORDER BY id DESC LIMIT 10"
        ).fetchall()

    if not linhas:
        await update.message.reply_text("Nada registrado ainda.")
        return

    corpo = "\n".join(
        f"· <code>#{r['id']}</code> {datetime.fromisoformat(r['data']).strftime('%d/%m')} "
        f"{moeda(r['valor'])} — {r['descricao']} ({r['categoria']})"
        for r in linhas
    )
    await update.message.reply_text(
        f"<b>Ultimos lancamentos</b>\n{corpo}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_regras(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    regras = regras_aprendidas()
    if not regras:
        await update.message.reply_text(
            "Nada aprendido ainda. Corrija a categoria de um gasto "
            "e eu passo a lembrar."
        )
        return
    corpo = "\n".join(f"· {p} → {c}" for p, c in sorted(regras.items()))
    await update.message.reply_text(
        f"<b>Aprendido com voce</b>\n{corpo}", parse_mode=ParseMode.HTML
    )


async def cmd_exportar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT data, valor, descricao, categoria, criado_em, raw"
            " FROM transacoes ORDER BY data, id"
        ).fetchall()

    if not linhas:
        await update.message.reply_text("Nada para exportar.")
        return

    buffer = BytesIO()
    texto = []
    escritor = csv.writer(_Sink(texto))
    escritor.writerow(["data", "valor", "descricao", "categoria", "criado_em", "raw"])
    for r in linhas:
        escritor.writerow([r["data"], r["valor"], r["descricao"],
                           r["categoria"], r["criado_em"], r["raw"]])
    buffer.write("".join(texto).encode("utf-8-sig"))
    buffer.seek(0)
    buffer.name = f"gastos_{date.today().isoformat()}.csv"

    await update.message.reply_document(
        document=buffer,
        filename=buffer.name,
        caption=f"{len(linhas)} lancamentos",
    )


class _Sink:
    """Coletor simples para o csv.writer escrever em memoria."""
    def __init__(self, destino):
        self.destino = destino

    def write(self, s):
        self.destino.append(s)


async def receber_gasto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    t = interpretar(texto)

    if not t:
        await update.message.reply_text(
            "Nao encontrei um valor. Tente:\n<code>45,90 mercado</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    tid = inserir(t)
    quando = t["data"].strftime("%d/%m")
    await update.message.reply_text(
        f"<b>{moeda(t['valor'])}</b>\n"
        f"{t['descricao']}\n"
        f"<i>{t['categoria']}</i> · {quando} · <code>#{tid}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=teclado_confirmacao(tid),
    )


async def tratar_botao(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    partes = q.data.split(":")
    acao, tid = partes[0], int(partes[1])

    if acao == "menu":
        await q.edit_message_reply_markup(reply_markup=teclado_categorias(tid))
        await q.answer("Escolha a categoria")

    elif acao == "cat":
        nova = CATEGORIAS[int(partes[2])]
        registro = buscar(tid)
        if not registro:
            await q.answer("Lancamento nao existe mais")
            return
        mudar_categoria(tid, nova)
        aprender(registro["descricao"], nova)
        await q.edit_message_text(
            f"<b>{moeda(registro['valor'])}</b>\n"
            f"{registro['descricao']}\n"
            f"<i>{nova}</i> · "
            f"{datetime.fromisoformat(registro['data']).strftime('%d/%m')} · "
            f"<code>#{tid}</code>",
            parse_mode=ParseMode.HTML,
        )
        await q.answer(f"Alterado para {nova}")

    elif acao == "del":
        remover(tid)
        await q.edit_message_text("Lancamento apagado.")
        await q.answer("Apagado")


async def erro(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Erro no update %s", update, exc_info=ctx.error)


# --------------------------------------------------------------------

def main():
    if not TOKEN:
        raise SystemExit("Falta TELEGRAM_TOKEN no arquivo .env")
    if not CHAT_ID:
        raise SystemExit("Falta CHAT_ID no arquivo .env")

    criar_tabelas()

    app = Application.builder().token(TOKEN).build()
    dono = filters.Chat(chat_id=CHAT_ID)

    app.add_handler(CommandHandler(["start", "ajuda"], cmd_start, filters=dono))
    app.add_handler(CommandHandler("hoje", cmd_hoje, filters=dono))
    app.add_handler(CommandHandler("mes", cmd_mes, filters=dono))
    app.add_handler(CommandHandler("ultimos", cmd_ultimos, filters=dono))
    app.add_handler(CommandHandler("regras", cmd_regras, filters=dono))
    app.add_handler(CommandHandler("exportar", cmd_exportar, filters=dono))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & dono, receber_gasto))
    app.add_handler(CallbackQueryHandler(tratar_botao))
    app.add_error_handler(erro)

    log.info("Bot no ar. Ctrl+C para parar.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
