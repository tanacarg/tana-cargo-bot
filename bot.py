import os
import threading
import sqlite3
import asyncio
import secrets
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, unquote, quote
import json

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, CallbackQueryHandler, filters,
)

# ==================================================
# CONFIG
# ==================================================

TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_USER_ID = os.getenv("SUPER_ADMIN_USER_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

SUPPORT_PHONE = "0960011010"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanapage"

START_IMAGE_ID = "AgACAgQAAxkBAAFWw_Vq389auQ7yoeq8rNETEyhfn02JagACCw9rG-tuAVNIehm8-b92jwEAAwIAA3kAAz0E"

DB_PATH = "tana_cargo.db"
CONVERSATION_TIMEOUT_SECONDS = 600

# ==================================================
# MEMORY & CACHE
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
brokers = []
car_rentals = []
car_sales = []
connection_requests = []
tracking_stops = []
admin_view_pii = {} # Tracks ON/OFF status for Admin/Super Admin

# ==================================================
# DATABASE (Persistent Fix)
# ==================================================

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, owner_name TEXT, owner_phone TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cargo_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, from_location TEXT, to_location TEXT, cargo_type TEXT, vehicle TEXT, size_name TEXT, weight TEXT, date_et TEXT, phone TEXT, price_display TEXT, active INTEGER DEFAULT 1, timestamp TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS truck_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, truck_type TEXT, plate TEXT, capacity TEXT, route TEXT, address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS brokers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, username TEXT, phone TEXT, address TEXT, active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0, timestamp TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS car_rentals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, car_type TEXT, model TEXT, plate TEXT, photo_id TEXT, name TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS car_sales (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, car_type TEXT, model TEXT, plate TEXT, photo_id TEXT, name TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT)")
    conn.commit()
    conn.close()

def load_all_from_db():
    """Render restart ሲያደርግ ዳታው እንዲመጣ (Registration እንዳይጠይቅ)"""
    global cargo_posts, truck_posts, brokers, car_rentals, car_sales
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT * FROM users").fetchall():
            u = dict(row)
            users[u["user_id"]] = {"name": u.get("name") or "", "username": u.get("username") or "", "id": u["user_id"]}
            if u.get("owner_phone"):
                users[u["user_id"]]["owner"] = {"name": u.get("owner_name") or "", "phone": u.get("owner_phone") or ""}
        
        cargo_posts.clear()
        for row in conn.execute("SELECT * FROM cargo_posts WHERE active = 1").fetchall():
            c = dict(row)
            c["from"] = c.pop("from_location")
            c["to"] = c.pop("to_location")
            c["type"] = c.pop("cargo_type")
            cargo_posts.append(c)
            
        truck_posts.clear()
        for row in conn.execute("SELECT * FROM truck_posts WHERE active = 1").fetchall():
            t = dict(row)
            t["type"] = t.pop("truck_type")
            truck_posts.append(t)
            
        brokers.clear()
        for row in conn.execute("SELECT * FROM brokers").fetchall():
            b = dict(row)
            b["active"] = bool(b.get("active"))
            b["approved"] = bool(b.get("approved"))
            brokers.append(b)

        car_rentals.clear()
        for row in conn.execute("SELECT * FROM car_rentals WHERE active = 1").fetchall():
            car_rentals.append(dict(row))

        car_sales.clear()
        for row in conn.execute("SELECT * FROM car_sales WHERE active = 1").fetchall():
            car_sales.append(dict(row))
            
        print(f"✅ DB Loaded: {len(users)} users, {len(cargo_posts)} cargo, {len(truck_posts)} trucks, {len(car_rentals)} rentals, {len(car_sales)} sales")
    except Exception as e:
        print(f"❌ DB LOAD ERROR: {e}")
    finally:
        conn.close()

def db_save_user(user_id, name, username, owner_name, owner_phone):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, name, username, owner_name, owner_phone) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username, owner_name = excluded.owner_name, owner_phone = excluded.owner_phone", (user_id, name, username, owner_name, owner_phone))
    conn.commit()
    conn.close()

def db_save_post(table, data):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    if table == "cargo_posts":
        cursor.execute("INSERT INTO cargo_posts (user_id, from_location, to_location, cargo_type, vehicle, size_name, weight, date_et, phone, price_display, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (data["user_id"], data["from"], data["to"], data["type"], data["vehicle"], data["size_name"], data["weight"], data["date_et"], data["phone"], data["price_display"], data["timestamp"], data["poster_type"]))
    elif table == "truck_posts":
        cursor.execute("INSERT INTO truck_posts (user_id, truck_type, plate, capacity, route, address, driver, phone, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (data["user_id"], data["type"], data["plate"], data["capacity"], data["route"], data["address"], data["driver"], data["phone"], data["timestamp"], data["poster_type"]))
    elif table == "car_rentals":
        cursor.execute("INSERT INTO car_rentals (user_id, car_type, model, plate, photo_id, name, phone, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (data["user_id"], data["type"], data["model"], data["plate"], data["photo_id"], data["name"], data["phone"], data["timestamp"]))
    elif table == "car_sales":
        cursor.execute("INSERT INTO car_sales (user_id, car_type, model, plate, photo_id, name, phone, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (data["user_id"], data["type"], data["model"], data["plate"], data["photo_id"], data["name"], data["phone"], data["timestamp"]))
    conn.commit()
    conn.close()

def db_save_broker(broker):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO brokers (user_id, name, username, phone, address, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (broker["user_id"], broker["name"], broker["username"], broker["phone"], broker["address"], broker["timestamp"]))
    conn.commit()
    conn.close()

# ==================================================
# HELPERS
# ==================================================

def is_user_registered(user_id):
    if user_id in users and users[user_id].get("owner", {}).get("phone"):
        return True
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]: return True
    finally: conn.close()
    return False

def get_user_phone(user_id):
    user = users.get(user_id, {})
    return user.get("owner", {}).get("phone", "N/A")

def validate_phone(phone):
    phone = phone.strip().replace(" ", "")
    if phone.startswith("+251"): phone = "0" + phone[4:]
    elif phone.startswith("251"): phone = "0" + phone[3:]
    if not (phone.startswith("09") or phone.startswith("07")): return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10: return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    return True, phone

def is_super_admin(user_id): return bool(SUPER_ADMIN_USER_ID) and str(user_id) == str(SUPER_ADMIN_USER_ID)
def is_admin(user_id): return is_super_admin(user_id) or (bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID))

def get_admin_ids():
    ids = []
    if SUPER_ADMIN_USER_ID: ids.append(int(SUPER_ADMIN_USER_ID))
    if ADMIN_USER_ID and str(ADMIN_USER_ID) != str(SUPER_ADMIN_USER_ID): ids.append(int(ADMIN_USER_ID))
    return ids

def clear_stale_user_state(context):
    for key in ("cargo", "truck", "broker", "car_rent", "car_sale", "connect_info", "gmaps_share", "tracking_stop"):
        context.user_data.pop(key, None)

def phone_request_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def confirm_phone_keyboard(phone):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ ትክክል ነው ({phone})", callback_data="confirm_reg_phone")],
        [InlineKeyboardButton("✍️ ሌላ ስልክ ቁጥር አስገባ", callback_data="enter_new_phone")]
    ])

