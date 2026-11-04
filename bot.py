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
SUPPORT_USERNAME = "tanacargosupportbot"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanapage"

START_IMAGE_ID = "AgACAgQAAxkBAAFWw_Vq389auQ7yoeq8rNETEyhfn02JagACCw9rG-tuAVNIehm8-b92jwEAAwIAA3kAAz0E"

TRACCAR_URL = "http://demo2.traccar.org"
TRACCAR_TOKEN = os.getenv("TRACCAR_TOKEN", "")
TRACCAR_DEVICE_ID = os.getenv("TRACCAR_DEVICE_ID", "92643104")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or ("https://" + os.getenv("RENDER_EXTERNAL_HOSTNAME", "") if os.getenv("RENDER_EXTERNAL_HOSTNAME") else "")).rstrip("/")

CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
DB_PATH = "tana_cargo.db"

# ==================================================
# ETHIOPIAN CALENDAR
# ==================================================

def is_ethiopian_leap_year(et_year): return (et_year % 4) == 3
def is_gregorian_leap_year(gc_year): return (gc_year % 4 == 0 and gc_year % 100 != 0) or (gc_year % 400 == 0)
def et_new_year_gregorian(et_year):
    gc_year = et_year + 7
    start_day = 12 if is_gregorian_leap_year(gc_year + 1) else 11
    return datetime(gc_year, 9, start_day)
def et_to_gc(et_year, et_month, et_day):
    max_day = 6 if et_month == 13 and is_ethiopian_leap_year(et_year) else 5 if et_month == 13 else 30
    if et_month < 1 or et_month > 13 or et_day < 1 or et_day > max_day: raise ValueError("Invalid date")
    return et_new_year_gregorian(et_year) + timedelta(days=(et_month - 1) * 30 + et_day - 1)
def gc_to_et_date(gc_date):
    candidate_year = gc_date.year - 7
    start = et_new_year_gregorian(candidate_year)
    if gc_date < start:
        candidate_year -= 1
        start = et_new_year_gregorian(candidate_year)
    days = (gc_date - start).days
    month = days // 30 + 1
    day = days % 30 + 1
    if month == 13:
        max_day = 6 if is_ethiopian_leap_year(candidate_year) else 5
        if day > max_day: raise ValueError("Out of year")
    return candidate_year, month, day
def parse_flexible_date(date_text):
    raw = date_text.strip().split("\n")[0].strip()
    raw = " ".join(raw.split())
    for sep in ["-", ",", ".", " "]: raw = raw.replace(sep, "/")
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if len(parts) != 3: return None, "የቀን ቅርጸቱ ትክክል አይደለም። ምሳሌ፦ 11/02/2019"
    try: day, month, year = (int(p) for p in parts)
    except ValueError: return None, "ቀን/ወር/ዓመት በቁጥር መሆን አለበት።"
    if year < 1900 or year > 2100: return None, "ዓመት ትክክል አይደለም።"
    return {"day": day, "month": month, "year": year}, ""
def convert_calendar(date_parts, calendar):
    day, month, year = date_parts["day"], date_parts["month"], date_parts["year"]
    try:
        if calendar == "ET":
            if month < 1 or month > 13: return "", "", "ወር 1 እና 13 መካከል መሆን አለበት።"
            max_day = 6 if month == 13 and is_ethiopian_leap_year(year) else 5 if month == 13 else 30
            if day < 1 or day > max_day: return "", "", f"ቀኑ 1 እና {max_day} መካከል መሆን አለበት።"
            gc_obj = et_to_gc(year, month, day)
            et_year, et_month, et_day = year, month, day
        else:
            gc_obj = datetime(year, month, day)
            et_year, et_month, et_day = gc_to_et_date(gc_obj)
    except (ValueError, OverflowError): return "", "", "ያስገቡት ቀን ትክክል አይደለም።"
    return f"{et_day:02d}/{et_month:02d}/{et_year}", f"{gc_obj.day:02d}/{gc_obj.month:02d}/{gc_obj.year}", ""

# ==================================================
# DATABASE
# ==================================================

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, owner_name TEXT, owner_phone TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cargo_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, from_location TEXT, to_location TEXT, cargo_type TEXT, vehicle TEXT, size INTEGER, size_name TEXT, weight TEXT, date_et TEXT, date_gc TEXT, phone TEXT, price REAL, price_display TEXT, price_status TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS truck_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, truck_type TEXT, plate TEXT, capacity TEXT, route TEXT, address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS brokers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, username TEXT, phone TEXT, address TEXT, active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0, timestamp TEXT)")
    
    # Vehicle Listings (Cars, Bajaj, Motorcycles)
    cursor.execute("CREATE TABLE IF NOT EXISTS vehicle_listings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, category TEXT NOT NULL, vehicle_type TEXT NOT NULL, location TEXT NOT NULL, details TEXT DEFAULT '', price TEXT DEFAULT '', phone TEXT NOT NULL, photo_ids TEXT DEFAULT '[]', photo_status TEXT DEFAULT 'provided', active INTEGER DEFAULT 1, created_at TEXT NOT NULL)")
    
    cursor.execute("CREATE TABLE IF NOT EXISTS public_tracking_tokens (token TEXT PRIMARY KEY, device_id TEXT NOT NULL, status TEXT DEFAULT 'active', label TEXT DEFAULT 'TANA CARGO', created_at TEXT NOT NULL, expires_at TEXT)")
    conn.commit()
    conn.close()

def load_all_from_db():
    global cargo_posts, truck_posts, brokers
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT * FROM users").fetchall():
            u = dict(row)
            users[u["user_id"]] = {"name": u.get("name") or "", "username": u.get("username") or "", "id": u["user_id"]}
            if u.get("owner_phone"):
                users[u["user_id"]]["owner"] = {"name": u.get("owner_name") or "", "phone": u.get("owner_phone") or ""}
        for row in conn.execute("SELECT * FROM cargo_posts WHERE active = 1").fetchall():
            c = dict(row)
            c["from"] = c.pop("from_location")
            c["to"] = c.pop("to_location")
            c["type"] = c.pop("cargo_type")
            cargo_posts.append(c)
        for row in conn.execute("SELECT * FROM truck_posts WHERE active = 1").fetchall():
            t = dict(row)
            t["type"] = t.pop("truck_type")
            truck_posts.append(t)
        for row in conn.execute("SELECT * FROM brokers").fetchall():
            b = dict(row)
            b["active"] = bool(b.get("active"))
            b["approved"] = bool(b.get("approved"))
            brokers.append(b)
        print(f"✅ DB Loaded: {len(users)} users, {len(cargo_posts)} cargo, {len(truck_posts)} trucks.")
    except Exception as e:
        print(f"❌ DB LOAD ERROR: {e}")
    finally:
        conn.close()

