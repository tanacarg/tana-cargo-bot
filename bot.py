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
# TRACCAR API FUNCTION
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

def db_get_driver_tracking(driver_user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT tracking_code, origin, destination, cargo_description, status,
                   location_updated_at
            FROM tracking_sessions
            WHERE driver_user_id = ? AND status IN ('ready', 'in_transit')
            ORDER BY created_at DESC
        """, (int(driver_user_id),)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def db_update_tracking_location(tracking_code, latitude, longitude):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("""
            UPDATE tracking_sessions
            SET latitude = ?, longitude = ?, status = 'in_transit',
                location_updated_at = ?
            WHERE tracking_code = ?
        """, (
            float(latitude), float(longitude),
            datetime.now().astimezone().isoformat(timespec="seconds"),
            tracking_code.upper().strip()
        ))
        conn.commit()
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
pending_track_requests = []

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

(
    TRACK_REQ_PHONE, TRACK_REQ_ROUTE, TRACK_REQ_LOCATION,
    CUST_TRACK_PHONE_ONLY
) = range(25, 29)

# 🔴 አዲስ ስቴቶች ለቀላል የመኪና ምዝገባ እና ፍለጋ
TRUCK_ALL_INFO = 29
FIND_TRUCK_QUERY = 30

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

def subscribe_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 TANA CARGO ቻናል ተቀላቀል",
            url=f"https://t.me/{CHANNEL_USERNAME}"
        )],
        [InlineKeyboardButton(
            "✅ Subscribe አድርጌያለሁ",
            callback_data="subscribed_check"
        )],
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

def schedule_delete(chat_id, message_id):
    delete_at = datetime.now() + timedelta(seconds=MESSAGE_AUTO_DELETE_SECONDS)
    pending_deletes.append((chat_id, message_id, delete_at))

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
# SUBSCRIBE GATE (BYPASSED)
# ==================================================

async def check_subscription(user_id, context):
    return True

async def subscribed_check_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    
    context.user_data["subscribed"] = True
    await query.answer("✅ አመሰግናለሁ! Subscribe ማድረግዎን አረጋግጠናል።", show_alert=True)
    try:
        await query.edit_message_text("✅ **አመሰግናለሁ!**\n\nSubscribe ማድረግዎን አረጋግጠናል።\n\n👉 አሁን የፈለጉትን ምናሌ በመንካት ይቀጥሉ!")
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=user_id,
        text="👇 አሁን የፈለጉትን ምናሌ ይንኩ፦",
        reply_markup=main_menu(user_id)
    )

# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    context.user_data.pop("truck", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
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
        if is_duplicate_broker_post(cargo, is_truck=False,
                                     broker_user_id=update.effective_user.id):
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

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
        f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
        f"💰 የመጫኛ ዋጋ፦ {price_display}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉@tanapage join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    for truck in truck_posts:
        if not truck.get("active", True):
            continue
        if vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=truck["user_id"],
                    text=(
                        "🔔 ተስማሚ አዲስ ጭነት ተገኝቷል!\n\n"
                        f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                        f"📦 {cargo['type']}\n"
                        f"🚛 የሚፈለገው፦ {cargo['vehicle']}\n"
                        f"📊 {cargo['size_name']}\n"
                        f"⚖️ {cargo['weight']}\n"
                        f"📅 {cargo.get('date_et', 'N/A')} (ET)\n"
                        f"💰 {price_display}\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

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
        await update.message.reply_text("❌ እባክዎ ሁሉንም 6 መረጃዎች በቅደም ተከተል ይላኩ። (የጎደለ መረጃ አለ።)")
        return TRUCK_ALL_INFO

    truck_type = lines[0]
    plate = lines[1]
    capacity = lines[2]
    route_address = lines[3]
    driver = lines[4]
    phone = lines[5]

    is_valid_plate, plate_err = validate_plate(plate)
    if not is_valid_plate:
        await update.message.reply_text(f"❌ ታርጋ ስህተት፦ {plate_err}\nእባክዎ በድጋሚ ይላኩ። (ቁጥር ብቻ ይሁን)")
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
        "type": truck_type,
        "plate": plate,
        "capacity": capacity,
        "route": route_address,
        "address": route_address,
        "driver": driver,
        "phone": phone,
        "user_id": update.effective_user.id,
        "active": True,
        "timestamp": datetime.now().isoformat(),
        "poster_type": "broker" if is_broker(update.effective_user.id) else "customer",
        "expiry_date": (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    }

    if truck["poster_type"] == "broker":
        if is_duplicate_broker_post(truck, is_truck=True, broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን መኪና ሌላ ደላላ (broker) አስመዝግቦታል።**\n\n"
                "እባክዎ ሌላ የጭነት መኪና ካለዎት ያስመዝግቡ።",
                reply_markup=main_menu(update.effective_user.id)
            )
            return ConversationHandler.END

    truck_posts.append(truck)
    try:
        db_save_truck(truck)
    except Exception as e:
        print(f"DB SAVE TRUCK ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"🛣️ መንገድ/አድራሻ፦ {truck['route']}\n"
        f"👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛችን ጭነት የሚያስጭንዎት ደንበኛ ጋር ስምምነት ላይ ደርሰዉ ጭነት ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነቱ ጭነቱን መከታተል እንዲችል የስልክዎን location on አድርገው የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ በመንካት አዲስ ጉዞ ክፈት ሹፌር የምትለዋን ነክተው ያስመዝግቡ። የጉዞ መዳረሻ ላይ ሲደርሱ location ያጥፉ እናመሰግናለን የሚል notfication ይጨመር። ለበለጠ መረጃ👉 @tanapage ነክተው join ያድርጉ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    for cargo in cargo_posts:
        if not cargo.get("active", True):
            continue
        if vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=cargo["user_id"],
                    text=(
                        "🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n"
                        f"🚛 አይነት፦ {truck['type']}\n"
                        f"⚖️ አቅም፦ {truck['capacity']}\n"
                        f"🛣️ መንገድ፦ {truck['route']}\n\n"
                        "🚛 የጭነት መኪና መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END
# ==================================================
# BROKER REGISTRATION
# ==================================================

async def broker_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("truck", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
    context.user_data["broker"] = {}
    await update.message.reply_text(
        "🆕 **የጭነት ደላላ ምዝገባ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።"
    )
    return BROKER_NAME

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀመር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return BROKER_PHONE

async def broker_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return BROKER_PHONE
    context.user_data["broker"]["phone"] = phone
    await update.message.reply_text(
        "3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር"
    )
    return BROKER_ADDRESS

async def broker_address(update, context):
    address = update.message.text.strip()
    user = update.effective_user
    context.user_data["broker"]["address"] = address

    broker = {
        "user_id": user.id,
        "name": context.user_data["broker"]["name"],
        "username": user.username or "",
        "phone": context.user_data["broker"]["phone"],
        "address": address,
        "timestamp": datetime.now().isoformat(),
    }

    brokers.append(broker)
    try:
        db_save_broker(broker)
    except Exception as e:
        print(f"DB SAVE BROKER ERROR: {e}")

    await update.message.reply_text(
        "✅ **ስለተመዘገቡ እናመሰግናለን!**\n\n"
        "የጭነት ደላላ መሆንዎን አረጋግጠን "
        "አብረን በጋራ እንሰራለን።\n\n"
        "📌 ማረጋገጫ ሲደረግ ይነገርዎታል።",
        reply_markup=main_menu(user.id)
    )

    await send_admin_broker_notification(context, broker)

    return ConversationHandler.END

# ==================================================
# SEND ADMIN BROKER NOTIFICATION
# ==================================================

async def send_admin_broker_notification(context, broker):
    notification_text = (
        "🆕 **TANA CARGO — አዲስ የጭነት ደላላ ተመዝግቧል!**\n\n"
        f"👤 **ስም:** {broker['name']}\n"
        f"🔗 **Username:** @{broker['username'] or 'የለም'}\n"
        f"🆔 **Telegram ID:** {broker['user_id']}\n"
        f"📞 **ስልክ:** {broker['phone']}\n"
        f"📍 **አድራሻ:** {broker['address']}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 **የጭነት ደላላ መሆኑን ያረጋግጡ።**\n"
        "✅ Right = አረጋግጥ\n"
        "❌ X = ውድቅ አድርግ"
    )

    broker_idx = len(brokers) - 1

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Right", callback_data=f"brokerright_{broker_idx}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"brokerxdelete_{broker_idx}"),
        ]
    ])

    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=notification_text,
                reply_markup=kb
            )
        except Exception:
            pass

# ==================================================
# BROKER RIGHT CONFIRM
# ==================================================

async def broker_right_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if index < 0 or index >= len(brokers):
        return

    broker = brokers[index]

    if broker.get("approved"):
        await query.answer("⚠️ ይህ ቀድሞ ጸድቋል!", show_alert=True)
        return

    broker["approved"] = True
    broker["active"] = True

    await query.edit_message_text("✅ **የጭነት ደላላው ጸድቋል!**\n\nለደላላው ማሳወቂያ ተልኳል።")

    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            approval_text = (
                "🎉 **እንኳን ደስ አለዎት!** 🎉\n\n"
                "**የጣና ጭነት አባል ሆነዋል!**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📦 **ጭነት ካለዎት** ወይም\n"
                "🚛 **የጭነት መኪና ካለዎት**\n\n"
                "✅ **በነፃ ይለጥፉ!**\n\n"
                "🔒 መረጃዎ **100% የተጠበቀ** ነው።\n\n"
                "💰 ደንበኛ ፈልገን ስናገናኛቸው ብቻ "
                "ከሚያገኙት ኮሚሽን ላይ **5% ብቻ** "
                "ለጣና ጭነት ይከፍላሉ።\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🙏 እኛን ስለመረጡ እናመሰግናለን!"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=approval_text)
        except Exception:
            pass

# ==================================================
# BROKER X DELETE
# ==================================================

async def broker_x_delete(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if index < 0 or index >= len(brokers):
        return

    broker = brokers[index]
    broker["active"] = False
    broker["approved"] = False

    await query.edit_message_text(
        "❌ **ውድቅ ተደርጓል!**\n\n"
        "ይህ የጭነት ደላላ በ Super Admin ውድቅ ተደርጓል።"
    )

    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            rejection_text = (
                "❌ **የጭነት ደላላ ምዝገባ ውድቅ ተደርጓል።**\n\n"
                "የጣና ካርጎ አባላቶች ባገኙት መረጃ መሰረት "
                "እርሶ **የጭነት ደላላ አይደሉም**።\n\n"
                "ገፃችንን ስለጎበኙ እናመሰግናለን! 🙏"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=rejection_text)
        except Exception:
            pass

# ==================================================
# SHOW BROKER REGISTRY
# ==================================================

async def show_broker_registry(update, context):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return

    if not brokers:
        await update.message.reply_text("📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።")
        return

    for i, broker in enumerate(brokers):
        if not broker.get("approved"):
            status = "⏳ በመጠበቅ"
        elif broker.get("active"):
            status = "✅ ጸድቋል"
        else:
            status = "❌ ውድቅ"

        text = (
            f"📋 **የጭነት ደላላ #{i + 1}**\n\n"
            f"👤 ስም: {broker.get('name', 'N/A')}\n"
            f"🔗 Username: @{broker.get('username') or 'የለም'}\n"
            f"🆔 Telegram ID: {broker.get('user_id', 'N/A')}\n"
            f"📞 ስልክ: {broker.get('phone', 'N/A')}\n"
            f"📍 አድራሻ: {broker.get('address', 'N/A')}\n"
            f"📌 ሁኔታ: {status}\n"
        )

        buttons = []
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{i + 1} አጥፋ (Super)",
                    callback_data=f"admindeletebroker_{i}"
                )
            ])

        if buttons:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await update.message.reply_text(text)

# ==================================================
# ADMIN DELETE BROKER
# ==================================================

async def admin_delete_broker(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[-1])
    except ValueError:
        return

    if index < 0 or index >= len(brokers):
        return

    brokers[index]["active"] = False
    brokers[index]["approved"] = False

    await query.edit_message_text("✅ **የጭነት ደላላው ተሰርዟል።**")

# ==================================================
# FIND CARGO
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
        is_selected = is_already_selected("cargo", real_index)

        status_mark = "❌ " if is_selected else ""

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
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        if is_selected:
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

# ==================================================
# FIND TRUCK (SIMPLIFIED)
# ==================================================

async def find_truck(update, context):
    await update.message.reply_text(
        "🔎 **የጭነት መኪና መፈለግ**\n\n"
        "እባክዎ የሚፈልጉትን የመኪና አይነት ወይም የመንገድ አቅጣጫ ይጻፉ።\n"
        "ለምሳሌ፦ Isuzu ወይም ባህር ዳር\n\n"
        "👉 ሁሉንም መኪኖች ለማየት 'ሁሉም' ብለው ይጻፉ።",
        reply_markup=ReplyKeyboardRemove()
    )
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
        await update.message.reply_text(
            f"🚛 '{query}' ጋር የሚዛመድ ንቁ የጭነት መኪና አልተገኘም።\n\n"
            "እባክዎ ሌላ ቃል ይፈልጉ ወይም 'ሁሉም' ብለው ይጻፉ።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"🔎 **የተገኙ የጭነት መኪናዎች (ጠቅላላ {len(active_trucks)})**",
        reply_markup=main_menu(update.effective_user.id)
    )

    is_admin_user = is_admin(update.effective_user.id)
    limit = 20 if is_admin_user else 10

    for i, truck in enumerate(active_trucks[:limit]):
        truck_id = f"{i + 1:03d}"
        route_display = truck.get("route", "N/A")
        real_index = truck_posts.index(truck)
        is_selected = is_already_selected("truck", real_index)
        status_mark = "❌ " if is_selected else ""

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
            f"🛣️ መንገድ/አድራሻ፦ {route_display}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"❌ መኪና {truck_id} ተመርጧል", callback_data="already_selected")
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"የመረጡት የጭነት መኪና {truck_id} ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{real_index}")
            ]])

        await update.message.reply_text(msg, reply_markup=kb)

    return ConversationHandler.END

# ==================================================
# ALREADY SELECTED CALLBACK
# ==================================================

async def already_selected_callback(update, context):
    query = update.callback_query
    await query.answer(
        "❌ ይቅርታ የመረጡት ጭነት/የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
        "እባክዎ ሌላ ተስማሚ ይምረጡ።",
        show_alert=True
    )

# ==================================================
# CARGO CONNECTION REQUEST
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(cargo_posts):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END

    cargo = cargo_posts[index]

    if not cargo.get("active", True):
        await query.edit_message_text("❌ ይህ ጭነት ተወግዷል።")
        return ConversationHandler.END

    requester = update.effective_user

    if is_selected_by_other("cargo", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት ጭነት በሌላ ደንበኛችን በድርድር ላይ ነው።", show_alert=True)
        return ConversationHandler.END

    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    for req in connection_requests:
        if (req.get("type") == "cargo" and req.get("cargo_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END

    context.user_data["connect_info"] = {
        "type": "cargo",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }

    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

# ==================================================
# TRUCK CONNECTION REQUEST
# ==================================================

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(truck_posts):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END

    truck = truck_posts[index]

    if not truck.get("active", True):
        await query.edit_message_text("❌ ይህ መኪና ተወግዷል።")
        return ConversationHandler.END

    requester = update.effective_user

    if is_selected_by_other("truck", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።", show_alert=True)
        return ConversationHandler.END

    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    for req in connection_requests:
        if (req.get("type") == "truck" and req.get("truck_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END

    context.user_data["connect_info"] = {
        "type": "truck",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }

    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

# ==================================================
# CONNECT_NAME → CONNECT_PHONE → CONNECT_TYPE
# ==================================================

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info

    await update.message.reply_text(
        "2️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
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

    await update.message.reply_text(
        "3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር"
    )
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip().split("\n")[0].strip()

    info["requester_address"] = text
    context.user_data["connect_info"] = info
    requester = update.effective_user

    if info.get("type") == "cargo":
        cargo = cargo_posts[info["item_index"]]
        request = {
            "type": "cargo",
            "cargo_index": info["item_index"],
            "cargo_owner_id": cargo["user_id"],
            "requester_id": requester.id,
            "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""),
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_plate": "",
            "status": "admin_confirming",
            "offers": [], "current_offer": None, "current_offer_by": None,
            "offer_version": 0, "final_price": None,
            "requester_confirmed": False, "other_confirmed": False,
            "requester_payment_submitted": False, "other_payment_submitted": False,
            "requester_payment_approved": False, "other_payment_approved": False,
            "requester_payment_rejected": False, "other_payment_rejected": False,
            "requester_payment_version": 0, "other_payment_version": 0,
            "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False,
            "owner_2_notified": False,
            "created_at": datetime.now().isoformat(),
        }
        
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
            "ውድ ደንበኛችን ባለጭነት ጋር ተስማምተው ጭነተዎን ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነት እርሶ የጫኑለትን ጭነት መከታተል እንዲችል የስልክዎን location ና የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ ተጭነው አዲስ ጉዞ ክፈት (ሹፌር) የምትለዋን ተጭነው መረጃ ያጋሩ ጉዞ ሲጨርሱ location off ያድርጉ። ለበለጠ መረጃ👉 @tanapage በመንካት join ያድርጉ።\n\n"
            "🙏 እናመሰግናለን!"
        )
    else:
        truck = truck_posts[info["item_index"]]
        request = {
            "type": "truck",
            "truck_index": info["item_index"],
            "truck_owner_id": truck["user_id"],
            "requester_id": requester.id,
            "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""),
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_route": "",
            "status": "admin_confirming",
            "offers": [], "current_offer": None, "current_offer_by": None,
            "offer_version": 0, "final_price": None,
            "requester_confirmed": False, "other_confirmed": False,
            "requester_payment_submitted": False, "other_payment_submitted": False,
            "requester_payment_approved": False, "other_payment_approved": False,
            "requester_payment_rejected": False, "other_payment_rejected": False,
            "requester_payment_version": 0, "other_payment_version": 0,
            "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False,
            "owner_2_notified": False,
            "created_at": datetime.now().isoformat(),
        }
        
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
            "ውድ ደንበኛችን ጭነተዎን በgoogle map መከታተል ከፈለጉ የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተል ኮድ ጠይቅ (ደንበኛ) የምትለዋን ተጭነው በመመሪያው መሰረት ይሙሉና ጭነተዎን ይከታተሉ። ለበለጠ መረጃ 👉@tanapage በመጫን join ያድርጉ።\n\n"
            "🙏 እናመሰግናለን!"
        )

    connection_requests.append(request)
    req_index = len(connection_requests) - 1
    context.user_data.pop("connect_info", None)

    await update.message.reply_text(
        msg_text,
        reply_markup=main_menu(requester.id)
    )

    await send_admin_notification(context, req_index)
    return ConversationHandler.END
# ==================================================
# SEND ADMIN NOTIFICATION
# ==================================================

async def send_admin_notification(context, req_index):
    req = connection_requests[req_index]

    if req.get("admin_notified"):
        return
    req["admin_notified"] = True

    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
        cargo_owner_username = users.get(cargo["user_id"], {}).get("username", "")
        cargo_owner_phone = cargo.get("phone", "N/A")
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"

        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"📦 **የጭነት ዝርዝር:** {poster_label}\n"
            f"   • መነሻ: {cargo['from']}\n"
            f"   • መድረሻ: {cargo['to']}\n"
            f"   • አይነት: {cargo['type']}\n"
            f"   • ክብደት: {cargo['weight']}\n"
            f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
            f"👤 **የጭነት ባለቤት:**\n"
            f"   • ስም: {cargo_owner_name}\n"
            f"   • Username: @{cargo_owner_username or 'የለም'}\n"
            f"   • ስልክ: {cargo_owner_phone}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
        truck_owner_username = users.get(truck["user_id"], {}).get("username", "")
        truck_owner_phone = truck.get("phone", "N/A")
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"

        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"🚛 **የመኪና ዝርዝር:** {poster_label}\n"
            f"   • አይነት: {truck['type']}\n"
            f"   • አቅም: {truck['capacity']}\n"
            f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
            f"   • መንገድ: {truck.get('route', 'N/A')}\n\n"
            f"👤 **የመኪና ባለቤት:**\n"
            f"   • ስም: {truck_owner_name}\n"
            f"   • Username: @{truck_owner_username or 'የለም'}\n"
            f"   • ስልክ: {truck_owner_phone}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )

    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text)
        except Exception:
            pass

# ==================================================
# ADMIN RIGHT CONFIRM
# ==================================================

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]

    if req.get("admin_right_confirmed"):
        await query.answer("⚠️ ይህ ቀድሞ ተረጋግጧል!", show_alert=True)
        return

    req["admin_right_confirmed"] = True
    req["status"] = "negotiating"

    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False

    await query.edit_message_text(
        "✅ **Right ተረጋግጧል!**\n\n"
        "📌 ጭነቱ/መኪናው ከገበያ ተወግዷል።\n"
        "📌 መረጃው **የግንኙነት ጥያቄዎች** ምናሌ ውስጥ ብቻ ይቀመጣል።\n"
        "📌 ለሁለቱም ወገኖች ማሳወቂያ ተልኳል።"
    )

    seeker_id = req["requester_id"]
    item_type = "የጭነት መኪና" if req.get("type") == "truck" else "የጭነት"

    seeker_text = f"ውድ ደንበኛችን፣\n\nየፈለጉት {item_type} ባለቤት ምላሽ እንደሰጡ እናሳውቃለን።\n\n📌 እባክዎ ትንሽ ይጠብቁ።\n\n🙏 እናመሰግናለን!"

    try:
        await context.bot.send_message(chat_id=seeker_id, text=seeker_text)
        req["seeker_notified"] = True
    except Exception:
        pass

    owner_id = other_party_id(req)

    if req.get("type") == "truck":
        owner_text = (
            "ውድ ደንበኛችን፣\n\nየለጠፉትን የጭነት መኪና የሚፈልግ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n📞 {SUPPORT_PHONE_2}\n\nወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n🙏 እናመሰግናለን!"
        )
    else:
        owner_text = (
            "ውድ ደንበኛችን፣\n\nየለጠፉትን ጭነት የሚወስድ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n📞 {SUPPORT_PHONE_2}\n\nወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n🙏 እናመሰግናለን!"
        )

    try:
        await context.bot.send_message(chat_id=owner_id, text=owner_text)
        req["owner_notified"] = True
    except Exception:
        pass

# ==================================================
# ADMIN X DELETE
# ==================================================

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ ማጥፋት ይችላል!", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]
    req["status"] = "deleted"

    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = True
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = True

    await query.edit_message_text("❌ **ተሰርዟል!**\n\nይህ ጥያቄ በ Super Admin ተሰርዟል።\n\n📌 ጭነት/መኪናው ነፃ ሆኗል።")

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text="❌ **ይቅርታ**\n\nየጠየቁት ጭነት/መኪና ውድቅ ሆኗል።\n\nእባክዎ ሌላ ተስማሚ ጭነት/መኪና ይምረጡ።"
        )
    except Exception:
        pass

# ==================================================
# ADMIN CLEAR
# ==================================================

async def admin_clear_notification(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        await query.delete_message()
    except Exception:
        try:
            await query.edit_message_text("🗑️ ማሳወቂያው ጸድቷል።")
        except Exception:
            pass

# ==================================================
# SHOW CONNECTION REQUESTS
# ==================================================

async def show_connection_requests(update, context):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
            reply_markup=main_menu(user_id)
        )
        context.user_data["sending_admin_message"] = True
        return

    received = []
    for i, req in enumerate(connection_requests):
        if req["status"] not in ["deleted"]:
            received.append((i, req))

    if not received:
        await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።", reply_markup=main_menu(user_id))
        return

    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            truck_owner_name = users.get(req["truck_owner_id"], {}).get("name", "N/A")
            truck_owner_username = users.get(req["truck_owner_id"], {}).get("username", "")
            truck_owner_phone = truck.get("phone", "N/A")
            truck_poster = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"

            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🚛 **የመኪና ባለቤት:** {truck_poster}\n"
                f"   • ስም: {truck_owner_name}\n"
                f"   • Username: @{truck_owner_username or 'የለም'}\n"
                f"   • ስልክ: {truck_owner_phone}\n"
                f"   • አይነት: {truck['type']}\n"
                f"   • አቅም: {truck['capacity']}\n"
                f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"   • መንገድ: {truck.get('route', 'N/A')}\n"
                f"   • ሹፌር: {truck.get('driver', 'N/A')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የጭነት ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            cargo_owner_name = users.get(req["cargo_owner_id"], {}).get("name", "N/A")
            cargo_owner_username = users.get(req["cargo_owner_id"], {}).get("username", "")
            cargo_owner_phone = cargo.get("phone", "N/A")
            cargo_poster = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"

            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **የጭነት ባለቤት:** {cargo_poster}\n"
                f"   • ስም: {cargo_owner_name}\n"
                f"   • Username: @{cargo_owner_username or 'የለም'}\n"
                f"   • ስልክ: {cargo_owner_phone}\n"
                f"   • መነሻ: {cargo['from']}\n"
                f"   • መድረሻ: {cargo['to']}\n"
                f"   • አይነት: {cargo['type']}\n"
                f"   • ክብደት: {cargo['weight']}\n"
                f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የመኪና ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )

        buttons = [[
            InlineKeyboardButton("✅ Right", callback_data=f"adminright_{i}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{i}"),
        ]]
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(f"🗑️ ሙሉ በሙሉ አጥፋ #{i + 1} (Super)", callback_data=f"admindeleteconnection_{i}")
            ])

        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ==================================================
# ADMIN DELETE CONNECTION
# ==================================================

async def admin_delete_connection(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return

    index = int(query.data.split("_")[-1])
    if index < 0 or index >= len(connection_requests):
        return

    connection_requests[index]["status"] = "deleted"
    await query.edit_message_text("✅ ተሰርዟል።")

# ==================================================
# HANDLE USER MESSAGE TO ADMINS
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

# ==================================================
# HANDLE USER PHOTO TO ADMINS
# ==================================================

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

# ==================================================
# ADMIN MESSAGE MODE STARTER
# ==================================================

async def _start_admin_message_mode(update, context):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
        reply_markup=main_menu(user_id)
    )
    context.user_data["sending_admin_message"] = True

# ==================================================
# OWNER REGISTRATION
# ==================================================

async def owner_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("truck", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("connect_info", None)
    context.user_data["owner"] = {}
    await update.message.reply_text("📦 የጭነት ባለቤት ምዝገባ\n\n1️⃣ ስምዎን ይጻፉ።")
    return OWNER_NAME

async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678"
    )
    return OWNER_PHONE

async def owner_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return OWNER_PHONE

    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id

    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = owner.copy()

    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=owner["name"], owner_phone=owner["phone"])
    except Exception as e:
        print(f"DB SAVE OWNER ERROR: {e}")

    await update.message.reply_text(
        f"✅ ምዝገባዎ ተጠናቋል!\n\n👤 {owner['name']}\n📞 {owner['phone']}",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

# ==================================================
# GET NEGOTIATION INDEX
# ==================================================

def get_negotiation_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("negotiation_request")
    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (req["status"] == "negotiating"
            and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
            return saved
    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (req["status"] == "negotiating"
            and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
            return i
    return None

# ==================================================
# SUBMIT PRICE
# ==================================================

async def submit_price(update, context):
    user_id = update.effective_user.id
    active_index = get_negotiation_index(user_id)
    if active_index is None:
        return

    req = connection_requests[active_index]
    text = update.message.text.strip().split("\n")[0].strip()

    if text in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price = None
        price_display = "በስምምነት"
    else:
        try:
            price = float(text.replace(",", "").replace("ብር", "").strip())
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text("❌ የዋጋውን ቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\nምሳሌ፦ 55000 ወይም በስምምነት")
            return
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return

    req["offer_version"] = req.get("offer_version", 0) + 1
    version = req["offer_version"]
    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None
    req["offers"].append({"user_id": user_id, "price": price, "display": price_display, "version": version})
    req["current_offer"] = price
    req["current_offer_by"] = user_id
    req["status"] = "negotiating"

    await update.message.reply_text(f"💰 የላኩት ዋጋ፦ {price_display}\n\n⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።")

    buttons = [
        [InlineKeyboardButton(f"✅ ተስማማለሁ {price_display}", callback_data=f"agreeprice_{active_index}_{version}")],
        [InlineKeyboardButton("❌ አልስማማሁም", callback_data=f"rejectprice_{active_index}_{version}")],
    ]

    other_user = other_party_id(req)
    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=f"💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n💵 የቀረበው ዋጋ፦ {price_display}\n\nከዚህ ዋጋ ጋር ከተስማሙ ✅ ይጫኑ።\nወይም ❌ አልስማማሁም ይጫኑ።",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception:
        pass

# ==================================================
# AGREE PRICE
# ==================================================

async def agree_price(update, context):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    if len(parts) != 3:
        return
    index = int(parts[1])
    button_version = int(parts[2])
    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]
    user_id = update.effective_user.id

    if button_version != req.get("offer_version", 0):
        await query.answer("⚠️ ይህ የድሮ ዋጋ ነው።", show_alert=True)
        return

    if req["status"] != "negotiating":
        await query.answer("⚠️ ይህ ድርድር ንቁ አይደለም።", show_alert=True)
        return

    if user_id != req["requester_id"] and user_id != other_party_id(req):
        await query.answer("❌ የእርስዎ ድርድር አይደለም።", show_alert=True)
        return

    price = req.get("current_offer")
    price_display = f"{price:,.2f} ብር" if price is not None else "በስምምነት"

    if user_id == req.get("current_offer_by"):
        await query.answer("⚠️ የራስዎን ዋጋ መቀበል አይችሉም።", show_alert=True)
        return

    if user_id == req["requester_id"]:
        if req["requester_confirmed"]:
            await query.answer("✅ እርስዎ ቀድሞ ተስማምተዋል።", show_alert=True)
            return
        req["requester_confirmed"] = True
    else:
        if req["other_confirmed"]:
            await query.answer("✅ እርስዎ ቀድሞ ተስማምተዋል።", show_alert=True)
            return
        req["other_confirmed"] = True

    if req["requester_confirmed"] and req["other_confirmed"]:
        req["final_price"] = price
        req["status"] = "awaiting_payment"

        for uid in [req["requester_id"], other_party_id(req)]:
            if uid in users:
                users[uid].pop("negotiation_request", None)
                users[uid]["payment_request"] = index

        each_side = price * 0.01 if price is not None else 0

        await query.edit_message_text(
            "🎉 **Congratulations!** 🎉\n\n✅ ሁለታችሁም ተስማምታችኋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price_display}\n\n"
            f"📌 ከእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n\n"
            "💳 አሁን እያንዳንዱ ወገን የራሱን 1% ይከፍላል።\n📸 ደረሰኙን ወደዚህ ቻት ይላኩ።"
        )

        success_text = (
            "🎉 **እንኳን ደስ ያላችሁ!** 🎉\n\n🤝 ሁለታችሁም ተስማምታችኋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price_display}\n\n"
            f"📌 የእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n\n"
            "💳 ደረሰኙን አሁን ወደዚህ ቻት ይላኩ።\n🔒 ሁለቱም ክፍያዎች Admin ሲያረጋግጥ ሙሉ መረጃ ይከፈታል።"
        )

        for uid in [req["requester_id"], other_party_id(req)]:
            try:
                await context.bot.send_message(chat_id=uid, text=success_text)
            except Exception:
                pass
        return

    other_user = (other_party_id(req) if user_id == req["requester_id"] else req["requester_id"])
    buttons = [
        [InlineKeyboardButton(f"✅ እኔም እስማማለሁ {price_display}", callback_data=f"agreeprice_{index}_{req.get('offer_version', 0)}")],
        [InlineKeyboardButton("❌ አልስማማሁም", callback_data=f"rejectprice_{index}_{req.get('offer_version', 0)}")],
    ]

    await query.edit_message_text(f"✅ {price_display} ላይ ተስማምተዋል።\n\n⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።")

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=f"🤝 የዋጋ ስምምነት ማረጋገጫ\n\n💰 {price_display}\n\nሌላኛው ወገን ተስማምቷል።\nእርስዎም ከተስማሙ ✅ ይጫኑ።",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception:
        pass

# ==================================================
# REJECT PRICE
# ==================================================

async def reject_price_button(update, context):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    if len(parts) != 3:
        return
    index = int(parts[1])
    button_version = int(parts[2])
    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]
    if button_version != req.get("offer_version", 0):
        await query.answer("⚠️ ይህ የድሮ ዋጋ ነው።", show_alert=True)
        return

    user_id = update.effective_user.id
    if user_id != req["requester_id"] and user_id != other_party_id(req):
        return

    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None

    await query.edit_message_text("❌ **ድርድራችሁ አልተሳካም**\n\nበድጋሚ መደራደር ይቻላል።\n\n💬 አዲስ ዋጋ ይጻፉ።")

    other_user = (other_party_id(req) if user_id == req["requester_id"] else req["requester_id"])
    try:
        await context.bot.send_message(chat_id=other_user, text="❌ ሌላኛው ወገን አልተስማማም።\n\n💬 አዲስ ዋጋ ያቅርቡ።")
    except Exception:
        pass

# ==================================================
# PAYMENT INDEX
# ==================================================

def get_payment_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("payment_request")
    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (req["status"] == "awaiting_payment" and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
            return saved
    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (req["status"] == "awaiting_payment" and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
            return i
    return None

# ==================================================
# RECEIPT MESSAGE
# ==================================================

async def receipt_message(update, context):
    user_id = update.effective_user.id
    index = get_payment_index(user_id)
    if index is None:
        return

    req = connection_requests[index]
    receipt = update.message.text.strip()
    side = "requester" if user_id == req["requester_id"] else "other"

    version_key = f"{side}_payment_version"
    req[version_key] = req.get(version_key, 0) + 1
    payment_version = req[version_key]

    req[f"{side}_payment_submitted"] = True
    req[f"{side}_payment_approved"] = False
    req[f"{side}_payment_rejected"] = False
    req[f"{side}_receipt"] = receipt
    req["status"] = "awaiting_payment"

    await update.message.reply_text("🧾 የክፍያ Receipt ተቀብለናል።\n\n⏳ Admin ይመረምራል።")

    await send_admin_payment_request(context, index, side, user_id, receipt_text=receipt, payment_version=payment_version)

async def receipt_photo(update, context):
    user_id = update.effective_user.id

    if context.user_data.get("sending_admin_message"):
        return await handle_user_photo(update, context)

    index = get_payment_index(user_id)
    if index is None:
        return

    req = connection_requests[index]
    file_id = update.message.photo[-1].file_id
    side = "requester" if user_id == req["requester_id"] else "other"

    version_key = f"{side}_payment_version"
    req[version_key] = req.get(version_key, 0) + 1
    payment_version = req[version_key]

    req[f"{side}_payment_submitted"] = True
    req[f"{side}_payment_approved"] = False
    req[f"{side}_payment_rejected"] = False
    req[f"{side}_receipt_photo"] = file_id
    req["status"] = "awaiting_payment"

    await update.message.reply_text("🧾 የክፍያ Screenshot ተቀብለናል።\n\n⏳ Admin ይመረምራል።")

    await send_admin_payment_request(context, index, side, user_id, photo_id=file_id, payment_version=payment_version)

# ==================================================
# SEND ADMIN PAYMENT REQUEST
# ==================================================

async def send_admin_payment_request(context, index, side, user_id, receipt_text=None, photo_id=None, payment_version=None):
    req = connection_requests[index]
    side_name = "ጠያቂ" if side == "requester" else ("የመኪና ባለቤት" if req.get("type") == "truck" else "የጭነት ባለቤት")

    if payment_version is None:
        payment_version = req.get(f"{side}_payment_version", 0)

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"adminapprove_{index}_{side}_{payment_version}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"adminreject_{index}_{side}_{payment_version}"),
    ]])

    user_name = users.get(user_id, {}).get("name", "Unknown")
    price = req.get("final_price") or 0

    text = (
        "🧾 TANA CARGO — የክፍያ ማስረጃ\n\n"
        f"📌 Request ID፦ {index}\n"
        f"👤 ተጠቃሚ፦ {user_name}\n"
        f"🆔 Telegram ID፦ {user_id}\n"
        f"👥 ወገን፦ {side_name}\n"
        f"💰 Final Price፦ {price:,.2f} ብር\n\n"
    )
    if receipt_text:
        text += f"🧾 Receipt፦\n{receipt_text}\n"
    if photo_id:
        text += "🖼️ Screenshot ከታች ተልኳል።\n"
    text += "\nእባክዎ ክፍያውን ይመርምሩ።"

    for admin_id in get_admin_ids():
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=admin_id, photo=photo_id, caption=text, reply_markup=buttons)
            else:
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=buttons)
        except Exception:
            pass

# ==================================================
# ADMIN PAYMENT ACTION
# ==================================================

async def admin_payment_action(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    parts = query.data.split("_")
    if len(parts) != 4:
        return

    action, index, side, button_version = (parts[0], int(parts[1]), parts[2], int(parts[3]))
    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]
    if side not in ["requester", "other"]:
        return

    if button_version != req.get(f"{side}_payment_version", 0):
        await query.answer("⚠️ ይህ የድሮ ማስረጃ ነው።", show_alert=True)
        return

    if not req.get(f"{side}_payment_submitted"):
        await query.answer("⚠️ ማስረጃ አልተላከም።", show_alert=True)
        return

    user_to_notify = (req["requester_id"] if side == "requester" else other_party_id(req))

    if action == "adminapprove":
        req[f"{side}_payment_approved"] = True
        req[f"{side}_payment_rejected"] = False
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        if (req.get("requester_payment_approved") and req.get("other_payment_approved")):
            await share_full_information(index, context)
        else:
            try:
                await context.bot.send_message(chat_id=user_to_notify, text="✅ ክፍያዎ ተረጋግጧል።\n\n⏳ የሁለተኛው ወገን ማረጋገጫ ይጠበቃል።")
            except Exception:
                pass

    elif action == "adminreject":
        req[f"{side}_payment_approved"] = False
        req[f"{side}_payment_rejected"] = True
        req[f"{side}_payment_submitted"] = False
        req.pop(f"{side}_receipt", None)
        req.pop(f"{side}_receipt_photo", None)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        try:
            await context.bot.send_message(chat_id=user_to_notify, text="❌ ክፍያዎ አልተፈቀደም።\n\nእባክዎ እንደገና ይላኩ።")
        except Exception:
            pass

# ==================================================
# TANA CARGO TRACKING (NEW FLOW)
# ==================================================

async def tracking_menu(update, context):
    context.user_data.pop("tracking_update_code", None)
    context.user_data.pop("tracking_lookup_waiting", None)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ አዲስ ጉዞ ክፍት (ሹፌር)", callback_data="tracking_driver_new")],
        [InlineKeyboardButton("📦 የኔን ጭነት መከታተያ ኮድ ጠይቅ (ደንበኛ)", callback_data="tracking_customer_new")],
    ])
    await update.effective_message.reply_text(
        "📍 **TANA CARGO — የጭነት ክትትል**\n\n"
        "🚛 ሹፌር፦ አዲስ ጉዞ ከፍተው ቦታ ይላኩ።\n"
        "📦 ደንበኛ፦ አዲስ ኮድ ይጠይቁ ወይም ያለዎትን ኮድ ያስገቡ።\n\n"
        "⚠️ አዲስ ጉዞ ሲከፈት አድሚን አጽድቆ ኮዱ ለባለጭነቱ ይላካል።",
        reply_markup=keyboard
    )

# --- የሹፌር አዲስ ጉዞ መክፈቻ ---
async def tracking_new_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_new"] = {}
    await query.message.reply_text(
        "📝 **አዲስ ጉዞ መክፈት**\n\n"
        "1️⃣ የባለጭነቱን (Customer) **ስልክ ቁጥር** ያስገቡ።\n"
        "ምሳሌ፦ 0912345678"
    )
    return TRACK_REQ_PHONE

async def tracking_req_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return TRACK_REQ_PHONE
    
    context.user_data["track_new"]["customer_phone"] = phone
    
    found_route = None
    for c in cargo_posts:
        if c.get("phone") == phone and c.get("active"):
            found_route = f"{c['from']} ➡️ {c['to']}"
            break
            
    if found_route:
        context.user_data["track_new"]["route"] = found_route
        await update.message.reply_text(
            f"✅ የደንበኛው ስልክ ቁጥር ተረጋግጧል!\n"
            f"🗺️ የመንገዱ ዝርዝር: **{found_route}**\n\n"
            "3️⃣ **የአሁኑን የመኪና ቦታ** ይላኩ።\n\n"
            "ከታች ያለውን ቁልፍ ተጭነው GPS ይላኩ።",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)],
                 [KeyboardButton("❌ ሰርዝ")]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
        return TRACK_REQ_LOCATION
    else:
        await update.message.reply_text(
            "⚠️ በዚህ ስልክ ቁጥር የተመዘገበ ንቁ ጭነት አልተገኘም።\n"
            "እባክዎ ስልክ ቁጥሩን ያረጋግጡ ወይም የመንገዱን ዝርዝር በእጅዎ ይጻፉ።\n\n"
            "2️⃣ **የመንገዱን ዝርዝር** ይጻፉ።\n"
            "ምሳሌ፦ ባህር ዳር ➡️ አዲስ አበባ"
        )
        return TRACK_REQ_ROUTE

async def tracking_req_route(update, context):
    route = update.message.text.strip()
    context.user_data["track_new"]["route"] = route
    await update.message.reply_text(
        "3️⃣ **የአሁኑን የመኪና ቦታ** ይላኩ።\n\n"
        "ከታች ያለውን ቁልፍ ተጭነው GPS ይላኩ።",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)],
             [KeyboardButton("❌ ሰርዝ")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    return TRACK_REQ_LOCATION

async def tracking_req_location(update, context):
    if not update.message.location:
        await update.message.reply_text("❌ እባክዎ ቦታውን በGPS ይላኩ።", reply_markup=ReplyKeyboardRemove())
        return TRACK_REQ_LOCATION

    loc = update.message.location
    req = context.user_data.get("track_new", {})
    req["driver_id"] = update.effective_user.id
    req["driver_name"] = update.effective_user.full_name
    req["lat"] = loc.latitude
    req["lon"] = loc.longitude
    
    pending_track_requests.append(req)
    req_idx = len(pending_track_requests) - 1

    await update.message.reply_text(
        "✅ ቦታው ተቀብለናል። አድሚን ሲያጸድቅ ኮዱ ይላክልዎታል።",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.pop("track_new", None)

    map_url = f"https://maps.google.com/?q={loc.latitude},{loc.longitude}"
    text = (
        "🆕 **TANA CARGO — አዲስ የክትትል ጥያቄ (ሹፌር)**\n\n"
        f"👤 ሹፌር: {req['driver_name']} (ID: {req['driver_id']})\n"
        f"📞 የባለጭነቱ ስልክ: {req['customer_phone']}\n"
        f"🛣️ መንገድ: {req['route']}\n\n"
        f"🗺️ [የመኪናውን ቦታ ለማየት]({map_url})\n\n"
        "📌 አጽድቀው ኮዱ ለባለጭነቱ እንዲላክ ይፈልጋሉ?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve & Send Code", callback_data=f"adminapprovetrack_{req_idx}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"adminrejecttrack_{req_idx}")
    ]])

    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass

    return ConversationHandler.END

async def admin_approve_tracking(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if idx < 0 or idx >= len(pending_track_requests):
        return

    req = pending_track_requests[idx]
    code = "TC" + secrets.token_hex(4).upper()
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tracking_sessions (
                tracking_code, request_type, driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                status, latitude, longitude, location_updated_at, created_at,
                customer_phone, driver_phone
            ) VALUES (?, 'cargo', ?, 0, ?, ?, '', '', 'in_transit', ?, ?, ?, ?, ?, ?)
        """, (
            code, req["driver_id"], req["route"], req["route"],
            req["lat"], req["lon"], datetime.now().astimezone().isoformat(),
            datetime.now().astimezone().isoformat(),
            req["customer_phone"], ""
        ))
        conn.commit()
    finally:
        conn.close()

    await query.edit_message_text(
        f"✅ ጥያቄው ጸድቋል!\n\n🔑 ኮድ፦ `{code}`\n\n"
        "ኮዱ ለሹፌሩም ለባለጭነቱም ተልኳል።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["driver_id"],
            text=f"🎉 ጉዞዎ ጸድቋል!\n\n🔑 **የክትትል ኮድ፦** `{code}`\n\n"
                 f"📞 ይህንን ኮድ ለባለጭነቱ ({req['customer_phone']}) ያስተላልፉ።\n"
                 f"ደንበኛው ኮዱን በቦት ውስጥ ሲያስገባ የመኪናውን ቦታ ማየት ይችላል።"
        )
    except Exception:
        pass

    customer_id = None
    for uid, udata in users.items():
        if udata.get("phone") == req["customer_phone"] or udata.get("owner", {}).get("phone") == req["customer_phone"]:
            customer_id = uid
            break
            
    if customer_id:
        try:
            await context.bot.send_message(
                chat_id=customer_id,
                text=f"📦 **TANA CARGO — የጭነት ክትትል**\n\n"
                     f"ሹፌሩ ጉዞ ጀምሯል! የክትትል ኮድዎ ይህ ነው፦\n\n"
                     f"🔑 `{code}`\n\n"
                     f"👉 ወደ '📍 የጭነት ክትትል' ሄደው '📦 ጭነቴን በኮድ መከታተል' ላይ ይህንን ኮድ ያስገቡ።"
            )
        except Exception:
            pass