# ==================================================
# STATES
# ==================================================

(CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE, CARGO_WEIGHT, CARGO_DATE, CARGO_PHONE, CARGO_PRICE) = range(10, 19)
(TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE, TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE) = range(20, 27)
(BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS) = range(30, 33)
(CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE) = range(35, 38)
(TRACK_SHARE_ADDRESS, TRACK_SHARE_NAME, TRACK_SHARE_PHONE, TRACK_STOP_NAME, TRACK_STOP_PHONE) = range(40, 45)
START_PHONE = 50
(CAR_RENT_TYPE, CAR_RENT_MODEL, CAR_RENT_PLATE, CAR_RENT_PHOTO, CAR_RENT_PHONE) = range(55, 60)
(CAR_SALE_TYPE, CAR_SALE_MODEL, CAR_SALE_PLATE, CAR_SALE_PHOTO, CAR_SALE_PHONE) = range(60, 65)
(FIND_CAR_SELECTION, FIND_CAR_PHONE) = range(65, 67)
SUPER_BROADCAST_TARGET, SUPER_BROADCAST_TEXT = range(70, 72)

# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚐🚗🚛 የመኪናኪራይና ሽያጭ መለጠፍ", "🔎 የኪራይ/የሽያጭመኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል", "🚐 የኪራይ ክትትል"],
            ["📚 የክትትል መዝገብ"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["📋 የጭነት ደላሎች መዝገብ"],
        ]
        if is_super_admin(user_id):
            keyboard.append(["📣 መረጃ ለደንበኞች/ደላሎች ለማጋራት"])
        else:
            keyboard.append(["💳 የአገልግሎት ክፍያ ለመፈፀም"])
        keyboard.append(["📞 Support", "ℹ️ About"])
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚐🚗🚛 የመኪናኪራይና ሽያጭ መለጠፍ", "🔎 የኪራይ/የሽያጭመኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል", "🚐 የኪራይ ክትትል"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
            ["🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ"],
            ["💳 የአገልግሎት ክፍያ ለመፈጸም"],
            ["📞 Support", "ℹ️ About"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def car_rent_sale_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗🚐 የመኪና ኪራይ", callback_data="post_car_rent")],
        [InlineKeyboardButton("🚗🚛 የመኪና ሽያጭ", callback_data="post_car_sale")]
    ])

def find_car_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 የሽያጭ መኪና ፈልግ 🟢", callback_data="find_car_sale")],
        [InlineKeyboardButton("🚐 የኪራይ መኪና ፈልግ 🔵", callback_data="find_car_rent")]
    ])
# ==================================================
# START & REGISTRATION
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    
    if not users:
        load_all_from_db()
    
    if is_user_registered(user.id):
        users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
        await update.message.reply_text(
            "እናመሰግናለን ምዝገባዎን አጠናቀዋል ከታች ካሉት ምናሌዎች የፈለጉትን መጠቀም ይችላሉ።",
            reply_markup=main_menu(user.id)
        )
        return ConversationHandler.END
    
    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "🚛 **TANA CARGO | ጣና ጭነት** 📦\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "START👈ይጫኑ ,ስልክ ያጋሩ የስልክ ምልክቱና የሸር ምልክትጋር\n\n"
                "👇 ከታች ያለውን '📱 ስልኬን አጋራ' ይጫኑ።"
            ),
            reply_markup=phone_request_keyboard(),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "START👈ይጫኑ ,ስልክ ያጋሩ የስልክ ምልክቱና የሸር ምልክትጋር",
            reply_markup=phone_request_keyboard()
        )
    return START_PHONE

async def start_phone_input(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return START_PHONE
        
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return START_PHONE
    
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = {"phone": result, "name": user.full_name}
    
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=result)
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")
        
    await update.message.reply_text(
        "እናመሰግናለን ምዝገባዎን አጠናቀዋል ከታች ካሉት ምናሌዎች የፈለጉትን መጠቀም ይችላሉ።",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or ""})
    owner = user.get("owner", {})
    
    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user.get('name', 'N/A')}\n"
        f"🔗 Username፦ @{user.get('username') or 'የለም'}\n"
        f"📞 ስልክ፦ {owner.get('phone', 'N/A')}\n"
    )
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

