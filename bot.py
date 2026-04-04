import os
import asyncio
import time
import aiosqlite
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ========== ТВОИ ДАННЫЕ ==========
TOKEN = "8767025443:AAEJ7q6kyqgRd3RsSW9-45t_2cJvp9bKPGw"
ADMIN_CHAT_ID = -1002489835677
CHANNEL_ID = -1002948114104
OWNER_ID = 6783350851
# =================================

DB_PATH = "bot.db"

COOLDOWN_SECONDS = 60
USER_COOLDOWN = {}
MAX_WARNS = 3

app = Client("bot", bot_token=TOKEN, api_id=6, api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e", in_memory=True)

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

async def write_log(msg_id: int, admin_username: str, admin_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO pub_logs (channel_id, post_message_id, admin_id, admin_username) VALUES (?,?,?,?)", (CHANNEL_ID, msg_id, admin_id, admin_username))
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Бан", callback_data=f"ban:{user_id}"), InlineKeyboardButton("✅ Разбан", callback_data=f"unban:{user_id}")],
        [InlineKeyboardButton("📢 Опубликовать", callback_data="publish")]
    ])

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("Отправь сообщение для публикации.")

@app.on_message(filters.command("mystats"))
async def mystats(client, message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM pub_logs WHERE admin_id=?", (message.from_user.id,))
        row = await cur.fetchone()
    await message.reply(f"📊 Постов: {row[0] if row else 0}")

@app.on_message(filters.command("topadmins"))
async def top_admins(client, message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT admin_username, COUNT(*) FROM pub_logs GROUP BY admin_id ORDER BY COUNT(*) DESC LIMIT 10")
        rows = await cur.fetchall()
    text = "🏆 Топ админов:\n\n"
    for i, (uname, total) in enumerate(rows, 1):
        text += f"{i}. {uname or '—'} — {total}\n"
    await message.reply(text)

@app.on_message(filters.command("warnlogs"))
async def warn_logs(client, message):
    if message.from_user.id != OWNER_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, reason, created_at FROM warn_logs ORDER BY id DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        await message.reply("📭 Логов нет")
        return
    text = ""
    for user_id, reason, dt in rows:
        text += f"👤 {user_id}\n⚠️ {reason or 'без причины'}\n🕒 {dt}\n\n"
    await message.reply(text)

@app.on_message(filters.chat(ADMIN_CHAT_ID) & filters.text & filters.reply)
async def admin_reply(client, message):
    user_id = await map_get(message.chat.id, message.reply_to_message.id)
    if not user_id:
        return
    try:
        await message.copy(user_id)
        await message.reply("✅ Ответ отправлен")
    except:
        await message.reply("❌ Ошибка")

@app.on_message(filters.private & ~filters.command(["start", "mystats", "topadmins", "warnlogs"]))
async def user_message(client, message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await message.reply("🚫 Вы заблокированы.")
        return
    if user_id != OWNER_ID:
        now = time.time()
        last = USER_COOLDOWN.get(user_id, 0)
        if now - last < COOLDOWN_SECONDS:
            await message.reply("⏳ Подожди")
            return
        USER_COOLDOWN[user_id] = now
    fwd = await message.forward(ADMIN_CHAT_ID)
    mod = await client.send_message(ADMIN_CHAT_ID, "Модерация:", reply_markup=moderation_kb(user_id), reply_to_message_id=fwd.id)
    await map_save(ADMIN_CHAT_ID, fwd.id, user_id)
    await map_save(ADMIN_CHAT_ID, mod.id, user_id)
    await message.reply("✅ Отправлено")

@app.on_callback_query()
async def handle_callback(client, call):
    if call.data.startswith("ban:"):
        user_id = int(call.data.split(":")[1])
        await ban_user_db(user_id)
        await call.answer("Бан", show_alert=True)
    elif call.data.startswith("unban:"):
        user_id = int(call.data.split(":")[1])
        await unban_user_db(user_id)
        await call.answer("Разбан", show_alert=True)
    elif call.data == "publish":
        msg = call.message.reply_to_message
        if msg:
            sent = await msg.copy(CHANNEL_ID)
            await write_log(sent.id, f"@{call.from_user.username}" if call.from_user.username else call.from_user.first_name, call.from_user.id)
            await call.answer("Опубликовано", show_alert=True)

@app.on_message(filters.chat(ADMIN_CHAT_ID) & filters.text & filters.regex(r"^(варн|-варн)"))
async def handle_warn(client, message):
    if message.from_user.id != OWNER_ID:
        return
    if not message.reply_to_message:
        await message.reply("❗ Ответь на сообщение админа")
        return
    target = message.reply_to_message.from_user
    user_id = target.id
    username = f"@{target.username}" if target.username else target.first_name
    if message.text.lower().startswith("варн"):
        reason = message.text.replace("варн", "").strip()
        count = await add_warn(user_id, message.from_user.id, reason)
        await message.reply(f"⚠️ {username} получил варн ({count}/{MAX_WARNS})\n📄 {reason or 'без причины'}")
        if count >= MAX_WARNS:
            try:
                await client.ban_chat_member(ADMIN_CHAT_ID, user_id)
                await client.unban_chat_member(ADMIN_CHAT_ID, user_id)
            except:
                pass
            await message.reply(f"🚫 {username} удалён из админ-чата")
    elif message.text.lower() == "-варн":
        count = await remove_warn(user_id)
        await message.reply(f"✅ У {username} осталось варнов: {count}")

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    print("Бот запущен!")
    loop.run_until_complete(app.run())

if __name__ == "__main__":
    main()