def db_save_user(user_id, name, username, owner_name=None, owner_phone=None):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, name, username, owner_name, owner_phone) VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username, owner_name = COALESCE(excluded.owner_name, users.owner_name), owner_phone = COALESCE(excluded.owner_phone, users.owner_phone)", (user_id, name, username, owner_name, owner_phone))
    conn.commit()
    conn.close()

def get_total_users_count():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try: return 7000 + conn.cursor().execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally: conn.close()

# ==================================================
# HELPERS & MEMORY
# ==================================================

users, cargo_posts, truck_posts, brokers = {}, [], [], []
connection_requests, tracking_stops, gmaps_share_requests = [], [], []
pending_gmaps_shares = []
admin_view_pii = {} 

def is_user_registered(user_id):
    if user_id in users and users[user_id].get("owner", {}).get("phone"): return True
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]: return True
    finally: conn.close()
    return False

def get_registered_phone(user_id):
    user = users.get(user_id, {})
    phone = user.get("owner", {}).get("phone", "")
    if not phone:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        try:
            row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row: phone = row[0]
        finally: conn.close()
    return phone

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
def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"): return True
    return False

# ==================================================
# STATES
# ==================================================

(CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE, CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE) = range(10)
(TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE, TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE) = range(10, 17)
OWNER_NAME, OWNER_PHONE = range(17, 19)
(CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE) = range(19, 22)
(BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS) = range(22, 25)
(TRACK_SHARE_ADDRESS, TRACK_SHARE_NAME, TRACK_SHARE_PHONE, TRACK_STOP_NAME, TRACK_STOP_PHONE) = range(25, 30)
START_PHONE = 34
RENTAL_TARGET_NAME, RENTAL_TARGET_PHONE = 35, 36

# Super Admin Broadcast States
SUPER_BROADCAST_TARGET, SUPER_BROADCAST_TEXT, SUPER_BROADCAST_PHOTO = range(37, 40)

# Vehicle Listing (Rent/Sale/Bajaj/Motorcycle)
VEHICLE_LIST_TYPE, VEHICLE_LIST_LOCATION, VEHICLE_LIST_DETAILS, VEHICLE_LIST_PRICE, VEHICLE_LIST_PHOTOS = range(40, 45)

# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚗 የሽያጭና/ኪራይ መኪና መለጠፍ", "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ"],
            ["🏍🛺 የባጃጅ/ሞተር ሳይክል መሸጥ", "🔎 ባጃጅ/ሞተር ሳይክል መግዛት"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል", "🚐 የኪራይ ክትትል"],
            ["📚 የክትትል መዝገብ"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["📋 የጭነት ደላሎች መዝገብ"],
            ["🗂️ የኪራይ/ሽያጭ መዝገብ"],
        ]
        if is_super_admin(user_id):
            keyboard.append(["📣 መልእክት ለደንበኞች ለመላክ"])
        else:
            keyboard.append(["💳 የአገልግሎት ክፍያ ለመፈፀም"])
        keyboard.append(["📞 Support", "ℹ️ About"])
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚗 የሽያጭና/ኪራይ መኪና መለጠፍ", "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ"],
            ["🏍🛺 የባጃጅ/ሞተር ሳይክል መሸጥ", "🔎 ባጃጅ/ሞተር ሳይክል መግዛት"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል", "🚐 የኪራይ ክትትል"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
            ["🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ"],
            ["💳 የአገልግሎት ክፍያ ለመፈጸም"],
            ["📞 Support", "ℹ️ About"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def phone_request_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def confirm_phone_keyboard(phone):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ ትክክል ነው ({phone})", callback_data="confirm_reg_phone")],
        [InlineKeyboardButton("✍️ ሌላ ስልክ ቁጥር አስገባ", callback_data="enter_new_phone")]
    ])

def car_rent_sale_action_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 የኪራይ መኪና", callback_data="action_car_rent")],
        [InlineKeyboardButton("🚙 የሽያጭ መኪና", callback_data="action_car_sale")]
    ])

def find_car_action_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 የኪራይ መኪና ፈልግ", callback_data="find_car_rent")],
        [InlineKeyboardButton("🚙 የሽያጭ መኪና ፈልግ", callback_data="find_car_sale")]
    ])

def bajaj_motor_action_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏍 ሞተር ሳይክል", callback_data="action_motor_sale")],
        [InlineKeyboardButton("🛺 ባጃጅ", callback_data="action_bajaj_sale")]
    ])

def find_bajaj_motor_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏍 ሞተር ሳይክል ፈልግ", callback_data="find_motor")],
        [InlineKeyboardButton("🛺 ባጃጅ ፈልግ", callback_data="find_bajaj")]
    ])
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    
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
    
    try: db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=result)
    except Exception as e: print(f"DB SAVE USER ERROR: {e}")
        
    await update.message.reply_text(
        "እናመሰግናለን ምዝገባዎን አጠናቀዋል ከታች ካሉት ምናሌዎች የፈለጉትን መጠቀም ይችላሉ።",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})
    owner = user.get("owner", {})
    
    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user.get('name', 'N/A')}\n"
        f"🔗 Username፦ @{user.get('username') or 'የለም'}\n"
        f"📞 ስልክ፦ {owner.get('phone', 'N/A')}\n"
        f"\n👥 **ጠቅላላ ተጠቃሚዎች፦** {get_total_users_count():,}\n"
    )
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        f"📞 TANA CARGO የጣና ጭነት እገዛ\n\n☎️ {SUPPORT_PHONE}\nTelegram: @{SUPPORT_USERNAME}\n\n🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text(
        "🚚 **TANA CARGO | ጣና ጭነት**\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት የተዘጋጀ አገልግሎት ነው።\n\n"
        "🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu(update.effective_user.id)
    )