# ==================================================
# BROADCAST (Super Admin only)
# ==================================================

async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Super Admin ብቻ!")
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ለሁሉም ደንበኞች", callback_data="broadcast_all")],
        [InlineKeyboardButton("ለደላሎች ብቻ", callback_data="broadcast_brokers")]
    ])
    await update.message.reply_text("📢 **መረጃ ለማጋራት**\n\nለማን ማጋራት ይፈልጋሉ?", reply_markup=kb)
    return SUPER_BROADCAST_TARGET

async def broadcast_target(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["broadcast_target"] = "all" if query.data == "broadcast_all" else "brokers"
    await query.message.reply_text("📝 የሚያጋሩትን መልእክት ይጻፉ፦\n\n(ስር ላይ የሚያስፈልገው የእጆት ስልክ እና የቴሌግራም አድራሻ በራሱ ይጨመራል)")
    return SUPER_BROADCAST_TEXT

async def broadcast_send(update, context):
    text = update.message.text
    target = context.user_data.get("broadcast_target")
    
    targets = []
    if target == "all":
        conn = sqlite3.connect(DB_PATH)
        targets = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
        conn.close()
    else:
        targets = [b["user_id"] for b in brokers if b.get("approved") and b.get("active")]
        
    final_text = (
        "📢 **ከTANACARGO & LOGISTIC የተላከ**\n\n"
        f"{text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 እባክዎን የሚፈልግ ደንበኛ እጆት ላይ ካለ\n"
        f"📞 በ {SUPPORT_PHONE} ይደውሉ\n"
        f"ወይም በቴሌግራም @tanacargosupportbot ላይ የፃፉልን የሚል ይኖረዋል።"
    )
    
    sent, failed = 0, 0
    for uid in targets:
        try:
            await context.bot.send_message(uid, final_text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except: failed += 1
            
    await update.message.reply_text(f"✅ መልእክቱ ተልኳል።\n📨 የደረሰው: {sent}\n⚠️ ያልደረሰው: {failed}", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END
# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    clear_stale_user_state(context)
    context.user_data["cargo"] = {}
    await update.message.reply_text("🚚 **ጭነት መለጠፍ**\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር", reply_markup=main_menu(update.effective_user.id))
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።\nምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ")
    return CARGO_TYPE

async def cargo_type(update, context):
    context.user_data["cargo"]["type"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የሚፈልጉትን የመኪና አይነት ይምረጡ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚛 ተሳቢ", callback_data="cargo_vehicle_ተሳቢ")], [InlineKeyboardButton("🚛 ካሶኒ", callback_data="cargo_vehicle_ካሶኒ")], [InlineKeyboardButton("✍️ ሌላ", callback_data="cargo_vehicle_other")]]))
    return CARGO_VEHICLE

async def cargo_vehicle(update, context):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("cargo_vehicle_", "")
    if value == "other":
        await query.edit_message_text("✍️ የሚፈልጉትን የመኪና አይነት ይጻፉ።")
        context.user_data["cargo"]["vehicle_waiting"] = True
        return CARGO_VEHICLE
    context.user_data["cargo"]["vehicle"] = value
    await query.edit_message_text(f"🚛 የተመረጠው መኪና፦ {value}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")], [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")]]))
    return CARGO_SIZE

async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"): return CARGO_VEHICLE
    cargo["vehicle"] = update.message.text.strip()
    cargo["vehicle_waiting"] = False
    await update.message.reply_text(f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")], [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")]]))
    return CARGO_SIZE

async def cargo_size(update, context):
    query = update.callback_query
    await query.answer()
    sizes = {"size_100": "ሙሉ ጭነት", "size_50": "ግማሽ ጭነት"}
    if query.data not in sizes: return CARGO_SIZE
    context.user_data["cargo"]["size_name"] = sizes[query.data]
    await query.edit_message_text(f"📦 የተመረጠው፦ {sizes[query.data]}\n\n6️⃣ የጭነቱን ክብደት ይግለጹ።\nበቶን፣ በቢያጆ፣ በኩንታል")
    return CARGO_WEIGHT

