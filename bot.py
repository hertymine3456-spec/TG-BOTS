import asyncio
import aiosqlite
import time
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, Update
from fastapi import FastAPI, Request, Response
import uvicorn

# ================= НАСТРОЙКИ =================
TOKEN = "8767025443:AAEJ7q6kyqgRd3RsSW9-45t_2cJvp9bKPGw"
ADMIN_CHAT_ID = -1002489835677
CHANNEL_ID = -1002948114104
OWNER_ID = 6783350851
DB_PATH = "bot.db"

COOLDOWN_SECONDS = 60
USER_COOLDOWN = {}
MAX_WARNS = 3

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS msg_map (admin_chat_id INTEGER, admin_message_id INTEGER, user_id INTEGER, PRIMARY KEY (admin_chat_id, admin_message_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS pub_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, post_message_id INTEGER, admin_id INTEGER, admin_username TEXT, created_at TEXT DEFAULT (datetime('now','localtime')))")
        await db.execute("CREATE TABLE IF NOT EXISTS warns (user_id INTEGER PRIMARY KEY, count INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS warn_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, admin_id INTEGER, reason TEXT, created_at TEXT DEFAULT (datetime('now','localtime')))")
        await db.commit()

async def is_banned(user_id: int):
    if user_id == OWNER_ID:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,))
        return await cur.fetchone() is not None

async def ban_user_db(user_id: int):
    if user_id == OWNER_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO bans VALUES(?)", (user_id,))
        await db.commit()