async def service_payment(update, context):
    await update.message.reply_text(
        "💳 የአገልግሎት ክፍያ መረጃ\n━━━━━━━━━━━━━━━━━━━━\n\n🏦 CBE\n🔢 1000031098231\n👤 Solomon Sisay\n\n📱 Telebirr\n📱 0918132914\n👤 Solomon Sisay\n\n📌 ክፍያ ካደረጉ በኋላ ደረሰኙን ወይም ስክሪንሾቱን ወደ @tanapage ይላኩ።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END
async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ለሁሉም ደንበኞች", callback_data="broadcast_all")],
        [InlineKeyboardButton("ለደላሎች ብቻ", callback_data="broadcast_brokers")],
        [InlineKeyboardButton("ለአንድ ደንበኛ", callback_data="broadcast_one")]
    ])
    await update.message.reply_text("📢 **መልእክት ለደንበኞች ለመላክ**\n\nለማን ማላክ ይፈልጋሉ?", reply_markup=kb)
    return SUPER_BROADCAST_TARGET

async def broadcast_target_choice(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["broadcast_target"] = query.data.replace("broadcast_", "")
    
    if context.user_data["broadcast_target"] == "one":
        # Simplified for this logic, just send to a single user ID
        await query.message.reply_text("📝 የደንበኛውን **Telegram Chat ID** ያስገቡ፦")
        return SUPER_BROADCAST_TARGET
    else:
        await query.message.reply_text("📝 የሚያጋሩትን **መልእክት (ጽሁፍ)** ይጻፉ።")
        return SUPER_BROADCAST_TEXT

async def broadcast_text_received(update, context):
    text = update.message.text.strip()
    target = context.user_data.get("broadcast_target")
    
    if target == "one" and not context.user_data.get("single_target_id"):
        context.user_data["single_target_id"] = text
        await update.message.reply_text("📝 ለዚህ ደንበኛ የሚላከውን **መልእክት (ጽሁፍ)** ይጻፉ።")
        return SUPER_BROADCAST_TEXT
        
    context.user_data["broadcast_msg_text"] = text
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("የለኝም (ፎቶ ሳይኖር ላክ)", callback_data="broadcast_photo_none")]])
    await update.message.reply_text("📸 ከመልእክቱ ጋር 1 ወይም 2 ፎቶ ማያያዝ ይፈልጋሉ?\nፎቶዎቹን ይላኩ ወይም ከታች ያለውን ይጫኑ።", reply_markup=kb)
    return SUPER_BROADCAST_PHOTO

async def broadcast_photo_received(update, context):
    photos = context.user_data.setdefault("broadcast_photos", [])
    if len(photos) >= 2:
        await update.message.reply_text("ከ2 ፎቶ በላይ መላክ አይቻልም።")
        return SUPER_BROADCAST_PHOTO
    
    photos.append(update.message.photo[-1].file_id)
    if len(photos) == 1:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("ፎቶ ጨርሻለሁ (አሁን ላክ)", callback_data="broadcast_photo_done")]])
        await update.message.reply_text("✅ 1ኛ ፎቶ ተቀብለናል። 2ኛ ፎቶ ይላኩ ወይም ከታች ያለውን ይጫኑ።", reply_markup=kb)
        return SUPER_BROADCAST_PHOTO
    return await execute_broadcast(update, context)

async def broadcast_photo_callback(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "broadcast_photo_none":
        return await execute_broadcast(update, context)
    return await execute_broadcast(update, context)

async def execute_broadcast(update, context):
    text = context.user_data.get("broadcast_msg_text", "")
    photos = context.user_data.get("broadcast_photos", [])
    target = context.user_data.get("broadcast_target")
    
    final_text = (
        "📢 **ከTANACARGO & LOGISTIC የተላከ**\n\n"
        f"{text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 እባክዎን የሚፈልግ ደንበኛ እጆት ላይ ካለ\n"
        f"📞 በ {SUPPORT_PHONE} ይደውሉ\n"
        f"ወይም በቴሌግራም @{SUPPORT_USERNAME} ላይ የፃፉልን የሚል ይኖረዋል።"
    )
    
    targets = []
    if target == "all":
        conn = sqlite3.connect(DB_PATH)
        targets = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
        conn.close()
    elif target == "brokers":
        targets = [b["user_id"] for b in brokers if b.get("approved") and b.get("active")]
    else:
        try:
            targets = [int(context.user_data.get("single_target_id"))]
        except:
            targets = []
            
    sent, failed = 0, 0
    for uid in targets:
        try:
            if photos:
                # Send with photos (as media group or individually with caption)
                if len(photos) == 1:
                    await context.bot.send_photo(uid, photo=photos[0], caption=final_text, parse_mode="Markdown")
                else:
                    media = [InputMediaPhoto(photos[0], caption=final_text, parse_mode="Markdown"), InputMediaPhoto(photos[1])]
                    await context.bot.send_media_group(uid, media=media)
            else:
                await context.bot.send_message(uid, final_text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except: failed += 1
            
    reply_target = update.message if hasattr(update.message, 'reply_text') else update.callback_query.message
    await reply_target.reply_text(f"✅ መልእክቱ ተልኳል።\n📨 የደረሰው: {sent}\n⚠️ ያልደረሰው: {failed}", reply_markup=main_menu(update.effective_user.id))
    
    context.user_data.pop("broadcast_msg_text", None)
    context.user_data.pop("broadcast_photos", None)
    context.user_data.pop("broadcast_target", None)
    context.user_data.pop("single_target_id", None)
    return ConversationHandler.END
# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    context.user_data.clear()
    context.user_data["cargo"] = {}
    await update.message.reply_text("🚚 **ጭነት መለጠፍ**\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር", reply_markup=main_menu(update.effective_user.id))
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።\nምሳሌ፦ ሲሚንቶ / እህል")
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
    sizes = {"size_100": (100, "ሙሉ ጭነት"), "size_50": (50, "ግማሽ ጭነት")}
    if query.data not in sizes: return CARGO_SIZE
    size, size_name = sizes[query.data]
    context.user_data["cargo"]["size"] = size
    context.user_data["cargo"]["size_name"] = size_name
    await query.edit_message_text(f"📦 የተመረጠው፦ {size_name}\n\n6️⃣ የጭነቱን ክብደት ይግለጹ።\nበቶን፣ በቢያጆ፣ በኩንታል")
    return CARGO_WEIGHT

async def cargo_weight(update, context):
    weight = update.message.text.strip()
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text("7️⃣ የሚጫንበትን ቀን ካላንደር ይምረጡ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇪🇹 ካላንደር", callback_data="cal_et")]]))
    return CARGO_CALENDAR

