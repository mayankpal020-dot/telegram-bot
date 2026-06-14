import os, json, sqlite3, asyncio, logging
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
from flask import Flask
from threading import Thread

load_dotenv()
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

Thread(target=run_web, daemon=True).start()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")

DB_FILE = "bot.db"
COOLDOWN_SECONDS = 20
user_cooldowns = {}

logging.basicConfig(level=logging.INFO)

def db():
    return sqlite3.connect(DB_FILE)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today():
    return date.today().strftime("%Y-%m-%d")

def is_owner(user_id):
    return user_id == OWNER_ID

def is_admin(user_id):
    if is_owner(user_id):
        return True

    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM admins WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()

    return row is not None
    
async def no_auth(update):
    await update.message.reply_text("❌ You are not authorized.")

async def owner_only(update):
    await update.message.reply_text("❌ Only Owner can use this command.")

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS qa(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT UNIQUE,
        answer TEXT,
        created_at TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS subscriptions(
        user_id INTEGER PRIMARY KEY,
        expires_at TEXT,
        created_at TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS answer_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        question TEXT,
        created_at TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS reminder_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        reminder_type TEXT,
        sent_date TEXT,
        UNIQUE(user_id, reminder_type, sent_date)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT,
        last_active TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS banned_users(
        user_id INTEGER PRIMARY KEY,
        banned_at TEXT
    )""")
    
    cur.execute("""CREATE TABLE IF NOT EXISTS admins(
        user_id INTEGER PRIMARY KEY,
        added_at TEXT
    )""")

    con.commit()
    con.close()

def save_user(update: Update):
    user = update.effective_user
    if not user:
        return

    con = db()
    cur = con.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO users(user_id, username, first_name, joined_at, last_active)
        VALUES(
            ?,
            ?,
            ?,
            COALESCE((SELECT joined_at FROM users WHERE user_id=?), ?),
            ?
        )
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        user.id,
        now(),
        now()
    ))
    con.commit()
    con.close()

def is_banned(user_id):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM banned_users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return row is not None

def active_sub(user_id):
    if is_owner(user_id):
        return True

    con = db()
    cur = con.cursor()
    cur.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()

    if not row:
        return False

    return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") > datetime.now()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update)
    uid = update.effective_user.id

    if is_banned(uid):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    text = (
        "🤖 Welcome!\n\n"
        "/myid - Apna user ID dekho\n"
        "/status - Subscription status dekho\n\n"
        f"Subscription ke liye contact: {ADMIN_USERNAME}"
    )

    if is_owner(uid):
        text += (
            "\n\n👑 Owner Commands:\n"
            "/add question | answer\n"
            "/update question | answer\n"
            "/del question\n"
            "/list\n"
            "/totalqa\n"
            "/grant user_id days\n"
            "/revoke user_id\n"
            "/addadmin user_id\n"
            "/removeadmin user_id\n"
            "/admins\n"
            "/overview\n"
            "/export\n"
            "/import\n"
            "/stats today\n"
            "/stats 7days\n"
            "/userstats user_id today\n"
            "/userstats user_id 7days\n"
            "/top today\n"
            "/top 7days\n"
            "/totalusers\n"
            "/active today\n"
            "/active 7days\n"
            "/newusers\n"
            "/broadcast message\n"
            "/ban user_id\n"
            "/unban user_id\n"
            "/banlist"
        )
    elif is_admin(uid):
        text += (
            "\n\n🛡️ Admin Commands:\n"
            "/add question | answer\n"
            "/update question | answer\n"
            "/del question\n"
            "/list"
        )

    await update.message.reply_text(text)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update)
    await update.message.reply_text(f"🆔 Your ID: {update.effective_user.id}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update)
    uid = update.effective_user.id

    if is_owner(uid):
        await update.message.reply_text("👑 You are Owner. Subscription required nahi hai.")
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (uid,))
    row = cur.fetchone()
    con.close()

    if not row:
        await update.message.reply_text(f"❌ No active subscription.\nContact: {ADMIN_USERNAME}")
        return

    exp = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")

    if exp > datetime.now():
        await update.message.reply_text(f"✅ Active\nExpires: {exp.strftime('%d-%m-%Y %H:%M')}")
    else:
        await update.message.reply_text(f"❌ Expired\nExpired: {exp.strftime('%d-%m-%Y %H:%M')}")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await no_auth(update)
        return

    text = update.message.text.replace("/add", "", 1).strip()

    if "|" not in text:
        await update.message.reply_text("Use:\n/add question | answer")
        return

    q, a = text.split("|", 1)
    q = " ".join(q.strip().lower().split())
    a = a.strip()

    con = db()
    cur = con.cursor()
    cur.execute("SELECT answer FROM qa WHERE LOWER(TRIM(question))=?", (q,))
    old = cur.fetchone()

    if old:
        con.close()
        await update.message.reply_text(
            f"⚠️ Question already saved.\n\n"
            f"Question: {q}\n\n"
            f"Current Answer:\n{old[0]}\n\n"
            f"Answer change karne ke liye use karo:\n/update {q} | new answer"
        )
        return

    cur.execute("INSERT INTO qa(question, answer, created_at) VALUES(?,?,?)", (q, a, now()))
    con.commit()
    con.close()

    await update.message.reply_text("✅ New Q&A saved.")

async def update_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await no_auth(update)
        return

    text = update.message.text.replace("/update", "", 1).strip()

    if "|" not in text:
        await update.message.reply_text("Use:\n/update question | new answer")
        return

    q, a = text.split("|", 1)
    q = q.strip().lower()
    a = a.strip()

    con = db()
    cur = con.cursor()
    cur.execute("SELECT id FROM qa WHERE question=?", (q,))
    exists = cur.fetchone()

    if not exists:
        con.close()
        await update.message.reply_text("❌ Question not found. Pehle /add se save karo.")
        return

    cur.execute("UPDATE qa SET answer=?, created_at=? WHERE question=?", (a, now(), q))
    con.commit()
    con.close()

    await update.message.reply_text("✅ Answer updated.")

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await no_auth(update)
        return

    q = update.message.text.replace("/del", "", 1).strip().lower()

    if not q:
        await update.message.reply_text("Use:\n/del question")
        return

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM qa WHERE question=?", (q,))
    deleted = cur.rowcount
    con.commit()
    con.close()

    await update.message.reply_text("✅ Deleted." if deleted else "❌ Question not found.")

async def make_list_message(page=1):
    limit = 20
    offset = (page - 1) * limit

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM qa")
    total = cur.fetchone()[0]

    cur.execute("SELECT question FROM qa ORDER BY id ASC LIMIT ? OFFSET ?", (limit, offset))
    rows = cur.fetchall()
    con.close()

    total_pages = max(1, (total + limit - 1) // limit)

    msg = f"📚 Questions List\nPage {page}/{total_pages}\nTotal: {total}\n\n"

    for i, (question,) in enumerate(rows, start=offset + 1):
        msg += f"{i}. {question}\n\n"

    buttons = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"list_{page-1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{page+1}"))
    if row:
        buttons.append(row)

    return msg, InlineKeyboardMarkup(buttons)

async def list_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    msg, keyboard = await make_list_message(1)
    sent_msg=await update.message.reply_text(msg, reply_markup=keyboard)
    asyncio.create_task(
    delete_after_20min(
        context,
        update.effective_chat.id,
        sent_msg.message_id
    )
)

async def list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(query.from_user.id):
        await query.edit_message_text("❌ Only Owner can use this.")
        return

    page = int(query.data.replace("list_", ""))
    msg, keyboard = await make_list_message(page)

    await query.edit_message_text(msg, reply_markup=keyboard)

async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 2:
        await update.message.reply_text("Use:\n/grant user_id days")
        return

    try:
        uid = int(context.args[0])
        days = int(context.args[1])
    except:
        await update.message.reply_text("❌ Invalid input.")
        return

    exp = datetime.now() + timedelta(days=days)

    con = db()
    cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO subscriptions(user_id, expires_at, created_at) VALUES(?,?,?)",
                (uid, exp.strftime("%Y-%m-%d %H:%M:%S"), now()))
    con.commit()
    con.close()

    await update.message.reply_text(f"✅ Subscription granted.\nUser: {uid}\nExpires: {exp.strftime('%d-%m-%Y %H:%M')}")

    try:
        await context.bot.send_message(uid, f"✅ Your subscription is active.\nExpires: {exp.strftime('%d-%m-%Y %H:%M')}")
    except:
        pass

async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use:\n/revoke user_id")
        return

    uid = int(context.args[0])

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
    con.commit()
    con.close()

    await update.message.reply_text("✅ Subscription revoked.")

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use:\n/addadmin user_id")
        return

    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid user_id.")
        return

    con = db()
    cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO admins(user_id, added_at) VALUES(?,?)", (uid, now()))
    con.commit()
    con.close()

    await update.message.reply_text(f"✅ Admin added.\nUser ID: {uid}")

async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use:\n/removeadmin user_id")
        return

    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid user_id.")
        return

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    con.commit()
    con.close()

    await update.message.reply_text(f"✅ Admin removed.\nUser ID: {uid}")

async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id, added_at FROM admins ORDER BY added_at DESC")
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No admins added.")
        return

    msg = "🛡️ Admin List:\n\n"
    for i, (uid, added_at) in enumerate(rows, 1):
        msg += f"{i}. {uid} | {added_at}\n"

    await update.message.reply_text(msg)

async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT question, answer FROM qa ORDER BY id ASC")
    rows = cur.fetchall()
    con.close()

    data = [{"question": q, "answer": a} for q, a in rows]
    filename = "qa_backup.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with open(filename, "rb") as f:
        await update.message.reply_document(
        document=f,
        filename=filename,
        caption="✅ Backup exported."
    )

async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    await update.message.reply_text("JSON backup file bhejo aur caption me /import likho.")

async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if "/import" not in (update.message.caption or ""):
        return

    doc = update.message.document

    if not doc.file_name.endswith(".json"):
        await update.message.reply_text("❌ Sirf .json file allowed hai.")
        return

    file = await context.bot.get_file(doc.file_id)
    path = "import_backup.json"
    await file.download_to_drive(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        con = db()
        cur = con.cursor()
        count = 0

        for item in data:
            q = " ".join(item.get("question", "").strip().lower().split())
            a = item.get("answer", "").strip()

            if q and a:
                cur.execute("INSERT OR REPLACE INTO qa(question, answer, created_at) VALUES(?,?,?)", (q, a, now()))
                count += 1

        con.commit()
        con.close()

        await update.message.reply_text(f"✅ Import complete. {count} Q&A restored.")

    except Exception as e:
        await update.message.reply_text(f"❌ Import failed: {e}")

    if os.path.exists(path):
        os.remove(path)

def get_start(period):
    if period == "today":
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7days":
        return datetime.now() - timedelta(days=7)
    return None

async def totalqa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM qa")
    total = cur.fetchone()[0]

    con.close()

    await update.message.reply_text(
        f"📚 Total Questions Stored: {total}"
    )

async def overview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    start_today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    con = db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM subscriptions WHERE expires_at > ?", (now(),))
    active_subs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM qa")
    total_questions = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM banned_users")
    banned_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM answer_logs WHERE created_at >= ?", (start_today,))
    answers_today = cur.fetchone()[0]

    con.close()

    sent_msg=await update.message.reply_text(
        "📊 Bot Overview\n\n"
        f"👥 Total Users: {total_users}\n"
        f"⭐ Active Subscribers: {active_subs}\n"
        f"📚 Questions Stored: {total_questions}\n"
        f"🚫 Banned Users: {banned_users}\n"
        f"📝 Answers Today: {answers_today}"
    )
    asyncio.create_task(
    delete_after_20min(
        context,
        update.effective_chat.id,
        sent_msg.message_id
    )
)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    period = context.args[0] if context.args else "today"
    start = get_start(period)

    if not start:
        await update.message.reply_text("Use:\n/stats today\n/stats 7days")
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(DISTINCT user_id), COUNT(*) FROM answer_logs WHERE created_at >= ?",
                (start.strftime("%Y-%m-%d %H:%M:%S"),))
    users, answers = cur.fetchone()
    con.close()

    await update.message.reply_text(f"📊 Stats: {period}\n\nUnique users: {users}\nTotal answers: {answers}")

async def userstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 2:
        await update.message.reply_text("Use:\n/userstats user_id today")
        return

    uid = int(context.args[0])
    period = context.args[1]
    start = get_start(period)

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM answer_logs WHERE user_id=? AND created_at >= ?",
                (uid, start.strftime("%Y-%m-%d %H:%M:%S")))
    count = cur.fetchone()[0]
    con.close()

    await update.message.reply_text(f"👤 User: {uid}\nPeriod: {period}\nAnswers: {count}")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    period = context.args[0] if context.args else "today"
    start = get_start(period)

    con = db()
    cur = con.cursor()
    cur.execute("""SELECT user_id, COUNT(*) FROM answer_logs
                   WHERE created_at >= ?
                   GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 10""",
                (start.strftime("%Y-%m-%d %H:%M:%S"),))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No data found.")
        return

    msg = f"🏆 Top Users: {period}\n\n"
    for i, (uid, total) in enumerate(rows, 1):
        msg += f"{i}. {uid} - {total} answers\n"

    await update.message.reply_text(msg)

async def totalusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    con.close()

    await update.message.reply_text(f"👥 Total Users: {total}")

async def active_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    period = context.args[0] if context.args else "today"
    start = get_start(period)

    if not start:
        await update.message.reply_text("Use:\n/active today\n/active 7days")
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (start.strftime("%Y-%m-%d %H:%M:%S"),))
    total = cur.fetchone()[0]
    con.close()

    await update.message.reply_text(f"📊 Active Users ({period}): {total}")

async def newusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    con = db()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (start.strftime("%Y-%m-%d %H:%M:%S"),))
    total = cur.fetchone()[0]
    con.close()

    await update.message.reply_text(f"🆕 New Users Today: {total}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    text = update.message.text.replace("/broadcast", "", 1).strip()

    if not text:
        await update.message.reply_text("Use:\n/broadcast your message")
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    con.close()

    sent = 0
    failed = 0

    await update.message.reply_text("📢 Broadcast started...")

    for (uid,) in rows:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1

    await update.message.reply_text(f"📢 Broadcast Complete\n\nSent: {sent}\nFailed: {failed}")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use:\n/ban user_id")
        return

    uid = int(context.args[0])

    con = db()
    cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO banned_users(user_id, banned_at) VALUES(?,?)", (uid, now()))
    con.commit()
    con.close()

    await update.message.reply_text(f"🚫 User banned: {uid}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use:\n/unban user_id")
        return

    uid = int(context.args[0])

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM banned_users WHERE user_id=?", (uid,))
    con.commit()
    con.close()

    await update.message.reply_text(f"✅ User unbanned: {uid}")

async def banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await owner_only(update)
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT user_id, banned_at FROM banned_users ORDER BY banned_at DESC LIMIT 50")
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No banned users.")
        return

    msg = "🚫 Banned Users:\n\n"
    for i, (uid, banned_at) in enumerate(rows, 1):
        msg += f"{i}. {uid} - {banned_at}\n"

    await update.message.reply_text(msg)
async def silently_delete_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    await asyncio.sleep(5)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except:
        pass

async def delete_after_20min(context, chat_id, message_id):
    await asyncio.sleep(1200)  # 20 min

    try:
        await context.bot.delete_message(
            chat_id=chat_id,
            message_id=message_id
        )
    except:
        pass

async def cooldown_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cooldown_msg = await update.message.reply_text(
        f"⏳ Cooldown Active\nNext question in: {COOLDOWN_SECONDS} seconds"
    )

    for sec in range(COOLDOWN_SECONDS - 1, 0, -1):
        await asyncio.sleep(1)
        try:
            await cooldown_msg.edit_text(f"⏳ Cooldown Active\nNext question in: {sec} seconds")
        except:
            pass

    try:
        await cooldown_msg.edit_text("✅ Cooldown finished. You can ask next question now.")
    except:
        pass

async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    save_user(update)
    uid = update.effective_user.id
    q = " ".join(update.message.text.strip().lower().split())
    if is_banned(uid):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    if not active_sub(uid):
        await update.message.reply_text(f"❌ Subscription active nahi hai ya expired hai.\nRenew: {ADMIN_USERNAME}")
        return

    if not is_owner(uid):
        current_time = datetime.now().timestamp()
        last_time = user_cooldowns.get(uid)

        if last_time:
            remaining = COOLDOWN_SECONDS - int(current_time - last_time)

            if remaining > 0:
                await update.message.reply_text(
                    f"⏳ Cooldown Active\n\nPlease wait {remaining} seconds before asking another question."
                )
                return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT answer FROM qa WHERE question=?", (q,))
    row = cur.fetchone()

    if row:
        cur.execute("INSERT INTO answer_logs(user_id, question, created_at) VALUES(?,?,?)", (uid, q, now()))
        con.commit()
        con.close()

        answer_msg = await update.message.reply_text(row[0])

        if not is_owner(uid):
            user_cooldowns[uid] = datetime.now().timestamp()

            asyncio.create_task(
                silently_delete_later(
                    context,
                    update.effective_chat.id,
                    answer_msg.message_id
                )
            )

            asyncio.create_task(
                cooldown_countdown(update, context)
            )

    else:
        con.close()
        await update.message.reply_text("❌ Is question ka answer saved nahi hai.")

async def reminder_loop(app):
    while True:
        try:
            con = db()
            cur = con.cursor()
            cur.execute("SELECT user_id, expires_at FROM subscriptions")
            rows = cur.fetchall()

            for uid, exp_text in rows:
                exp = datetime.strptime(exp_text, "%Y-%m-%d %H:%M:%S")
                days_left = (exp.date() - datetime.now().date()).days

                rtype, msg = None, None

                if days_left == 2:
                    rtype = "2days"
                    msg = f"🔔 Reminder: Aapka subscription 2 din me expire hone wala hai.\nRenew: {ADMIN_USERNAME}"
                elif days_left == 0 and exp > datetime.now():
                    rtype = "today"
                    msg = f"⚠️ Aapka subscription aaj expire hone wala hai.\nRenew: {ADMIN_USERNAME}"
                elif exp <= datetime.now():
                    rtype = "expired"
                    msg = f"❌ Your subscription expired.\nRenew: {ADMIN_USERNAME}"

                if rtype:
                    cur.execute("SELECT id FROM reminder_logs WHERE user_id=? AND reminder_type=? AND sent_date=?",
                                (uid, rtype, today()))

                    if not cur.fetchone():
                        try:
                            await app.bot.send_message(uid, msg)
                            cur.execute("INSERT INTO reminder_logs(user_id, reminder_type, sent_date) VALUES(?,?,?)",
                                        (uid, rtype, today()))
                            con.commit()
                        except:
                            pass

            con.close()

        except Exception as e:
            print("Reminder error:", e)

        await asyncio.sleep(86400)

async def post_init(app):
    asyncio.create_task(reminder_loop(app))

def main():
    init_db()

    request = HTTPXRequest(
        connect_timeout=60,
        read_timeout=60,
        write_timeout=60,
        pool_timeout=60
    )

    app = Application.builder().token(BOT_TOKEN).request(request).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("status", status))

    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("update", update_qa))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CommandHandler("list", list_qa))
    app.add_handler(CallbackQueryHandler(list_callback, pattern="^list_"))
    
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("admins", admins))

    app.add_handler(CommandHandler("grant", grant))
    app.add_handler(CommandHandler("revoke", revoke))

    app.add_handler(CommandHandler("export", export))
    app.add_handler(CommandHandler("import", import_cmd))

    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("totalqa", totalqa))
    app.add_handler(CommandHandler("overview", overview))
    app.add_handler(CommandHandler("userstats", userstats))
    app.add_handler(CommandHandler("top", top))

    app.add_handler(CommandHandler("totalusers", totalusers))
    app.add_handler(CommandHandler("active", active_users))
    app.add_handler(CommandHandler("newusers", newusers))

    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("banlist", banlist))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))

    print("Bot running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
