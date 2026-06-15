import os
import sqlite3
import json
from datetime import datetime
import anthropic
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- Database ---
def init_db():
    conn = sqlite3.connect("finance.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount REAL,
            category TEXT,
            description TEXT,
            date TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_transaction(user_id, type_, amount, category, description):
    conn = sqlite3.connect("finance.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO transactions (user_id, type, amount, category, description, date) VALUES (?,?,?,?,?,?)",
        (user_id, type_, amount, category, description, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()

def get_summary(user_id):
    conn = sqlite3.connect("finance.db")
    c = conn.cursor()
    c.execute("SELECT type, SUM(amount) FROM transactions WHERE user_id=? GROUP BY type", (user_id,))
    rows = c.fetchall()
    c.execute("SELECT type, category, SUM(amount) FROM transactions WHERE user_id=? GROUP BY type, category", (user_id,))
    cats = c.fetchall()
    conn.close()
    return rows, cats

def get_recent(user_id, limit=10):
    conn = sqlite3.connect("finance.db")
    c = conn.cursor()
    c.execute(
        "SELECT type, amount, category, description, date FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

def clear_user_data(user_id):
    conn = sqlite3.connect("finance.db")
    c = conn.cursor()
    c.execute("DELETE FROM transactions WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# --- Claude: xabarni tahlil qilish ---
def parse_transaction(text):
    prompt = f"""Foydalanuvchi moliyaviy xabar yozdi. Uni tahlil qil va JSON formatida qaytар.

Xabar: "{text}"

Agar bu xarajat yoki daromad bo'lsa, quyidagi JSON qaytар:
{{
  "type": "xarajat" yoki "daromad",
  "amount": raqam (faqat son),
  "category": kategoriya (masalan: oziq-ovqat, transport, maosh, ijara, o'yin-kulgi, boshqa),
  "description": qisqa tavsif
}}

Agar bu moliyaviy savol yoki maslahat so'rovi bo'lsa:
{{"type": "savol"}}

Agar bu butunlay boshqa narsa bo'lsa:
{{"type": "boshqa"}}

Faqat JSON qaytар, boshqa hech narsa yozma."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    try:
        return json.loads(response.content[0].text.strip())
    except Exception:
        return {"type": "boshqa"}

def get_advice(user_id, question):
    rows, cats = get_summary(user_id)
    summary_text = ""
    if rows:
        for r in rows:
            summary_text += f"- {r[0]}: {r[1]:,.0f} so'm\n"
    if cats:
        summary_text += "\nKategoriyalar bo'yicha:\n"
        for c in cats:
            summary_text += f"  {c[0]} / {c[1]}: {c[2]:,.0f} so'm\n"

    prompt = f"""Sen moliyaviy maslahatchi botsan. Faqat o'zbek tilida javob ber.

Foydalanuvchining moliyaviy holati:
{summary_text if summary_text else "Hali hech qanday ma'lumot yo'q."}

Savol: {question}

Qisqa, aniq va amaliy maslahat ber."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# --- Handlers ---
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📊 Hisobot", "📋 So'nggi 10 ta"], ["💡 Maslahat", "🗑 Tozalash"]],
    resize_keyboard=True
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men sizning shaxsiy moliyaviy yordamchingizman 💰\n\n"
        "Quyidagilarni yozishingiz mumkin:\n"
        "• *Xarajat:* \"bugun 50000 so'm ovqatga xarajat qildim\"\n"
        "• *Daromad:* \"1500000 so'm maosh oldim\"\n"
        "• *Savol:* \"qanday tejash mumkin?\"\n\n"
        "Yoki tugmalardan foydalaning 👇",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Tugmalar
    if text == "📊 Hisobot":
        await send_report(update, user_id)
        return
    if text == "📋 So'nggi 10 ta":
        await send_recent(update, user_id)
        return
    if text == "💡 Maslahat":
        await update.message.reply_text("Moliyaviy savolingizni yozing, maslahat beraman.")
        return
    if text == "🗑 Tozalash":
        clear_user_data(user_id)
        await update.message.reply_text("Barcha ma'lumotlar o'chirildi.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    data = parse_transaction(text)

    if data["type"] in ("xarajat", "daromad"):
        save_transaction(user_id, data["type"], data["amount"], data["category"], data["description"])
        icon = "📉" if data["type"] == "xarajat" else "📈"
        await update.message.reply_text(
            f"{icon} *{data['type'].capitalize()}* saqlandi!\n\n"
            f"💰 Miqdor: *{data['amount']:,.0f} so'm*\n"
            f"📁 Kategoriya: {data['category']}\n"
            f"📝 {data['description']}",
            parse_mode="Markdown"
        )
    elif data["type"] == "savol":
        advice = get_advice(user_id, text)
        await update.message.reply_text(f"💡 {advice}")
    else:
        await update.message.reply_text(
            "Tushunmadim. Iltimos, xarajat, daromad yoki moliyaviy savol yozing.\n\n"
            "Masalan: \"30000 so'm taksiga xarajat qildim\""
        )

async def send_report(update: Update, user_id: int):
    rows, cats = get_summary(user_id)
    if not rows:
        await update.message.reply_text("Hali hech qanday ma'lumot yo'q.")
        return

    total_income = 0
    total_expense = 0
    for r in rows:
        if r[0] == "daromad":
            total_income = r[1]
        elif r[0] == "xarajat":
            total_expense = r[1]

    balance = total_income - total_expense
    balance_icon = "✅" if balance >= 0 else "⚠️"

    msg = "📊 *Moliyaviy Hisobot*\n\n"
    msg += f"📈 Daromad:  *{total_income:,.0f} so'm*\n"
    msg += f"📉 Xarajat:  *{total_expense:,.0f} so'm*\n"
    msg += f"{balance_icon} Balans:  *{balance:,.0f} so'm*\n\n"

    if cats:
        msg += "📁 *Kategoriyalar:*\n"
        for c in cats:
            icon = "📈" if c[0] == "daromad" else "📉"
            msg += f"  {icon} {c[1]}: {c[2]:,.0f} so'm\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

async def send_recent(update: Update, user_id: int):
    rows = get_recent(user_id)
    if not rows:
        await update.message.reply_text("Hali hech qanday ma'lumot yo'q.")
        return

    msg = "📋 *So'nggi operatsiyalar:*\n\n"
    for r in rows:
        icon = "📈" if r[0] == "daromad" else "📉"
        msg += f"{icon} {r[4]}  |  {r[2]}  |  *{r[1]:,.0f} so'm*  — {r[3]}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

# --- Main ---
init_db()
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
print("Finance bot ishga tushdi! ✅")
app.run_polling()