async def cargo_calendar(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["cargo"]["calendar"] = "ET"
    await query.edit_message_text("ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\nምሳሌ፦ 26/1/2019")
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    parts, err = parse_flexible_date(date_text)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return CARGO_DATE
    et_date, gc_date, err = convert_calendar(parts, "ET")
    if err:
        await update.message.reply_text(f"❌ {err}")
        return CARGO_DATE
        
    cargo = context.user_data.setdefault("cargo", {})
    cargo["date_et"], cargo["date_gc"] = et_date, gc_date
    cargo["expiry_date"] = (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(
        f"📅 ቀን (ET): {et_date}\n\n8️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_registered_phone(update.effective_user.id)
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
    if raw in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price, price_status, price_display = None, "agreement", "በስምምነት"
    else:
        try:
            price = float(raw.replace(",", "").replace("ብር", "").strip())
            price_status, price_display = "fixed", f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text("❌ ዋጋውን በቁጥር ወይም 'በስምምነት' ይጻፉ።")
            return CARGO_PRICE
            
    cargo = context.user_data["cargo"]
    cargo.update({"price": price, "price_status": price_status, "price_display": price_display, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer"})
    cargo_posts.append(cargo.copy())
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("INSERT INTO cargo_posts (user_id, from_location, to_location, cargo_type, vehicle, size, size_name, weight, date_et, date_gc, phone, price, price_display, price_status, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (cargo["user_id"], cargo["from"], cargo["to"], cargo["type"], cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"], cargo["date_et"], cargo["date_gc"], cargo["phone"], cargo["price"], cargo["price_display"], cargo["price_status"], cargo["timestamp"], cargo["poster_type"]))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ **ጭነትዎ በትክክል ተመዝግቧል!**\n\nለበለጠ መረጃ 👉 @tanapage ይጠቀሙ።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# ==================================================
# TRUCK POSTING
# ==================================================

async def truck_start(update, context):
    context.user_data.clear()
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
    await update.message.reply_text("4️⃣ የመንገድ ዝርዝር/አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS

async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    context.user_data["truck"]["route"] = "—"
    await update.message.reply_text("5️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER

async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text.strip()
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(
        f"6️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return TRUCK_PHONE

async def truck_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_registered_phone(update.effective_user.id)
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
    truck.update({"phone": phone, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer"})
    truck_posts.append(truck.copy())
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("INSERT INTO truck_posts (user_id, truck_type, plate, capacity, route, address, driver, phone, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (truck["user_id"], truck["type"], truck["plate"], truck["capacity"], truck["route"], truck["address"], truck["driver"], truck["phone"], truck["timestamp"], truck["poster_type"]))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ **መኪናዎ በትክክል ተመዝግቧል!**\n\nለበለጠ መረጃ 👉 @tanapage ይጠቀሙ።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END
# ==================================================
# CAR RENTAL / SALE POSTING (Combined Menu)
# ==================================================

async def car_post_start(update, context):
    context.user_data.clear()
    await update.message.reply_text("🚗 **የሽያጭና/ኪራይ መኪና መለጠፍ**\n\nየሚለጥፉትን ዓይነት ይምረጡ፦", reply_markup=car_rent_sale_action_kb())
    return VEHICLE_LIST_TYPE

async def vehicle_listing_type_action(update, context):
    query = update.callback_query
    await query.answer()
    action = query.data # action_car_rent or action_car_sale
    
    if action == "action_car_rent":
        context.user_data["vehicle_listing"] = {"category": "rental", "photos": []}
        await query.edit_message_text("🚗 **የኪራይ መኪና ማስመዝገብ**\n\n1️⃣ የመኪናውን አይነት/ሞዴል ይጻፉ።")
    elif action == "action_car_sale":
        context.user_data["vehicle_listing"] = {"category": "sale", "photos": []}
        await query.edit_message_text("🚙 **የሽያጭ መኪና ማስመዝገብ**\n\n1️⃣ የመኪናውን አይነት/ሞዴል ይጻፉ።")
    elif action == "action_motor_sale":
        context.user_data["vehicle_listing"] = {"category": "motor", "photos": []}
        await query.edit_message_text("🏍 **የሞተር ሳይክል ማስመዝገብ**\n\n1️⃣ የሞተሩን አይነት ይጻፉ።")
    elif action == "action_bajaj_sale":
        context.user_data["vehicle_listing"] = {"category": "bajaj", "photos": []}
        await query.edit_message_text("🛺 **የባጃጅ ማስመዝገብ**\n\n1️⃣ የባጃጁን አይነት ይጻፉ።")
        
    return VEHICLE_LIST_LOCATION

async def vehicle_listing_location(update, context):
    context.user_data["vehicle_listing"]["vehicle_type"] = update.message.text.strip()
    cat = context.user_data["vehicle_listing"]["category"]
    
    if cat == "motor":
        await update.message.reply_text("2️⃣ የሞተሩን ሞዴል (አ/ም) ይጻፉ።")
        context.user_data["vehicle_listing"]["step"] = "model"
        return VEHICLE_LIST_DETAILS
    elif cat == "bajaj":
        await update.message.reply_text("2️⃣ የባጃጁን ታርጋ ይጻፉ።")
        context.user_data["vehicle_listing"]["step"] = "plate"
        return VEHICLE_LIST_DETAILS
    else:
        await update.message.reply_text("2️⃣ መኪናው የሚገኝበትን ከተማ/ቦታ ይጻፉ።")
        context.user_data["vehicle_listing"]["step"] = "location"
        return VEHICLE_LIST_DETAILS

async def vehicle_listing_details(update, context):
    text = update.message.text.strip()
    listing = context.user_data["vehicle_listing"]
    
    if listing.get("step") == "model": # For motor
        listing["model"] = text
        await update.message.reply_text("3️⃣ የሞተሩን CC ይጻፉ።")
        listing["step"] = "cc"
        return VEHICLE_LIST_DETAILS
    elif listing.get("step") == "cc": # For motor
        listing["cc"] = text
        await update.message.reply_text("4️⃣ የመሸጫ ዋጋ ይጻፉ።\nምሳሌ፦ 150,000 ብር")
        listing["step"] = "price"
        return VEHICLE_LIST_PRICE
    elif listing.get("step") == "plate": # For bajaj
        listing["plate"] = text
        await update.message.reply_text("3️⃣ የመሸጫ ዋጋ ይጻፉ።")
        listing["step"] = "price"
        return VEHICLE_LIST_PRICE
    else: # For cars
        listing["location"] = text
        await update.message.reply_text("3️⃣ ስለ መኪናው ተጨማሪ መረጃ ይጻፉ።\nምሳሌ፦ የሞዴል ዓመት፣ ሁኔታ።")
        listing["step"] = "details"
        return VEHICLE_LIST_DETAILS

async def vehicle_listing_price(update, context):
    text = update.message.text.strip()
    listing = context.user_data["vehicle_listing"]
    
    if listing.get("step") == "details": # For cars, this is the price step
        listing["details"] = text
        await update.message.reply_text("4️⃣ የመሸጫ/የኪራይ ዋጋ ይጻፉ።")
        listing["step"] = "price"
        return VEHICLE_LIST_PRICE
    else:
        listing["price"] = text
        listing["phone"] = get_registered_phone(update.effective_user.id)
        listing["name"] = update.effective_user.full_name
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("ለጊዜው የፎቶ ማስረጃ የለም", callback_data="vehicle_photo_none")]])
        await update.message.reply_text("5️⃣ የመኪናውን/ሞተሩን 1 ወይም 2 ፎቶ ይላኩ።\nፎቶ ከሌለዎት ከታች ያለውን አማራጭ ይጫኑ።", reply_markup=kb)
        return VEHICLE_LIST_PHOTOS

async def vehicle_listing_photo(update, context):
    listing = context.user_data.get("vehicle_listing", {})
    photos = listing.setdefault("photos", [])
    if not update.message.photo:
        await update.message.reply_text("❌ እባክዎ ፎቶ ይላኩ ወይም 'የለም' የሚለውን ይጫኑ።")
        return VEHICLE_LIST_PHOTOS
    if len(photos) >= 2:
        await update.message.reply_text("ከ2 ፎቶ በላይ መላክ አይቻልም።")
        return VEHICLE_LIST_PHOTOS
    photos.append(update.message.photo[-1].file_id)
    if len(photos) == 1:
        await update.message.reply_text("✅ 1ኛ ፎቶ ተቀብለናል። 2ኛ ፎቶ ይላኩ ወይም 'ፎቶ ጨርሻለሁ' ይጫኑ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ፎቶ ጨርሻለሁ", callback_data="vehicle_photo_done")]]))
        return VEHICLE_LIST_PHOTOS
    return await save_vehicle_listing(update, context, photo_status="provided")

async def vehicle_listing_photo_callback(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "vehicle_photo_none":
        return await save_vehicle_listing(update, context, photo_status="missing")
    return await save_vehicle_listing(update, context, photo_status="provided")

async def save_vehicle_listing(update, context, photo_status):
    listing = context.user_data.get("vehicle_listing", {})
    uid = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.execute("INSERT INTO vehicle_listings (user_id,category,vehicle_type,location,details,price,phone,photo_ids,photo_status,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (uid, listing.get("category"), listing.get("vehicle_type"), listing.get("location", ""), json.dumps({k: v for k, v in listing.items() if k in ["model", "cc", "plate", "details"]}), listing.get("price"), listing.get("phone"), json.dumps(listing.get("photos", [])), photo_status, 1, datetime.now().astimezone().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()
    
    cat_label = "መኪና" if listing.get("category") in ["rental", "sale"] else ("ሞተር ሳይክል" if listing.get("category") == "motor" else "ባጃጅ")
    msg = f"✅ **{cat_label}ዎ ተመዝግቧል!**\n\n🚘 {listing.get('vehicle_type')}\n💰 {listing.get('price')}\n\nእናመሰግናለን ምዝገባዎን ጨርሰዋል ፈላጊ ደንበኛ ሲኖር እንደውልሎታለን። በተጨማሪም በቴሌግራም እናሳውቆዎታለን።\n\nለበለጠ መረጃ 👉 @tanapage ይጠቀሙ።"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=main_menu(uid))
    else:
        await update.message.reply_text(msg, reply_markup=main_menu(uid))
        
    context.user_data.pop("vehicle_listing", None)
    return ConversationHandler.END

# ==================================================
# BAJAJ / MOTORCYCLE POSTING
# ==================================================

async def bajaj_motor_post_start(update, context):
    context.user_data.clear()
    await update.message.reply_text("🏍🛺 **የባጃጅ/ሞተር ሳይክል መሸጥ**\n\nየሚሸጡትን ዓይነት ይምረጡ፦", reply_markup=bajaj_motor_action_kb())
    return VEHICLE_LIST_TYPE
    # ==================================================
# FIND CARGO / TRUCK / VEHICLES
# ==================================================

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    if not active_cargos:
        return await update.message.reply_text("📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።", reply_markup=main_menu(update.effective_user.id))
    
    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for i, cargo in enumerate(active_cargos):
        real_index = cargo_posts.index(cargo)
        admin_info = f"👤 ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n" if is_admin_user and show_pii else ""
        msg = f"📦 **ጭነት {i+1:03d}**\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n📦 አይነት፦ {cargo['type']}\n🚛 መኪና፦ {cargo['vehicle']}\n⚖️ ክብደት፦ {cargo['weight']}\n💰 ዋጋ፦ {cargo.get('price_display', 'በስምምነት')}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
        
        kb_buttons = [[InlineKeyboardButton(f"የመረጡት ጭነት {i+1:03d} ከሆነ እዚጋ ያጫኑ 👈", callback_data=f"connect_{real_index}")]]
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_cargo_{real_index}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_cargo_{real_index}")])
            
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]
    if not active_trucks:
        return await update.message.reply_text("🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።", reply_markup=main_menu(update.effective_user.id))
        
    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for i, truck in enumerate(active_trucks):
        real_index = truck_posts.index(truck)
        admin_info = f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n" if is_admin_user and show_pii else ""
        msg = f"🚛 **መኪና {i+1:03d}**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n📍 አድራሻ፦ {truck['address']}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
        
        kb_buttons = [[InlineKeyboardButton(f"የመረጡት መኪና {i+1:03d} ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{real_index}")]]
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_truck_{real_index}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_truck_{real_index}")])
            
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def find_vehicle_listings(update, context, category=None):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        category = query.data.split("_")[-1] # rent or sale or motor or bajaj
        await query.message.delete()
    else:
        await update.message.reply_text("🔎 **የሽያጭና/ኪራይ መኪናዎችን ፈልግ**\n\nየሚፈልጉትን ዓይነት ይምረጡ፦", reply_markup=find_car_action_kb())
        return
        
    cat_map = {"rent": "rental", "sale": "sale", "motor": "motor", "bajaj": "bajaj"}
    db_cat = cat_map.get(category, category)
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try: records = [dict(r) for r in conn.execute("SELECT * FROM vehicle_listings WHERE category=? AND active=1 ORDER BY id DESC LIMIT 30", (db_cat,)).fetchall()]
    finally: conn.close()
    
    label = "የኪራይ መኪና" if category == "rent" else "የሽያጭ መኪና" if category == "sale" else "ሞተር ሳይክል" if category == "motor" else "ባጃጅ"
    if not records:
        return await context.bot.send_message(update.effective_chat.id, f"🔎 አሁን የተመዘገበ {label} የለም።", reply_markup=main_menu(update.effective_user.id))
        
    is_admin_user = is_admin(update.effective_user.id)
    show_pii = admin_view_pii.get(update.effective_user.id, False)

    for rec in records:
        photos = json.loads(rec.get("photo_ids") or "[]")
        details = json.loads(rec.get("details") or "{}")
        
        info_text = f"🚘 {rec['vehicle_type']}\n"
        if rec['category'] == 'motor': info_text += f"📝 ሞዴል: {details.get('model','N/A')}\n⚙️ CC: {details.get('cc','N/A')}\n"
        elif rec['category'] == 'bajaj': info_text += f"🔢 ታርጋ: {details.get('plate','N/A')}\n"
        else: info_text += f"📍 {rec['location']}\n📝 {details.get('details','N/A')}\n"
        
        admin_info = f"👤 ባለቤት: {rec.get('name', 'N/A')}\n📞 ስልክ: {rec.get('phone', 'N/A')}\n" if is_admin_user and show_pii else ""
        text_msg = f"{info_text}💰 {rec['price']}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
        
        kb_buttons = [[InlineKeyboardButton("ይህንን ከመረጡ 👈 ይጫኑ", callback_data=f"car_select_{category}_{rec['id']}")]]
        if is_admin_user:
            toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
            kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_car_{category}_{rec['id']}")])
            kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_car_{category}_{rec['id']}")])
            
        kb = InlineKeyboardMarkup(kb_buttons)
        try:
            if photos:
                await context.bot.send_photo(update.effective_chat.id, photo=photos[0], caption=text_msg, reply_markup=kb)
            else:
                await context.bot.send_message(update.effective_chat.id, text_msg, reply_markup=kb)
        except:
            await context.bot.send_message(update.effective_chat.id, text_msg, reply_markup=kb)

# ==================================================
# PII TOGGLE & SHARE FEATURE
# ==================================================

async def toggle_pii_cargo(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    admin_view_pii[update.effective_user.id] = not admin_view_pii.get(update.effective_user.id, False)
    index = int(query.data.split("_")[-1])
    cargo = cargo_posts[index]
    show_pii = admin_view_pii[update.effective_user.id]
    
    admin_info = f"👤 ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n" if show_pii else ""
    msg = f"📦 **ጭነት**\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n📦 አይነት፦ {cargo['type']}\n🚛 መኪና፦ {cargo['vehicle']}\n⚖️ ክብደት፦ {cargo['weight']}\n💰 ዋጋ፦ {cargo.get('price_display', 'በስምምነት')}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
    
    kb_buttons = [[InlineKeyboardButton("የመረጡት ጭነት ከሆነ እዚጋ ያጫኑ 👈", callback_data=f"connect_{index}")]]
    toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
    kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_cargo_{index}")])
    kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_cargo_{index}")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def toggle_pii_truck(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    admin_view_pii[update.effective_user.id] = not admin_view_pii.get(update.effective_user.id, False)
    index = int(query.data.split("_")[-1])
    truck = truck_posts[index]
    show_pii = admin_view_pii[update.effective_user.id]
    
    admin_info = f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n" if show_pii else ""
    msg = f"🚛 **መኪና**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n📍 አድራሻ፦ {truck['address']}\n{admin_info}━━━━━━━━━━━━━━━━━━━━"
    
    kb_buttons = [[InlineKeyboardButton("የመረጡት መኪና ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{index}")]]
    toggle_label = "🙈 OFF (ደብቅ)" if show_pii else "👁️ ON (አሳይ)"
    kb_buttons.append([InlineKeyboardButton(f"ሙሉ መረጃ: {toggle_label}", callback_data=f"toggle_pii_truck_{index}")])
    kb_buttons.append([InlineKeyboardButton("📤 Share/Copy አድርግ", callback_data=f"share_truck_{index}")])
    
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb_buttons))

async def share_listing_prompt(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    parts = query.data.split("_")
    item_type = parts[1]
    index = parts[2] 
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ለሁሉም ደንበኞች", callback_data=f"do_share_{item_type}_{index}_all")],
        [InlineKeyboardButton("ለደላሎች ብቻ", callback_data=f"do_share_{item_type}_{index}_brokers")]
    ])
    await query.message.reply_text("📤 ይህንን መረጃ ለማን ማጋራት ይፈልጋሉ?", reply_markup=kb)