async def cargo_weight(update, context):
    weight = update.message.text.strip()
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text("7️⃣ የሚጫንበትን ቀን በካላንደር ይጻፉ።\nምሳሌ፦ 26/1/2019")
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    context.user_data["cargo"]["date_et"] = date_text
    context.user_data["cargo"]["expiry_date"] = (datetime.now() + timedelta(hours=72)).isoformat()
    
    reg_phone = get_user_phone(update.effective_user.id)
    await update.message.reply_text(
        f"📅 ቀን: {date_text}\n\n8️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_user_phone(update.effective_user.id)
            await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦")
            return CARGO_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid:
            await update.message.reply_text(f"❌ {phone}")
            return CARGO_PHONE

    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text("9️⃣ የመጫኛ ዋጋ ይጻፉ።\nበብር ወይም 'በስምምነት' ብለው ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return CARGO_PRICE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
    price_display = raw if raw in ["በስምምነት", "በድርድር"] else f"{raw} ብር"
    
    cargo = context.user_data["cargo"]
    cargo.update({"price_display": price_display, "user_id": update.effective_user.id, "timestamp": datetime.now().isoformat(), "poster_type": "customer"})
    cargo_posts.append(cargo.copy())
    try: db_save_post("cargo_posts", cargo)
    except Exception as e: print(e)
    
    await update.message.reply_text("✅ **ጭነትዎ በትክክል ተመዝግቧል!**", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# ==================================================
# TRUCK POSTING
# ==================================================

async def truck_start(update, context):
    clear_stale_user_state(context)
    context.user_data["truck"] = {}
    await update.message.reply_text("🚛 **የጭነት መኪና ማስመዝገብ**\n\n1️⃣ የመኪናውን አይነት ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return TRUCK_PLATE

async def truck_plate(update, context):
    context.user_data["truck"]["plate"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።")
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    context.user_data["truck"]["capacity"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የመንገድ ዝርዝር ይጻፉ።")
    return TRUCK_ROUTE

async def truck_route(update, context):
    context.user_data["truck"]["route"] = update.message.text.strip()
    await update.message.reply_text("5️⃣ የመኪናውን አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS

async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    await update.message.reply_text("6️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER

async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text.strip()
    
    reg_phone = get_user_phone(update.effective_user.id)
    await update.message.reply_text(
        f"7️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return TRUCK_PHONE

async def truck_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_user_phone(update.effective_user.id)
            await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦")
            return TRUCK_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid:
            await update.message.reply_text(f"❌ {phone}")
            return TRUCK_PHONE

    truck = context.user_data["truck"]
    truck.update({"phone": phone, "user_id": update.effective_user.id, "timestamp": datetime.now().isoformat(), "poster_type": "customer"})
    truck_posts.append(truck.copy())
    try: db_save_post("truck_posts", truck)
    except Exception as e: print(e)
    
    await update.message.reply_text("✅ **መኪናዎ በትክክል ተመዝግቧል!**", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END
# ==================================================
# CAR RENTAL & SALE POSTING
# ==================================================

async def car_post_start(update, context):
    clear_stale_user_state(context)
    await update.message.reply_text("🚐🚗🚛 **የመኪና ኪራይና ሽያጭ መለጠፍ**\n\nየሚለጥፉትን ዓይነት ይምረጡ፦", reply_markup=car_rent_sale_keyboard())
    return FIND_CAR_SELECTION

async def car_post_type(update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "post_car_rent":
        await query.edit_message_text("🚗 **የመኪና ኪራይ ማስመዝገብ**\n\n1️⃣ የሚያከራዩትን የመኪና አይነት ይጻፉ።\nምሳሌ፦ ተሳቢ / ካሶኒ / ሚኒባስ")
        return CAR_RENT_TYPE
    elif query.data == "post_car_sale":
        await query.edit_message_text("🚗 **የመኪና ሽያጭ ማስመዝገብ**\n\n1️⃣ የሚሸጡትን የመኪና አይነት ይጻፉ።\nምሳሌ፦ ተሳቢ / ካሶኒ / ሚኒባስ")
        return CAR_SALE_TYPE
    return ConversationHandler.END

# --- RENT FLOW ---
async def car_rent_type(update, context):
    context.user_data["car_data"] = {"type": update.message.text.strip()}
    await update.message.reply_text("2️⃣ የመኪናውን ሞዴል (አ/ም) ይጻፉ።\nምሳሌ፦ 2015 / Toyota")
    return CAR_RENT_MODEL

async def car_rent_model(update, context):
    context.user_data["car_data"]["model"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የመኪናውን ታርጋ ይጻፉ።\nምሳሌ፦ 12345")
    return CAR_RENT_PLATE

async def car_rent_plate(update, context):
    context.user_data["car_data"]["plate"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የመኪናውን 1 ወይም 2 ፎቶ ይላኩ (ካለዎት)።\nከሌለዎት 'የለኝም' ብለው ይጻፉ።")
    return CAR_RENT_PHOTO

async def car_rent_photo(update, context):
    photo_id = update.message.photo[-1].file_id if update.message.photo else ""
    context.user_data["car_data"]["photo_id"] = photo_id
    
    reg_name = update.effective_user.full_name
    reg_phone = get_user_phone(update.effective_user.id)
    context.user_data["car_data"]["name"] = reg_name
    
    await update.message.reply_text(
        f"5️⃣ **የርሶ መረጃ**\n👤 ስም፦ {reg_name}\n📞 ስልክ፦ `{reg_phone}`\n\nይህንን መረጃ ለመጠቀም ✅ ይጫኑ ወይም ሌላ ስልክ ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CAR_RENT_PHONE

async def car_rent_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_user_phone(update.effective_user.id)
            await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦")
            return CAR_RENT_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid:
            await update.message.reply_text(f"❌ {phone}")
            return CAR_RENT_PHONE

    car = context.user_data["car_data"]
    car.update({"phone": phone, "user_id": update.effective_user.id, "timestamp": datetime.now().isoformat()})
    car_rentals.append(car.copy())
    try: db_save_post("car_rentals", car)
    except Exception as e: print(e)
    
    await update.message.reply_text(
        "እናመሰግናለን መኪናዎን አስመዝግበዋል። መኪናዎን የሚከራይ ደንበኛ እንደተገኘ በቴሌግራም አካውንትዎ መልእክት እንልካለን ወይም ከላይ ባለው ስልክቁጥሮ እንደውልሎታለን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

# --- SALE FLOW ---
async def car_sale_type(update, context):
    context.user_data["car_data"] = {"type": update.message.text.strip()}
    await update.message.reply_text("2️⃣ የመኪናውን ሞዴል (አ/ም) ይጻፉ።")
    return CAR_SALE_MODEL

async def car_sale_model(update, context):
    context.user_data["car_data"]["model"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return CAR_SALE_PLATE

async def car_sale_plate(update, context):
    context.user_data["car_data"]["plate"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የመኪናውን 1 ወይም 2 ፎቶ ይላኩ (ካለዎት)።\nከሌለዎት 'የለኝም' ብለው ይጻፉ።")
    return CAR_SALE_PHOTO

async def car_sale_photo(update, context):
    photo_id = update.message.photo[-1].file_id if update.message.photo else ""
    context.user_data["car_data"]["photo_id"] = photo_id
    
    reg_name = update.effective_user.full_name
    reg_phone = get_user_phone(update.effective_user.id)
    context.user_data["car_data"]["name"] = reg_name
    
    await update.message.reply_text(
        f"5️⃣ **የርሶ መረጃ**\n👤 ስም፦ {reg_name}\n📞 ስልክ፦ `{reg_phone}`\n\nይህንን መረጃ ለመጠቀም ✅ ይጫኑ ወይም ሌላ ስልክ ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CAR_SALE_PHONE

async def car_sale_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_user_phone(update.effective_user.id)
            await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦")
            return CAR_SALE_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid:
            await update.message.reply_text(f"❌ {phone}")
            return CAR_SALE_PHONE

    car = context.user_data["car_data"]
    car.update({"phone": phone, "user_id": update.effective_user.id, "timestamp": datetime.now().isoformat()})
    car_sales.append(car.copy())
    try: db_save_post("car_sales", car)
    except Exception as e: print(e)
    
    await update.message.reply_text(
        "እናመሰግናለን ምዝገባዎን አጠናቀዋል። የርሶን መኪና የሚገዛ ደንበኛ እንዳገኘን እንደውልሎታለን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END
    # ==================================================
# FIND & SHARE FEATURE (Admin PII Toggle)
# ==================================================

async def find_cargo(update, context):
    clear_stale_user_state(context)
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    if not active_cargos:
        return await update.message.reply_text("📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።", reply_markup=main_menu(update.effective_user.id))
    
    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for i, cargo in enumerate(active_cargos):
        cargo_id = f"{i + 1:03d}"
        real_index = cargo_posts.index(cargo)
        
        admin_info = ""
        if is_admin_user and show_pii:
            owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
            admin_info = f"👤 ስም: {owner_name}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n"
            
        msg = (
            f"📦 **ጭነት {cargo_id}**\n"
            f"📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n🚛 መኪና፦ {cargo['vehicle']}\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n💰 ዋጋ፦ {cargo.get('price_display', 'በስምምነት')}\n"
            f"{admin_info}━━━━━━━━━━━━━━━━━━━━"
        )
        
        kb_buttons = [[InlineKeyboardButton(f"የመረጡት ጭነት {cargo_id} ከሆነ እዚጋ ያጫኑ 👈", callback_data=f"connect_{real_index}")]]
        
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_cargo_{real_index}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_cargo_{real_index}")])
            
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def toggle_pii_cargo(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    admin_view_pii[update.effective_user.id] = not admin_view_pii.get(update.effective_user.id, False)
    index = int(query.data.split("_")[3])
    cargo = cargo_posts[index]
    show_pii = admin_view_pii[update.effective_user.id]
    
    admin_info = f"👤 ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n" if show_pii else ""
    msg = f"📦 **ጭነት {index+1:03d}**\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n📦 አይነት፦ {cargo['type']}\n🚛 መኪና፦ {cargo['vehicle']}\n⚖️ ክብደት፦ {cargo['weight']}\n💰 ዋጋ፦ {cargo.get('price_display', 'በስምምነት')}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
    
    kb_buttons = [[InlineKeyboardButton(f"የመረጡት ጭነት {index+1:03d} ከሆነ እዚጋ ያጫኑ 👈", callback_data=f"connect_{index}")]]
    toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
    kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_cargo_{index}")])
    kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_cargo_{index}")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def share_post_prompt(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    parts = query.data.split("_")
    p_type = parts[1]
    index = int(parts[2])
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ለሁሉም ደንበኞች", callback_data=f"do_share_all_{p_type}_{index}")],
        [InlineKeyboardButton("ለደላሎች ብቻ", callback_data=f"do_share_brokers_{p_type}_{index}")]
    ])
    await query.message.reply_text("📤 ይህንን መረጃ ለማን ማጋራት ይፈልጋሉ?", reply_markup=kb)

async def do_share_post(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    parts = query.data.split("_")
    target = parts[2]
    p_type = parts[3]
    index = int(parts[4])
    
    if p_type == "cargo":
        item = cargo_posts[index]
        text = f"📦 **አዲስ ጭነት!**\n📍 {item['from']} ➡️ {item['to']}\n📦 {item['type']}\n🚛 {item['vehicle']}\n⚖️ {item['weight']}\n💰 {item.get('price_display', 'በስምምነት')}"
    elif p_type == "truck":
        item = truck_posts[index]
        text = f"🚛 **አዲስ የጭነት መኪና!**\n🚘 {item['type']}\n⚖️ {item['capacity']}\n🛣️ {item['route']}"
    elif p_type == "rent":
        item = car_rentals[index]
        text = f"🚗 **የመኪና ኪራይ!**\n🚘 {item['type']} ({item['model']})"
    elif p_type == "sale":
        item = car_sales[index]
        text = f"🚗 **የመኪና ሽያጭ!**\n🚘 {item['type']} ({item['model']})"
        
    final_text = f"📢 **ከTANACARGO & LOGISTIC የተላከ**\n\n{text}\n\n━━━━━━━━━━━━━━━━━━━━\n📞 በ {SUPPORT_PHONE} ይደውሉ\nወይም በቴሌግራም @tanacargosupportbot ላይ የፃፉልን።"
    
    targets = []
    if target == "all":
        conn = sqlite3.connect(DB_PATH)
        targets = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
        conn.close()
    else:
        targets = [b["user_id"] for b in brokers if b.get("approved") and b.get("active")]
        
    sent = 0
    for uid in targets:
        try:
            await context.bot.send_message(uid, final_text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except: pass
        
    await query.edit_message_text(f"✅ መልእክቱ ለ {sent} ተጠቃሚዎች ተልኳል።")

async def find_truck(update, context):
    clear_stale_user_state(context)
    active_trucks = [t for t in truck_posts if t.get("active", True)]
    if not active_trucks:
        return await update.message.reply_text("🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።", reply_markup=main_menu(update.effective_user.id))
        
    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for i, truck in enumerate(active_trucks):
        truck_id = f"{i + 1:03d}"
        real_index = truck_posts.index(truck)
        
        admin_info = f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n" if is_admin_user and show_pii else ""
            
        msg = (
            f"🚛 **መኪና {truck_id}**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n📍 አድራሻ፦ {truck['address']}\n"
            f"{admin_info}━━━━━━━━━━━━━━━━━━━━"
        )
        
        kb_buttons = [[InlineKeyboardButton(f"የመረጡት መኪና {truck_id} ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{real_index}")]]
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_truck_{real_index}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_truck_{real_index}")])
            
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def toggle_pii_truck(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    admin_view_pii[update.effective_user.id] = not admin_view_pii.get(update.effective_user.id, False)
    index = int(query.data.split("_")[3])
    truck = truck_posts[index]
    show_pii = admin_view_pii[update.effective_user.id]
    
    admin_info = f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n" if show_pii else ""
    msg = f"🚛 **መኪና {index+1:03d}**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n📍 አድራሻ፦ {truck['address']}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
    
    kb_buttons = [[InlineKeyboardButton(f"የመረጡት መኪና {index+1:03d} ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{index}")]]
    toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
    kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_truck_{index}")])
    kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_truck_{index}")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

# ==================================================
# FIND CAR (RENTAL / SALE)
# ==================================================

async def find_car_start(update, context):
    clear_stale_user_state(context)
    await update.message.reply_text("🔎 **የኪራይ/የሽያጭ መኪና መፈለግ**\n\nየሚፈልጉትን ዓይነት ይምረጡ፦", reply_markup=find_car_keyboard())
    return FIND_CAR_SELECTION

async def find_car_list(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    
    is_sale = query.data == "find_car_sale"
    target_list = car_sales if is_sale else car_rentals
    title = "የሽያጭ መኪናዎች" if is_sale else "የኪራይ መኪናዎች"
    p_type = "sale" if is_sale else "rent"
    
    if not target_list:
        await context.bot.send_message(update.effective_chat.id, f"❌ በአሁኑ ጊዜ የ{title} ዝርዝር የለም።", reply_markup=main_menu(update.effective_user.id))
        return

    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for i, car in enumerate(target_list):
        real_index = target_list.index(car)
        owner_info = f"👤 ባለቤት: {car['name']}\n📞 ስልክ: {car['phone']}\n🔢 ታርጋ: {car['plate']}\n" if is_admin_user and show_pii else ""
        
        msg = f"🚗 **{title} {i+1:03d}**\n🚘 አይነት፦ {car['type']}\n📅 ሞዴል፦ {car['model']}\n{owner_info}━━━━━━━━━━━━━━━━━━━━"
        
        kb_buttons = [[InlineKeyboardButton(f"ይህንን መኪና ከመረጡ 👈 ይጫኑ", callback_data=f"car_select_{p_type}_{real_index}")]]
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_car_{p_type}_{real_index}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_{p_type}_{real_index}")])

        kb = InlineKeyboardMarkup(kb_buttons)
        
        if car.get("photo_id"):
            await context.bot.send_photo(update.effective_chat.id, photo=car["photo_id"], caption=msg, reply_markup=kb)
        else:
            await context.bot.send_message(update.effective_chat.id, msg, reply_markup=kb)
            
    return ConversationHandler.END

async def toggle_pii_car(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    admin_view_pii[update.effective_user.id] = not admin_view_pii.get(update.effective_user.id, False)
    parts = query.data.split("_")
    p_type = parts[3]
    index = int(parts[4])
    target_list = car_sales if p_type == "sale" else car_rentals
    car = target_list[index]
    
    title = "የሽያጭ መኪናዎች" if p_type == "sale" else "የኪራይ መኪናዎች"
    show_pii = admin_view_pii[update.effective_user.id]
    owner_info = f"👤 ባለቤት: {car['name']}\n📞 ስልክ: {car['phone']}\n🔢 ታርጋ: {car['plate']}\n" if show_pii else ""
    
    msg = f"🚗 **{title} {index+1:03d}**\n🚘 አይነት፦ {car['type']}\n📅 ሞዴል፦ {car['model']}\n{owner_info}━━━━━━━━━━━━━━━━━━━━"
    
    kb_buttons = [[InlineKeyboardButton(f"ይህንን መኪና ከመረጡ 👈 ይጫኑ", callback_data=f"car_select_{p_type}_{index}")]]
    toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
    kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_car_{p_type}_{index}")])
    kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_{p_type}_{index}")])
    
    if query.message.photo:
        await query.edit_message_caption(caption=msg, reply_markup=InlineKeyboardMarkup(kb_buttons))
    else:
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))
# ==================================================
# CONNECTIONS & REQUESTS
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(cargo_posts): return
    cargo = cargo_posts[index]
    requester = update.effective_user
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    
    context.user_data["connect_info"] = {"type": "cargo", "item_index": index, "requester_id": requester.id}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(truck_posts): return
    truck = truck_posts[index]
    requester = update.effective_user
    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    
    context.user_data["connect_info"] = {"type": "truck", "item_index": index, "requester_id": requester.id}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip()
    context.user_data["connect_info"] = info
    
    reg_phone = get_user_phone(update.effective_user.id)
    await update.message.reply_text(
        f"2️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CONNECT_PHONE

async def connect_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_user_phone(update.effective_user.id)
            await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦")
            return CONNECT_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid:
            await update.message.reply_text(f"❌ {phone}")
            return CONNECT_PHONE

    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
    context.user_data["connect_info"] = info
    await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\nምሳሌ፦ አዲስ አበባ")
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    info["requester_address"] = update.message.text.strip()
    context.user_data["connect_info"] = info
    requester = update.effective_user
    
    req_data = {"type": info["type"], "item_index": info["item_index"], "requester_id": requester.id, "requester_name": info["name"], "requester_phone": info["phone"], "requester_address": info["requester_address"], "status": "admin_confirming", "created_at": datetime.now().isoformat()}
    connection_requests.append(req_data)
    
    await update.message.reply_text("✅ እናመሰግናለን! አድሚን አጽድቆ መረጃውን ይልክልዎታል።", reply_markup=main_menu(requester.id))
    
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(admin_id, f"🔔 **አዲስ ግንኙነት ጥያቄ!**\n👤 ስም: {info['name']}\n📞 ስልክ: {info['phone']}\n📍 አድራሻ: {info['requester_address']}\n👉 '🤝 የግንኙነት ጥያቄዎች' ምናሌ ይመልከቱ።")
        except: pass
            
    return ConversationHandler.END

async def show_connection_requests(update, context):
    if not is_admin(update.effective_user.id): return
    if not connection_requests: return await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።")
    
    for i, req in enumerate(connection_requests):
        if req["status"] == "deleted" or req["status"] == "completed": continue
        desc = f"📦 {cargo_posts[req['item_index']]['from']}" if req["type"] == "cargo" else f"🚛 {truck_posts[req['item_index']]['type']}"
        text = f"📥 **ጥያቄ #{i + 1}**\n{desc}\n👤 ስም: {req.get('requester_name')}\n📞 ስልክ: {req.get('requester_phone')}\n📌 ሁኔታ: {req['status']}"
        buttons = [[InlineKeyboardButton("✅ Approve (Right)", callback_data=f"adminright_{i}"), InlineKeyboardButton("❌ Reject (X)", callback_data=f"adminxdelete_{i}")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    index = int(query.data.split("_")[1])
    req = connection_requests[index]
    req["status"] = "completed"
    
    if req["type"] == "cargo":
        cargo = cargo_posts[req["item_index"]]
        cargo["active"] = False
        owner_id, seeker_id = cargo["user_id"], req["requester_id"]
        owner_phone, seeker_phone = cargo["phone"], req["requester_phone"]
    else:
        truck = truck_posts[req["item_index"]]
        truck["active"] = False
        owner_id, seeker_id = truck["user_id"], req["requester_id"]
        owner_phone, seeker_phone = truck["phone"], req["requester_phone"]
        
    await query.edit_message_text("✅ **Right ተረጋግጧል!**\nመረጃው ለሁለቱም ወገን ተልኳል።")
    try: await context.bot.send_message(seeker_id, f"🎉 የፈለጉት ነገር ባለቤት ምላሽ ሰጥቷል!\n📞 የባለቤቱ ስልክ: {owner_phone}")
    except: pass
    try: await context.bot.send_message(owner_id, f"🎉 የፈለጉትን ደንበኛ አግኝተናል!\n📞 የጠያቂው ስልክ: {seeker_phone}")
    except: pass

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id): return
    index = int(query.data.split("_")[1])
    connection_requests[index]["status"] = "deleted"
    await query.edit_message_text("❌ **ተሰርዟል!**")

# ==================================================
# TRACKING & GPS
# ==================================================

async def tracking_menu(update, context):
    clear_stale_user_state(context)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺️ የጎግል መረጃ ለባለጭነቶች ለመላክ 👈", callback_data="tracking_share_gmaps")],
        [InlineKeyboardButton("🛑 ክትትል አቁም", callback_data="tracking_stop_request")],
    ])
    await update.message.reply_text("📍 **TANA CARGO — የጭነት ክትትል**", reply_markup=keyboard)

async def tracking_share_gmaps_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["gmaps_share"] = {}
    await query.message.reply_text("1️⃣ **የራስዎን አድራሻ** ይጻፉ።")
    return TRACK_SHARE_ADDRESS

async def tracking_share_address(update, context):
    context.user_data["gmaps_share"]["address"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ **መረጃው የሚላክለት ሰው ስም** ይጻፉ።")
    return TRACK_SHARE_NAME

async def tracking_share_name(update, context):
    context.user_data["gmaps_share"]["name"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ **መረጃው የሚላክለት ሰው ስልክ ቁጥር** ይጻፉ።")
    return TRACK_SHARE_PHONE

async def tracking_share_phone(update, context):
    is_valid, phone = validate_phone(update.message.text.strip())
    if not is_valid:
        await update.message.reply_text(f"❌ {phone}")
        return TRACK_SHARE_PHONE
    info = context.user_data.pop("gmaps_share")
    info.update({"phone": phone, "sender_id": update.effective_user.id, "sender_name": update.effective_user.full_name})
    tracking_stops.append(info) # Storing temporarily
    idx = len(tracking_stops) - 1
    
    await update.message.reply_text("✅ መረጃዎን ልከናል! አድሚን አጽድቆ ይደርስዎታል።", reply_markup=main_menu(update.effective_user.id))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approvegmaps_{idx}")]])
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(admin_id, f"🔔 **የGoogle Maps ማጋሪያ ጥያቄ!**\nከ: {info['sender_name']}\nለ: {info['name']} ({phone})", reply_markup=kb)
        except: pass
    return ConversationHandler.END

async def admin_approve_gmaps(update, context):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    info = tracking_stops[idx]
    await query.edit_message_text("✅ ጸድቋል! መልእክት ተልኳል።")
    try: await context.bot.send_message(info["sender_id"], f"✅ የእርስዎ ማጋሪያ ጸድቋል! ለ {info['name']} ተልኳል።")
    except: pass

async def tracking_stop_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_stop"] = {}
    await query.message.reply_text("1️⃣ **የሚከታተለው ሰው ስም** ይጻፉ።")
    return TRACK_STOP_NAME

async def tracking_stop_name(update, context):
    context.user_data["tracking_stop"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ **ስልክ ቁጥር** ይጻፉ።")
    return TRACK_STOP_PHONE

async def tracking_stop_phone(update, context):
    is_valid, phone = validate_phone(update.message.text.strip())
    if not is_valid:
        await update.message.reply_text(f"❌ {phone}")
        return TRACK_STOP_PHONE
    info = context.user_data.pop("tracking_stop")
    info.update({"phone": phone, "requester_id": update.effective_user.id})
    tracking_stops.append(info)
    idx = len(tracking_stops) - 1
    await update.message.reply_text("✅ ጥያቄው ለአድሚን ተልኳል።", reply_markup=main_menu(update.effective_user.id))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approvestop_{idx}")]])
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(admin_id, f"🛑 **ክትትል ማቆሚያ ጥያቄ**\nስም: {info['name']}\nስልክ: {phone}", reply_markup=kb)
        except: pass
    return ConversationHandler.END

async def admin_approve_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    info = tracking_stops[idx]
    await query.edit_message_text("✅ ክትትል ተቆሟል!")
    try: await context.bot.send_message(info["requester_id"], "✅ የክትትል ማቆሚያ ጥያቄዎ ጸድቋል!")
    except: pass

# ==================================================
# MAIN ROUTERS & OTHER
# ==================================================

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    if not is_user_registered(uid):
        await update.message.reply_text("⚠️ እባክዎ መጀመሪያ /start ይጫኑ።")
        return ConversationHandler.END

    clear_stale_user_state(context)
    
    if text == "🚚 ጭነት መለጠፍ": return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ": await find_cargo(update, context); return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ": return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ": await find_truck(update, context); return ConversationHandler.END
    if text == "🚐🚗🚛 የመኪናኪራይና ሽያጭ መለጠፍ": return await car_post_start(update, context)
    if text == "🔎 የኪራይ/የሽያጭመኪና መፈለግ": return await find_car_start(update, context)
    if text == "👤 የኔ መረጃ": await profile(update, context); return ConversationHandler.END
    if text == "📍 የጭነት ክትትል": await tracking_menu(update, context); return ConversationHandler.END
    if text == "📣 መረጃ ለደንበኞች/ደላሎች ለማጋራት" and is_super_admin(uid): return await broadcast_start(update, context)
    if text == "🤝 የግንኙነት ጥያቄዎች": await show_connection_requests(update, context); return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ": await update.message.reply_text("📋 በመስራት ላይ..."); return ConversationHandler.END
    
    return ConversationHandler.END

def main():
    if not TOKEN: raise RuntimeError("BOT_TOKEN missing")
    init_db()
    load_all_from_db()
    
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚐🚗🚛 የመኪናኪራይና ሽያጭ መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 የኪራይ/የሽያጭመኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የጭነት ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^📣 መረጃ ለደንበኞች/ደላሎች ለማጋራት$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
        ],
        states={
            START_PHONE: [MessageHandler(filters.CONTACT, start_phone_input)],
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [CallbackQueryHandler(cargo_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            
            FIND_CAR_SELECTION: [CallbackQueryHandler(car_post_type, pattern=r"^post_car_")],
            CAR_RENT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_rent_type)],
            CAR_RENT_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_rent_model)],
            CAR_RENT_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_rent_plate)],
            CAR_RENT_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, car_rent_photo)],
            CAR_RENT_PHONE: [CallbackQueryHandler(car_rent_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, car_rent_phone)],
            
            CAR_SALE_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_sale_type)],
            CAR_SALE_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_sale_model)],
            CAR_SALE_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_sale_plate)],
            CAR_SALE_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, car_sale_photo)],
            CAR_SALE_PHONE: [CallbackQueryHandler(car_sale_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, car_sale_phone)],
            
            SUPER_BROADCAST_TARGET: [CallbackQueryHandler(broadcast_target, pattern=r"^broadcast_(all|brokers)$")],
            SUPER_BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
            
            CONNECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [CallbackQueryHandler(connect_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
            
            TRACK_SHARE_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_address)],
            TRACK_SHARE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_name)],
            TRACK_SHARE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_phone)],
            TRACK_STOP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_name)],
            TRACK_STOP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)
    
    # Callbacks outside conversation
    application.add_handler(CallbackQueryHandler(find_car_list, pattern=r"^find_car_(sale|rent)$"))
    application.add_handler(CallbackQueryHandler(toggle_pii_cargo, pattern=r"^toggle_pii_cargo_"))
    application.add_handler(CallbackQueryHandler(toggle_pii_truck, pattern=r"^toggle_pii_truck_"))
    application.add_handler(CallbackQueryHandler(toggle_pii_car, pattern=r"^toggle_pii_car_"))
    application.add_handler(CallbackQueryHandler(share_post_prompt, pattern=r"^share_"))
    application.add_handler(CallbackQueryHandler(do_share_post, pattern=r"^do_share_"))
    application.add_handler(CallbackQueryHandler(connection_request, pattern=r"^connect_"))
    application.add_handler(CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_"))
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_"))
    application.add_handler(CallbackQueryHandler(admin_approve_gmaps, pattern=r"^approvegmaps_"))
    application.add_handler(CallbackQueryHandler(admin_approve_stop_request, pattern=r"^approvestop_"))
    
    print("🚀 TANA CARGO Bot is running...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