async def admin_reject_tracking(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    if idx < 0 or idx >= len(pending_track_requests):
        return

    req = pending_track_requests[idx]
    await query.edit_message_text("❌ ጥያቄው ውድቅ ተደርጓል።")
    
    try:
        await context.bot.send_message(chat_id=req["driver_id"], text="❌ የጉዞ ጥያቄዎ በአድሚን ውድቅ ተደርጓል።")
    except Exception:
        pass

# --- 🔴 የደንበኛ አዲስ ኮድ መጠየቂያ (ስልክ ብቻ) ---
async def customer_track_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["cust_track_phone_only"] = True
    await query.message.reply_text(
        "📦 **የክትትል ኮድ ለማግኘት**\n\n"
        "እባክዎን ሹፌሩ ያስመዘገበውን **የራስዎን ስልክ ቁጥር** ያስገቡ።\n"
        "ምሳሌ፦ 0912345678\n\n"
        "⚠️ ስልክ ቁጥሩ በሹፌሩ ጉዞ መክፈቻ ላይ የተመዘገበ መሆን አለበት።",
        reply_markup=ReplyKeyboardRemove()
    )
    return CUST_TRACK_PHONE_ONLY

async def cust_track_phone_only(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CUST_TRACK_PHONE_ONLY
    
    context.user_data.pop("cust_track_phone_only", None)
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT * FROM tracking_sessions 
            WHERE customer_phone = ? AND status IN ('ready', 'in_transit')
            ORDER BY created_at DESC LIMIT 1
        """, (phone,)).fetchall()
    finally:
        conn.close()

    if rows:
        record = dict(rows[0])
        await update.message.reply_text(
            "✅ **የክትትል ኮድዎ ተገኝቷል! በራስ-ሰር ወደ ማፕ እየተዛወረ ነው...**",
            reply_markup=ReplyKeyboardRemove()
        )
        await tracking_show_result(update, context, record)
    else:
        await update.message.reply_text(
            "❌ በዚህ ስልክ ቁጥር የተመዘገበ ንቁ ጉዞ አልተገኘም።\n\n"
            "እባክዎ ስልክ ቁጥሩን ያረጋግጡ ወይም ሹፌሩ ቁጥርዎን መዝግቦ መሆኑን ያረጋግጡ።",
            reply_markup=main_menu(update.effective_user.id)
        )
    return ConversationHandler.END

# --- ክትትል ማየት እና ማቆም ---
async def tracking_customer_choose(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_lookup_waiting"] = True
    await query.message.reply_text(
        "📦 የተሰጠዎትን የክትትል ኮድ ያስገቡ።\nምሳሌ፦ TC1A2B3C4"
    )

async def tracking_driver_choose(update, context):
    query = update.callback_query
    await query.answer()
    jobs = db_get_driver_tracking(query.from_user.id)
    if not jobs:
        await query.message.reply_text("🚛 ለእርስዎ የተመዘገበ ንቁ የክትትል ጉዞ አልተገኘም።\n\nአዲስ ጉዞ ለመክፈት '➕ አዲስ ጉዞ ክፍት' የሚለውን ይጫኑ።")
        return
    
    buttons = []
    for job in jobs[:20]:
        route = f"{job.get('origin') or 'መነሻ አልተገለጸም'} ➡️ {job.get('destination') or 'መድረሻ አልተገለጸም'}"
        label = f"📍 {job['tracking_code']} — {route}"
        buttons.append([InlineKeyboardButton(label[:60], callback_data=f"tracking_update:{job['tracking_code']}")])
        buttons.append([InlineKeyboardButton(f"🛑 {job['tracking_code']} — ክትትል አቁም", callback_data=f"stop_req:{job['tracking_code']}")])
        
    await query.message.reply_text("ቦታ ለመላክ የጉዞውን ኮድ ይምረጡ፦", reply_markup=InlineKeyboardMarkup(buttons))

async def tracking_begin_location(update, context):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1].upper()
    record = db_get_tracking(code)
    if not record or int(record["driver_user_id"]) != query.from_user.id:
        await query.message.reply_text("❌ ይህን ጉዞ የማዘመን ፈቃድ የለዎትም።")
        return
    if record["status"] == "delivered":
        await query.message.reply_text("✅ ይህ ጉዞ እንደተጠናቀቀ ተመዝግቧል።")
        return
    context.user_data["tracking_update_code"] = code
    await query.message.reply_text(
        f"📍 ለጉዞ **{code}** የአሁኑን የመኪና ቦታ ይላኩ።\n\nከታች ያለውን ቁልፍ ይጫኑ።",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)],
             [KeyboardButton("❌ ክትትሉን ሰርዝ")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )

async def request_stop_tracking(update, context):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":")[1].upper()
    record = db_get_tracking(code)
    
    if not record:
        await query.message.reply_text("❌ ኮዱ አልተገኘም።")
        return
        
    user_id = update.effective_user.id
    if int(record["driver_user_id"]) != user_id and int(record["customer_user_id"]) != user_id and not is_admin(user_id):
        await query.message.reply_text("❌ ይህን ክትትል ማቆም አይችሉም።")
        return

    text = (
        "🛑 **የክትትል ማቆሚያ ጥያቄ**\n\n"
        f"🔑 ኮድ: {code}\n"
        f"👤 የጠየቀው: {update.effective_user.full_name}\n"
        f"🆔 ID: {user_id}\n\n"
        "ይህንን ክትትል ማቆም ይፈቀድ?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ፍቀድ (Stop)", callback_data=f"stop_approve:{code}"),
        InlineKeyboardButton("❌ ውድቅ አድርግ", callback_data=f"stop_reject:{code}")
    ]])

    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception:
            pass

    await query.message.reply_text("✅ የማቆሚያ ጥያቄዎ ለአድሚን ተልኳል። ሲፈቀድ ይነገርዎታል።")

async def admin_approve_stop(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    code = query.data.split(":")[1]
    record = db_get_tracking(code)
    
    if not record:
        await query.edit_message_text("❌ ኮዱ አልተገኘም።")
        return

    info_text = (
        f"✅ **የክትትል ማቆሚያ ጥያቄ ጸድቋል!**\n\n"
        f"🔑 ኮድ: {code}\n"
        f"👤 የባለጭነቱ ስም: {record.get('customer_name') or 'አልተመዘገበም'}\n"
        f"📞 የባለጭነቱ ስልክ: {record.get('customer_phone') or 'አልተመዘገበም'}\n"
        f"🏁 መዳረሻ: {record.get('destination') or 'አልተመዘገበም'}\n"
        f"👨‍✈️ የሹፌሩ ስም: {record.get('driver_description') or 'አልተመዘገበም'}\n"
        f"📞 የሹፌሩ ስልክ: {record.get('driver_phone') or 'አልተመዘገበም'}\n\n"
        "ክትትሉ ተዘግቷል።"
    )
    
    db_finish_tracking(code)
    await query.edit_message_text(info_text)

    for uid in [int(record['driver_user_id']), int(record['customer_user_id'])]:
        if uid != 0:
            try:
                await context.bot.send_message(chat_id=uid, text=f"🛑 የክትትል ኮድ {code} በአድሚን ተዘግቷል። ከዚህ በኋላ ቦታ አይቀበልም።")
            except Exception:
                pass

async def admin_reject_stop(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    code = query.data.split(":")[1]
    await query.edit_message_text("❌ የማቆሚያ ጥያቄው ውድቅ ተደርጓል።")

    record = db_get_tracking(code)
    if record:
        for uid in [int(record['driver_user_id']), int(record['customer_user_id'])]:
            if uid != 0:
                try:
                    await context.bot.send_message(chat_id=uid, text=f"❌ የክትትል ኮድ {code} የማቆሚያ ጥያቄ በአድሚን ውድቅ ተደርጓል።")
                except Exception:
                    pass

async def tracking_location_handler(update, context):
    code = context.user_data.get("tracking_update_code")
    if not code or not update.effective_user or not update.message or not update.message.location:
        return
    record = db_get_tracking(code)
    if not record or int(record["driver_user_id"]) != update.effective_user.id:
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("❌ የክትትል ፈቃዱ አልተረጋገጠም።")
        return
    if record["status"] == "delivered":
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("✅ ጉዞው ተዘግቷል።", reply_markup=ReplyKeyboardRemove())
        return
    loc = update.message.location
    db_update_tracking_location(code, loc.latitude, loc.longitude)
    context.user_data.pop("tracking_update_code", None)
    await update.message.reply_text(
        f"✅ የጉዞ {code} ቦታ ተመዝግቧል።\n\n📍 ኬክሮስ፦ {loc.latitude:.6f}\n📍 ኬንትሮስ፦ {loc.longitude:.6f}\nደንበኛው ይህን ቦታ በክትትል ኮዱ ማየት ይችላል።",
        reply_markup=ReplyKeyboardRemove()
    )

async def tracking_show_result(update, context, record):
    code = record["tracking_code"]
    status_labels = {"ready": "ጉዞ ለመጀመር ዝግጁ", "in_transit": "በመንገድ ላይ", "delivered": "ጉዞው ተጠናቋል"}
    msg = (
        f"📦 **TANA CARGO — የቀጥታ ክትትል**\n\n"
        f"🔑 ኮድ፦ `{code}`\n"
        f"📍 መነሻ/መረጃ፦ {record.get('origin') or 'አልተገለጸም'}\n"
        f"🏁 መድረሻ/መንገድ፦ {record.get('destination') or 'አልተገለጸም'}\n"
        f"📊 ሁኔታ፦ {status_labels.get(record.get('status'), record.get('status'))}\n"
    )
    
    loc = get_traccar_location(TRACCAR_DEVICE_ID)
    
    if loc and loc.get("latitude"):
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        map_url = f"https://maps.google.com/?q={lat},{lon}"
        msg += (
            f"\n📍 **የመጨረሻ ቦታ፦** {loc.get('address')}\n"
            f"🕒 **የተላከበት ሰዓት፦** {loc.get('timestamp')}\n"
            f"🗺️ [በካርታ ላይ ለማየት እዚህ ይጫኑ]({map_url})\n\n"
            "⚠️ ይህ የመጨረሻ የተላከ ቦታ ነው።"
        )
    elif record.get("latitude") is not None:
        lat = float(record["latitude"])
        lon = float(record["longitude"])
        map_url = f"https://maps.google.com/?q={lat},{lon}"
        msg += f"\n📍 የመጨረሻ የተላከ ቦታ፦ {lat:.6f}, {lon:.6f}\n🗺️ [በካርታ ላይ ለማየት]({map_url})"
    else:
        msg += "\n📍 ሹፌሩ እስካሁን የGPS ቦታ አልላከም።"
        
    kb = None
    if record.get('status') != 'delivered':
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 ክትትል አቁም (Stop Request)", callback_data=f"stop_req:{code}")
        ]])

    await update.effective_message.reply_text(msg, disable_web_page_preview=True, reply_markup=kb)

async def tracking_text_handler(update, context):
    if not update.effective_user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    
    if context.user_data.get("tracking_update_code") and text == "❌ ክትትሉን ሰርዝ":
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("ክትትሉ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data.get("track_new") and text == "❌ ሰርዝ":
        context.user_data.pop("track_new", None)
        await update.message.reply_text("ጉዞ መክፈቱ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
        
    if not context.user_data.get("tracking_lookup_waiting"):
        return

    if text == "❌ ክትትሉን ሰርዝ":
        context.user_data.pop("tracking_lookup_waiting", None)
        await update.message.reply_text("የክትትል ፍለጋው ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return

    code = "".join(text.upper().split())
    if len(code) < 6 or len(code) > 20:
        await update.message.reply_text("❌ የክትትል ኮዱ ትክክል አይመስልም።")
        return
    
    record = db_get_tracking(code)
    if not record:
        await update.message.reply_text("❌ ይህ የክትትል ኮድ አልተገኘም።")
        return
        
    context.user_data.pop("tracking_lookup_waiting", None)
    await tracking_show_result(update, context, record)

# ==================================================
# SHARE FULL INFORMATION
# ==================================================

async def share_full_information(index, context):
    req = connection_requests[index]
    if req.get("full_info_shared"):
        return
    if not (req.get("requester_payment_approved") and req.get("other_payment_approved")):
        return

    try:
        tracking_code = db_create_tracking_session(req, index)
        req["tracking_code"] = tracking_code
    except Exception as e:
        tracking_code = None
        print(f"TRACKING SESSION CREATE ERROR: {e}")

    req["full_info_shared"] = True
    req["status"] = "completed"

    if req.get("type") == "truck":
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
    else:
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False

    requester_id = req["requester_id"]
    owner_id = other_party_id(req)
    requester_name = req.get("requester_name", "N/A")
    requester_phone = req.get("requester_phone", "N/A")

    if req.get("type") == "truck":
        truck = truck_posts[req["truck_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የመኪና ባለቤት")
        owner_phone = truck.get("phone", "")

        requester_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ባለቤት፦ {owner_name}\n📞 ስልክ፦ {owner_phone or 'የለም'}\n"
            f"🚛 መኪና፦ {truck['type']}\n🔢 ታርጋ፦ {truck.get('plate', 'የለም')}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 ጠያቂ፦ {requester_name}\n📞 ስልክ፦ {requester_phone}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
    else:
        cargo = cargo_posts[req["cargo_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የጭነት ባለቤት")
        owner_phone = cargo.get("phone", "")

        requester_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የጭነት ባለቤት፦ {owner_name}\n📞 ስልክ፦ {owner_phone or 'የለም'}\n\n"
            f"📦 {cargo['type']}\n📍 {cargo['from']} ➡️ {cargo['to']}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ጠያቂ፦ {requester_name}\n📞 ስልክ፦ {requester_phone}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )

    for uid, msg in [(requester_id, requester_msg), (owner_id, owner_msg)]:
        if tracking_code:
            if uid == int(req.get("requester_id", 0)) and req.get("type") == "cargo":
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "🚛 እርስዎ የመኪናውን ቦታ ለመላክ በTANA CARGO ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
            elif uid == int(req.get("truck_owner_id", -1)) and req.get("type") == "truck":
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "🚛 እርስዎ የመኪናውን ቦታ ለመላክ በTANA CARGO ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
            else:
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "የመኪናውን የመጨረሻ የተላከ ቦታ ለማየት በTANA CARGO ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception:
            pass
# ==================================================
# STALE STATE CLEANER
# ==================================================

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner",
                "connect_info", "sending_admin_message",
                "tracking_update_code", "tracking_lookup_waiting", "track_new", "cust_track_phone_only"):
        if key not in keep:
            context.user_data.pop(key, None)

# ==================================================
# CONNECT FALLBACK ROUTER
# ==================================================

async def connect_fallback_router(update, context):
    info = context.user_data.get("connect_info")
    if not info:
        return

    text = update.message.text.strip().split("\n")[0].strip()
    if not text:
        return

    name = (info.get("name") or "").strip()
    phone = (info.get("phone") or "").strip()
    address = (info.get("requester_address") or "").strip()

    if not name:
        info["name"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text("2️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678")
        return

    if not phone:
        is_valid, error = validate_phone(text)
        if not is_valid:
            await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
            return
        info["phone"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር")
        return

    if not address:
        await connect_type(update, context)
        return

# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(update, context):
    if update.effective_user is None:
        return
    user_id = update.effective_user.id

    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)

    if context.user_data.get("tracking_lookup_waiting") or context.user_data.get("tracking_update_code") or context.user_data.get("track_new"):
        return await tracking_text_handler(update, context)

    if context.user_data.get("connect_info"):
        return await connect_fallback_router(update, context)

    if get_payment_index(user_id) is not None:
        return await receipt_message(update, context)

    if get_negotiation_index(user_id) is not None:
        return await submit_price(update, context)

# ==================================================
# START / PROFILE / SUPPORT / ABOUT / PAYMENT / CANCEL
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    try:
        db_save_user(user.id, user.full_name, user.username or "")
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።\n\n"
        "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦",
        reply_markup=main_menu(user.id)
    )

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})

    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user['name']}\n🔗 Username፦ @{user['username'] or 'የለም'}\n🆔 Telegram ID፦ {user['id']}\n"
    )

    if "owner" in user:
        owner = user["owner"]
        message += f"\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n👤 {owner.get('name', 'N/A')}\n📞 {owner.get('phone', 'N/A')}\n"

    if is_broker(user_id):
        message += "\n🏷️ የጭነት ደላላ: ✅ ጸድቋል\n"

    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]

    message += f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"

    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        "📞 TANA CARGO የጣና ጭነት እገዛ\n\nለእገዛ ለማግኘት @tanapage ይጠቀሙ።\n\n"
        f"☎️ {SUPPORT_PHONE}\n☎️ {SUPPORT_PHONE_2}\n\n🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text(
        "🚚 **TANA CARGO | ጣና ጭነት**\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት፣ የጭነት ማጓጓዣ ሂደትን "
        "ለማቀላጠፍ እና ቀላልና የተደራጀ አገልግሎት ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "❤️ TANA CARGO — ጭነትንና መኪናን እናገናኛለን።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu(update.effective_user.id)
    )

async def service_payment(update, context):
    await update.message.reply_text(payment_methods_text(), reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# ==================================================
# ERROR HANDLER / HEALTH SERVER / EXPIRY CLEANER
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
            await context.bot.send_message(chat_id=cargo_posts[i]["user_id"], text=f"⏰ **የጭነት ጊዜ ገደብ**\n\n📍 {cargo_posts[i]['from']} ➡️ {cargo_posts[i]['to']}\n📦 {cargo_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass

    for i in expired_trucks:
        truck_posts[i]["active"] = False
        try:
            await context.bot.send_message(chat_id=truck_posts[i]["user_id"], text=f"⏰ **የመኪና ጊዜ ገደብ**\n\n🚛 {truck_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass

# ==================================================
# MENU ROUTER & INTERRUPT
# ==================================================

MASTER_MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 የጭነት መኪና ማስመዝገብ|🚛 የጭነት መኪና መፈለግ|"
    r"👤 የኔ መረጃ|📍 የጭነት ክትትል|🤝 የግንኙነት ጥያቄዎች|📋 የጭነት ደላሎች መዝገብ|"
    r"🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ|📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ|"
    r"💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"
)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id

    context.user_data["subscribed"] = True
    clear_stale_user_state(context)

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        return await find_truck(update, context)
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "📍 የጭነት ክትትል":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ":
        await show_broker_registry(update, context)
        return ConversationHandler.END
    if text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ":
        return await broker_start(update, context)
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await _start_admin_message_mode(update, context)
        return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END

    return ConversationHandler.END

async def menu_interrupt(update, context):
    clear_stale_user_state(context)
    text = update.message.text

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        return await find_truck(update, context)
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "📍 የጭነት ክትትል":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ":
        await show_broker_registry(update, context)
        return ConversationHandler.END
    if text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ":
        return await broker_start(update, context)
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await _start_admin_message_mode(update, context)
        return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END

    return ConversationHandler.END

# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    start_health_server()

    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    application.add_error_handler(error_handler)

    # Simple commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cargo", cargo_start))
    application.add_handler(CommandHandler("truck", truck_start))
    application.add_handler(CommandHandler("findcargo", find_cargo))
    application.add_handler(CommandHandler("findtruck", find_truck))
    application.add_handler(CommandHandler("myprofile", profile))
    application.add_handler(CommandHandler("support", support_start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("owner", owner_start))
    application.add_handler(CommandHandler("broker", broker_start))
    application.add_handler(CommandHandler("track", tracking_menu))

    # Master Conversation
    master_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የጭነት ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^📋 የጭነት ደላሎች መዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),
            CommandHandler("owner", owner_start),
            CommandHandler("broker", broker_start),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"),
            CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"),
            CallbackQueryHandler(tracking_new_start, pattern=r"^tracking_driver_new$"),
            CallbackQueryHandler(customer_track_start, pattern=r"^tracking_customer_new$"),
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone),
            ],
            CARGO_PRICE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price),
            ],
            
            # 🔴 አዲስ የተቀየሩት የመኪና ስቴቶች
            TRUCK_ALL_INFO: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_all_info),
            ],
            FIND_TRUCK_QUERY: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, find_truck_query),
            ],
            
            OWNER_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name),
            ],
            OWNER_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, owner_phone),
            ],
            BROKER_NAME: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name),
            ],
            BROKER_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broker_phone),
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone),
            ],
            CONNECT_TYPE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type),
            ],
            TRACK_REQ_PHONE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_phone),
            ],
            TRACK_REQ_ROUTE: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_route),
            ],
            TRACK_REQ_LOCATION: [
                MessageHandler(filters.LOCATION, tracking_req_location),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_text_handler),
            ],
            CUST_TRACK_PHONE_ONLY: [
                MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cust_track_phone_only),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )
    application.add_handler(master_conversation)

    # Tracking callbacks
    application.add_handler(CallbackQueryHandler(tracking_driver_choose, pattern=r"^tracking_driver$"))
    application.add_handler(CallbackQueryHandler(tracking_customer_choose, pattern=r"^tracking_customer$"))
    application.add_handler(CallbackQueryHandler(tracking_begin_location, pattern=r"^tracking_update:[A-Z0-9]+$"))
    
    application.add_handler(CallbackQueryHandler(request_stop_tracking, pattern=r"^stop_req:[A-Z0-9]+$"))
    application.add_handler(CallbackQueryHandler(admin_approve_stop, pattern=r"^stop_approve:[A-Z0-9]+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_stop, pattern=r"^stop_reject:[A-Z0-9]+$"))
    
    application.add_handler(CallbackQueryHandler(admin_approve_tracking, pattern=r"^adminapprovetrack_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_tracking, pattern=r"^adminrejecttrack_\d+$"))
    
    application.add_handler(MessageHandler(filters.LOCATION, tracking_location_handler), group=1)

    # Connection callbacks
    application.add_handler(CallbackQueryHandler(already_selected_callback, pattern=r"^already_selected$"))

    # Subscribe check
    application.add_handler(CallbackQueryHandler(subscribed_check_callback, pattern=r"^subscribed_check$"))

    # Broker callbacks
    application.add_handler(CallbackQueryHandler(broker_right_confirm, pattern=r"^brokerright_\d+$"))
    application.add_handler(CallbackQueryHandler(broker_x_delete, pattern=r"^brokerxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_broker, pattern=r"^admindeletebroker_\d+$"))

    # Admin right/X/Clear
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_clear_notification, pattern=r"^adminclear_\d+$"))

    # Negotiation
    application.add_handler(CallbackQueryHandler(agree_price, pattern=r"^agreeprice_\d+_\d+$"))
    application.add_handler(CallbackQueryHandler(reject_price_button, pattern=r"^rejectprice_\d+_\d+$"))

    # Admin payment
    application.add_handler(CallbackQueryHandler(
        admin_payment_action,
        pattern=r"^admin(approve|reject)_\d+_(requester|other)_\d+$"
    ))

    # Admin delete connection
    application.add_handler(CallbackQueryHandler(
        admin_delete_connection,
        pattern=r"^admindeleteconnection_\d+$"
    ))

    # Photo/Receipt
    application.add_handler(MessageHandler(filters.PHOTO, receipt_photo))
    application.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND, receipt_photo
    ))

    # Text router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Job queue
    if application.job_queue:
        application.job_queue.run_repeating(clean_expired_posts, interval=3600, first=60)
        application.job_queue.run_repeating(auto_delete_messages, interval=60, first=30)
        print("✅ Expiry cleaner + Auto-delete started")

    print("TANA CARGO Bot is starting...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
