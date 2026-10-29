import os
import threading
import sqlite3
import asyncio
import secrets
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup, Contact
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
SUPPORT_PHONE_2 = "0912991128"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanapage"

START_IMAGE_ID = "AgACAgQAAxkBAAFWw_Vq389auQ7yoeq8rNETEyhfn02JagACCw9rG-tuAVNIehm8-b92jwEAAwIAA3kAAz0E"

TRACCAR_URL = "http://demo2.traccar.org"
TRACCAR_TOKEN = "RjBEAiBc98uaEhRRlPGxO9_vgSO0ZpaM6RfAKBJ6wRYXgO_pZgIgPR5JRatK9XmfQXCwT9Xc-Di5OiLQHUviT9NF6RxwefZ7ImkiOjg5MjE4NDA2Mzc2MTgzNjE1NzUsInUiOjgzNjcsImUiOiIyMDI2LTEwLTI4VDIxOjAwOjAwLjAwMCswMDowMCJ9"
TRACCAR_DEVICE_ID = "92643104"

CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
MESSAGE_AUTO_DELETE_SECONDS = 1800
DB_PATH = "tana_cargo.db"

# ==================================================
# ETHIOPIAN CALENDAR
# ==================================================

def is_ethiopian_leap_year(et_year):
    return (et_year % 4) == 3

def is_gregorian_leap_year(gc_year):
    return (gc_year % 4 == 0 and gc_year % 100 != 0) or (gc_year % 400 == 0)

def et_new_year_gregorian(et_year):
    gc_year = et_year + 7
    start_day = 12 if is_gregorian_leap_year(gc_year + 1) else 11
    return datetime(gc_year, 9, start_day)

def et_to_gc(et_year, et_month, et_day):
    max_day = 6 if et_month == 13 and is_ethiopian_leap_year(et_year) else 5 if et_month == 13 else 30
    if et_month < 1 or et_month > 13 or et_day < 1 or et_day > max_day:
        raise ValueError("Invalid Ethiopian date")
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
        if day > max_day:
            raise ValueError("Gregorian date conversion outside Ethiopian year")
    return candidate_year, month, day

def parse_flexible_date(date_text):
    raw = date_text.strip().split("\n")[0].strip()
    raw = " ".join(raw.split())
    for sep in ["-", ",", ".", " "]:
        raw = raw.replace(sep, "/")
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if len(parts) != 3:
        return None, "የቀን ቅርጸቱ ትክክል አይደለም። ምሳሌ፦ 11/02/2019"
    try:
        day, month, year = (int(p) for p in parts)
    except ValueError:
        return None, "ቀን/ወር/ዓመት በቁጥር መሆን አለበት።"
    if year < 1900 or year > 2100:
        return None, "ዓመት ትክክል አይደለም።"
    return {"day": day, "month": month, "year": year}, ""

def convert_calendar(date_parts, calendar):
    day, month, year = date_parts["day"], date_parts["month"], date_parts["year"]
    try:
        if calendar == "ET":
            if month < 1 or month > 13:
                return "", "", "ወር 1 እና 13 መካከል መሆን አለበት።"
            max_day = 6 if month == 13 and is_ethiopian_leap_year(year) else 5 if month == 13 else 30
            if day < 1 or day > max_day:
                return "", "", f"በዚህ ወር ቀኑ 1 እና {max_day} መካከል መሆን አለበት።"
            gc_obj = et_to_gc(year, month, day)
            et_year, et_month, et_day = year, month, day
        else:
            gc_obj = datetime(year, month, day)
            et_year, et_month, et_day = gc_to_et_date(gc_obj)
    except (ValueError, OverflowError):
        return "", "", "ያስገቡት ቀን ትክክል አይደለም።"
    et_date = f"{et_day:02d}/{et_month:02d}/{et_year}"
    gc_date = f"{gc_obj.day:02d}/{gc_obj.month:02d}/{gc_obj.year}"
    return et_date, gc_date, ""

# ==================================================
# DATABASE
# ==================================================

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, owner_name TEXT, owner_phone TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cargo_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, from_location TEXT, to_location TEXT, cargo_type TEXT, vehicle TEXT, size INTEGER, size_name TEXT, weight TEXT, date_et TEXT, date_gc TEXT, phone TEXT, price REAL, price_display TEXT, price_status TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer', trucks_needed INTEGER DEFAULT 1, trucks_matched INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS truck_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, truck_type TEXT, plate TEXT, capacity TEXT, route TEXT, address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS brokers (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, username TEXT, phone TEXT, address TEXT, active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0, timestamp TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS tracking_sessions (tracking_code TEXT PRIMARY KEY, request_type TEXT NOT NULL, driver_user_id INTEGER NOT NULL, customer_user_id INTEGER NOT NULL, origin TEXT DEFAULT '', destination TEXT DEFAULT '', cargo_description TEXT DEFAULT '', truck_description TEXT DEFAULT '', status TEXT DEFAULT 'ready', latitude REAL, longitude REAL, location_updated_at TEXT, created_at TEXT NOT NULL, completed_at TEXT)")
    try:
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN customer_name TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN customer_phone TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN driver_phone TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
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
                users[u["user_id"]]["owner"] = {"name": u.get("owner_name") or "", "phone": u.get("owner_phone") or "", "user_id": u["user_id"]}
        for row in conn.execute("SELECT * FROM cargo_posts WHERE active = 1").fetchall():
            c = dict(row)
            c["active"] = True
            c["from"] = c.pop("from_location")
            c["to"] = c.pop("to_location")
            cargo_posts.append(c)
        for row in conn.execute("SELECT * FROM truck_posts WHERE active = 1").fetchall():
            t = dict(row)
            t["active"] = True
            t["type"] = t.pop("truck_type")
            truck_posts.append(t)
        for row in conn.execute("SELECT * FROM brokers").fetchall():
            b = dict(row)
            b["active"] = bool(b.get("active"))
            b["approved"] = bool(b.get("approved"))
            brokers.append(b)
        print(f"✅ Loaded: {len(users)} users, {len(cargo_posts)} cargo, {len(truck_posts)} trucks, {len(brokers)} brokers")
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
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return 3000 + cursor.fetchone()[0]
    finally:
        conn.close()

def db_save_cargo(cargo):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO cargo_posts (user_id, from_location, to_location, cargo_type, vehicle, size, size_name, weight, date_et, date_gc, phone, price, price_display, price_status, active, timestamp, expiry_date, poster_type, trucks_needed, trucks_matched) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (cargo["user_id"], cargo["from"], cargo["to"], cargo["type"], cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"], cargo.get("date_et", ""), cargo.get("date_gc", ""), cargo["phone"], cargo["price"], cargo["price_display"], cargo["price_status"], 1, cargo["timestamp"], cargo.get("expiry_date", ""), cargo.get("poster_type", "customer"), cargo.get("trucks_needed", 1), 0))
    conn.commit()
    conn.close()

def db_save_truck(truck):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO truck_posts (user_id, truck_type, plate, capacity, route, address, driver, phone, active, timestamp, expiry_date, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (truck["user_id"], truck["type"], truck["plate"], truck["capacity"], truck["route"], truck["address"], truck["driver"], truck["phone"], 1, truck["timestamp"], truck.get("expiry_date", ""), truck.get("poster_type", "customer")))
    conn.commit()
    conn.close()

def db_save_broker(broker):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO brokers (user_id, name, username, phone, address, active, approved, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (broker["user_id"], broker["name"], broker["username"], broker["phone"], broker["address"], 1, 0, broker["timestamp"]))
    conn.commit()
    conn.close()

def db_get_tracking(tracking_code):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tracking_sessions WHERE tracking_code = ?", (tracking_code.upper().strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_finish_tracking(tracking_code):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("UPDATE tracking_sessions SET status = 'delivered', completed_at = ? WHERE tracking_code = ?", (datetime.now().astimezone().isoformat(timespec="seconds"), tracking_code.upper().strip()))
        conn.commit()
    finally:
        conn.close()

def find_user_by_phone(phone):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        clean_phone = phone.strip().replace(" ", "")
        if clean_phone.startswith("+251"): clean_phone = "0" + clean_phone[4:]
        elif clean_phone.startswith("251"): clean_phone = "0" + clean_phone[3:]
        row = conn.execute("SELECT user_id FROM users WHERE owner_phone = ? LIMIT 1", (clean_phone,)).fetchone()
        if row: return int(row[0])
    finally:
        conn.close()
    return None

# ==================================================
# MEMORY & STATES
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
brokers = []
connection_requests = []
pending_deletes = []
tracking_stops = []

(CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE, CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE, CARGO_TRUCKS_NEEDED) = range(11)
(TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE, TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE) = range(11, 18)
OWNER_NAME, OWNER_PHONE = range(18, 20)
(CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE) = range(20, 23)
(BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS) = range(23, 26)
(TRACK_REQ_NAME, TRACK_REQ_PHONE, TRACK_REQ_ROUTE, TRACK_REQ_LOCATION) = range(26, 30)
(TRACK_STOP_NAME, TRACK_STOP_PHONE) = range(30, 32)

START_PHONE = 32
ADMIN_BROADCAST_TEXT = 33
SOL_MESSAGE_TEXT = 34

# ==================================================
# MENUS & HELPERS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        if is_super_admin(user_id):
            keyboard = [["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"], ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"], ["👤 የኔ መረጃ"], ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"], ["📊 የክትትል መዝገብ (Admin)"], ["🤝 የግንኙነት ጥያቄዎች"], ["📋 የጭነት ደላሎች መዝገብ"], ["💳 የአገልግሎት ክፍያ ለመፈፀም"], ["📢 ለደንበኞች መረጃ መላኪያ"], ["💬 Sol Message (ለሁሉም)"], ["📞 Support", "ℹ️ About"]]
        else:
            keyboard = [["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"], ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"], ["👤 የኔ መረጃ"], ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"], ["📊 የክትትል መዝገብ (Admin)"], ["🤝 የግንኙነት ጥያቄዎች"], ["📋 የጭነት ደላሎች መዝገብ"], ["💳 የአገልግሎት ክፍያ ለመፈፀም"], ["📞 Support"]]
    else:
        keyboard = [["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"], ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"], ["👤 የኔ መረጃ"], ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"], ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"], ["🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ"], ["💳 የአገልግሎት ክፍያ ለመፈፀም"], ["📞 Support"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def phone_request_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def location_request_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)], [KeyboardButton("❌ ሰርዝ")]], resize_keyboard=True, one_time_keyboard=True)