async def do_share_post(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    
    parts = query.data.split("_")
    item_type = parts[2]
    index = parts[3]
    target = parts[4]
    
    text = ""
    if item_type == "cargo":
        item = cargo_posts[int(index)]
        text = f"📦 **አዲስ ጭነት!**\n📍 {item['from']} ➡️ {item['to']}\n📦 {item['type']}\n🚛 {item['vehicle']}\n⚖️ {item['weight']}\n💰 {item.get('price_display', 'በስምምነት')}"
    elif item_type == "truck":
        item = truck_posts[int(index)]
        text = f"🚛 **አዲስ የጭነት መኪና!**\n🚘 {item['type']}\n⚖️ {item['capacity']}\n🛣️ {item['route']}"
    elif item_type == "car":
        cat = parts[3] # rent or sale
        real_id = int(parts[4])
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        item = dict(conn.execute("SELECT * FROM vehicle_listings WHERE id=?", (real_id,)).fetchone())
        conn.close()
        label = "የመኪና ኪራይ" if item["category"] == "rental" else "የመኪና ሽያጭ"
        text = f"🚗 **{label}!**\n🚘 {item['vehicle_type']}\n📍 {item['location']}\n💰 {item['price']}"
        
    final_text = f"📢 **ከTANACARGO & LOGISTIC የተላከ**\n\n{text}\n\n━━━━━━━━━━━━━━━━━━━━\n📌 እባክዎን የሚፈልግ ደንበኛ እጆት ላይ ካለ\n📞 በ {SUPPORT_PHONE} ይደውሉ\nወይም በቴሌግራም @{SUPPORT_USERNAME} ላይ የፃፉልን የሚል ይኖረዋል።"
    
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
# ==================================================
# CONNECTIONS
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
    
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(
        f"2️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።",
        reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown"
    )
    return CONNECT_PHONE

async def connect_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "confirm_reg_phone":
            phone = get_registered_phone(update.effective_user.id)
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
    requester = update.effective_user
    
    req_data = {"type": info["type"], "item_index": info["item_index"], "requester_id": requester.id, "requester_name": info["name"], "requester_phone": info["phone"], "requester_address": info["requester_address"], "status": "admin_confirming", "created_at": datetime.now().isoformat()}
    connection_requests.append(req_data)
    
    await update.message.reply_text("✅ እናመሰግናለን! አድሚን አጽድቆ መረጃውን ይልክልዎታል።", reply_markup=main_menu(requester.id))
    
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(admin_id, f"🔔 **አዲስ ግንኙነት ጥያቄ!**\n👤 ስም: {info['name']}\n📞 ስልክ: {info['phone']}\n👉 '🤝 የግንኙነት ጥያቄዎች' ምናሌ ይመልከቱ።")
        except: pass
    return ConversationHandler.END

async def show_connection_requests(update, context):
    if not is_admin(update.effective_user.id): return
    if not connection_requests: return await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።")
    
    for i, req in enumerate(connection_requests):
        if req["status"] == "deleted" or req["status"] == "completed": continue
        desc = f"📦 {cargo_posts[req['item_index']]['from']}" if req["type"] == "cargo" else f"🚛 {truck_posts[req['item_index']]['type']}"
        text = f"📥 **ጥያቄ #{i + 1}**\n{desc}\n👤 ስም: {req.get('requester_name')}\n📞 ስልክ: {req.get('requester_phone')}\n📌 ሁኔታ: {req['status']}"
        buttons = [[InlineKeyboardButton("✅ Approve", callback_data=f"adminright_{i}"), InlineKeyboardButton("❌ Reject", callback_data=f"adminxdelete_{i}")]]
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
# TRACKING & GPS (Fixed HTML)
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
    tracking_stops.append(info)
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
# HEALTH SERVER & TRACCAR LIVE MAP
# ==================================================

def create_public_tracking_link(label="TANA CARGO"):
    token = secrets.token_urlsafe(24)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("INSERT INTO public_tracking_tokens (token, device_id, status, label, created_at) VALUES (?, ?, 'active', ?, ?)", (token, str(TRACCAR_DEVICE_ID), label, datetime.now().astimezone().isoformat(timespec="seconds")))
        conn.commit()
    finally: conn.close()
    return token

def get_traccar_location(device_id):
    if not TRACCAR_TOKEN: return None
    try:
        headers = {"Authorization": f"Bearer {TRACCAR_TOKEN}"}
        res = requests.get(f"{TRACCAR_URL}/api/devices?uniqueId={device_id}", headers=headers, timeout=10)
        if res.status_code != 200: return None
        devices = res.json()
        if not devices: return None
        pos_id = devices[0].get("positionId")
        if not pos_id: return None
        res_pos = requests.get(f"{TRACCAR_URL}/api/positions?id={pos_id}", headers=headers, timeout=10)
        positions = res_pos.json()
        if not positions: return None
        pos = positions[0]
        return {"latitude": pos.get("latitude"), "longitude": pos.get("longitude"), "address": pos.get("address", "አድራሻ አልተገኘም"), "timestamp": pos.get("fixTime", "")[:16]}
    except: return None

def get_public_tracking_token(token):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM public_tracking_tokens WHERE token=? AND status='active'", (token,)).fetchone()
        return dict(row) if row else None
    finally: conn.close()

class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.strip("/")
        if path == "": return self._send(200, "text/plain", "TANA CARGO is running")
        parts = path.split("/")
        if len(parts) == 2 and parts[0] in ("track", "api-track"):
            token = unquote(parts[1])
            record = get_public_tracking_token(token)
            if not record: return self._send(404, "text/html", "<h2>TANA CARGO</h2><p>ይህ የክትትል ሊንክ ተዘግቷል ወይም አልተገኘም።</p>")
            if parts[0] == "api-track":
                loc = get_traccar_location(record["device_id"])
                if not loc: return self._send(503, "application/json", json.dumps({"ok": False, "message": "GPS መረጃ ገና አልተገኘም።"}))
                return self._send(200, "application/json", json.dumps({"ok": True, **loc}))
            
            # Branded Live Map Page (Fixed Header, removed offline text, added car icon)
            page = r'''<!doctype html><html lang="am"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TANA CARGO Live</title><style>body{margin:0;background:#f2f5f7;font-family:sans-serif;color:#17212b}.top{background:#174d75;color:white;padding:18px;text-align:center}.top b{font-size:22px}.wrap{padding:16px;max-width:900px;margin:auto}.card{background:white;border-radius:14px;padding:16px;box-shadow:0 4px 12px #0001;margin-bottom:12px}#map{height:60vh;min-height:350px;border-radius:12px;background:#e4e9ed;overflow:hidden}iframe{width:100%;height:100%;border:0}.muted{color:#657786;font-size:14px}a{color:#1769aa}</style></head><body><div class="top"><b>🚐 TANA CARGO | ጣና ጭነት</b><div>ከTANACARGO & LOGISTIC የተላከ</div></div><div class="wrap"><div class="card"><b>🚗 የቀጥታ የመኪና አካባቢ</b><p id="status" class="muted">🚗 GPS መረጃ በመፈለግ ላይ...</p><div id="map">GPS ቦታ ሲገኝ ካርታው ይታያል።</div><a id="google" target="_blank" style="display:none">በGoogle Maps ክፈት</a></div><div class="muted" style="text-align:center;">ይህ የTANA CARGO የክትትል ገጽ ነው። Traccar Client መተግበሪያ ውስጥ መግባት አያስፈልግም።<br>👉 @tanapage</div></div><script>const api='/api-track/TOKEN_VALUE';async function refresh(){try{const r=await fetch(api,{cache:'no-store'});const d=await r.json();if(!d.ok){document.getElementById('status').textContent='🚗 '+d.message;return}const lat=Number(d.latitude),lon=Number(d.longitude);if(!Number.isFinite(lat))throw Error();document.getElementById('status').textContent='🚗 📍 '+(d.address||'አድራሻ አልተገኘም');document.getElementById('map').innerHTML='<iframe src="https://maps.google.com/maps?q='+lat+','+lon+'&z=15&output=embed"></iframe>';const a=document.getElementById('google');a.href='https://www.google.com/maps?q='+lat+','+lon;a.style.display='inline-block'}catch(e){document.getElementById('status').textContent='🚗 GPS መረጃ ማግኘት አልተቻለም።'}}refresh();setInterval(refresh,10000);</script></body></html>'''
            page = page.replace("TOKEN_VALUE", token)
            return self._send(200, "text/html", page)
        return self._send(404, "text/plain", "Not found")

    def log_message(self, format, *args): return

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"✅ Health server running on port {port}")
# ==================================================
# CANCEL & TEXT ROUTERS
# ==================================================

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner",
                "connect_info", "sending_admin_message",
                "tracking_update_code", "tracking_lookup_waiting",
                "track_new", "cust_track_phone_only", "vehicle_listing",
                "gmaps_share", "tracking_stop"):
        if key not in keep:
            context.user_data.pop(key, None)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    
    if not is_user_registered(uid):
        await update.message.reply_text("⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**")
        return ConversationHandler.END

    clear_stale_user_state(context)
    if text == "🚚 ጭነት መለጠፍ": return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ": await find_cargo(update, context); return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ": return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ": await find_truck(update, context); return ConversationHandler.END
    if text == "🚗 የሽያጭና/ኪራይ መኪና መለጠፍ": return await car_post_start(update, context)
    if text == "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ": await find_vehicle_listings(update, context); return ConversationHandler.END
    if text == "🏍🛺 የባጃጅ/ሞተር ሳይክል መሸጥ": return await bajaj_motor_post_start(update, context)
    if text == "🔎 ባጃጅ/ሞተር ሳይክል መግዛት": await update.message.reply_text("የሚፈልጉትን ዓይነት ይምረጡ፦", reply_markup=find_bajaj_motor_kb()); return ConversationHandler.END
    if text == "👤 የኔ መረጃ": await profile(update, context); return ConversationHandler.END
    if text == "📍 የጭነት ክትትል": await tracking_menu(update, context); return ConversationHandler.END
    if text == "📣 መልእክት ለደንበኞች ለመላክ" and is_super_admin(uid): await broadcast_start(update, context); return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች": await show_connection_requests(update, context); return ConversationHandler.END
    if text == "📞 Support": await support_start(update, context); return ConversationHandler.END
    if text == "ℹ️ About": await about(update, context); return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም": await service_payment(update, context); return ConversationHandler.END
    
    return ConversationHandler.END

