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
SUPPORT_PHONE_2 = "0912991128"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanapage"

# Traccar Config
TRACCAR_URL = "http://demo2.traccar.org"
TRACCAR_TOKEN = "RjBEAiBc98uaEhRRlPGxO9_vgSO0ZpaM6RfAKBJ6wRYXgO_pZgIgPR5JRatK9XmfQXCwT9Xc-Di5OiLQHUviT9NF6RxwefZ7ImkiOjg5MjE4NDA2Mzc2MTgzNjE1NzUsInUiOjgzNjcsImUiOiIyMDI2LTEwLTI4VDIxOjAwOjAwLjAwMCswMDowMCJ9"
TRACCAR_DEVICE_ID = "92643104"

NEGOTIATION_TIMEOUT_SECONDS = 300
CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
MESSAGE_AUTO_DELETE_SECONDS = 1800
DB_PATH = "tana_cargo.db"

# ==================================================
# TRACCAR API FUNCTION (LIVE)
# ==================================================

def get_traccar_location(device_id: str):
    try:
        headers = {"Authorization": f"Bearer {TRACCAR_TOKEN}"}
        url_dev = f"{TRACCAR_URL}/api/devices?uniqueId={device_id}"
        response = requests.get(url_dev, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None
            
        devices = response.json()
        if not devices:
            return None
        
        position_id = devices[0].get("positionId")
        if not position_id:
            return None

        url_pos = f"{TRACCAR_URL}/api/positions?id={position_id}"
        pos_response = requests.get(url_pos, headers=headers, timeout=10)
        positions = pos_response.json()
        
        if not positions:
            return None
            
        pos = positions[0]
        return {
            "latitude": pos.get("latitude"),
            "longitude": pos.get("longitude"),
            "address": pos.get("address", "አድራሻ አልተገኘም"),
            "timestamp": pos.get("fixTime", "")[:16]
        }
    except Exception as e:
        print(f"Traccar API Error: {e}")
        return None

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

def gc_to_et(gc_year, gc_month, gc_day):
    return gc_to_et_date(datetime(gc_year, gc_month, gc_day))

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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT, username TEXT,
            owner_name TEXT, owner_phone TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cargo_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            from_location TEXT, to_location TEXT, cargo_type TEXT,
            vehicle TEXT, size INTEGER, size_name TEXT, weight TEXT,
            date_et TEXT, date_gc TEXT, phone TEXT, price REAL,
            price_display TEXT, price_status TEXT, active INTEGER DEFAULT 1,
            timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS truck_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            truck_type TEXT, plate TEXT, capacity TEXT, route TEXT,
            address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1,
            timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brokers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            name TEXT, username TEXT, phone TEXT, address TEXT,
            active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0,
            timestamp TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracking_sessions (
            tracking_code TEXT PRIMARY KEY,
            request_type TEXT NOT NULL,
            driver_user_id INTEGER NOT NULL,
            customer_user_id INTEGER NOT NULL,
            origin TEXT DEFAULT '',
            destination TEXT DEFAULT '',
            cargo_description TEXT DEFAULT '',
            truck_description TEXT DEFAULT '',
            status TEXT DEFAULT 'ready',
            latitude REAL,
            longitude REAL,
            location_updated_at TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    try:
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN customer_name TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN customer_phone TEXT DEFAULT ''")
        cursor.execute("ALTER TABLE tracking_sessions ADD COLUMN driver_phone TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

def db_save_user(user_id, name, username, owner_name=None, owner_phone=None):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, name, username, owner_name, owner_phone)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name, username = excluded.username,
            owner_name = COALESCE(excluded.owner_name, users.owner_name),
            owner_phone = COALESCE(excluded.owner_phone, users.owner_phone)
    """, (user_id, name, username, owner_name, owner_phone))
    conn.commit()
    conn.close()

def db_save_cargo(cargo):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cargo_posts (
            user_id, from_location, to_location, cargo_type, vehicle,
            size, size_name, weight, date_et, date_gc, phone, price,
            price_display, price_status, active, timestamp, expiry_date, poster_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cargo["user_id"], cargo["from"], cargo["to"], cargo["type"],
        cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"],
        cargo.get("date_et", ""), cargo.get("date_gc", ""), cargo["phone"],
        cargo["price"], cargo["price_display"], cargo["price_status"],
        1, cargo["timestamp"], cargo.get("expiry_date", ""),
        cargo.get("poster_type", "customer")
    ))
    conn.commit()
    conn.close()

def db_save_truck(truck):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO truck_posts (
            user_id, truck_type, plate, capacity, route, address,
            driver, phone, active, timestamp, expiry_date, poster_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        truck["user_id"], truck["type"], truck["plate"], truck["capacity"],
        truck["route"], truck["address"], truck["driver"], truck["phone"],
        1, truck["timestamp"], truck.get("expiry_date", ""),
        truck.get("poster_type", "customer")
    ))
    conn.commit()
    conn.close()

def db_save_broker(broker):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO brokers (
            user_id, name, username, phone, address, active, approved, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        broker["user_id"], broker["name"], broker["username"],
        broker["phone"], broker["address"], 1, 0,
        broker["timestamp"]
    ))
    conn.commit()
    conn.close()

def db_create_tracking_session(req, request_index):
    if req.get("type") == "cargo":
        cargo_index = req.get("cargo_index")
        if not isinstance(cargo_index, int) or not (0 <= cargo_index < len(cargo_posts)):
            raise ValueError("Cargo record not available for tracking")
        cargo = cargo_posts[cargo_index]
        driver_user_id = int(req["requester_id"])
        customer_user_id = int(req["cargo_owner_id"])
        origin = str(cargo.get("from", ""))
        destination = str(cargo.get("to", ""))
        cargo_description = str(cargo.get("type", ""))
        truck_description = str(req.get("requester_truck_type", ""))
    else:
        truck_index = req.get("truck_index")
        if not isinstance(truck_index, int) or not (0 <= truck_index < len(truck_posts)):
            raise ValueError("Truck record not available for tracking")
        truck = truck_posts[truck_index]
        driver_user_id = int(req["truck_owner_id"])
        customer_user_id = int(req["requester_id"])
        origin = str(req.get("requester_cargo_info", ""))
        destination = str(truck.get("route", ""))
        cargo_description = str(req.get("requester_cargo_info", ""))
        truck_description = str(truck.get("type", ""))

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        for _ in range(12):
            code = "TC" + secrets.token_hex(4).upper()
            exists = cursor.execute(
                "SELECT 1 FROM tracking_sessions WHERE tracking_code = ?", (code,)
            ).fetchone()
            if exists:
                continue
            cursor.execute("""
                INSERT INTO tracking_sessions (
                    tracking_code, request_type, driver_user_id, customer_user_id,
                    origin, destination, cargo_description, truck_description,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
            """, (
                code, str(req.get("type", "cargo")), driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                datetime.now().astimezone().isoformat(timespec="seconds")
            ))
            conn.commit()
            return code
        raise RuntimeError("Unable to generate a unique tracking code")
    finally:
        conn.close()

def db_get_tracking(tracking_code):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM tracking_sessions WHERE tracking_code = ?",
            (tracking_code.upper().strip(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_finish_tracking(tracking_code):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("""
            UPDATE tracking_sessions SET status = 'delivered', completed_at = ?
            WHERE tracking_code = ?
        """, (
            datetime.now().astimezone().isoformat(timespec="seconds"),
            tracking_code.upper().strip()
        ))
        conn.commit()
    finally:
        conn.close()

# ==================================================
# MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
brokers = []
connection_requests = []
pending_deletes = []

# ==================================================
# STATES
# ==================================================

(
    CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE,
    CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE,
) = range(10)

(
    TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE,
    TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE,
) = range(10, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)
(
    CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE,
) = range(19, 22)

(
    BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS,
) = range(22, 25)

TRUCK_ALL_INFO = 29
FIND_TRUCK_QUERY = 30

(
    TRACK_SEND_NAME, TRACK_SEND_PHONE, TRACK_SEND_LOCATION,
    STOP_TRACK_NAME, STOP_TRACK_PHONE, STOP_TRACK_CONFIRM
) = range(31, 37)

# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["📋 የጭነት ደላሎች መዝገብ"],
            ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
            ["📞 Support", "ℹ️ About"],
        ]
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
            ["🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ"],
            ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
            ["📞 Support", "ℹ️ About"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def size_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")],
        [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")],
        [InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25")],
    ])

def vehicle_keyboard(prefix="vehicle"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚛 ተሳቢ", callback_data=f"{prefix}_ተሳቢ")],
        [InlineKeyboardButton("🚛 ካሶኒ", callback_data=f"{prefix}_ካሶኒ")],
        [InlineKeyboardButton("🚛 ኦባማ", callback_data=f"{prefix}_ኦባማ")],
        [InlineKeyboardButton("🚛 Isuzu", callback_data=f"{prefix}_isuzu")],
        [InlineKeyboardButton("✍️ ሌላ", callback_data=f"{prefix}_other")],
    ])

def calendar_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (EC)", callback_data="cal_et")],
    ])

def route_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 በማንኛውም ቦታ", callback_data="route_anywhere")],
        [InlineKeyboardButton("📍 በርሶ ምርጫ", callback_data="route_custom")],
    ])
# ==================================================
# HELPERS
# ==================================================

def normalize(text):
    if text is None:
        return ""
    return str(text).strip().lower().replace(" ", "")

def vehicle_match(truck_type, requested_vehicle):
    if not requested_vehicle:
        return True
    return normalize(truck_type) == normalize(requested_vehicle)

def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"):
            return True
    return False

def get_poster_label(user_id):
    if is_broker(user_id):
        return "🏷️ Broker"
    return "🏷️ Customer"

def payment_methods_text():
    return (
        "💳 የአገልግሎት ክፍያ መረጃ\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000031098231\n👤 Solomon Sisay\n\n"
        "📱 Telebirr\n📱 0918132914\n👤 Solomon Sisay\n\n"
        "💰 CBE Birr\n📱 0918132914\n👤 Solomon Sisay\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000139288697\n👤 Melak Gebeyhu\n\n"
        "📱 Telebirr\n📱 0918161179\n👤 Melak Gebeyhu\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 ክፍያ ካደረጉ በኋላ ደረሰኙን ወይም "
        "ስክሪንሾቱን ወደ @tanapage ይላኩ።\n\n"
        "🙏 እኛን ስለመረጡን እናመሰግናለን!"
    )

def other_party_id(req):
    if req.get("type") == "truck":
        return req["truck_owner_id"]
    return req["cargo_owner_id"]

def validate_phone(phone):
    phone = phone.strip()
    if not (phone.startswith("09") or phone.startswith("07")):
        return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10:
        return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    if not phone.isdigit():
        return False, "ስልክ ቁጥር ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_plate(plate):
    plate = plate.strip()
    if not plate.isdigit():
        return False, "ታርጋ ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_weight(weight):
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]):
        return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

def is_super_admin(user_id):
    return bool(SUPER_ADMIN_USER_ID) and str(user_id) == str(SUPER_ADMIN_USER_ID)

def is_admin(user_id):
    if is_super_admin(user_id):
        return True
    return bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID)

def get_admin_ids():
    ids = []
    if SUPER_ADMIN_USER_ID:
        ids.append(int(SUPER_ADMIN_USER_ID))
    if ADMIN_USER_ID and str(ADMIN_USER_ID) != str(SUPER_ADMIN_USER_ID):
        ids.append(int(ADMIN_USER_ID))
    return ids

async def auto_delete_messages(context):
    now = datetime.now()
    remaining = []
    for chat_id, msg_id, delete_at in pending_deletes:
        if now >= delete_at:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        else:
            remaining.append((chat_id, msg_id, delete_at))
    pending_deletes.clear()
    pending_deletes.extend(remaining)

def is_duplicate_broker_post(new_item, is_truck, broker_user_id):
    if is_truck:
        for t in truck_posts:
            if (t.get("active") and t.get("poster_type") == "broker"
                and t.get("plate") == new_item.get("plate")
                and t.get("user_id") != broker_user_id):
                return True
    else:
        for c in cargo_posts:
            if (c.get("active") and c.get("poster_type") == "broker"
                and normalize(c.get("from")) == normalize(new_item.get("from"))
                and normalize(c.get("to")) == normalize(new_item.get("to"))
                and c.get("user_id") != broker_user_id):
                return True
    return False

def is_already_selected(item_type, item_index):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("status") in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
                return True
    return False

def is_selected_by_other(item_type, item_index, requester_id):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("requester_id") != requester_id
                and req.get("status") in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
                return True
    return False

# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    context.user_data.clear()
    context.user_data["cargo"] = {}
    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር"
    )
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
    await update.message.reply_text(
        "4️⃣ ለዚህ ጭነት የሚፈልጉትን የመኪና አይነት ይምረጡ።",
        reply_markup=vehicle_keyboard("cargo_vehicle")
    )
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
    await query.edit_message_text(
        f"🚛 የተመረጠው መኪና፦ {value}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard()
    )
    return CARGO_SIZE

async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"):
        return
    cargo["vehicle"] = update.message.text.strip()
    cargo["vehicle_waiting"] = False
    await update.message.reply_text(
        f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard()
    )
    return CARGO_SIZE

async def cargo_size(update, context):
    query = update.callback_query
    await query.answer()
    sizes = {
        "size_100": (100, "ሙሉ ጭነት"),
        "size_50": (50, "ግማሽ ጭነት"),
        "size_25": (25, "እሩብ ጭነት"),
    }
    if query.data not in sizes:
        return CARGO_SIZE
    size, size_name = sizes[query.data]
    context.user_data["cargo"]["size"] = size
    context.user_data["cargo"]["size_name"] = size_name
    await query.edit_message_text(
        f"📦 የተመረጠው፦ {size_name} ({size}%)\n\n"
        "6️⃣ የጭነቱን ክብደት ይግለጹ።\n"
        "በቶን፣ በቢያጆ፣ በኩንታል\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
    )
    return CARGO_WEIGHT

async def cargo_weight(update, context):
    weight = update.message.text.strip()
    is_valid, error = validate_weight(weight)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል")
        return CARGO_WEIGHT
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text(
        "7️⃣ **እባክዎ የሚጫንበትን ቀን ካላንደር ይምረጡ።**\n\n"
        "የሚጠቀሙበትን ካላንደር ይምረጡ፦",
        reply_markup=calendar_keyboard()
    )
    return CARGO_CALENDAR

async def cargo_calendar(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cal_et":
        context.user_data["cargo"]["calendar"] = "ET"
        await query.edit_message_text(
            "🇪🇹 **የኢትዮጵያ ካላንደር ተመርጧል።**\n\n"
            "እባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 26/1/2019 ወይም 26-1-2019 ወይም 26,1,2019"
        )
    elif query.data == "cal_gc":
        context.user_data["cargo"]["calendar"] = "GC"
        await query.edit_message_text(
            "🌍 **የአውሮፓ ካላንደር ተመርጧል።**\n\n"
            "እባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 22/2/2026 ወይም 22-2-2026 ወይም 22,2,2026"
        )
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
        await update.message.reply_text(
            "❌ ያለፈ ቀን አይቀበልም።\n\n"
            "ዛሬን ወይም ወደፊት ያለ ቀን በኢትዮጵያ ካላንደር (ቀን/ወር/ዓ.ም.) ያስገቡ።\n"
            "ምሳሌ፦ 11/02/2019"
        )
        return CARGO_DATE

    max_date = today + timedelta(days=30)
    if given_date > max_date:
        await update.message.reply_text(
            "❌ ከ30 ቀናት በላይ የሆነ ቀን አይቀበልም።\n\n"
            "እባክዎ ዛሬን ወይም በሚቀጥሉት 30 ቀናት ውስጥ ያለ ቀን ያስገቡ።"
        )
        return CARGO_DATE

    cargo = context.user_data.setdefault("cargo", {})
    cargo["date"] = date_text
    cargo["date_et"] = et_date
    cargo["date_gc"] = gc_date
    cargo["expiry_date"] = (gc_obj + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()

    await update.message.reply_text(
        "📅 የተመረጠው ቀን፦\n"
        f"🇪🇹 ET: {et_date}\n"
        f"🌍 GC: {gc_date}\n\n"
        "8️⃣ ስልክ ቁጥር ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CARGO_PHONE
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n"
        "በብር ብቻ ወይም **በስምምነት / በድርድር** ብለው ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት"
    )
    return CARGO_PRICE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
    if raw in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price = None
        price_status = "agreement"
        price_display = raw if raw in ["በስምምነት", "በድርድር"] else "በስምምነት"
    else:
        try:
            price = float(raw.replace(",", "").replace("ብር", "").strip())
            price_status = "fixed"
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text(
                "❌ እባክዎ ዋጋውን በቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\n"
                "ምሳሌ፦ 55000 ወይም በስምምነት"
            )
            return CARGO_PRICE
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return CARGO_PRICE

    cargo = context.user_data["cargo"]
    cargo["price"] = price
    cargo["price_status"] = price_status
    cargo["price_display"] = price_display
    cargo["user_id"] = update.effective_user.id
    cargo["active"] = True
    cargo["timestamp"] = datetime.now().isoformat()
    cargo["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"

    if cargo["poster_type"] == "broker":
        if is_duplicate_broker_post(cargo, is_truck=False, broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን ጭነት ሌላ ደላላ (broker) ለጥፎታል።**\n\n"
                "እባክዎ ሌላ ጭነት ካለዎት ይለጥፉ።"
            )
            return CARGO_PRICE

    cargo_posts.append(cargo.copy())
    try:
        db_save_cargo(cargo)
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n{poster_label}\n"
        f"📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
        f"💰 የመጫኛ ዋጋ፦ {price_display}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉@tanapage join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    for truck in truck_posts:
        if not truck.get("active", True): continue
        if vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=truck["user_id"],
                    text=(f"🔔 ተስማሚ አዲስ ጭነት ተገኝቷል!\n\n📍 {cargo['from']} ➡️ {cargo['to']}\n"
                          f"📦 {cargo['type']}\n🚛 የሚፈለገው፦ {cargo['vehicle']}\n📊 {cargo['size_name']}\n"
                          f"⚖️ {cargo['weight']}\n📅 {cargo.get('date_et', 'N/A')} (ET)\n💰 {price_display}\n\n"
                          "🔎 ለማየት የጭነት መፈለግን ይጫኑ።")
                )
            except Exception: pass

    return ConversationHandler.END

# ==================================================
# TRUCK REGISTRATION (SIMPLIFIED)
# ==================================================

async def truck_start(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "🚛 **የጭነት መኪና ማስመዝገብ (ቀላል መንገድ)**\n\n"
        "እባክዎ የሚከተሉትን 6 መረጃዎች በቅደም ተከተል በአንድ መልዕክት ይላኩልን (እያንዳንዱን በአዲስ መስመር)፦\n\n"
        "1️⃣ የመኪና አይነት (ምሳሌ፦ Isuzu)\n"
        "2️⃣ ታርጋ ቁጥር (ቁጥር ብቻ - ምሳሌ፦ 12345)\n"
        "3️⃣ የመጫን አቅም (ምሳሌ፦ 30 ቶን)\n"
        "4️⃣ የሚሰሩበት መንገድ እና አድራሻ (ምሳሌ፦ ባህርዳር - አዲስ አበባ)\n"
        "5️⃣ የሹፌር ስም (ምሳሌ፦ አበበ)\n"
        "6️⃣ ስልክ ቁጥር (ምሳሌ፦ 0912345678)\n\n"
        "👇 አሁን ይጻፉና ይላኩ።"
    )
    return TRUCK_ALL_INFO

async def truck_all_info(update, context):
    text = update.message.text.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    if len(lines) < 6:
        await update.message.reply_text("❌ እባክዎ ሁሉንም 6 መረጃዎች በቅደም ተከተል ይላኩ።")
        return TRUCK_ALL_INFO

    truck_type, plate, capacity, route_address, driver, phone = lines[:6]

    is_valid_plate, plate_err = validate_plate(plate)
    if not is_valid_plate:
        await update.message.reply_text(f"❌ ታርጋ ስህተት፦ {plate_err}\nእባክዎ በድጋሚ ይላኩ።")
        return TRUCK_ALL_INFO

    is_valid_cap, cap_err = validate_weight(capacity)
    if not is_valid_cap:
        await update.message.reply_text(f"❌ የመጫን አቅም ስህተት፦ {cap_err}\nእባክዎ በድጋሚ ይላኩ።")
        return TRUCK_ALL_INFO

    is_valid_phone, phone_err = validate_phone(phone)
    if not is_valid_phone:
        await update.message.reply_text(f"❌ ስልክ ስህተት፦ {phone_err}\nእባክዎ በድጋሚ ይላኩ።")
        return TRUCK_ALL_INFO

    truck = {
        "type": truck_type, "plate": plate, "capacity": capacity,
        "route": route_address, "address": route_address,
        "driver": driver, "phone": phone,
        "user_id": update.effective_user.id, "active": True,
        "timestamp": datetime.now().isoformat(),
        "poster_type": "broker" if is_broker(update.effective_user.id) else "customer",
        "expiry_date": (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    }

    if truck["poster_type"] == "broker":
        if is_duplicate_broker_post(truck, is_truck=True, broker_user_id=update.effective_user.id):
            await update.message.reply_text("❌ **ይቅርታ ይህንን መኪና ሌላ ደላላ አስመዝግቦታል።**", reply_markup=main_menu(update.effective_user.id))
            return ConversationHandler.END

    truck_posts.append(truck)
    try: db_save_truck(truck)
    except Exception as e: print(f"DB SAVE TRUCK ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n{poster_label}\n"
        f"🚛 አይነት፦ {truck['type']}\n🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ/አድራሻ፦ {truck['route']}\n👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛችን ጭነት የሚያስጭንዎት ደንበኛ ጋር ስምምነት ላይ ደርሰዉ ጭነት ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነቱ ጭነቱን መከታተል እንዲችል የስልክዎን location on አድርገው የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ በመንካት አዲስ ጉዞ ክፈት ሹፌር የምትለዋን ነክተው ያስመዝግቡ። የጉዞ መዳረሻ ላይ ሲደርሱ location ያጥፉ እናመሰግናለን የሚል notfication ይጨመር። ለበለጠ መረጃ👉 @tanapage ነክተው join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    for cargo in cargo_posts:
        if not cargo.get("active", True): continue
        if vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=cargo["user_id"],
                    text=(f"🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n🚛 አይነት፦ {truck['type']}\n"
                          f"⚖️ አቅም፦ {truck['capacity']}\n🛣️ መንገድ፦ {truck['route']}\n\n"
                          "🚛 የጭነት መኪና መፈለግን ይጫኑ።")
                )
            except Exception: pass

    return ConversationHandler.END
# ==================================================
# BROKER REGISTRATION
# ==================================================

async def broker_start(update, context):
    context.user_data.clear()
    context.user_data["broker"] = {}
    await update.message.reply_text("🆕 **የጭነት ደላላ ምዝገባ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return BROKER_NAME

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ ስልክ ቁጥርዎን ይጻፉ።\nበ09 ወይም በ07 የሚጀመር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678")
    return BROKER_PHONE

async def broker_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return BROKER_PHONE
    context.user_data["broker"]["phone"] = phone
    await update.message.reply_text("3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር")
    return BROKER_ADDRESS

async def broker_address(update, context):
    address = update.message.text.strip()
    user = update.effective_user
    context.user_data["broker"]["address"] = address

    broker = {
        "user_id": user.id, "name": context.user_data["broker"]["name"],
        "username": user.username or "", "phone": context.user_data["broker"]["phone"],
        "address": address, "timestamp": datetime.now().isoformat(),
    }

    brokers.append(broker)
    try: db_save_broker(broker)
    except Exception as e: print(f"DB SAVE BROKER ERROR: {e}")

    await update.message.reply_text(
        "✅ **ስለተመዘገቡ እናመሰግናለን!**\n\nየጭነት ደላላ መሆንዎን አረጋግጠን አብረን በጋራ እንሰራለን።\n\n📌 ማረጋገጫ ሲደረግ ይነገርዎታል።",
        reply_markup=main_menu(user.id)
    )
    await send_admin_broker_notification(context, broker)
    return ConversationHandler.END

async def send_admin_broker_notification(context, broker):
    notification_text = (
        "🆕 **TANA CARGO — አዲስ የጭነት ደላላ ተመዝግቧል!**\n\n"
        f"👤 **ስም:** {broker['name']}\n🔗 **Username:** @{broker['username'] or 'የለም'}\n"
        f"🆔 **Telegram ID:** {broker['user_id']}\n📞 **ስልክ:** {broker['phone']}\n"
        f"📍 **አድራሻ:** {broker['address']}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n📌 **የጭነት ደላላ መሆኑን ያረጋግጡ።**\n✅ Right = አረጋግጥ\n❌ X = ውድቅ አድርግ"
    )
    broker_idx = len(brokers) - 1
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Right", callback_data=f"brokerright_{broker_idx}"), InlineKeyboardButton("❌ X አጥፋ", callback_data=f"brokerxdelete_{broker_idx}")]])

    for admin_id in get_admin_ids():
        try: await context.bot.send_message(chat_id=admin_id, text=notification_text, reply_markup=kb)
        except Exception: pass

async def broker_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try: index = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    if index < 0 or index >= len(brokers): return

    broker = brokers[index]
    if broker.get("approved"):
        await query.answer("⚠️ ይህ ቀድሞ ጸድቋል!", show_alert=True)
        return

    broker["approved"] = True
    broker["active"] = True
    await query.edit_message_text("✅ **የጭነት ደላላው ጸድቋል!**\n\nለደላላው ማሳወቂያ ተልኳል።")

    if broker.get("user_id"):
        try:
            await context.bot.send_message(chat_id=broker["user_id"], text="🎉 **እንኳን ደስ አለዎት!** 🎉\n\n**የጣና ጭነት አባል ሆነዋል!**\n\n📦 **ጭነት ካለዎት** ወይም\n🚛 **የጭነት መኪና ካለዎት**\n\n✅ **በነፃ ይለጥፉ!**\n\n🔒 መረጃዎ **100% የተጠበቀ** ነው።\n\n💰 ደንበኛ ፈልገን ስናገናኛቸው ብቻ ከሚያገኙት ኮሚሽን ላይ **5% ብቻ** ለጣና ጭነት ይከፍላሉ።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን!")
        except Exception: pass

async def broker_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try: index = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    if index < 0 or index >= len(brokers): return

    broker = brokers[index]
    broker["active"] = False
    broker["approved"] = False
    await query.edit_message_text("❌ **ውድቅ ተደርጓል!**\n\nይህ የጭነት ደላላ በ Super Admin ውድቅ ተደርጓል።")
    if broker.get("user_id"):
        try: await context.bot.send_message(chat_id=broker["user_id"], text="❌ **የጭነት ደላላ ምዝገባ ውድቅ ተደርጓል።**\n\nገፃችንን ስለጎበኙ እናመሰግናለን! 🙏")
        except Exception: pass

async def show_broker_registry(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return
    if not brokers:
        await update.message.reply_text("📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።")
        return
    for i, broker in enumerate(brokers):
        status = "⏳ በመጠበቅ" if not broker.get("approved") else ("✅ ጸድቋል" if broker.get("active") else "❌ ውድቅ")
        text = (f"📋 **የጭነት ደላላ #{i + 1}**\n\n👤 ስም: {broker.get('name', 'N/A')}\n🔗 Username: @{broker.get('username') or 'የለም'}\n"
                f"🆔 Telegram ID: {broker.get('user_id', 'N/A')}\n📞 ስልክ: {broker.get('phone', 'N/A')}\n"
                f"📍 አድራሻ: {broker.get('address', 'N/A')}\n📌 ሁኔታ: {status}\n")
        buttons = []
        if is_super_admin(user_id):
            buttons.append([InlineKeyboardButton(f"🗑️ #{i + 1} አጥፋ (Super)", callback_data=f"admindeletebroker_{i}")])
        if buttons: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else: await update.message.reply_text(text)

async def admin_delete_broker(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try: index = int(query.data.split("_")[-1])
    except ValueError: return
    if index < 0 or index >= len(brokers): return
    brokers[index]["active"] = False
    brokers[index]["approved"] = False
    await query.edit_message_text("✅ **የጭነት ደላላው ተሰርዟል።**")

# ==================================================
# FIND CARGO / TRUCK
# ==================================================

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    is_admin_user = is_admin(update.effective_user.id)

    if not active_cargos:
        await update.message.reply_text("📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።", reply_markup=main_menu(update.effective_user.id))
        return

    for i, cargo in enumerate(active_cargos):
        cargo_id = f"{i + 1:03d}"
        real_index = cargo_posts.index(cargo)
        is_selected = is_already_selected("cargo", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user:
            admin_info = (f"👤 ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n"
                          f"🔗 @{users.get(cargo['user_id'], {}).get('username', '') or 'የለም'}\n"
                          f"🆔 ID: {cargo['user_id']}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n")
        
        msg = (f"{status_mark}📦 **ጭነት {cargo_id}**\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n"
               f"📦 አይነት፦ {cargo['type']}\n🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
               f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n⚖️ ክብደት፦ {cargo['weight']}\n"
               f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n💰 ዋጋ፦ {cargo.get('price_display', 'N/A')}\n{admin_info}"
               "━━━━━━━━━━━━━━━━━━━━")
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ተመርጧል", callback_data="already_selected")]]) if is_selected else \
             InlineKeyboardMarkup([[InlineKeyboardButton("ይህን ጭነት ምረጥ 👈", callback_data=f"connect_{real_index}")]])
        await update.message.reply_text(msg, reply_markup=kb)

async def find_truck(update, context):
    await update.message.reply_text("🔎 **የጭነት መኪና መፈለግ**\n\nእባክዎ የሚፈልጉትን የመኪና አይነት ወይም የመንገድ አቅጣጫ ይጻፉ።\nለምሳሌ፦ Isuzu ወይም ባህር ዳር\n\n👉 ሁሉንም መኪኖች ለማየት 'ሁሉም' ብለው ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return FIND_TRUCK_QUERY

async def find_truck_query(update, context):
    query = update.message.text.strip().lower()
    active_trucks = [t for t in truck_posts if t.get("active", True)]

    if query != "ሁሉም":
        filtered = []
        for t in active_trucks:
            route_text = t.get("route", "").lower()
            address_text = t.get("address", "").lower()
            if (query in t["type"].lower() or query in route_text or query in address_text):
                filtered.append(t)
        active_trucks = filtered

    if not active_trucks:
        await update.message.reply_text(f"🚛 '{query}' ጋር የሚዛመድ ንቁ የጭነት መኪና አልተገኘም።", reply_markup=main_menu(update.effective_user.id))
        return ConversationHandler.END

    await update.message.reply_text(f"🔎 **የተገኙ የጭነት መኪናዎች (ጠቅላላ {len(active_trucks)})**", reply_markup=main_menu(update.effective_user.id))
    is_admin_user = is_admin(update.effective_user.id)
    limit = 20 if is_admin_user else 10

    for i, truck in enumerate(active_trucks[:limit]):
        truck_id = f"{i + 1:03d}"
        real_index = truck_posts.index(truck)
        is_selected = is_already_selected("truck", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user:
            admin_info = (f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n"
                          f"📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n"
                          f"👨‍✈️ ሹፌር: {truck.get('driver', 'N/A')}\n")
        
        msg = (f"{status_mark}🚛 **የጭነት መኪና {truck_id}**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n"
               f"🛣️ መንገድ/አድራሻ፦ {truck.get('route', 'N/A')}\n{admin_info}━━━━━━━━━━━━━━━━━━━━")
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ተመርጧል", callback_data="already_selected")]]) if is_selected else \
             InlineKeyboardMarkup([[InlineKeyboardButton("ይህን መኪና ምረጥ 👈", callback_data=f"truckconnect_{real_index}")]])
        await update.message.reply_text(msg, reply_markup=kb)

    return ConversationHandler.END

async def already_selected_callback(update, context):
    await update.callback_query.answer("❌ ይቅርታ የመረጡት በሌላ ደንበኛችን በድርድር ላይ ነው።", show_alert=True)

# ==================================================
# CONNECTIONS
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(cargo_posts): return ConversationHandler.END
    cargo = cargo_posts[index]
    if not cargo.get("active", True): return ConversationHandler.END
    requester = update.effective_user
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    context.user_data["connect_info"] = {"type": "cargo", "item_index": index, "requester_id": requester.id, "requester_name": requester.full_name, "requester_username": requester.username or ""}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(truck_posts): return ConversationHandler.END
    truck = truck_posts[index]
    if not truck.get("active", True): return ConversationHandler.END
    requester = update.effective_user
    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    context.user_data["connect_info"] = {"type": "truck", "item_index": index, "requester_id": requester.id, "requester_name": requester.full_name, "requester_username": requester.username or ""}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info
    await update.message.reply_text("2️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678")
    return CONNECT_PHONE

async def connect_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CONNECT_PHONE
    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
    context.user_data["connect_info"] = info
    await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር")
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    info["requester_address"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info
    requester = update.effective_user

    if info.get("type") == "cargo":
        cargo = cargo_posts[info["item_index"]]
        request = {
            "type": "cargo", "cargo_index": info["item_index"], "cargo_owner_id": cargo["user_id"],
            "requester_id": requester.id, "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""), "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""), "status": "admin_confirming",
            "offers": [], "current_offer": None, "current_offer_by": None, "offer_version": 0, "final_price": None,
            "requester_confirmed": False, "other_confirmed": False, "requester_payment_submitted": False, "other_payment_submitted": False,
            "requester_payment_approved": False, "other_payment_approved": False, "requester_payment_rejected": False, "other_payment_rejected": False,
            "requester_payment_version": 0, "other_payment_version": 0, "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False, "created_at": datetime.now().isoformat()
        }
        msg_text = ("✅ **እናመሰግናለን!**\n\n📌 ምዝገባዎን ጨርሰዋል።\n⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
                    "ውድ ደንበኛችን ባለጭነት ጋር ተስማምተው ጭነተዎን ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነት እርሶ የጫኑለትን ጭነት መከታተል እንዲችል የስልክዎን location ና የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ ተጭነው አዲስ ጉዞ ክፈት (ሹፌር) የምትለዋን ተጭነው መረጃ ያጋሩ ጉዞ ሲጨርሱ location off ያድርጉ። ለበለጠ መረጃ👉 @tanapage በመንካት join ያድርጉ።\n\n🙏 እናመሰግናለን!")
    else:
        truck = truck_posts[info["item_index"]]
        request = {
            "type": "truck", "truck_index": info["item_index"], "truck_owner_id": truck["user_id"],
            "requester_id": requester.id, "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""), "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""), "status": "admin_confirming",
            "offers": [], "current_offer": None, "current_offer_by": None, "offer_version": 0, "final_price": None,
            "requester_confirmed": False, "other_confirmed": False, "requester_payment_submitted": False, "other_payment_submitted": False,
            "requester_payment_approved": False, "other_payment_approved": False, "requester_payment_rejected": False, "other_payment_rejected": False,
            "requester_payment_version": 0, "other_payment_version": 0, "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False, "created_at": datetime.now().isoformat()
        }
        msg_text = ("✅ **እናመሰግናለን!**\n\n📌 ምዝገባዎን ጨርሰዋል።\n⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
                    "ውድ ደንበኛችን ጭነተዎን በgoogle map መከታተል ከፈለጉ የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተል ኮድ ጠይቅ (ደንበኛ) የምትለዋን ተጭነው በመመሪያው መሰረት ይሙሉና ጭነተዎን ይከታተሉ። ለበለጠ መረጃ 👉@tanapage በመጫን join ያድርጉ።\n\n🙏 እናመሰግናለን!")

    connection_requests.append(request)
    req_index = len(connection_requests) - 1
    context.user_data.pop("connect_info", None)
    await update.message.reply_text(msg_text, reply_markup=main_menu(requester.id))
    await send_admin_notification(context, req_index)
    return ConversationHandler.END
# ==================================================
# SEND ADMIN NOTIFICATION
# ==================================================

async def send_admin_notification(context, req_index):
    req = connection_requests[req_index]
    if req.get("admin_notified"): return
    req["admin_notified"] = True

    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        notification_text = (f"🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n📌 Request ID: {req_index}\n\n"
                             f"📦 **የጭነት ዝርዝር:**\n   • መነሻ: {cargo['from']}\n   • መድረሻ: {cargo['to']}\n   • አይነት: {cargo['type']}\n   • ክብደት: {cargo['weight']}\n   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
                             f"👤 **የጭነት ባለቤት:**\n   • ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n   • Username: @{users.get(cargo['user_id'], {}).get('username', '') or 'የለም'}\n   • ስልክ: {cargo.get('phone', 'N/A')}\n\n"
                             f"👤 **ጠያቂ:**\n   • ስም: {req.get('requester_name', 'N/A')}\n   • ስልክ: {req.get('requester_phone', 'N/A')}\n   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
                             "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።")
    else:
        truck = truck_posts[req["truck_index"]]
        notification_text = (f"🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n📌 Request ID: {req_index}\n\n"
                             f"🚛 **የመኪና ዝርዝር:**\n   • አይነት: {truck['type']}\n   • አቅም: {truck['capacity']}\n   • ታርጋ: {truck.get('plate', 'N/A')}\n   • መንገድ: {truck.get('route', 'N/A')}\n\n"
                             f"👤 **የመኪና ባለቤት:**\n   • ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n   • ስልክ: {truck.get('phone', 'N/A')}\n\n"
                             f"👤 **ጠያቂ:**\n   • ስም: {req.get('requester_name', 'N/A')}\n   • ስልክ: {req.get('requester_phone', 'N/A')}\n\n"
                             "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።")

    for admin_id in get_admin_ids():
        try: await context.bot.send_message(chat_id=admin_id, text=notification_text)
        except Exception: pass

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id): return
    try: index = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    if index < 0 or index >= len(connection_requests): return
    req = connection_requests[index]
    if req.get("admin_right_confirmed"):
        await query.answer("⚠️ ይህ ቀድሞ ተረጋግጧል!", show_alert=True)
        return

    req["admin_right_confirmed"] = True
    req["status"] = "negotiating"
    if req.get("type") == "cargo" and 0 <= req.get("cargo_index", -1) < len(cargo_posts):
        cargo_posts[req["cargo_index"]]["active"] = False
    elif req.get("type") == "truck" and 0 <= req.get("truck_index", -1) < len(truck_posts):
        truck_posts[req["truck_index"]]["active"] = False

    await query.edit_message_text("✅ **Right ተረጋግጧል!**\n\n📌 ጭነቱ/መኪናው ከገበያ ተወግዷል።")

    seeker_id = req["requester_id"]
    owner_id = other_party_id(req)
    item_type = "የጭነት መኪና" if req.get("type") == "truck" else "የጭነት"
    try: await context.bot.send_message(chat_id=seeker_id, text=f"ውድ ደንበኛችን፣\n\nየፈለጉት {item_type} ባለቤት ምላሽ እንደሰጡ እናሳውቃለን።\n\n📌 እባክዎ ትንሽ ይጠብቁ።")
    except Exception: pass

    owner_text = (f"ውድ ደንበኛችን፣\n\nየለጠፉትን {item_type} የሚፈልግ ደንበኛ አግኝተናል!\n\nእባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n📞 {SUPPORT_PHONE}\n📞 {SUPPORT_PHONE_2}\n\n🙏 እናመሰግናለን!")
    try: await context.bot.send_message(chat_id=owner_id, text=owner_text)
    except Exception: pass

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try: index = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    if index < 0 or index >= len(connection_requests): return
    req = connection_requests[index]
    req["status"] = "deleted"
    if req.get("type") == "cargo" and 0 <= req.get("cargo_index", -1) < len(cargo_posts):
        cargo_posts[req["cargo_index"]]["active"] = True
    elif req.get("type") == "truck" and 0 <= req.get("truck_index", -1) < len(truck_posts):
        truck_posts[req["truck_index"]]["active"] = True
    await query.edit_message_text("❌ **ተሰርዟል!**\n\n📌 ጭነት/መኪናው ነፃ ሆኗል።")
    try: await context.bot.send_message(chat_id=req["requester_id"], text="❌ **ይቅርታ**\n\nየጠየቁት ጭነት/መኪና ውድቅ ሆኗል።")
    except Exception: pass

async def show_connection_requests(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።", reply_markup=main_menu(user_id))
        context.user_data["sending_admin_message"] = True
        return

    received = [(i, req) for i, req in enumerate(connection_requests) if req["status"] != "deleted"]
    if not received:
        await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።", reply_markup=main_menu(user_id))
        return

    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            text = (f"📥 **ጥያቄ #{i + 1}**\n🚛 **የመኪና ባለቤት:** {users.get(req['truck_owner_id'], {}).get('name', 'N/A')}\n"
                    f"   • ስልክ: {truck.get('phone', 'N/A')}\n   • አይነት: {truck['type']}\n   • ታርጋ: {truck.get('plate', 'N/A')}\n\n"
                    f"👤 **የጭነት ፈላጊ:** {req.get('requester_name', 'N/A')}\n   • ስልክ: {req.get('requester_phone', 'N/A')}\n   • ሁኔታ: {req['status']}")
        else:
            cargo = cargo_posts[req["cargo_index"]]
            text = (f"📥 **ጥያቄ #{i + 1}**\n📦 **የጭነት ባለቤት:** {users.get(req['cargo_owner_id'], {}).get('name', 'N/A')}\n"
                    f"   • ስልክ: {cargo.get('phone', 'N/A')}\n   • መነሻ: {cargo['from']}\n   • መድረሻ: {cargo['to']}\n\n"
                    f"👤 **የመኪና ፈላጊ:** {req.get('requester_name', 'N/A')}\n   • ስልክ: {req.get('requester_phone', 'N/A')}\n   • ሁኔታ: {req['status']}")

        buttons = [[InlineKeyboardButton("✅ Right", callback_data=f"adminright_{i}"), InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{i}")]]
        if is_super_admin(user_id): buttons.append([InlineKeyboardButton(f"🗑️ ሙሉ በሙሉ አጥፋ #{i + 1}", callback_data=f"admindeleteconnection_{i}")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def admin_delete_connection(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id): return
    index = int(query.data.split("_")[-1])
    if 0 <= index < len(connection_requests): connection_requests[index]["status"] = "deleted"
    await query.edit_message_text("✅ ተሰርዟል።")

# ==================================================
# TRACKING FLOW (LIVE TRACCAR INTEGRATION)
# ==================================================

async def tracking_menu(update, context):
    context.user_data.pop("track_send", None)
    context.user_data.pop("stop_track", None)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ አድራሻ መላክ (ሹፌር)", callback_data="tracking_send_loc")],
        [InlineKeyboardButton("📦 የመጨረሻ አድራሻዬን ተመልከት (ደንበኛ)", callback_data="tracking_get_loc")],
        [InlineKeyboardButton("🛑 ክትትል ማስቆም (ሹፌር)", callback_data="tracking_stop")],
    ])
    await update.effective_message.reply_text(
        "📍 **TANA CARGO — የጭነት ክትትል (LIVE)**\n\n"
        "🚛 ሹፌር፦ አዲስ አድራሻ መላክ ወይም ክትትል ማስቆም ይችላሉ።\n"
        "📦 ደንበኛ፦ የመጨረሻ አድራሻዎን ይመልከቱ (ማስፈንጠሪያ አለው)።",
        reply_markup=keyboard
    )

async def tracking_send_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_send"] = {}
    await query.message.reply_text("1️⃣ አድራሻ መላክ የፈለጉለትን ሰው **ሙሉ ስም** ይጻፉ።")
    return TRACK_SEND_NAME

async def tracking_send_name(update, context):
    context.user_data["track_send"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሰውየውን **ቴሌግራም የሚጠቀሙበትን ስልክ ቁጥር** ያስገቡ።\nምሳሌ፦ 0912345678")
    return TRACK_SEND_PHONE

async def tracking_send_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return TRACK_SEND_PHONE
    context.user_data["track_send"]["phone"] = phone
    await update.message.reply_text("3️⃣ **የአሁኑን ቦታዎን** ይላኩ።\n\nከታች ያለውን ቁልፍ ተጭነው GPS ይላኩ።", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)], [KeyboardButton("❌ ሰርዝ")]], resize_keyboard=True, one_time_keyboard=True))
    return TRACK_SEND_LOCATION

async def tracking_send_location(update, context):
    if not update.message.location:
        await update.message.reply_text("❌ እባክዎ ቦታውን በGPS ይላኩ።", reply_markup=ReplyKeyboardRemove())
        return TRACK_SEND_LOCATION

    loc = update.message.location
    req = context.user_data.get("track_send", {})
    customer_name, customer_phone = req.get("name"), req.get("phone")
    driver_id, driver_name = update.effective_user.id, update.effective_user.full_name
    map_url = f"https://maps.google.com/?q={loc.latitude},{loc.longitude}"
    
    customer_user_id = None
    for uid, udata in users.items():
        if udata.get("phone") == customer_phone or udata.get("owner", {}).get("phone") == customer_phone:
            customer_user_id = uid
            break
            
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        code = "TC" + secrets.token_hex(4).upper()
        cursor.execute("""
            INSERT INTO tracking_sessions (
                tracking_code, request_type, driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                status, latitude, longitude, location_updated_at, created_at,
                customer_name, customer_phone
            ) VALUES (?, 'cargo', ?, ?, '', '', '', '', 'in_transit', ?, ?, ?, ?, ?, ?)
        """, (code, driver_id, customer_user_id or 0, loc.latitude, loc.longitude, datetime.now().astimezone().isoformat(), datetime.now().astimezone().isoformat(), customer_name, customer_phone))
        conn.commit()
    finally:
        conn.close()

    if customer_user_id:
        try:
            await context.bot.send_message(
                chat_id=customer_user_id,
                text=f"📍 **TANA CARGO — አዲስ አድራሻ**\n\nሹፌሩ ({driver_name}) የመኪናውን ቦታ ልኮልዎታል።\n\n🗺️ [በካርታ ላይ ለማየት እዚህ ይጫኑ]({map_url})\n\n💡 አድራሻውን በቦቱ ውስጥ ለማየት '📍 የጭነት ክትትል' -> '📦 የመጨረሻ አድራሻዬን ተመልከት' የሚለውን ይጫኑ።"
            )
            success_msg = f"✅ አድራሻው በቴሌግራም አካውንቱ ለ **{customer_name}** ({customer_phone}) ተልኳል።\n\n⚠️ እባክዎ የላኩለት ሰው on line ካልሆነ ደውለው ይንገሩት።"
        except Exception:
            success_msg = f"⚠️ ለ **{customer_name}** ({customer_phone}) መልዕክት መላክ አልተቻለም። እባክዎ ደውለው ያሳውቁት።"
    else:
        success_msg = f"✅ አድራሻው ተመዝግቧል!\n\n⚠️ ግን የላኩለት ሰው ({customer_name} - {customer_phone}) በቦቱ ላይ አልተመዘገበም።\nእባክዎ ደውለው ይንገሩት ወይም ይህንን ሊንክ ይላኩለት፦\n{map_url}"

    await update.message.reply_text(success_msg, reply_markup=ReplyKeyboardRemove())
    context.user_data.pop("track_send", None)
    return ConversationHandler.END

# --- የTraccar Live ማሳያ (Refresh ያለው) ---
async def tracking_get_loc_callback(update, context):
    query = update.callback_query
    await query.answer()
    customer_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("""
            SELECT * FROM tracking_sessions 
            WHERE customer_user_id = ? AND status = 'in_transit'
            ORDER BY created_at DESC LIMIT 1
        """, (customer_id,)).fetchone()
    finally:
        conn.close()

    if row:
        record = dict(row)
        code = record["tracking_code"]
        
        # Traccar API ከሆነ የቅርብ ጊዜውን ቦታ መሳብ
        traccar_loc = get_traccar_location(TRACCAR_DEVICE_ID)
        
        if traccar_loc and traccar_loc.get("latitude"):
            lat = float(traccar_loc["latitude"])
            lon = float(traccar_loc["longitude"])
            address = traccar_loc.get("address")
            time = traccar_loc.get("timestamp")
        elif record.get("latitude") is not None:
            lat = float(record["latitude"])
            lon = float(record["longitude"])
            address = "የተላከ የመጀመሪያ ቦታ"
            time = record.get("location_updated_at")[:16] if record.get("location_updated_at") else "N/A"
        else:
            await query.message.reply_text("❌ አሁንም አዲስ የGoogle Map መረጃ አልተላከልም።\n\nእባክዎ አድራሻ እንዲልክልዎ የፈለጉትን ሰው ደውለው 'አድራሻህን ላክልኝ' ይበሉት።")
            return

        map_url = f"https://maps.google.com/?q={lat},{lon}"
        msg = (
            f"📦 **TANA CARGO — የቀጥታ አድራሻ (LIVE)**\n\n"
            f"🔑 ኮድ፦ `{code}`\n"
            f"👤 ሹፌር፦ {record.get('driver_description') or 'N/A'}\n"
            f"📍 **የቅርብ ጊዜ ቦታ፦** {address}\n"
            f"🕒 **የተላከበት ሰዓት፦** {time}\n\n"
            f"🗺️ [በGoogle Maps ላይ ለማየት እዚህ ይጫኑ]({map_url})"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 አድስ (Refresh Map)", callback_data="refresh_map")
        ]])
        await query.message.reply_text(msg, reply_markup=kb, disable_web_page_preview=True)
    else:
        await query.message.reply_text("❌ ይቅርታ! ምንም አይነት የGoogle Map መረጃ አልተላከልዎትም።\n\nእባክዎ አድራሻ እንዲልክልዎ የፈለጉትን ሰው ደውለው 'አድራሻህን ላክልኝ' ይበሉት።")

async def refresh_map_callback(update, context):
    # አድሳት ሲጫኑ እንደገና የቅርብ ጊዜውን ያመጣል
    await tracking_get_loc_callback(update, context)

async def tracking_stop_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["stop_track"] = {}
    await query.message.reply_text("1️⃣ ክትትል ማስቆም የፈለጉለትን ሰው **ሙሉ ስም** ይጻፉ።")
    return STOP_TRACK_NAME

async def tracking_stop_name(update, context):
    context.user_data["stop_track"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሰውየውን **ስልክ ቁጥር** ያስገቡ።\nምሳሌ፦ 0912345678")
    return STOP_TRACK_PHONE

async def tracking_stop_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return STOP_TRACK_PHONE
    context.user_data["stop_track"]["phone"] = phone
    
    name = context.user_data["stop_track"]["name"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Right (አረጋግጥ)", callback_data="stop_track_confirm")]])
    await update.message.reply_text(
        f"📝 **ማረጋገጫ**\n\n👤 ስም፦ {name}\n📞 ስልክ፦ {phone}\n\nክትትል ማቆምዎን ለማረጋገጥ ✅ Right ይጫኑ።",
        reply_markup=kb
    )
    return STOP_TRACK_CONFIRM

async def tracking_stop_confirm(update, context):
    query = update.callback_query
    await query.answer()
    
    req = context.user_data.get("stop_track", {})
    name, phone = req.get("name"), req.get("phone")
    driver_id = update.effective_user.id
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT customer_user_id FROM tracking_sessions 
            WHERE driver_user_id = ? AND customer_phone = ? AND status = 'in_transit'
            ORDER BY created_at DESC LIMIT 1
        """, (driver_id, phone))
        row = cursor.fetchone()
        
        if not row:
            await query.edit_message_text("❌ በዚህ ስልክ ቁጥር ላይ ንቁ የክትትል ጉዞ አልተገኘም።")
            return
            
        customer_user_id = row[0]
        
        cursor.execute("""
            UPDATE tracking_sessions 
            SET status = 'delivered', completed_at = ?
            WHERE driver_user_id = ? AND customer_phone = ? AND status = 'in_transit'
        """, (datetime.now().astimezone().isoformat(), driver_id, phone))
        conn.commit()
    finally:
        conn.close()

    await query.edit_message_text(f"✅ **ክትትል በማቋረጥ ተጠናቋል!**\n\nለ **{name}** ({phone}) የነበረው ክትትል ተዘግቷል።")

    if customer_user_id and customer_user_id != 0:
        try:
            await context.bot.send_message(chat_id=customer_user_id, text=f"🛑 **TANA CARGO**\n\nሹፌሩ ({name}) የጭነቱን ክትትል አቁሟል።\nጉዞዎ በደህና ይጠናቀቅ!")
        except Exception: pass

    context.user_data.pop("stop_track", None)
    return ConversationHandler.END

async def tracking_text_handler(update, context):
    if not update.effective_user or not update.message or not update.message.text: return
    text = update.message.text.strip()
    
    if text == "❌ ሰርዝ":
        if context.user_data.get("track_send"):
            context.user_data.pop("track_send", None)
            await update.message.reply_text("ሂደቱ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
            return
        if context.user_data.get("stop_track"):
            context.user_data.pop("stop_track", None)
            await update.message.reply_text("ሂደቱ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
            return
# ==================================================
# STALE STATE CLEANER & TEXT ROUTER
# ==================================================

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner", "connect_info", "sending_admin_message", "track_send", "stop_track"):
        if key not in keep:
            context.user_data.pop(key, None)

async def connect_fallback_router(update, context):
    info = context.user_data.get("connect_info")
    if not info: return
    text = update.message.text.strip().split("\n")[0].strip()
    if not text: return

    name, phone, address = (info.get("name") or "").strip(), (info.get("phone") or "").strip(), (info.get("requester_address") or "").strip()

    if not name:
        info["name"] = text; context.user_data["connect_info"] = info
        await update.message.reply_text("2️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።")
        return
    if not phone:
        is_valid, error = validate_phone(text)
        if not is_valid: await update.message.reply_text(f"❌ {error}"); return
        info["phone"] = text; context.user_data["connect_info"] = info
        await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።")
        return
    if not address:
        await connect_type(update, context); return

async def text_router(update, context):
    if update.effective_user is None: return
    user_id = update.effective_user.id

    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)
    if context.user_data.get("connect_info"):
        return await connect_fallback_router(update, context)
    if get_payment_index(user_id) is not None:
        return await receipt_message(update, context)
    if get_negotiation_index(user_id) is not None:
        return await submit_price(update, context)

# ==================================================
# HANDLE USER MESSAGE/PHOTO TO ADMINS
# ==================================================

async def handle_user_message(update, context):
    user_id, text, username, name = update.effective_user.id, update.message.text.strip(), update.effective_user.username or "የለም", update.effective_user.full_name
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(chat_id=admin_id, text=f"📨 **TANA CARGO — አዲስ መልዕክት**\n\n👤 ስም፦ {name}\n🔗 Username፦ @{username}\n🆔 ID፦ {user_id}\n\n💬 **መልዕክት፦**\n{text}")
        except Exception: pass
    await update.message.reply_text("✅ መልዕክትዎ ለ Admin/Super Admin ተልኳል።", reply_markup=main_menu(user_id))

async def handle_user_photo(update, context):
    user_id, username, name, caption = update.effective_user.id, update.effective_user.username or "የለም", update.effective_user.full_name, update.message.caption or ""
    file_id = update.message.photo[-1].file_id if update.message.photo else (update.message.document.file_id if update.message.document else None)
    file_type = "photo" if update.message.photo else "document"
    if not file_id: return

    for admin_id in get_admin_ids():
        try:
            text = f"📸 **TANA CARGO — አዲስ ፎቶ/ስክሪንሾት**\n\n👤 ስም፦ {name}\n🔗 Username፦ @{username}\n🆔 ID፦ {user_id}\n"
            if caption: text += f"\n💬 **ጽሑፍ፦**\n{caption}"
            if file_type == "photo": await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=text)
            else: await context.bot.send_document(chat_id=admin_id, document=file_id, caption=text)
        except Exception: pass
    await update.message.reply_text("✅ ፎቶ/ስክሪንሾትዎ ለ Admin/Super Admin ተልኳል።", reply_markup=main_menu(user_id))

async def _start_admin_message_mode(update, context):
    await update.message.reply_text("📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።", reply_markup=main_menu(update.effective_user.id))
    context.user_data["sending_admin_message"] = True

# ==================================================
# OWNER & PAYMENTS (NEGOTIATION & RECEIPTS)
# ==================================================

async def owner_start(update, context):
    context.user_data.clear(); context.user_data["owner"] = {}
    await update.message.reply_text("📦 የጭነት ባለቤት ምዝገባ\n\n1️⃣ ስምዎን ይጻፉ።")
    return OWNER_NAME

async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ ስልክ ቁጥርዎን ይጻፉ።")
    return OWNER_PHONE

async def owner_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid: await update.message.reply_text(f"❌ {error}"); return OWNER_PHONE

    owner = context.user_data["owner"]; owner["phone"] = phone; owner["user_id"] = update.effective_user.id
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})["owner"] = owner.copy()
    try: db_save_user(user.id, user.full_name, user.username or "", owner_name=owner["name"], owner_phone=owner["phone"])
    except Exception as e: print(f"DB ERR: {e}")

    await update.message.reply_text(f"✅