def size_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")], [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")], [InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25")]])

def vehicle_keyboard(prefix="vehicle"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚛 ተሳቢ", callback_data=f"{prefix}_ተሳቢ")], [InlineKeyboardButton("🚛 ካሶኒ", callback_data=f"{prefix}_ካሶኒ")], [InlineKeyboardButton("🚛 ኦባማ", callback_data=f"{prefix}_ኦባማ")], [InlineKeyboardButton("🚛 Isuzu", callback_data=f"{prefix}_isuzu")], [InlineKeyboardButton("✍️ ሌላ", callback_data=f"{prefix}_other")]])

def calendar_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (EC)", callback_data="cal_et")]])

def route_choice_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🌍 በማንኛውም ቦታ", callback_data="route_anywhere")], [InlineKeyboardButton("📍 በርሶ ምርጫ", callback_data="route_custom")]])

def tracking_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ክትትል ጀምር", callback_data="track_start")], [InlineKeyboardButton("📦 ጭነቴን በኮድ መከታተል", callback_data="track_lookup")], [InlineKeyboardButton("🔴 ክትትል አቁም", callback_data="track_stop")]])

def vehicle_tracking_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ክትትል ጀምር", callback_data="vehicle_track_start")], [InlineKeyboardButton("📦 ጭነቴን በኮድ መከታተል", callback_data="track_lookup")], [InlineKeyboardButton("🔴 ክትትል አቁም", callback_data="track_stop")]])

def is_user_registered(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]: return True
    finally:
        conn.close()
    return False

def normalize(text):
    if text is None: return ""
    return str(text).strip().lower().replace(" ", "")

def vehicle_match(truck_type, requested_vehicle):
    if not requested_vehicle: return True
    return normalize(truck_type) == normalize(requested_vehicle)

def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"): return True
    return False

def get_poster_label(user_id):
    return "🏷️ Broker" if is_broker(user_id) else "🏷️ Customer"

def payment_methods_text():
    return ("💳 የአገልግሎት ክፍያ መረጃ\n━━━━━━━━━━━━━━━━━━━━\n\n🏦 Commercial Bank of Ethiopia (CBE)\n🔢 1000031098231\n👤 Solomon Sisay\n\n📱 Telebirr\n📱 0918132914\n👤 Solomon Sisay\n\n💰 CBE Birr\n📱 0918132914\n👤 Solomon Sisay\n\n━━━━━━━━━━━━━━━━━━━━\n\n📌 ክፍያ ካደረጉ በኋላ ደረሰኙን ወይም ስክሪንሾቱን ወደ @tanapage ይላኩ።\n\n🙏 እኛን ስለመረጡን እናመሰግናለን!")

def validate_phone(phone):
    phone = phone.strip().replace(" ", "") # ክፍተቶችን ያጽዳል (ለ +251 91... ችግር መፍትሄ)
    if phone.startswith("+251"): phone = "0" + phone[4:]
    elif phone.startswith("251"): phone = "0" + phone[3:]
    if not (phone.startswith("09") or phone.startswith("07")): return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10: return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    if not phone.isdigit(): return False, "ስልክ ቁጥር ቁጥር ብቻ መሆን አለበት።"
    return True, phone

def validate_plate(plate):
    plate = plate.strip()
    if not plate.isdigit(): return False, "ታርጋ ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_weight(weight):
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]): return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

def is_super_admin(user_id): return bool(SUPER_ADMIN_USER_ID) and str(user_id) == str(SUPER_ADMIN_USER_ID)
def is_admin(user_id): return is_super_admin(user_id) or (bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID))

def get_admin_ids():
    ids = []
    if SUPER_ADMIN_USER_ID: ids.append(int(SUPER_ADMIN_USER_ID))
    if ADMIN_USER_ID and str(ADMIN_USER_ID) != str(SUPER_ADMIN_USER_ID): ids.append(int(ADMIN_USER_ID))
    return ids