async def unban_user_db(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
        await db.commit()

async def map_save(chat_id, msg_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO msg_map VALUES (?,?,?)", (chat_id, msg_id, user_id))
        await db.commit()

async def map_get(chat_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM msg_map WHERE admin_chat_id=? AND admin_message_id=?", (chat_id, msg_id))
        row = await cur.fetchone()
        return row[0] if row else None

async def write_log(msg_id: int, admin: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO pub_logs (channel_id, post_message_id, admin_id, admin_username) VALUES (?,?,?,?)", (CHANNEL_ID, msg_id, admin.id, f"@{admin.username}" if admin.username else None))
        await db.commit()

async def add_warn(user_id: int, admin_id: int, reason: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT count FROM warns WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row:
            count = row[0] + 1
            await db.execute("UPDATE warns SET count=? WHERE user_id=?", (count, user_id))
        else:
            count = 1
            await db.execute("INSERT INTO warns(user_id, count) VALUES (?,?)", (user_id, count))
        await db.execute("INSERT INTO warn_logs(user_id, admin_id, reason) VALUES (?,?,?)", (user_id, admin_id, reason))
        await db.commit()
    return count

async def remove_warn(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT count FROM warns WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return 0
        count = row[0]
        if count <= 1:
            await db.execute("DELETE FROM warns WHERE user_id=?", (user_id,))
            await db.commit()
            return 0
        await db.execute("UPDATE warns SET count=? WHERE user_id=?", (count - 1, user_id))
        await db.commit()
        return count - 1

def moderation_kb(user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Бан", callback_data=f"ban:{user_id}"), InlineKeyboardButton(text="✅ Разбан", callback_data=f"unban:{user_id}")],
        [InlineKeyboardButton(text="📢 Опубликовать", callback_data="publish")]
    ])

# ================= КОМАНДЫ =================
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("Отправь сообщение для публикации.")

@dp.message(Command("mystats"))
async def mystats(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM pub_logs WHERE admin_id=?", (message.from_user.id,))
        row = await cur.fetchone()
    await message.answer(f"📊 Постов: {row[0] if row else 0}")

@dp.message(Command("topadmins"))
async def top_admins(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT admin_username, COUNT(*) FROM pub_logs GROUP BY admin_id ORDER BY COUNT(*) DESC LIMIT 10")
        rows = await cur.fetchall()
    text = "🏆 Топ админов:\n\n"
    for i, (uname, total) in enumerate(rows, 1):
        text += f"{i}. {uname or '—'} — {total}\n"
    await message.answer(text)

@dp.message(Command("warnlogs"))
async def warn_logs(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, reason, created_at FROM warn_logs ORDER BY id DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("📭 Логов нет")
        return
    text = ""
    for user_id, reason, dt in rows:
        text += f"👤 {user_id}\n⚠️ {reason or 'без причины'}\n🕒 {dt}\n\n"
    await message.answer(text)

@dp.message(lambda m: m.chat.id == ADMIN_CHAT_ID and m.text and m.text.lower().startswith("варн"))
async def warn_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if not message.reply_to_message:
        await message.reply("❗ Ответь на сообщение админа")
        return
    target = message.reply_to_message.from_user
    user_id = target.id
    reason = message.text.replace("варн", "").strip()
    count = await add_warn(user_id, message.from_user.id, reason)
    username = f"@{target.username}" if target.username else target.full_name
    await message.answer(f"⚠️ {username} получил варн ({count}/{MAX_WARNS})\n📄 {reason or 'без причины'}")
    if count >= MAX_WARNS:
        try:
            await bot.ban_chat_member(ADMIN_CHAT_ID, user_id)
            await bot.unban_chat_member(ADMIN_CHAT_ID, user_id)
        except:
            pass
        await message.answer(f"🚫 {username} удалён из админ-чата")

@dp.message(lambda m: m.chat.id == ADMIN_CHAT_ID and m.text and m.text.lower() == "-варн")
async def unwarn(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if not message.reply_to_message:
        await message.reply("❗ Ответь на сообщение админа")
        return
    target = message.reply_to_message.from_user
    user_id = target.id
    count = await remove_warn(user_id)
    username = f"@{target.username}" if target.username else target.full_name
    await message.answer(f"✅ У {username} осталось варнов: {count}")

@dp.message(lambda m: m.chat.id == ADMIN_CHAT_ID and m.reply_to_message)
async def admin_reply(message: Message):
    user_id = await map_get(message.chat.id, message.reply_to_message.message_id)
    if not user_id:
        return
    try:
        await message.copy_to(user_id)
        await message.reply("✅ Ответ отправлен")
    except:
        await message.reply("❌ Ошибка")

@dp.message(lambda m: m.chat.type == "private")
async def user_message(message: Message):
    if message.text and message.text.startswith("/"):
        return
    user_id = message.from_user.id
    if await is_banned(user_id):
        await message.answer("🚫 Вы заблокированы.")
        return
    if user_id != OWNER_ID:
        now = time.time()
        last = USER_COOLDOWN.get(user_id, 0)
        if now - last < COOLDOWN_SECONDS:
            await message.answer("⏳ Подожди")
            return
        USER_COOLDOWN[user_id] = now
    fwd = await message.forward(ADMIN_CHAT_ID)
    mod = await bot.send_message(ADMIN_CHAT_ID, "Модерация:", reply_markup=moderation_kb(user_id), reply_to_message_id=fwd.message_id)
    await map_save(ADMIN_CHAT_ID, fwd.message_id, user_id)
    await map_save(ADMIN_CHAT_ID, mod.message_id, user_id)
    await message.answer("✅ Отправлено")

@dp.callback_query(lambda c: c.data.startswith("ban:"))
async def ban_cb(call: types.CallbackQuery):
    user_id = int(call.data.split(":")[1])
    await ban_user_db(user_id)
    await call.answer("Бан")

@dp.callback_query(lambda c: c.data.startswith("unban:"))
async def unban_cb(call: types.CallbackQuery):
    user_id = int(call.data.split(":")[1])
    await unban_user_db(user_id)
    await call.answer("Разбан")

@dp.callback_query(lambda c: c.data == "publish")
async def publish(call: types.CallbackQuery):
    msg = call.message.reply_to_message
    if not msg:
        return
    sent = await msg.copy_to(CHANNEL_ID)
    await write_log(sent.message_id, call.from_user)
    await call.answer("Опубликовано")

# ================= ВЕБХУК =================
@app.post("/webhook")
async def webhook(request: Request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"status": "Bot is running", "webhook": "/webhook"}

@app.on_event("startup")
async def on_startup():
    await init_db()
    port = int(os.environ.get("PORT", 8000))
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}/webhook"
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook set to {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()

# ================= ЗАПУСК =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