async def text_router(update, context):
    if update.effective_user is None: return
    user_id = update.effective_user.id
    if not is_user_registered(user_id):
        await update.message.reply_text("⚠️ እባክዎ መጀመሪያ /start ይጫኑ።")
        return
    
    if context.user_data.get("connect_info"):
        info = context.user_data["connect_info"]
        if not info.get("name"):
            info["name"] = update.message.text.strip()
            context.user_data["connect_info"] = info
            reg_phone = get_registered_phone(user_id)
            await update.message.reply_text(f"2️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\n✅ ይጫኑ ወይም አዲስ ይጻፉ።", reply_markup=confirm_phone_keyboard(reg_phone), parse_mode="Markdown")
            return
        elif not info.get("phone"):
            is_valid, phone = validate_phone(update.message.text.strip())
            if not is_valid:
                await update.message.reply_text(f"❌ {phone}")
                return
            info["phone"] = phone
            context.user_data["connect_info"] = info
            await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።")
            return
# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN: raise RuntimeError("BOT_TOKEN missing")
    start_health_server()
    init_db()
    load_all_from_db()
    
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚗 የሽያጭና/ኪራይ መኪና መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🏍🛺 የባጃጅ/ሞተር ሳይክል መሸጥ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ባጃጅ/ሞተር ሳይክል መግዛት$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የጭነት ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^📣 መልእክት ለደንበኞች ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), menu_router),
        ],
        states={
            START_PHONE: [MessageHandler(filters.CONTACT, start_phone_input)],
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_CALENDAR: [CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [CallbackQueryHandler(cargo_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [CallbackQueryHandler(truck_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
            VEHICLE_LIST_TYPE: [CallbackQueryHandler(vehicle_listing_type_action, pattern=r"^action_")],
            VEHICLE_LIST_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_location)],
            VEHICLE_LIST_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_details)],
            VEHICLE_LIST_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_price)],
            VEHICLE_LIST_PHOTOS: [CallbackQueryHandler(vehicle_listing_photo_callback, pattern=r"^vehicle_photo_(done|none)$"), MessageHandler(filters.PHOTO, vehicle_listing_photo)],
            CONNECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [CallbackQueryHandler(connect_phone, pattern=r"^(confirm_reg_phone|enter_new_phone)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
            SUPER_BROADCAST_TARGET: [CallbackQueryHandler(broadcast_target_choice, pattern=r"^broadcast_")],
            SUPER_BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text_received)],
            SUPER_BROADCAST_PHOTO: [CallbackQueryHandler(broadcast_photo_callback, pattern=r"^broadcast_photo_"), MessageHandler(filters.PHOTO, broadcast_photo_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)
    
    # Callbacks outside conversation
    application.add_handler(CallbackQueryHandler(find_vehicle_listings, pattern=r"^find_car_"))
    application.add_handler(CallbackQueryHandler(find_vehicle_listings, pattern=r"^find_(motor|bajaj)$"))
    application.add_handler(CallbackQueryHandler(toggle_pii_cargo, pattern=r"^toggle_pii_cargo_"))
    application.add_handler(CallbackQueryHandler(toggle_pii_truck, pattern=r"^toggle_pii_truck_"))
    application.add_handler(CallbackQueryHandler(share_listing_prompt, pattern=r"^share_"))
    application.add_handler(CallbackQueryHandler(do_share_post, pattern=r"^do_share_"))
    application.add_handler(CallbackQueryHandler(connection_request, pattern=r"^connect_"))
    application.add_handler(CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_"))
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_"))
    application.add_handler(CallbackQueryHandler(admin_approve_gmaps, pattern=r"^approvegmaps_"))
    application.add_handler(CallbackQueryHandler(admin_approve_stop_request, pattern=r"^approvestop_"))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    
    print("🚀 TANA CARGO Bot is running...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