def is_duplicate_broker_post(new_item, is_truck, broker_user_id):
    if is_truck:
        for t in truck_posts:
            if (t.get("active") and t.get("poster_type") == "broker" and t.get("plate") == new_item.get("plate") and t.get("user_id") != broker_user_id): return True
    else:
        for c in cargo_posts:
            if (c.get("active") and c.get("poster_type") == "broker" and normalize(c.get("from")) == normalize(new_item.get("from")) and normalize(c.get("to")) == normalize(new_item.get("to")) and c.get("user_id") != broker_user_id): return True
    return False

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner", "connect_info", "sending_admin_message", "tracking_update_code", "tracking_lookup_waiting", "track_new", "cust_track_phone_only", "gmaps_share", "tracking_stop", "vehicle_track"):
        if key not in keep: context.user_data.pop(key, None)

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
    await update.message.reply_text("4️⃣ ለዚህ ጭነት የሚፈልጉትን የመኪና አይነት ይምረጡ።", reply_markup=vehicle_keyboard("cargo_vehicle"))
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
    await query.edit_message_text(f"🚛 የተመረጠው መኪና፦ {value}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=size_keyboard())
    return CARGO_SIZE

async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"): return CARGO_VEHICLE
    cargo["vehicle"] = update.message.text.strip()
    cargo["vehicle_waiting"] = False
    await update.message.reply_text(f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=size_keyboard())
    return CARGO_SIZE

async def cargo_size(update, context):
    query = update.callback_query
    await query.answer()
    sizes = {"size_100": (100, "ሙሉ ጭነት"), "size_50": (50, "ግማሽ ጭነት"), "size_25": (25, "እሩብ ጭነት")}
    if query.data not in sizes: return CARGO_SIZE
    size, size_name = sizes[query.data]
    context.user_data["cargo"]["size"] = size
    context.user_data["cargo"]["size_name"] = size_name
    await query.edit_message_text(f"📦 የተመረጠው፦ {size_name} ({size}%)\n\n6️⃣ የጭነቱን ክብደት ይግለጹ።\nበቶን፣ በቢያጆ፣ በኩንታል\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል")
    return CARGO_WEIGHT

async def cargo_weight(update, context):
    weight = update.message.text.strip()
    is_valid, error = validate_weight(weight)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል")
        return CARGO_WEIGHT
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text("6.5️⃣ ለዚህ ጭነት **ስንት መኪና** ይፈልጋሉ? (ቁጥር ብቻ ይጻፉ)\nምሳሌ፦ 1 ወይም 2 ወይም 3")
    return CARGO_TRUCKS_NEEDED

async def cargo_trucks_needed(update, context):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ እባክዎ ከ1 በላይ የሆነ ትክክለኛ ቁጥር ይጻፉ።")
        return CARGO_TRUCKS_NEEDED
    context.user_data["cargo"]["trucks_needed"] = int(text)
    await update.message.reply_text("7️⃣ **እባክዎ የሚጫንበትን ቀን ካላንደር ይምረጡ።**\n\nየሚጠቀሙበትን ካላንደር ይምረጡ፦", reply_markup=calendar_keyboard())
    return CARGO_CALENDAR

async def cargo_calendar(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cal_et":
        context.user_data["cargo"]["calendar"] = "ET"
        await query.edit_message_text("🇪🇹 **የኢትዮጵያ ካላንደር ተመርጧል።**\n\nእባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\nምሳሌ፦ 26/1/2019 ወይም 26-1-2019 ወይም 26,1,2019")
    else:
        return CARGO_CALENDAR
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    calendar = context.user_data.get("cargo", {}).get("calendar", "ET")
    date_parts, error = parse_flexible_date(date_text)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ቀኑን በቀን/ወር/ዓመት ቅርጽ እንደገና ያስገቡ።")
        return CARGO_DATE
    et_date, gc_date, error = convert_calendar(date_parts, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ትክክለኛውን ቀን ያስገቡ።")
        return CARGO_DATE
    try:
        gc_obj = datetime.strptime(gc_date, "%d/%m/%Y")
        today = datetime.now(ZoneInfo("Africa/Addis_Ababa")).date()
        given_date = gc_obj.date()
    except (ValueError, TypeError):
        await update.message.reply_text("❌ የቀን ስሌት ስህተት። እባክዎ ቀኑን እንደገና ያስገቡ።")
        return CARGO_DATE
    if given_date < today:
        await update.message.reply_text("❌ ያለፈ ቀን አይቀበልም።\n\nዛሬን ወይም ወደፊት ያለ ቀን በኢትዮጵያ ካላንደር (ቀን/ወር/ዓ.ም.) ያስገቡ።\nምሳሌ፦ 11/02/2019")
        return CARGO_DATE
    if given_date > today + timedelta(days=30):
        await update.message.reply_text("❌ ከ30 ቀናት በላይ የሆነ ቀን አይቀበልም።\n\nእባክዎ ዛሬን ወይም በሚቀጥሉት 30 ቀናት ውስጥ ያለ ቀን ያስገቡ።")
        return CARGO_DATE
    cargo = context.user_data.setdefault("cargo", {})
    cargo["date"] = date_text
    cargo["date_et"] = et_date
    cargo["date_gc"] = gc_date
    cargo["expiry_date"] = (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    await update.message.reply_text(f"📅 የተመረጠው ቀን፦\n🇪🇹 ET: {et_date}\n🌍 GC: {gc_date}\n\n8️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
    return CARGO_PHONE

async def cargo_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = result
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text("9️⃣ የመጫኛ ዋጋ ይጻፉ።\nበብር ብቻ ወይም **በስምምነት / በድርድር** ብለው ይጻፉ።\nምሳሌ፦ 55000 ወይም በስምምነት", reply_markup=main_menu(update.effective_user.id))
    return CARGO_PRICE

async def cargo_phone_fallback(update, context):
    await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
    return CARGO_PHONE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
    if raw in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price, price_status, price_display = None, "agreement", (raw if raw in ["በስምምነት", "በድርድር"] else "በስምምነት")
    else:
        try:
            price = float(raw.replace(",", "").replace("ብር", "").strip())
            price_status, price_display = "fixed", f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text("❌ እባክዎ ዋጋውን በቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\nምሳሌ፦ 55000 ወይም በስምምነት")
            return CARGO_PRICE
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return CARGO_PRICE
    cargo = context.user_data["cargo"]
    cargo.update({"price": price, "price_status": price_status, "price_display": price_display, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer"})
    if cargo["poster_type"] == "broker" and is_duplicate_broker_post(cargo, is_truck=False, broker_user_id=update.effective_user.id):
        await update.message.reply_text("❌ **ይቅርታ ይህንን ጭነት ሌላ ደላላ (broker) ለጥፎታል።**\n\nእባክዎ ሌላ ጭነት ካለዎት ይለጥፉ።", reply_markup=main_menu(update.effective_user.id))
        return ConversationHandler.END
    cargo_posts.append(cargo.copy())
    try: db_save_cargo(cargo)
    except Exception as e: print(f"DB SAVE CARGO ERROR: {e}")
    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    poster_label = get_poster_label(update.effective_user.id)
    trucks_needed = cargo.get("trucks_needed", 1)
    await update.message.reply_text(
        "✅ **ጭነትዎ በትክክል ተመዝግቧል!**\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n{poster_label}\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n📦 አይነት፦ {cargo['type']}\n🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n⚖️ ክብደት፦ {cargo['weight']}\n🚚 **የሚፈለጉ መኪናዎች፦ {trucks_needed}**\n📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n💰 የመጫኛ ዋጋ፦ {price_display}\n\n🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\nውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉@tanapage join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )
    for truck in truck_posts:
        if truck.get("active", True) and vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(chat_id=truck["user_id"], text=f"🔔 ተስማሚ አዲስ ጭነት ተገኝቷል!\n\n📍 {cargo['from']} ➡️ {cargo['to']}\n📦 {cargo['type']}\n🚛 የሚፈለገው፦ {cargo['vehicle']}\n📊 {cargo['size_name']}\n⚖️ {cargo['weight']}\n🚚 የሚፈለጉ መኪናዎች፦ {trucks_needed}\n📅 {cargo.get('date_et', 'N/A')} (ET)\n💰 {price_display}\n\n🔎 ለማየት የጭነት መፈለግን ይጫኑ።")
            except Exception: pass
    return ConversationHandler.END

# ==================================================
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    clear_stale_user_state(context)
    context.user_data["truck"] = {}
    await update.message.reply_text("🚛 **የጭነት መኪና ማስመዝገብ**\n\n1️⃣ የመኪናውን አይነት ይጻፉ።\nምሳሌ፦ Isuzu / ካሶኒ / ኦባማ", reply_markup=main_menu(update.effective_user.id))
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።\nቁጥር ብቻ መሆን አለበት።\nምሳሌ፦ 12345")
    return TRUCK_PLATE

async def truck_plate(update, context):
    plate = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_plate(plate)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 12345")
        return TRUCK_PLATE
    context.user_data["truck"]["plate"] = plate
    await update.message.reply_text("3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    capacity = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_weight(capacity)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
        return TRUCK_CAPACITY
    context.user_data["truck"]["capacity"] = capacity
    await update.message.reply_text("4️⃣ የመንገድ ዝርዝር ይጻፉ።\n\nእባክዎ የሚጓዙበትን መንገድ ዓይነት ይምረጡ፦", reply_markup=route_choice_keyboard())
    return TRUCK_ROUTE

async def truck_route_choice(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "route_anywhere":
        context.user_data["truck"]["route"] = "በማንኛውም ቦታ"
        context.user_data["truck"]["route_type"] = "anywhere"
        await query.edit_message_text("✅ **በማንኛውም ቦታ** ተመርጧል።\n\n5️⃣ የመኪናውን አድራሻ ይጻፉ።")
        return TRUCK_ADDRESS
    elif query.data == "route_custom":
        context.user_data["truck"]["route_type"] = "custom"
        await query.edit_message_text("📍 **በርሶ ምርጫ** ተመርጧል።\n\nለምሳሌ፦ ጅማ → ባህርዳር → አዲስ አበባ")
        return TRUCK_ROUTE
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
    await update.message.reply_text("7️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
    return TRUCK_PHONE

async def truck_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return TRUCK_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return TRUCK_PHONE
    phone = result
    truck = context.user_data["truck"]
    truck.update({"phone": phone, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer", "expiry_date": (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()})
    if truck["poster_type"] == "broker" and is_duplicate_broker_post(truck, is_truck=True, broker_user_id=update.effective_user.id):
        await update.message.reply_text("❌ **ይቅርታ ይህንን መኪና ሌላ ደላላ (broker) አስመዝግቦታል።**\n\nእባክዎ ሌላ የጭነት መኪና ካለዎት ያስመዝግቡ።", reply_markup=main_menu(update.effective_user.id))
        return ConversationHandler.END
    truck_posts.append(truck.copy())
    try: db_save_truck(truck)
    except Exception as e: print(f"DB SAVE TRUCK ERROR: {e}")
    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    route_display = truck.get("route", "በማንኛውም ቦታ")
    poster_label = get_poster_label(update.effective_user.id)
    await update.message.reply_text(
        f"✅ **መኪናዎ በትክክል ተመዝግቧል!**\n\n👤 የመኪና ባለቤት፦ {owner_name}\n{poster_label}\n🚛 አይነት፦ {truck['type']}\n🔢 ታርጋ፦ {truck['plate']}\n⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {route_display}\n📍 አድራሻ፦ {truck['address']}\n👨‍✈️ ሹፌር፦ {truck['driver']}\n\n🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።\n\nውድ ደንበኛችን ጭነት የሚያስጭንዎት ደንበኛ ጋር ስምምነት ላይ ደርሰዉ ጭነት ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነቱ ጭነቱን መከታተል እንዲችል የስልክዎን location on አድርገው የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ በመንካት አዲስ ጉዞ ክፈት ሹፌር የምትለዋን ነክተው ያስመዝግቡ። የጉዞ መዳረሻ ላይ ሲደርሱ location ያጥፉ እናመሰግናለን የሚል notfication ይጨመር። ለበለጠ መረጃ👉 @tanapage ነክተው join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )
    for cargo in cargo_posts:
        if cargo.get("active", True) and vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(chat_id=cargo["user_id"], text=f"🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n🚛 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {route_display}\n📍 አድራሻ፦ {truck['address']}\n\n🚛 መኪና መፈለግን ይጫኑ።")
            except Exception: pass
    return ConversationHandler.END

async def truck_phone_fallback(update, context):
    await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
    return TRUCK_PHONE

# ==================================================
# BROKER REGISTRATION
# ==================================================

async def broker_start(update, context):
    clear_stale_user_state(context)
    context.user_data["broker"] = {}
    await update.message.reply_text("🆕 **የጭነት ደላላ ምዝገባ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return BROKER_NAME

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
    return BROKER_PHONE

async def broker_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return BROKER_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return BROKER_PHONE
    context.user_data["broker"]["phone"] = result
    await update.message.reply_text("3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር", reply_markup=main_menu(update.effective_user.id))
    return BROKER_ADDRESS

async def broker_phone_fallback(update, context):
    await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
    return BROKER_PHONE

async def broker_address(update, context):
    address = update.message.text.strip()
    user = update.effective_user
    context.user_data["broker"]["address"] = address
    broker = {"user_id": user.id, "name": context.user_data["broker"]["name"], "username": user.username or "", "phone": context.user_data["broker"]["phone"], "address": address, "timestamp": datetime.now().isoformat()}
    brokers.append(broker)
    try: db_save_broker(broker)
    except Exception as e: print(f"DB SAVE BROKER ERROR: {e}")
    await update.message.reply_text("✅ **ስለተመዘገቡ እናመሰግናለን!**\n\nየጭነት ደላላ መሆንዎን አረጋግጠን አብረን በጋራ እንሰራለን።\n\n📌 ማረጋገጫ ሲደረግ ይነገርዎታል።", reply_markup=main_menu(user.id))
    await send_admin_broker_notification(context, broker)
    return ConversationHandler.END

async def send_admin_broker_notification(context, broker):
    text = f"🆕 **TANA CARGO — አዲስ የጭነት ደላላ ተመዝግቧል!**\n\n👤 **ስም:** {broker['name']}\n🔗 **Username:** @{broker['username'] or 'የለም'}\n🆔 **Telegram ID:** {broker['user_id']}\n📞 **ስልክ:** {broker['phone']}\n📍 **አድራሻ:** {broker['address']}\n\n━━━━━━━━━━━━━━━━━━━━\n📌 **የጭነት ደላላ መሆኑን ያረጋግጡ።**\n✅ Right = አረጋግጥ\n❌ X = ውድቅ አድርግ"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Right", callback_data=f"brokerright_{len(brokers)-1}"), InlineKeyboardButton("❌ X አጥፋ", callback_data=f"brokerxdelete_{len(brokers)-1}")]])
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception: pass

async def broker_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return await query.answer("❌ Admin ብቻ።", show_alert=True)
    try: index = int(query.data.split("_")[1])
    except: return
    if index < 0 or index >= len(brokers): return
    broker = brokers[index]
    if broker.get("approved"): return await query.answer("⚠️ ይህ ቀድሞ ጸድቋል!", show_alert=True)
    broker["approved"], broker["active"] = True, True
    await query.edit_message_text("✅ **የጭነት ደላላው ጸድቋል!**\n\nለደላላው ማሳወቂያ ተልኳል።")
    if broker.get("user_id"):
        try: await context.bot.send_message(chat_id=broker["user_id"], text="🎉 **እንኳን ደስ አለዎት!** 🎉\n\n**የጣና ጭነት አባል ሆነዋል!**\n\n📦 **ጭነት ካለዎት** ወይም 🚛 **የጭነት መኪና ካለዎት**\n✅ **በነፃ ይለጥፉ!**\n\n🔒 መረጃዎ **100% የተጠበቀ** ነው።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን!")
        except Exception: pass

async def broker_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id): return await query.answer("❌ Super Admin ብቻ!", show_alert=True)
    try: index = int(query.data.split("_")[1])
    except: return
    if index < 0 or index >= len(brokers): return
    broker = brokers[index]
    broker["active"], broker["approved"] = False, False
    await query.edit_message_text("❌ **ውድቅ ተደርጓል!**\n\nይህ የጭነት ደላላ በ Super Admin ውድቅ ተደርጓል።")
    if broker.get("user_id"):
        try: await context.bot.send_message(chat_id=broker["user_id"], text="❌ **የጭነት ደላላ ምዝገባ ውድቅ ተደርጓል።**\n\nየጣና ካርጎ አባላቶች ባገኙት መረጃ መሰረት እርሶ **የጭነት ደላላ አይደሉም**።\n\nገፃችንን ስለጎበኙ እናመሰግናለን! 🙏")
        except Exception: pass

async def show_broker_registry(update, context):
    if not is_admin(update.effective_user.id): return await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
    if not brokers: return await update.message.reply_text("📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።")
    for i, broker in enumerate(brokers):
        status = "⏳ በመጠበቅ" if not broker.get("approved") else ("✅ ጸድቋል" if broker.get("active") else "❌ ውድቅ")
        text = f"📋 **የጭነት ደላላ #{i + 1}**\n\n👤 ስም: {broker.get('name', 'N/A')}\n🔗 Username: @{broker.get('username') or 'የለም'}\n🆔 Telegram ID: {broker.get('user_id', 'N/A')}\n📞 ስልክ: {broker.get('phone', 'N/A')}\n📍 አድራሻ: {broker.get('address', 'N/A')}\n📌 ሁኔታ: {status}\n"
        buttons = [[InlineKeyboardButton(f"🗑️ #{i + 1} አጥፋ (Super)", callback_data=f"admindeletebroker_{i}")]] if is_super_admin(update.effective_user.id) else []
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def admin_delete_broker(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id): return await query.answer("❌ Super Admin ብቻ!", show_alert=True)
    try: index = int(query.data.split("_")[-1])
    except: return
    if 0 <= index < len(brokers):
        brokers[index]["active"], brokers[index]["approved"] = False, False
        await query.edit_message_text("✅ **የጭነት ደላላው ተሰርዟል።**")
# ==================================================
# SUPER ADMIN BROADCAST + SOL MESSAGE
# ==================================================

async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 **ለደንበኞች መረጃ መላኪያ**\n\n"
        "እባክዎ ለደንበኞች ለማጋራት የፈለጉትን መልእክት ይፃፉ።\n\n"
        "📌 እኔ ፅፌ እልካለሁ ማለት ነው።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ADMIN_BROADCAST_TEXT

async def broadcast_message(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    await update.message.reply_text("⏳ በመላክ ላይ ነው... እባክዎ ይጠብቁ።")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        users_list = conn.execute("SELECT user_id FROM users").fetchall()
    finally:
        conn.close()
    success = 0
    failed = 0
    for u in users_list:
        uid = u[0]
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **TANA CARGO | ጣና ጭነት**\n\n{text}")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"✅ **መልእክቱ ተልኳል!**\n\n"
        f"✔️ የደረሰው፦ {success}\n"
        f"❌ ያልደረሰው (ቦቱን የታገዱ ወይም የሌሉ)፦ {failed}",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def sol_message_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END
    await update.message.reply_text(
        "💬 **Sol Message — የግል መልእክት መላኪያ**\n\n"
        "ለሁሉም የተመዘገቡ ደንበኞች በግል (personal) የሚላክ መልእክት ነው።\n\n"
        "📌 ልክ 'TANA CARGO' prefix ያለው መልእክት ይደርሳል።\n\n"
        "የሚላከውን መልእክት ይፃፉ፦",
        reply_markup=main_menu(update.effective_user.id)
    )
    return SOL_MESSAGE_TEXT

async def sol_message_send(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    await update.message.reply_text("⏳ Sol Message በመላክ ላይ ነው... እባክዎ ይጠብቁ።")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        users_list = conn.execute("SELECT user_id FROM users").fetchall()
    finally:
        conn.close()
    success = 0
    failed = 0
    for u in users_list:
        uid = u[0]
        try:
            await context.bot.send_message(chat_id=uid, text=f"💬 **TANA CARGO**\n\n{text}")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(
        f"✅ **Sol Message ተልኳል!**\n\n"
        f"✔️ የደረሰው፦ {success}\n"
        f"❌ ያልደረሰው፦ {failed}",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

# ==================================================
# FIND CARGO / TRUCK
# ==================================================

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    is_admin_user = is_admin(update.effective_user.id)
    if not active_cargos:
        await update.message.reply_text(
            "📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።\n\n"
            "🔎 ጭነት እያፈላለግን ነው።\n"
            "🔔 ተስማሚ ጭነት ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return

    for i, cargo in enumerate(active_cargos):
        cargo_id = f"{i + 1:03d}"
        price_display = cargo.get("price_display", "በስምምነት")
        real_index = cargo_posts.index(cargo)
        needed = cargo.get("trucks_needed", 1)
        matched = cargo.get("trucks_matched", 0)
        is_full = (not cargo.get("active", True)) or (matched >= needed)
        status_mark = "❌ " if is_full else ""

        admin_info = ""
        if is_admin_user:
            owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
            owner_username = users.get(cargo["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {cargo['user_id']}\n"
                f"📞 ስልክ: {cargo.get('phone', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"

        msg = (
            f"{status_mark}📦 **ጭነት {cargo_id}**\n"
            f"{poster_label}\n"
            f"📍 መነሻ፦ {cargo['from']}\n"
            f"📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
            f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n"
            f"🚚 **የሚፈለጉ መኪናዎች፦ {needed}**\n"
            f"✅ **የተመደቡ፦ {matched}**\n"
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        if is_full:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ ጭነት {cargo_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት ጭነት {cargo_id} ከሆነ እዚጋ ያጫኑ 👈",
                    callback_data=f"connect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]
    is_admin_user = is_admin(update.effective_user.id)
    if not active_trucks:
        await update.message.reply_text(
            "🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።\n\n"
            "🔎 መኪና እያፈላለግን ነው።\n"
            "🔔 ተስማሚ መኪና ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return

    for i, truck in enumerate(active_trucks):
        truck_id = f"{i + 1:03d}"
        route_display = truck.get("route", "N/A")
        real_index = truck_posts.index(truck)
        is_taken = not truck.get("active", True)
        status_mark = "❌ " if is_taken else ""

        admin_info = ""
        if is_admin_user:
            owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
            owner_username = users.get(truck["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {truck['user_id']}\n"
                f"📞 ስልክ: {truck.get('phone', 'N/A')}\n"
                f"🔢 ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"👨‍✈️ ሹፌር: {truck.get('driver', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"

        msg = (
            f"{status_mark}🚛 **የጭነት መኪና {truck_id}**\n"
            f"{poster_label}\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {route_display}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        if is_taken:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ መኪና {truck_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት የጭነት መኪና {truck_id} ከሆነ ይጫኑ 👈",
                    callback_data=f"truckconnect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

async def already_selected_callback(update, context):
    query = update.callback_query
    await query.answer(
        "❌ ይቅርታ የመረጡት ጭነት/የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
        "እባክዎ ሌላ ተስማሚ ይምረጡ።",
        show_alert=True
    )

# ==================================================
# CONNECTION REQUEST — DIRECT PHONE SHARING
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return ConversationHandler.END
    if index < 0 or index >= len(cargo_posts):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    cargo = cargo_posts[index]
    needed = cargo.get("trucks_needed", 1)
    matched = cargo.get("trucks_matched", 0)
    if not cargo.get("active", True) or matched >= needed:
        await query.answer("❌ ይህ ጭነት ተወግዷል ወይም በሙሉ ተመድቧል።", show_alert=True)
        return ConversationHandler.END
    requester = update.effective_user
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "cargo"
                and req.get("item_index") == index
                and req.get("requester_id") == requester.id):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "cargo",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text(
        "📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።",
        reply_markup=main_menu(requester.id)
    )
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return ConversationHandler.END
    if index < 0 or index >= len(truck_posts):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    truck = truck_posts[index]
    if not truck.get("active", True):
        await query.answer("❌ ይህ መኪና ተወግዷል።", show_alert=True)
        return ConversationHandler.END
    requester = update.effective_user
    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "truck"
                and req.get("item_index") == index
                and req.get("requester_id") == requester.id):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "truck",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text(
        "📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።",
        reply_markup=main_menu(requester.id)
    )
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info
    await update.message.reply_text(
        "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return CONNECT_PHONE

async def connect_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text(
            "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።",
            reply_markup=phone_request_keyboard()
        )
        return CONNECT_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።",
            reply_markup=phone_request_keyboard()
        )
        return CONNECT_PHONE
    phone = result
    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
    context.user_data["connect_info"] = info
    await update.message.reply_text(
        "3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር",
        reply_markup=main_menu(update.effective_user.id)
    )
    return CONNECT_TYPE

async def connect_phone_fallback(update, context):
    await update.message.reply_text(
        "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።",
        reply_markup=phone_request_keyboard()
    )
    return CONNECT_PHONE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip().split("\n")[0].strip()
    info["requester_address"] = text
    context.user_data["connect_info"] = info
    requester = update.effective_user

    try:
        if info.get("type") == "cargo":
            await _connect_cargo_direct(update, context, info, requester)
        else:
            await _connect_truck_direct(update, context, info, requester)
    except Exception as e:
        print(f"CONNECT ERROR: {e}")
        await update.message.reply_text(
            "❌ ስህተት ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።",
            reply_markup=main_menu(requester.id)
        )

    context.user_data.pop("connect_info", None)
    return ConversationHandler.END

async def _connect_cargo_direct(update, context, info, requester):
    cargo = cargo_posts[info["item_index"]]
    cargo_owner_id = cargo["user_id"]
    needed = cargo.get("trucks_needed", 1)
    matched = cargo.get("trucks_matched", 0)

    if not cargo.get("active", True) or matched >= needed:
        await update.message.reply_text(
            "❌ ይቅርታ፣ ይህ ጭነት በሙሉ ተመድቧል ወይም ተወግዷል።\n\n"
            "እባክዎ ሌላ ጭነት ይምረጡ።",
            reply_markup=main_menu(requester.id)
        )
        return

    cargo["trucks_matched"] = matched + 1
    if cargo["trucks_matched"] >= needed:
        cargo["active"] = False

    owner_phone = cargo.get("phone", "")
    owner_name = users.get(cargo_owner_id, {}).get("name", "የጭነት ባለቤት")

    await update.message.reply_text(
        "✅ **ግንኙነት ተፈጽሟል!**\n\n"
        f"📦 **ጭነት፦** {cargo['from']} ➡️ {cargo['to']}\n"
        f"📦 **አይነት፦** {cargo['type']}\n"
        f"⚖️ **ክብደት፦** {cargo['weight']}\n\n"
        f"👤 **የጭነት ባለቤት፦** {owner_name}\n"
        f"📞 **ስልክ፦** `{owner_phone}`\n\n"
        "📌 **አሁን ደውለው ይደራደሩ!**",
        reply_markup=main_menu(requester.id),
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=cargo_owner_id,
            text=(
                "🔔 **አዲስ የመኪና ባለቤት ፍላጎት አሳይቷል!**\n\n"
                f"📦 **ጭነትዎ፦** {cargo['from']} ➡️ {cargo['to']}\n\n"
                f"👤 **ስም፦** {info.get('name', requester.full_name)}\n"
                f"📞 **ስልክ፦** `{info.get('phone', '')}`\n"
                f"📍 **አድራሻ፦** {info.get('requester_address', '')}\n\n"
                "📌 **አሁን ደውለው ይደራደሩ!**"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"OWNER NOTIFY ERROR: {e}")

    connection_requests.append({
        "type": "cargo",
        "item_index": info["item_index"],
        "owner_id": cargo_owner_id,
        "requester_id": requester.id,
        "requester_name": info.get("name", requester.full_name),
        "requester_username": info.get("requester_username", ""),
        "requester_phone": info.get("phone", ""),
        "requester_address": info.get("requester_address", ""),
        "created_at": datetime.now().isoformat(),
    })
    await _notify_admin_direct_connection(
        context, info, requester,
        owner_id=cargo_owner_id, owner_name=owner_name,
        owner_phone=owner_phone, item_type="cargo", item=cargo
    )

async def _connect_truck_direct(update, context, info, requester):
    truck = truck_posts[info["item_index"]]
    truck_owner_id = truck["user_id"]

    if not truck.get("active", True):
        await update.message.reply_text(
            "❌ ይቅርታ፣ ይህ መኪና ከአሁን በኋላ አይገኝም።\n\n"
            "እባክዎ ሌላ መኪና ይምረጡ።",
            reply_markup=main_menu(requester.id)
        )
        return

    truck["active"] = False
    owner_phone = truck.get("phone", "")
    owner_name = users.get(truck_owner_id, {}).get("name", "የመኪና ባለቤት")

    await update.message.reply_text(
        "✅ **ግንኙነት ተፈጽሟል!**\n\n"
        f"🚛 **መኪና፦** {truck['type']}\n"
        f"⚖️ **አቅም፦** {truck['capacity']}\n"
        f"🛣️ **መንገድ፦** {truck.get('route', 'N/A')}\n\n"
        f"👤 **የመኪና ባለቤት፦** {owner_name}\n"
        f"📞 **ስልክ፦** `{owner_phone}`\n\n"
        "📌 **አሁን ደውለው ይደራደሩ!**",
        reply_markup=main_menu(requester.id),
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=truck_owner_id,
            text=(
                "🔔 **አዲስ የጭነት ባለቤት ፍላጎት አሳይቷል!**\n\n"
                f"🚛 **መኪናዎ፦** {truck['type']}\n\n"
                f"👤 **ስም፦** {info.get('name', requester.full_name)}\n"
                f"📞 **ስልክ፦** `{info.get('phone', '')}`\n"
                f"📍 **አድራሻ፦** {info.get('requester_address', '')}\n\n"
                "📌 **አሁን ደውለው ይደራደሩ!**"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"OWNER NOTIFY ERROR: {e}")

    connection_requests.append({
        "type": "truck",
        "item_index": info["item_index"],
        "owner_id": truck_owner_id,
        "requester_id": requester.id,
        "requester_name": info.get("name", requester.full_name),
        "requester_username": info.get("requester_username", ""),
        "requester_phone": info.get("phone", ""),
        "requester_address": info.get("requester_address", ""),
        "created_at": datetime.now().isoformat(),
    })
    await _notify_admin_direct_connection(
        context, info, requester,
        owner_id=truck_owner_id, owner_name=owner_name,
        owner_phone=owner_phone, item_type="truck", item=truck
    )

async def _notify_admin_direct_connection(context, info, requester,
                                            owner_id, owner_name, owner_phone,
                                            item_type, item):
    if item_type == "cargo":
        desc = f"📦 {item['from']} ➡️ {item['to']} ({item['type']})"
    else:
        desc = f"🚛 {item['type']} ({item['capacity']})"

    text = (
        "📊 **TANA CARGO — አዲስ ግንኙነት (ለመዝገብ)**\n\n"
        f"{desc}\n\n"
        f"👤 **ባለቤት፦** {owner_name}\n"
        f"📞 **ስልክ፦** {owner_phone}\n"
        f"🆔 **ID፦** {owner_id}\n\n"
        f"👤 **ጠያቂ፦** {info.get('name', requester.full_name)}\n"
        f"📞 **ስልክ፦** {info.get('phone', '')}\n"
        f"🆔 **ID፦** {requester.id}\n"
        f"📍 **አድራሻ፦** {info.get('requester_address', '')}\n\n"
        "✅ ስልክ ቁጥሮች ለሁለቱም ወገን ተልከዋል።"
    )
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            pass

# ==================================================
# ADMIN CONNECTION LOG
# ==================================================

async def show_connection_requests(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\n"
            "ማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n"
            "📌 Admin ወይም Super Admin ብቻ ያየዋል።",
            reply_markup=main_menu(user_id)
        )
        context.user_data["sending_admin_message"] = True
        return

    if not connection_requests:
        await update.message.reply_text(
            "🤝 እስካሁን የግንኙነት ጥያቄ የለም።",
            reply_markup=main_menu(user_id)
        )
        return

    recent = connection_requests[-20:]
    for idx, req in enumerate(recent):
        real_index = len(connection_requests) - len(recent) + idx
        if req.get("type") == "truck":
            truck = truck_posts[req["item_index"]] if req["item_index"] < len(truck_posts) else {}
            desc = f"🚛 {truck.get('type', 'N/A')} ({truck.get('capacity', 'N/A')})"
        else:
            cargo = cargo_posts[req["item_index"]] if req["item_index"] < len(cargo_posts) else {}
            desc = f"📦 {cargo.get('from', 'N/A')} ➡️ {cargo.get('to', 'N/A')}"

        created = req.get("created_at", "")[:16].replace("T", " ")
        text = (
            f"📥 **ግንኙነት #{real_index + 1}**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{desc}\n\n"
            f"👤 **ባለቤት ID፦** {req.get('owner_id', 'N/A')}\n"
            f"👤 **ጠያቂ፦** {req.get('requester_name', 'N/A')}\n"
            f"🔗 **@{req.get('requester_username', '') or 'የለም'}**\n"
            f"📞 **ስልክ፦** {req.get('requester_phone', 'N/A')}\n"
            f"📍 **አድራሻ፦** {req.get('requester_address', 'N/A')}\n"
            f"📅 **ቀን፦** {created}\n\n"
            "✅ ስልክ ቁጥሮች ለሁለቱም ወገን ተልከዋል።"
        )
        await update.message.reply_text(text)

# ==================================================
# TRACKING
# ==================================================

async def tracking_menu(update, context):
    clear_stale_user_state(context)
    await update.effective_message.reply_text(
        "📍 **TANA CARGO — የጭነት ክትትል**\n\n"
        "🟢 **ክትትል ጀምር** — አዲስ የጉዞ መረጃ ያስመዝግቡ\n"
        "📦 **ጭነቴን በኮድ መከታተል** — የተሰጠዎትን ኮድ ያስገቡ\n"
        "🔴 **ክትትል አቁም** — ክትትል ማቆም ይችላሉ",
        reply_markup=tracking_menu_keyboard()
    )

async def vehicle_tracking_menu(update, context):
    clear_stale_user_state(context)
    await update.effective_message.reply_text(
        "🚚 **TANA CARGO — የመኪና ክትትል**\n\n"
        "🟢 **ክትትል ጀምር** — አዲስ የጉዞ መረጃ ያስመዝግቡ\n"
        "📦 **ጭነቴን በኮድ መከታተል** — የተሰጠዎትን ኮድ ያስገቡ\n"
        "🔴 **ክትትል አቁም** — ክትትል ማቆም ይችላሉ",
        reply_markup=vehicle_tracking_menu_keyboard()
    )

async def tracking_registry(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM tracking_sessions ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text("📊 እስካሁን ምንም የክትትል መዝገብ የለም።")
        return

    for i, row in enumerate(rows):
        record = dict(row)
        code = record["tracking_code"]
        status_labels = {
            "ready": "🟡 ዝግጁ",
            "in_transit": "🟢 በመንገድ ላይ",
            "delivered": "✅ ተጠናቋል",
        }
        msg = (
            f"📊 **የክትትል መዝገብ #{i+1}**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 ኮድ፦ `{code}`\n"
            f"📌 ሁኔታ፦ {status_labels.get(record['status'], record['status'])}\n"
            f"🚛 ሹፌር ID: `{record['driver_user_id']}`\n"
            f"👤 ደንበኛ ID: `{record['customer_user_id']}`\n"
            f"📞 ደንበኛ ስልክ: {record.get('customer_phone') or 'N/A'}\n"
            f"📍 መነሻ: {record.get('origin') or 'N/A'}\n"
            f"🏁 መድረሻ: {record.get('destination') or 'N/A'}\n"
        )
        if record.get("latitude") and record.get("longitude"):
            msg += (
                f"🗺️ የመጨረሻ ቦታ: "
                f"`{record['latitude']:.5f}, {record['longitude']:.5f}`\n"
            )
        if record.get("location_updated_at"):
            msg += f"🕐 የተሻሻለው: {record['location_updated_at'][:16].replace('T', ' ')}\n"

        buttons = []
        if record.get("status") != "delivered":
            if record.get("latitude") and record.get("longitude"):
                buttons.append([
                    InlineKeyboardButton(
                        "🗺️ Live ቦታ አሳይ",
                        callback_data=f"admin_view_live:{code}"
                    )
                ])
            if is_super_admin(user_id):
                buttons.append([
                    InlineKeyboardButton(
                        "🛑 ክትትል አቁም (Super)",
                        callback_data=f"admin_force_stop:{code}"
                    )
                ])

        if buttons:
            await update.message.reply_text(
                msg, reply_markup=InlineKeyboardMarkup(buttons),
                disable_web_page_preview=True
            )
        else:
            await update.message.reply_text(msg, disable_web_page_preview=True)

async def admin_view_live(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    try:
        code = query.data.split(":")[1]
    except IndexError:
        return
    record = db_get_tracking(code)
    if record and record.get("latitude") and record.get("longitude"):
        try:
            await context.bot.send_location(
                chat_id=update.effective_user.id,
                latitude=float(record["latitude"]),
                longitude=float(record["longitude"]),
                live_period=3600
            )
        except Exception:
            await query.message.reply_text(
                f"📍 `{record['latitude']:.6f}, {record['longitude']:.6f}`",
                parse_mode="Markdown"
            )
    else:
        await query.message.reply_text("⚠️ ምንም የተላከ ቦታ የለም።")

async def admin_force_stop(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        code = query.data.split(":")[1]
    except IndexError:
        return
    db_finish_tracking(code)
    await query.edit_message_text(f"✅ ክትትል ኮድ `{code}` በራስዎ ተዘግቷል።", parse_mode="Markdown")

async def tracking_new_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_new"] = {"type": "cargo"}
    await query.message.reply_text(
        "📝 **አዲስ ጭነት ክትትል መክፈት**\n\n"
        "⚠️ **ከመጀመርዎ በፊት፦**\n"
        "1️⃣ የስልክዎን **Location (GPS) ON** ያድርጉ\n"
        "2️⃣ **የኢንተርኔት ዳታ** ያብሩ\n"
        "3️⃣ ቦታውን ከላኩ በኋላ **Location OFF** ማድረግ ይችላሉ\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ የባለጭነቱን (Customer) **ስልክ ቁጥር** ያስገቡ።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=main_menu(update.effective_user.id)
    )
    return TRACK_REQ_PHONE

async def vehicle_tracking_new_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_new"] = {"type": "vehicle"}
    await query.message.reply_text(
        "📝 **አዲስ የመኪና ክትትል መክፈት**\n\n"
        "⚠️ **ከመጀመርዎ በፊት፦**\n"
        "1️⃣ የስልክዎን **Location (GPS) ON** ያድርጉ\n"
        "2️⃣ **የኢንተርኔት ዳታ** ያብሩ\n"
        "3️⃣ ቦታውን ከላኩ በኋላ **Location OFF** ማድረግ ይችላሉ\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ Live Location ማጋራት የፈለጉትን ሰው **ስም** ይጻፉ።\n"
        "ምሳሌ፦ አበበ ከበደ",
        reply_markup=main_menu(update.effective_user.id)
    )
    return TRACK_REQ_NAME

async def tracking_req_name(update, context):
    name = update.message.text.strip().split("\n")[0].strip()
    if not name or len(name) < 2:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ ስም ይጻፉ።")
        return TRACK_REQ_NAME
    context.user_data["track_new"]["customer_name"] = name
    await update.message.reply_text(
        "2️⃣ የዚህ ሰው **ስልክ ቁጥር** ያስገቡ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=main_menu(update.effective_user.id)
    )
    return TRACK_REQ_PHONE

async def tracking_req_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nምሳሌ፦ 0912345678")
        return TRACK_REQ_PHONE

    target_id = find_user_by_phone(result)
    req = context.user_data.get("track_new", {})

    if not target_id:
        await update.message.reply_text(
            "❌ **ውድ ደንበኛችን!**\n\n"
            f"`{result}` በዚህ ስልክ ቁጥር የተመዘገበ ተጠቃሚ አልተገኘም።\n\n"
            "📌 **እባክዎ የሚከተሉትን ይሞክሩ፦**\n\n"
            "1️⃣ ሌላ **TANA CARGO የተመዘገበበት ስልክ** ካለ እሱን ያስገቡ\n\n"
            "2️⃣ ወይም 👇 **Live Location Share (Copy)** በማድረግ "
            "ቀጥታ ቦታውን ወደ ተፈለገው ሰው ይላኩ።",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📋 Live Location Share (Copy)",
                    callback_data="track_live_share_copy"
                )
            ]]),
            parse_mode="Markdown"
        )
        return TRACK_REQ_PHONE

    req["customer_phone"] = result
    req["target_user_id"] = target_id
    context.user_data["track_new"] = req

    if req.get("type") == "vehicle":
        await update.message.reply_text(
            "3️⃣ **የመንገዱን ዝርዝር** ይጻፉ።\n"
            "ምሳሌ፦ ባህር ዳር ➡️ አዲስ አበባ",
            reply_markup=main_menu(update.effective_user.id)
        )
    else:
        await update.message.reply_text(
            "2️⃣ **የመንገዱን ዝርዝር** ይጻፉ።\n"
            "ምሳሌ፦ ባህር ዳር ➡️ አዲስ አበባ",
            reply_markup=main_menu(update.effective_user.id)
        )
    return TRACK_REQ_ROUTE

async def tracking_req_route(update, context):
    route = update.message.text.strip()
    if not route:
        await update.message.reply_text("❌ እባክዎ የመንገዱን ዝርዝር ይጻፉ።")
        return TRACK_REQ_ROUTE
    context.user_data["track_new"]["route"] = route

    step = "4️⃣" if context.user_data["track_new"].get("type") == "vehicle" else "3️⃣"
    await update.message.reply_text(
        f"{step} **የአሁኑን የመኪና ቦታ** ይላኩ።\n\n"
        "⚠️ **ማሳሰቢያ፦** የስልክዎን Location ON ያድርጉ፣ "
        "የኢንተርኔት ዳታ ያብሩ፣ ከዚያ ቦታውን ይላኩ።\n"
        "ቦታውን ከላኩ በኋላ **Location OFF** ማድረግ ይችላሉ።\n\n"
        "ከታች ያለውን ቁልፍ ተጭነው GPS ይላኩ።",
        reply_markup=location_request_keyboard()
    )
    return TRACK_REQ_LOCATION

async def tracking_req_location(update, context):
    if not update.message.location:
        if update.message.text and "ሰርዝ" in update.message.text:
            context.user_data.pop("track_new", None)
            await update.message.reply_text(
                "❌ ጉዞ መክፈቱ ተሰርዟል።",
                reply_markup=main_menu(update.effective_user.id)
            )
            return ConversationHandler.END
        await update.message.reply_text(
            "❌ እባክዎ ቦታውን በGPS ይላኩ (📍 የአሁኑን ቦታ ላክ)።",
            reply_markup=location_request_keyboard()
        )
        return TRACK_REQ_LOCATION

    loc = update.message.location
    req = context.user_data.get("track_new", {})
    req["driver_id"] = update.effective_user.id
    req["driver_name"] = update.effective_user.full_name
    req["lat"] = loc.latitude
    req["lon"] = loc.longitude

    code = "TC" + secrets.token_hex(4).upper()
    customer_name = req.get("customer_name", "")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tracking_sessions (
                tracking_code, request_type, driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                status, latitude, longitude, location_updated_at, created_at,
                customer_name, customer_phone, driver_phone
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', 'in_transit', ?, ?, ?, ?, ?, ?, ?)
        """, (
            code, req.get("type", "cargo"), req["driver_id"], req["target_user_id"],
            req["route"], req["route"],
            req["lat"], req["lon"],
            datetime.now().astimezone().isoformat(timespec="seconds"),
            datetime.now().astimezone().isoformat(timespec="seconds"),
            customer_name, req["customer_phone"], ""
        ))
        conn.commit()
    finally:
        conn.close()

    context.user_data.pop("track_new", None)

    await update.message.reply_text(
        "✅ **ቦታው ተቀብለናል!**\n\n"
        f"🔑 **የክትትል ኮድ፦** `{code}`\n\n"
        "📞 ይህንን ኮድ ለባለጭነቱ ያስተላልፉ።\n\n"
        "⚠️ **ማሳሰቢያ፦** አሁን የስልክዎን **Location OFF** ማድረግ ይችላሉ።",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_location(
            chat_id=req["target_user_id"],
            latitude=float(req["lat"]),
            longitude=float(req["lon"]),
            live_period=3600
        )
    except Exception as e:
        print(f"SEND LOCATION ERROR: {e}")

    try:
        await context.bot.send_photo(
            chat_id=req["target_user_id"],
            photo=START_IMAGE_ID,
            caption=(
                "📦 **TANA CARGO — የጭነት ክትትል**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 **ለ፦** {customer_name or 'ውድ ደንበኛችን'}\n"
                "🚛 **ሹፌሩ ጉዞ ጀምሯል!**\n\n"
                f"🔑 **የክትትል ኮድ፦** `{code}`\n\n"
                "🗺️ **Live ቦታው** ከላይ ተልኳል — በመንካት ይከታተሉ።\n\n"
                "👉 **ቀጣይ ጊዜያት** ወደ '📍 የጭነት ክትትል' → "
                "'📦 ጭነቴን በኮድ መከታተል' በመግባት ኮዱን በማስገባት "
                "ቦታውን ማየት ይችላሉ።\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 @{BOT_USERNAME} ተጭነው የ TANA CARGO አባል ይሁኑ\n"
                "🎁 አገልግሎት በነፃ!"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"SEND BRANDED PHOTO ERROR: {e}")
        try:
            await context.bot.send_message(
                chat_id=req["target_user_id"],
                text=(
                    f"🔑 **የክትትል ኮድ፦** `{code}`\n\n"
                    f"👉 @{BOT_USERNAME} ተጭነው የ TANA CARGO አባል ይሁኑ"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

    return ConversationHandler.END

async def tracking_live_share_copy(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📋 **Live Location Share (Copy)**\n\n"
        "⚠️ **እባክዎ የሚከተሉትን ያድርጉ፦**\n\n"
        "1️⃣ የስልክዎን **Location ON** ያድርጉ\n"
        "2️⃣ **የኢንተርኔት ዳታ** ያብሩ\n"
        "3️⃣ **Telegram ውስጥ** ወደ ተፈለገው ሰው ግልጽ መልእክት ይሂዱ\n"
        "4️⃣ **📎 (Attach)** → **Location** → **Share Live Location** "
        "የሚለውን ይጫኑ\n"
        "5️⃣ ጊዜውን (ለምሳሌ 1 ሰዓት) ይምረጡ → **Send**\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 **ቦታውን ከላኩ በኋላ Location OFF ማድረግ ይችላሉ።**\n\n"
        "🎁 **TANA CARGO አገልግሎት በነፃ!**\n\n"
        f"👉 @{BOT_USERNAME} ተጭነው የ TANA CARGO አባል ይሁኑ",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode="Markdown"
    )
    context.user_data.pop("track_new", None)
    return ConversationHandler.END

async def tracking_customer_choose(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_lookup_waiting"] = True
    await query.message.reply_text(
        "📦 **ጭነቴን በኮድ መከታተል**\n\n"
        "የተሰጠዎትን የክትትል ኮድ ያስገቡ።\n"
        "ምሳሌ፦ `TC1A2B3C4`\n\n"
        "📌 ኮዱን ካላወቁ ሹፌሩን ይጠይቁ።",
        reply_markup=main_menu(update.effective_user.id),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def tracking_text_handler(update, context):
    if not update.effective_user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    if context.user_data.get("track_new") and "ሰርዝ" in text:
        context.user_data.pop("track_new", None)
        await update.message.reply_text(
            "❌ ጉዞ መክፈቱ ተሰርዟል።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return ConversationHandler.END

    if not context.user_data.get("tracking_lookup_waiting"):
        return

    code = "".join(text.upper().split())
    if len(code) < 6 or len(code) > 20:
        await update.message.reply_text(
            "❌ የክትትል ኮዱ ትክክል አይመስልም።\n"
            "ምሳሌ፦ TC1A2B3C4"
        )
        return

    record = db_get_tracking(code)
    if not record:
        await update.message.reply_text(
            "❌ ይህ የክትትል ኮድ አልተገኘም።\n\n"
            "እባክዎ ኮዱን በትክክል መጻፍዎን ያረጋግጡ።"
        )
        return

    context.user_data.pop("tracking_lookup_waiting", None)
    await tracking_show_result(update, context, record)
    return ConversationHandler.END

async def tracking_show_result(update, context, record):
    code = record["tracking_code"]
    status_labels = {
        "ready": "🟡 ጉዞ ለመጀመር ዝግጁ",
        "in_transit": "🟢 በመንገድ ላይ",
        "delivered": "✅ ጉዞው ተጠናቋል",
    }
    msg = (
        f"📦 **TANA CARGO — የቀጥታ ክትትል**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔑 ኮድ፦ `{code}`\n"
        f"📊 ሁኔታ፦ {status_labels.get(record.get('status'), record.get('status'))}\n"
        f"📍 መነሻ/መረጃ፦ {record.get('origin') or 'አልተገለጸም'}\n"
        f"🏁 መድረሻ/መንገድ፦ {record.get('destination') or 'አልተገለጸም'}\n"
    )
    if record.get("latitude") is not None and record.get("longitude") is not None:
        lat = float(record["latitude"])
        lon = float(record["longitude"])
        msg += f"\n📍 **የመጨረሻ ቦታ፦** `{lat:.6f}, {lon:.6f}`"
        if record.get("location_updated_at"):
            msg += f"\n🕐 **የተሻሻለው፦** {record['location_updated_at'][:16].replace('T', ' ')}"
        try:
            await update.effective_message.reply_location(
                latitude=lat, longitude=lon, live_period=3600
            )
        except Exception:
            pass
    else:
        msg += "\n📍 ሹፌሩ እስካሁን የGPS ቦታ አልላከም።"

    msg += (
        "\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 @{BOT_USERNAME} ተጭነው የ TANA CARGO አባል ይሁኑ\n"
        "🎁 አገልግሎት በነፃ!"
    )
    await update.effective_message.reply_text(
        msg, disable_web_page_preview=True, parse_mode="Markdown"
    )

async def tracking_stop_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_stop"] = {}
    await query.message.reply_text(
        "🛑 **ክትትል ማቆም**\n\n"
        "1️⃣ እባክዎ **የሚከታተለው ሰው ስም** ይጻፉ።\n"
        "ምሳሌ፦ አበበ ከበደ",
        reply_markup=main_menu(update.effective_user.id)
    )
    return TRACK_STOP_NAME

async def tracking_stop_name(update, context):
    name = update.message.text.strip().split("\n")[0].strip()
    if not name or len(name) < 2:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ ስም ይጻፉ።")
        return TRACK_STOP_NAME
    context.user_data["tracking_stop"]["name"] = name
    await update.message.reply_text(
        "2️⃣ **የሚከታተለው ሰው ስልክ ቁጥር** ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=main_menu(update.effective_user.id)
    )
    return TRACK_STOP_PHONE

async def tracking_stop_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nምሳሌ፦ 0912345678")
        return TRACK_STOP_PHONE

    info = context.user_data.get("tracking_stop", {})
    info["phone"] = result
    info["requester_id"] = update.effective_user.id
    info["requester_name"] = update.effective_user.full_name
    info["timestamp"] = datetime.now().astimezone().isoformat()
    tracking_stops.append(info)
    idx = len(tracking_stops) - 1
    context.user_data.pop("tracking_stop", None)

    await update.message.reply_text(
        "✅ **የክትትል ማቆሚያ ጥያቄዎ ለአድሚን ተልኳል።**\n\n"
        "⏳ አድሚን ሲያጸድቅ የክትትሉ ባለቤት ማሳወቂያ ይደርሰዋል።",
        reply_markup=main_menu(update.effective_user.id)
    )

    text = (
        "🛑 **TANA CARGO — አዲስ የክትትል ማቆሚያ ጥያቄ**\n\n"
        f"👤 ጠያቂ: {info['requester_name']} (ID: {info['requester_id']})\n"
        f"📋 የሚከታተለው ሰው ስም: {info['name']}\n"
        f"📞 ስልክ: {info['phone']}\n\n"
        "📌 አጽድቀው ክትትሉ እንዲቆም ይፈልጋሉ?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ክትትል አቁም (Right)", callback_data=f"approvestop_{idx}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejectstop_{idx}")
    ]])
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception:
            pass
    return ConversationHandler.END

async def admin_approve_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM tracking_sessions WHERE customer_phone = ? "
            "AND status IN ('ready', 'in_transit') ORDER BY created_at DESC LIMIT 1",
            (info["phone"],)
        ).fetchone()
    finally:
        conn.close()

    if row:
        record = dict(row)
        db_finish_tracking(record["tracking_code"])
        driver_id = int(record["driver_user_id"])
        try:
            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    f"🛑 **ክትትል ቁማል**\n\n"
                    f"🔑 ኮድ: `{record['tracking_code']}`\n"
                    f"👤 የተከታተለው ሰው: {info['name']}\n"
                    f"📞 ስልክ: {info['phone']}\n\n"
                    "ጭነትዎ መዳረሻው ላይ ደርሷል።"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await query.edit_message_text(
            f"✅ **ክትትል ተቆሟል!**\n\n"
            f"🔑 ኮድ: `{record['tracking_code']}`\n"
            "ለሹፌሩ ማሳወቂያ ተልኳል።",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("⚠️ በዚህ ስልክ ቁጥር ንቁ የክትትል ጉዞ አልተገኘም።")

    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text=f"✅ **ክትትል ተቆሟል!**\n\n📞 {info['phone']} ለሚለው ስልክ ክትትል ተዘግቷል።"
        )
    except Exception:
        pass

async def admin_reject_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]
    await query.edit_message_text("❌ የክትትል ማቆሚያ ጥያቄው ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text="❌ የክትትል ማቆሚያ ጥያቄዎ በአድሚን ውድቅ ተደርጓል።"
        )
    except Exception:
        pass

# ==================================================
# OWNER, PROFILE, SUPPORT, ABOUT, PAYMENTS
# ==================================================

async def owner_start(update, context):
    clear_stale_user_state(context)
    context.user_data["owner"] = {}
    await update.message.reply_text(
        "📦 **የጭነት ባለቤት ምዝገባ**\n\n1️⃣ ስምዎን ይጻፉ።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return OWNER_NAME

async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return OWNER_PHONE

async def owner_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text(
            "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።",
            reply_markup=phone_request_keyboard()
        )
        return OWNER_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።",
            reply_markup=phone_request_keyboard()
        )
        return OWNER_PHONE
    phone = result
    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id
    user = update.effective_user
    users.setdefault(user.id, {
        "name": user.full_name,
        "username": user.username or "",
        "id": user.id,
    })
    users[user.id]["owner"] = owner.copy()
    try:
        db_save_user(
            user.id, user.full_name, user.username or "",
            owner_name=owner["name"], owner_phone=owner["phone"]
        )
    except Exception as e:
        print(f"DB SAVE OWNER ERROR: {e}")
    await update.message.reply_text(
        f"✅ **ምዝገባዎ ተጠናቋል!**\n\n"
        f"👤 {owner['name']}\n📞 {owner['phone']}",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

async def owner_phone_fallback(update, context):
    await update.message.reply_text(
        "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።",
        reply_markup=phone_request_keyboard()
    )
    return OWNER_PHONE

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {
        "name": update.effective_user.full_name,
        "username": update.effective_user.username or "",
        "id": user_id,
    })
    total_users = get_total_users_count()
    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user['name']}\n"
        f"🔗 Username፦ @{user['username'] or 'የለም'}\n"
        f"🆔 Telegram ID፦ {user['id']}\n\n"
        f"👥 **ጠቅላላ ተጠቃሚዎች፦** {total_users:,}\n"
    )
    if "owner" in user:
        owner = user["owner"]
        message += (
            f"\n📦 **የጭነት ባለቤት ምዝገባ፦** ✅\n"
            f"👤 {owner.get('name', 'N/A')}\n"
            f"📞 {owner.get('phone', 'N/A')}\n"
        )
    if is_broker(user_id):
        message += "\n🏷️ **የጭነት ደላላ፦** ✅ ጸድቋል\n"
    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id and c.get("active", True)]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id and t.get("active", True)]
    message += (
        f"\n🚚 **የለጠፉት ጭነት፦** {len(my_cargo)}\n"
        f"🚛 **የተመዘገቡ መኪኖች፦** {len(my_trucks)}"
    )
    if is_admin(user_id):
        role = "Super Admin" if is_super_admin(user_id) else "Admin"
        message += f"\n\n👑 **ሚና፦** {role}"
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        "📞 **TANA CARGO — እገዛ**\n\n"
        "ለእገዛ ለማግኘት @tanapage ይጠቀሙ።\n\n"
        f"☎️ {SUPPORT_PHONE}\n"
        f"☎️ {SUPPORT_PHONE_2}\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ ይህ መረጃ ለ Super Admin ብቻ ነው።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "🚚 **TANA CARGO | ጣና ጭነት** — ስርዓት መረጃ\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 **Bot፦** @tanacargobot\n"
        "📢 **Channel፦** @tanapage\n"
        f"☎️ **Support 1፦** {SUPPORT_PHONE}\n"
        f"☎️ **Support 2፦** {SUPPORT_PHONE_2}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **የስርዓቱ ሁኔታ፦**\n"
        f"• 👥 ተጠቃሚዎች፦ {len(users)}\n"
        f"• 📦 ንቁ ጭነቶች፦ {len([c for c in cargo_posts if c.get('active', True)])}\n"
        f"• 🚛 ንቁ መኪኖች፦ {len([t for t in truck_posts if t.get('active', True)])}\n"
        f"• 🏷️ ደላሎች፦ {len([b for b in brokers if b.get('approved') and b.get('active')])}\n"
        f"• 📊 ግንኙነቶች፦ {len(connection_requests)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "❤️ TANA CARGO — ጭነትንና መኪናን እናገናኛለን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def service_payment(update, context):
    await update.message.reply_text(
        payment_methods_text(),
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

# ==================================================
# USER → ADMIN MESSAGE / PHOTO
# ==================================================

async def handle_user_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    username = update.effective_user.username or "የለም"
    name = update.effective_user.full_name
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📨 **TANA CARGO — አዲስ መልዕክት**\n\n"
                    f"👤 ስም፦ {name}\n"
                    f"🔗 Username፦ @{username}\n"
                    f"🆔 Telegram ID፦ {user_id}\n\n"
                    f"💬 **መልዕክት፦**\n{text}"
                )
            )
        except Exception:
            pass
    await update.message.reply_text(
        "✅ መልዕክትዎ ለ Admin/Super Admin ተልኳል።\n\n📸 ፎቶም መላክ ይችላሉ!",
        reply_markup=main_menu(user_id)
    )
    context.user_data.pop("sending_admin_message", None)

async def handle_user_photo(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or "የለም"
    name = update.effective_user.full_name
    caption = update.message.caption or ""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    else:
        return
    for admin_id in get_admin_ids():
        try:
            text = (
                "📸 **TANA CARGO — አዲስ ፎቶ/ስክሪንሾት**\n\n"
                f"👤 ስም፦ {name}\n"
                f"🔗 Username፦ @{username}\n"
                f"🆔 Telegram ID፦ {user_id}\n"
            )
            if caption:
                text += f"\n💬 **ጽሑፍ፦**\n{caption}"
            if file_type == "photo":
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=text)
            else:
                await context.bot.send_document(chat_id=admin_id, document=file_id, caption=text)
        except Exception:
            pass
    await update.message.reply_text(
        "✅ ፎቶ/ስክሪንሾትዎ ለ Admin/Super Admin ተልኳል።\n\n📌 ሌላ መልዕክት ወይም ፎቶ መላክ ይችላሉ።",
        reply_markup=main_menu(user_id)
    )
    context.user_data.pop("sending_admin_message", None)

async def _start_admin_message_mode(update, context):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\n"
        "ማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n"
        "📌 Admin ወይም Super Admin ብቻ ያየዋል።",
        reply_markup=main_menu(user_id)
    )
    context.user_data["sending_admin_message"] = True

async def receipt_photo(update, context):
    if context.user_data.get("sending_admin_message"):
        return await handle_user_photo(update, context)
    return await handle_user_photo(update, context)

# ==================================================
# /start, CANCEL, ROUTERS
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user

    if is_user_registered(user.id):
        users.setdefault(user.id, {
            "name": user.full_name,
            "username": user.username or "",
            "id": user.id,
        })
        total_users = get_total_users_count()
        caption = (
            "🎉 **እንኳን ደህና መጡ ወደ TANA CARGO!** 🎉\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
            "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦"
        )
        try:
            await update.message.reply_photo(
                photo=START_IMAGE_ID,
                caption=caption,
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text(
                caption,
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
        return ConversationHandler.END

    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "🚛 **TANA CARGO | ጣና ጭነት** 📦\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "✨ **እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!** ✨\n\n"
                "🔹 **ጭነት ያለዎት?** → ተስማሚ መኪና ያግኙ\n"
                "🔹 **መኪና አለዎት?** → ተስማሚ ጭነት ያግኙ\n"
                "🔹 **በቀጥታ ይደራደሩ!**\n\n"
                "📱 **ለመጀመር የራስዎን ስልክ ቁጥር ያጋሩ።**\n\n"
                "📢 ለበለጠ መረጃ 👉 @tanapage Join ያድርጉ\n\n"
                "⚠️ **ማሳሰቢያ፦** ይህ ስልክ ቁጥርዎ በTANA CARGO ለሚደረጉ ግንኙነቶች ይጠቅማል።"
            ),
            reply_markup=phone_request_keyboard(),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "🚛 **TANA CARGO | ጣና ጭነት** 📦\n\n"
            "✨ **እንኳን ደህና መጡ!** ✨\n\n"
            "📱 **ለመጀመር የራስዎን ስልክ ቁጥር ያጋሩ።**\n"
            "📢 ለበለጠ መረጃ 👉 @tanapage Join ያድርጉ",
            reply_markup=phone_request_keyboard(),
            parse_mode="Markdown"
        )
    return START_PHONE

async def start_phone_input(update, context):
    if not update.message.contact:
        await update.message.reply_text(
            "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።",
            reply_markup=phone_request_keyboard()
        )
        return START_PHONE

    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።",
            reply_markup=phone_request_keyboard()
        )
        return START_PHONE
    phone = result

    user = update.effective_user
    users.setdefault(user.id, {
        "name": user.full_name,
        "username": user.username or "",
        "id": user.id,
    })
    users[user.id]["owner"] = {
        "phone": phone,
        "name": user.full_name,
        "user_id": user.id,
    }
    try:
        db_save_user(
            user.id, user.full_name, user.username or "",
            owner_name=user.full_name, owner_phone=phone
        )
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")

    total_users = get_total_users_count()

    welcome_caption = (
        "🎉 **እንኳን ወደ TANA CARGO በደህና መጡ!** 🎉\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 **ስልክ ቁጥርዎ በትክክል ተመዝግቧል፦** `{phone}`\n\n"
        f"👥 **ከታች እነሆ በለፅሀለፍ የደንበኛ ቁጥር፦**\n"
        f"🌟 **{total_users:,}** ተጠቃሚዎች ተቀላቅለዋል!\n\n"
        "📢 ስለ TANA CARGO ሙሉ መረጃ 👉 @tanapage Join ያድርጉ።\n\n"
        "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦"
    )
    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=welcome_caption,
            reply_markup=main_menu(user.id),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            welcome_caption,
            reply_markup=main_menu(user.id),
            parse_mode="Markdown"
        )
    return ConversationHandler.END

async def start_phone_fallback(update, context):
    await update.message.reply_text(
        "❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።\n"
        "ወይም መጀመሪያ /start ይጫኑ።",
        reply_markup=phone_request_keyboard()
    )
    return START_PHONE

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ ሂደቱ ተሰርዟል።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

MASTER_MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 የጭነት መኪና ማስመዝገብ|🚛 የጭነት መኪና መፈለግ|"
    r"👤 የኔ መረጃ|📍 የጭነት ክትትል|🚚 የመኪና ክትትል|"
    r"📊 የክትትል መዝገብ \(Admin\)|📢 ለደንበኞች መረጃ መላኪያ|"
    r"💬 Sol Message \(ለሁሉም\)|"
    r"🤝 የግንኙነት ጥያቄዎች|📋 የጭነት ደላሎች መዝገብ|"
    r"🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ|📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ|"
    r"💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"
)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id

    if not is_user_registered(uid):
        await update.message.reply_text(
            "⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**"
        )
        return ConversationHandler.END

    context.user_data["subscribed"] = True
    clear_stale_user_state(context)

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    elif text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    elif text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    elif text == "🚛 የጭነት መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END
    elif text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    elif text == "📍 የጭነት ክትትል":
        await tracking_menu(update, context)
        return ConversationHandler.END
    elif text == "🚚 የመኪና ክትትል":
        await vehicle_tracking_menu(update, context)
        return ConversationHandler.END
    elif text == "📊 የክትትል መዝገብ (Admin)":
        await tracking_registry(update, context)
        return ConversationHandler.END
    elif text == "📢 ለደንበኞች መረጃ መላኪያ":
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Super Admin ብቻ!")
            return ConversationHandler.END
        return await broadcast_start(update, context)
    elif text == "💬 Sol Message (ለሁሉም)":
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Super Admin ብቻ!")
            return ConversationHandler.END
        return await sol_message_start(update, context)
    elif text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    elif text == "📋 የጭነት ደላሎች መዝገብ":
        await show_broker_registry(update, context)
        return ConversationHandler.END
    elif text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ":
        return await broker_start(update, context)
    elif text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await _start_admin_message_mode(update, context)
        return ConversationHandler.END
    elif text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END
    elif text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    elif text == "ℹ️ About":
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Super Admin ብቻ!")
            return ConversationHandler.END
        await about(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

async def menu_interrupt(update, context):
    return await menu_router(update, context)

async def text_router(update, context):
    if update.effective_user is None:
        return
    user_id = update.effective_user.id

    if not is_user_registered(user_id):
        # ተጠቃሚው ኮንታክት ከላከ በቀጥታ እንዲመዘገብ ማድረግ
        if update.message and update.message.contact:
            return await start_phone_input(update, context)
            
        await update.message.reply_text(
            "⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**"
        )
        return

    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)

    if context.user_data.get("tracking_lookup_waiting") or context.user_data.get("track_new"):
        return await tracking_text_handler(update, context)

    if context.user_data.get("connect_info"):
        return await connect_fallback_router(update, context)

async def connect_fallback_router(update, context):
    info = context.user_data.get("connect_info")
    if not info:
        return
    if not info.get("name") and update.message.text:
        info["name"] = update.message.text.strip()
        context.user_data["connect_info"] = info
        await update.message.reply_text(
            "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
            reply_markup=phone_request_keyboard()
        )
        return

# ==================================================
# EXPIRY CLEANER / ERROR / HEALTH / MAIN
# ==================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"TANA CARGO ERROR: {context.error}")
    try:
        print(f"ERROR UPDATE: {update}")
    except Exception:
        pass

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"TANA CARGO is running")
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"✅ TANA CARGO health server listening on port {port}")
    return server

async def clean_expired_posts(context):
    now = datetime.now()
    expired_cargos = []
    expired_trucks = []

    for i, cargo in enumerate(cargo_posts):
        if not cargo.get("active", True):
            continue
        ed = cargo.get("expiry_date")
        if ed:
            try:
                if now >= datetime.fromisoformat(ed):
                    expired_cargos.append(i)
            except Exception:
                pass

    for i, truck in enumerate(truck_posts):
        if not truck.get("active", True):
            continue
        ed = truck.get("expiry_date")
        if ed:
            try:
                if now >= datetime.fromisoformat(ed):
                    expired_trucks.append(i)
            except Exception:
                pass

    for i in expired_cargos:
        cargo_posts[i]["active"] = False
        try:
            await context.bot.send_message(
                chat_id=cargo_posts[i]["user_id"],
                text=(
                    "⏰ **የጭነት ጊዜ ገደብ**\n\n"
                    f"📍 {cargo_posts[i]['from']} ➡️ {cargo_posts[i]['to']}\n"
                    f"📦 {cargo_posts[i]['type']}\n\n"
                    "72 ሰዓት ስለሞላ ተወግዷል።"
                )
            )
        except Exception:
            pass

    for i in expired_trucks:
        truck_posts[i]["active"] = False
        try:
            await context.bot.send_message(
                chat_id=truck_posts[i]["user_id"],
                text=(
                    "⏰ **የመኪና ጊዜ ገደብ**\n\n"
                    f"🚛 {truck_posts[i]['type']}\n\n"
                    "72 ሰዓት ስለሞላ ተወግዷል።"
                )
            )
        except Exception:
            pass

async def cmd_find_cargo(update, context):
    return await find_cargo(update, context)

async def cmd_find_truck(update, context):
    return await find_truck(update, context)

async def cmd_profile(update, context):
    return await profile(update, context)

async def cmd_track(update, context):
    return await tracking_menu(update, context)

async def cmd_cargo(update, context):
    clear_stale_user_state(context)
    return await cargo_start(update, context)

async def cmd_truck(update, context):
    clear_stale_user_state(context)
    return await truck_start(update, context)

async def cmd_owner(update, context):
    clear_stale_user_state(context)
    return await owner_start(update, context)

async def cmd_broker(update, context):
    clear_stale_user_state(context)
    return await broker_start(update, context)

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    start_health_server()

    try:
        init_db()
        load_all_from_db()
        print("✅ Database initialized and loaded")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    application.add_error_handler(error_handler)

    # Command Handlers
    application.add_handler(CommandHandler("findcargo", cmd_find_cargo))
    application.add_handler(CommandHandler("findtruck", cmd_find_truck))
    application.add_handler(CommandHandler("myprofile", cmd_profile))
    application.add_handler(CommandHandler("support", support_start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("track", cmd_track))

    master_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("cargo", cmd_cargo),
            CommandHandler("truck", cmd_truck),
            CommandHandler("owner", cmd_owner),
            CommandHandler("broker", cmd_broker),

            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የጭነት ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^🚚 የመኪና ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^📊 የክትትል መዝገብ \(Admin\)$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 ለደንበኞች መረጃ መላኪያ$"), menu_router),
            MessageHandler(filters.Regex(r"^💬 Sol Message \(ለሁሉም\)$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^📋 የጭነት ደላሎች መዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),

            CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"),
            CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"),
            CallbackQueryHandler(tracking_new_start, pattern=r"^track_start$"),
            CallbackQueryHandler(vehicle_tracking_new_start, pattern=r"^vehicle_track_start$"),
            CallbackQueryHandler(tracking_stop_start, pattern=r"^track_stop$"),
            CallbackQueryHandler(tracking_customer_choose, pattern=r"^track_lookup$"),
            CallbackQueryHandler(tracking_live_share_copy, pattern=r"^track_live_share_copy$"),
        ],
        states={
            CARGO_FROM: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from),
            ],
            CARGO_TO: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to),
            ],
            CARGO_TYPE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type),
            ],
            CARGO_VEHICLE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text),
            ],
            CARGO_SIZE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                CallbackQueryHandler(cargo_size, pattern=r"^size_"),
            ],
            CARGO_WEIGHT: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight),
            ],
            CARGO_TRUCKS_NEEDED: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_trucks_needed),
            ],
            CARGO_CALENDAR: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                CallbackQueryHandler(cargo_calendar, pattern=r"^cal_"),
            ],
            CARGO_DATE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date),
            ],
            CARGO_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.CONTACT, cargo_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone_fallback),
            ],
            CARGO_PRICE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price),
            ],
            TRUCK_TYPE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type),
            ],
            TRUCK_PLATE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate),
            ],
            TRUCK_CAPACITY: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity),
            ],
            TRUCK_ROUTE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                CallbackQueryHandler(truck_route_choice, pattern=r"^route_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_route),
            ],
            TRUCK_ADDRESS: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address),
            ],
            TRUCK_DRIVER: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver),
            ],
            TRUCK_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.CONTACT, truck_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone_fallback),
            ],
            OWNER_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name),
            ],
            OWNER_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.CONTACT, owner_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, owner_phone_fallback),
            ],
            BROKER_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name),
            ],
            BROKER_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.CONTACT, broker_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broker_phone_fallback),
            ],
            BROKER_ADDRESS: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broker_address),
            ],
            CONNECT_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name),
            ],
            CONNECT_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.CONTACT, connect_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone_fallback),
            ],
            CONNECT_TYPE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type),
            ],
            TRACK_REQ_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_phone),
            ],
            TRACK_REQ_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_name),
            ],
            TRACK_REQ_ROUTE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_route),
            ],
            TRACK_REQ_LOCATION: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.LOCATION, tracking_req_location),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_location),
            ],
            TRACK_STOP_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_name),
            ],
            TRACK_STOP_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_phone),
            ],
            START_PHONE: [
                MessageHandler(filters.CONTACT, start_phone_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, start_phone_fallback),
            ],
            ADMIN_BROADCAST_TEXT: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message),
            ],
            SOL_MESSAGE_TEXT: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sol_message_send),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )
    application.add_handler(master_conversation)

    # EXTRA CALLBACKS
    application.add_handler(CallbackQueryHandler(already_selected_callback, pattern=r"^already_selected$"))
    application.add_handler(CallbackQueryHandler(admin_approve_stop_request, pattern=r"^approvestop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_stop_request, pattern=r"^rejectstop_\d+$"))
    application.add_handler(CallbackQueryHandler(broker_right_confirm, pattern=r"^brokerright_\d+$"))
    application.add_handler(CallbackQueryHandler(broker_x_delete, pattern=r"^brokerxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_broker, pattern=r"^admindeletebroker_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_view_live, pattern=r"^admin_view_live:"))
    application.add_handler(CallbackQueryHandler(admin_force_stop, pattern=r"^admin_force_stop:"))

    # MESSAGES
    application.add_handler(MessageHandler(filters.PHOTO, receipt_photo))
    application.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, receipt_photo))
    
    # ስልክ በሚላክበት ጊዜ እንኳን እንዲይዝ (filters.CONTACT)
    application.add_handler(MessageHandler(filters.TEXT | filters.CONTACT, text_router))

    if application.job_queue:
        application.job_queue.run_repeating(clean_expired_posts, interval=3600, first=60)
        application.job_queue.run_repeating(auto_delete_messages, interval=60, first=30)
        print("✅ Expiry cleaner + Auto-delete started")

    print("🚀 TANA CARGO Bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
