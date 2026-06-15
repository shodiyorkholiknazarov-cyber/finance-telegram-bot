import asyncio
import logging
import sqlite3
import json
import os
from datetime import datetime, date
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import anthropic

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def init_db():
    conn = sqlite3.connect("sprint.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, due_date TEXT,
        priority TEXT DEFAULT 'normal', status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person TEXT NOT NULL, topic TEXT,
        meeting_date TEXT, meeting_time TEXT,
        status TEXT DEFAULT 'planned', created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS finances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL NOT NULL, category TEXT NOT NULL,
        description TEXT, type TEXT NOT NULL,
        transaction_date TEXT DEFAULT CURRENT_DATE,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect("sprint.db")

def build_context():
    conn = get_db()
    c = conn.cursor()
    today = date.today().isoformat()
    c.execute("SELECT title, due_date, priority FROM tasks WHERE status='pending' ORDER BY due_date ASC LIMIT 20")
    tasks = c.fetchall()
    c.execute("SELECT person, topic, meeting_date, meeting_time FROM meetings WHERE status='planned' ORDER BY meeting_date LIMIT 10")
    meetings = c.fetchall()
    current_month = today[:7]
    c.execute("""SELECT type, category, SUM(amount) FROM finances 
                 WHERE transaction_date LIKE ? GROUP BY type, category""", (f"{current_month}%",))
    finances = c.fetchall()
    conn.close()
    return f"""Bugun: {today}
VAZIFALAR: {json.dumps([{"vazifa": t[0], "muddat": t[1], "muhimlik": t[2]} for t in tasks], ensure_ascii=False)}
UCHRASHUVLAR: {json.dumps([{"kim": m[0], "mavzu": m[1], "sana": m[2], "vaqt": m[3]} for m in meetings], ensure_ascii=False)}
MOLIYA: {json.dumps([{"tur": f[0], "kategoriya": f[1], "jami": f[2]} for f in finances], ensure_ascii=False)}"""

def ask_claude(user_message, context):
    system = """Sen Shodiyor uchun shaxsiy AI assistantsan. O'zbek tilida do'stona javob ber.
Vazifalar, uchrashuvlar, moliya va har qanday savolga yordam ber.
Agar vazifa qo'shish kerak bo'lsa javob oxiriga qo'sh:
ACTION: {"type": "add_task", "title": "...", "due_date": "YYYY-MM-DD", "priority": "high/normal/low"}
Agar uchrashuv qo'shish kerak:
ACTION: {"type": "add_meeting", "person": "...", "topic": "...", "meeting_date": "YYYY-MM-DD", "meeting_time": "HH:MM"}
Agar xarajat/daromad:
ACTION: {"type": "add_finance", "amount": 0, "category": "...", "description": "...", "fin_type": "expense/income"}
Agar vazifa bajarildi:
ACTION: {"type": "complete_task", "title": "..."}
Joriy ma'lumotlar:\n""" + context

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

def execute_action(response_text):
    if "ACTION:" not in response_text:
        return response_text, ""
    parts = response_text.split("ACTION:", 1)
    clean = parts[0].strip()
    action_str = parts[1].strip()
    try:
        action = json.loads(action_str)
        conn = get_db()
        c = conn.cursor()
        result = ""
        if action["type"] == "add_task":
            c.execute("INSERT INTO tasks (title, due_date, priority) VALUES (?,?,?)",
                     (action.get("title"), action.get("due_date"), action.get("priority", "normal")))
            result = f"\n✅ Vazifa qo'shildi: *{action.get('title')}*"
        elif action["type"] == "add_meeting":
            c.execute("INSERT INTO meetings (person, topic, meeting_date, meeting_time) VALUES (?,?,?,?)",
                     (action.get("person"), action.get("topic"), action.get("meeting_date"), action.get("meeting_time")))
            result = f"\n📅 Uchrashuv qo'shildi: *{action.get('person')}* bilan"
        elif action["type"] == "add_finance":
            c.execute("INSERT INTO finances (amount, category, description, type) VALUES (?,?,?,?)",
                     (action.get("amount"), action.get("category"), action.get("description"), action.get("fin_type")))
            emoji = "💸" if action.get("fin_type") == "expense" else "💰"
            result = f"\n{emoji} Yozildi: *{action.get('amount'):,.0f}* so'm — {action.get('category')}"
        elif action["type"] == "complete_task":
            c.execute("UPDATE tasks SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE title LIKE ? AND status='pending'",
                     (f"%{action.get('title')}%",))
            result = f"\n✔️ Bajarildi: *{action.get('title')}*"
        conn.commit()
        conn.close()
        return clean, result
    except:
        return response_text, ""

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if ALLOWED_USER_ID != 0 and message.from_user.id != ALLOWED_USER_ID:
        await message.answer("Ruxsat yo'q.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Bugungi rejam", callback_data="today"),
         InlineKeyboardButton(text="📊 Moliya", callback_data="finance")],
        [InlineKeyboardButton(text="✅ Vazifalar", callback_data="tasks"),
         InlineKeyboardButton(text="📅 Uchrashuvlar", callback_data="meetings")],
    ])
    await message.answer("Salom, Shodiyor! 👋\nXohlagan narsani yoz — yordam beraman!", reply_markup=keyboard)

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    await callback.answer()
    queries = {
        "today": "Bugun nima qilishim kerak? Vazifalar va uchrashuvlarni ko'rsat.",
        "finance": "Bu oyning moliyaviy hisobini ko'rsat.",
        "tasks": "Barcha pending vazifalarni muhimlik bo'yicha ko'rsat.",
        "meetings": "Rejalashtirilgan uchrashuvlarni ko'rsat."
    }
    query = queries.get(callback.data, "Salom")
    context = build_context()
    response = ask_claude(query, context)
    clean, action = execute_action(response)
    await callback.message.answer(clean + action, parse_mode="Markdown")

@dp.message(F.text)
async def handle_message(message: Message):
    if ALLOWED_USER_ID != 0 and message.from_user.id != ALLOWED_USER_ID:
        await message.answer("Ruxsat yo'q.")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    context = build_context()
    response = ask_claude(message.text, context)
    clean, action = execute_action(response)
    await message.answer(clean + action, parse_mode="Markdown")

async def main():
    init_db()
    logging.info("Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
