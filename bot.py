import os
import threading
import sqlite3
import asyncio
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

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

NEGOTIATION_TIMEOUT_SECONDS = 300
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
    """Gregorian date on which the given Ethiopian year begins."""
    gc_year = et_year + 7
    start_day = 12 if is_gregorian_leap_year(gc_year + 1) else 11
    return datetime(gc_year, 9, start_day)


def et_to_gc(et_year, et_month, et_day):
    max_day = 6 if et_month == 13 and is_ethiopian_leap_year(et_year) else 5 if et_month == 13 else 30
    if et_month < 1 or et_month > 13 or et_day < 1 or et_day > max_day:
        raise ValueError("Invalid Ethiopian date")
    return et_new_year_gregorian(et_year) + timedelta(days=(et_month - 1) * 30 + et_day - 1)


def gc_to_et_date(gc_date):
    """Convert a Gregorian date object to an Ethiopian (year, month, day) tuple."""
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
    """Parse DD/MM/YYYY into numeric parts; actual validation depends on chosen calendar."""
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
# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
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
            ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
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

# ==================================================
# MESSAGE AUTO-DELETE
# ==================================================

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

# ==================================================
# DUPLICATE CHECK
# ==================================================

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
# SUBSCRIBE GATE
# ==================================================

async def check_subscription(user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}",
            user_id=user_id
        )
        if member.status in ["member", "administrator", "creator"]:
            return True
        return False
    except Exception as e:
        print(f"SUBSCRIBE CHECK ERROR: {e}")
        return True

async def subscribed_check_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id

    is_subscribed = await check_subscription(user_id, context)

    if is_subscribed:
        context.user_data["subscribed"] = True
        await query.answer(
            "✅ አመሰግናለሁ! Subscribe ማድረግዎን አረጋግጠናል።",
            show_alert=True
        )
        try:
            await query.edit_message_text(
                "✅ **አመሰግናለሁ!**\n\n"
                "Subscribe ማድረግዎን አረጋግጠናል።\n\n"
                "👉 አሁን የፈለጉትን ምናሌ በመንካት ይቀጥሉ!"
            )
        except Exception:
            pass

        await context.bot.send_message(
            chat_id=user_id,
            text="👇 አሁን የፈለጉትን ምናሌ ይንኩ፦",
            reply_markup=main_menu(user_id)
        )
    else:
        await query.answer(
            "❌ እስካሁን @tanapage Subscribe አላደረጉም።\n\n"
            "እባክዎ በመጀመሪያ Subscribe ያድርጉ፣ ከዚያ ይህን ይጫኑ።",
            show_alert=True
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
        "🚚 ጭነት መለጠፍ\n\n"
        "1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\n"
        "ምሳሌ፦ ባህር ዳር"
    )
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text(
        "3️⃣ የጭነቱን አይነት ይጻፉ።\n"
        "ምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ"
    )
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
        "በቶን፣ በቢያጆ፣ በኩንታል\n"
        "ምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
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
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።",
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
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
    context.user_data["truck"] = {}
    await update.message.reply_text(
        "🚛 መኪና ማስመዝገብ\n\n"
        "1️⃣ የመኪናውን አይነት ይጻፉ።\n"
        "ምሳሌ፦ Isuzu / ካሶኒ / ኦባማ"
    )
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ የመኪናውን ታርጋ ይጻፉ።\nቁጥር ብቻ መሆን አለበት።\nምሳሌ፦ 12345"
    )
    return TRUCK_PLATE

async def truck_plate(update, context):
    plate = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_plate(plate)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nእባክዎ እንደገና ይጻፉ።\nምሳሌ፦ 12345")
        return TRUCK_PLATE
    context.user_data["truck"]["plate"] = plate
    await update.message.reply_text(
        "3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።\n"
        "በቶን፣ በኩንታል፣ በቢያጆ\n"
        "ምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ"
    )
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    capacity = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_weight(capacity)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
        return TRUCK_CAPACITY
    context.user_data["truck"]["capacity"] = capacity
    await update.message.reply_text(
        "4️⃣ የመንገድ ዝርዝር ይጻፉ።\n\n"
        "እባክዎ የሚጓዙበትን መንገድ ዓይነት ይምረጡ፦",
        reply_markup=route_choice_keyboard()
    )
    return TRUCK_ROUTE

async def truck_route_choice(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "route_anywhere":
        context.user_data["truck"]["route"] = "በማንኛውም ቦታ"
        context.user_data["truck"]["route_type"] = "anywhere"
        await query.edit_message_text(
            "✅ **በማንኛውም ቦታ** ተመርጧል።\n\n5️⃣ የመኪናውን አድራሻ ይጻፉ።"
        )
        return TRUCK_ADDRESS
    elif query.data == "route_custom":
        context.user_data["truck"]["route_type"] = "custom"
        await query.edit_message_text(
            "📍 **በርሶ ምርጫ** ተመርጧል።\n\n"
            "እባክዎ ምርጫዎትን ይግለጹ።\n"
            "ለምሳሌ፦ ጅማ → ባህርዳር → አዲስ አበባ"
        )
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
    await update.message.reply_text(
        "7️⃣ የስልክ ቁጥር ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return TRUCK_PHONE

async def truck_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nእባክዎ እንደገና ይጻፉ።\nምሳሌ፦ 0912345678")
        return TRUCK_PHONE

    truck = context.user_data["truck"]
    truck["phone"] = phone
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck["timestamp"] = datetime.now().isoformat()
    truck["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"

    expiry_date = datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)
    truck["expiry_date"] = expiry_date.isoformat()

    if truck["poster_type"] == "broker":
        if is_duplicate_broker_post(truck, is_truck=True,
                                     broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን መኪና ሌላ ደላላ (broker) አስመዝግቦታል።**\n\n"
                "እባክዎ ሌላ የጭነት መኪና ካለዎት ያስመዝግቡ።"
            )
            return TRUCK_PHONE

    truck_posts.append(truck.copy())
    try:
        db_save_truck(truck)
    except Exception as e:
        print(f"DB SAVE TRUCK ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    route_display = truck.get("route", "በማንኛውም ቦታ")
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"🛣️ መንገድ፦ {route_display}\n"
        f"📍 አድራሻ፦ {truck['address']}\n"
        f"👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።",
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
                        f"🛣️ መንገድ፦ {route_display}\n"
                        f"📍 አድራሻ፦ {truck['address']}\n\n"
                        "🚛 መኪና መፈለግን ይጫኑ።"
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
        "🆕 **የጭነት ደላላ ምዝገባ**\n\n"
        "1️⃣ ሙሉ ስምዎን ይጻፉ።"
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
        "3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር"
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
            InlineKeyboardButton(
                "✅ Right",
                callback_data=f"brokerright_{broker_idx}"
            ),
            InlineKeyboardButton(
                "❌ X አጥፋ",
                callback_data=f"brokerxdelete_{broker_idx}"
            ),
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

    await query.edit_message_text(
        "✅ **የጭነት ደላላው ጸድቋል!**\n\n"
        "ለደላላው ማሳወቂያ ተልኳል።"
    )

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
                "ሚያገኙት ኮሚሽን ላይ **5% ብቻ** "
                "ለጣና ጭነት ይከፍላሉ።\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🙏 እኛን ስለመረጡ እናመሰግናለን!"
            )
            await context.bot.send_message(
                chat_id=broker_user_id,
                text=approval_text
            )
        except Exception:
            pass

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
            await context.bot.send_message(
                chat_id=broker_user_id,
                text=rejection_text
            )
        except Exception:
            pass

async def show_broker_registry(update, context):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ Admin ብቻ ይህን ማየት ይችላል።"
        )
        return

    if not brokers:
        await update.message.reply_text(
            "📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።"
        )
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
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await update.message.reply_text(text)

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

    await query.edit_message_text(
        "✅ **የጭነት ደላላው ተሰርዟል።**"
    )

# ==================================================
# FIND CARGO & TRUCK
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
                    f"👉 ጭነት {cargo_id} ን መርጠዋል ይጫኑ",
                    callback_data=f"connect_{real_index}"
                )
            ]])

        await update.message.reply_text(msg, reply_markup=kb)

    await update.message.reply_text(
        f"📋 ጠቅላላ {len(active_cargos)} ጭነቶች ተገኝተዋል።"
    )

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
            f"🛣️ መንገድ፦ {route_display}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ መኪና {truck_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"👉 መኪና {truck_id} ን መርጠዋል ይጫኑ",
                    callback_data=f"truckconnect_{real_index}"
                )
            ]])

        await update.message.reply_text(msg, reply_markup=kb)

    await update.message.reply_text(
        f"📋 ጠቅላላ {len(active_trucks)} መኪኖች ተገኝተዋል።"
    )

async def already_selected_callback(update, context):
    query = update.callback_query
    await query.answer(
        "❌ ይቅርታ የመረጡት ጭነት/የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
        "እባክዎ ሌላ ተስማሚ ይምረጡ።",
        show_alert=True
    )
# ==================================================
# CONNECTION REQUESTS & NEGOTIATION
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
        await query.answer(
            "❌ ይቅርታ የመረጡት ጭነት በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
            "እባክዎ ሌላ ተስማሚ ጭነት ይምረጡ።",
            show_alert=True
        )
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

    await query.edit_message_text(
        "📝 **መረጃዎን ያስገቡ**\n\n"
        "1️⃣ ሙሉ ስምዎን ይጻፉ።"
    )
    return CONNECT_NAME

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
        await query.answer(
            "❌ ይቅርታ የመረጡት የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
            "እባክዎ ሌላ ተስማሚ መኪና ይምረጡ።",
            show_alert=True
        )
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

    await query.edit_message_text(
        "📝 **መረጃዎን ያስገቡ**\n\n"
        "1️⃣ ሙሉ ስምዎን ይጻፉ።"
    )
    return CONNECT_NAME

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

    if info.get("type") == "cargo":
        await update.message.reply_text(
            "3️⃣ ጭነቱን ለማጓጓዝ የሚጠቀሙበትን **የመኪና አይነት** ይጻፉ።\n"
            "ምሳሌ፦ Isuzu / ካሶኒ / ኦባማ / ተሳቢ"
        )
    else:
        await update.message.reply_text(
            "3️⃣ **የጭነት አይነት** እና **የጭነት አድራሻ** ይጻፉ።\n"
            "ምሳሌ፦ ሲሚንቶ ባህርዳር"
        )
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip().split("\n")[0].strip()

    if info.get("type") == "cargo":
        info["truck_type"] = text
    else:
        info["cargo_info"] = text

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
            "requester_truck_type": info.get("truck_type", ""),
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
            "requester_cargo_info": info.get("cargo_info", ""),
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

    connection_requests.append(request)
    req_index = len(connection_requests) - 1
    context.user_data.pop("connect_info", None)

    await update.message.reply_text(
        "✅ **እናመሰግናለን!**\n\n"
        "📌 ምዝገባዎን ጨርሰዋል።\n\n"
        "⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
        "🙏 እናመሰግናለን!",
        reply_markup=main_menu(requester.id)
    )

    await send_admin_notification(context, req_index)

    return ConversationHandler.END

async def send_admin_notification(context, req_index):
    req = connection_requests[req_index]
    if req.get("admin_notified"):
        return
    req["admin_notified"] = True

    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n"
            f"📍 መነሻ: {cargo['from']} ➡️ መድረሻ: {cargo['to']}\n"
            f"👤 የጭነት ባለቤት: {cargo_owner_name} ({cargo.get('phone', 'N/A')})\n"
            f"👤 ጠያቂ: {req.get('requester_name', 'N/A')} ({req.get('requester_phone', 'N/A')})"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n"
            f"🚛 መኪና: {truck['type']} (ታርጋ: {truck.get('plate', 'N/A')})\n"
            f"👤 የመኪና ባለቤት: {truck_owner_name} ({truck.get('phone', 'N/A')})\n"
            f"👤 ጠያቂ: {req.get('requester_name', 'N/A')} ({req.get('requester_phone', 'N/A')})"
        )

    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text)
        except Exception:
            pass

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
    req["admin_right_confirmed"] = True
    req["status"] = "negotiating"
    await query.edit_message_text("✅ **Right ተረጋግጧል!** ድርድር ተጀምሯል።")

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(connection_requests):
        return
    connection_requests[index]["status"] = "deleted"
    await query.edit_message_text("❌ **ተሰርዟል!**")

async def admin_clear_notification(update, context):
    query = update.callback_query
    await query.answer()
    try:
        await query.delete_message()
    except Exception:
        pass

async def show_connection_requests(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\nማንኛውም መረጃ ወይም ፎቶ ማስተላለፍ ይችላሉ።",
            reply_markup=main_menu(user_id)
        )
        context.user_data["sending_admin_message"] = True
        return

    received = [req for req in connection_requests if req["status"] not in ["deleted"]]
    if not received:
        await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።", reply_markup=main_menu(user_id))
        return

    for i, req in enumerate(connection_requests):
        if req["status"] == "deleted":
            continue
        text = f"📥 **ጥያቄ #{i + 1}**\n📌 ሁኔታ: {req['status']}"
        buttons = [[
            InlineKeyboardButton("✅ Right", callback_data=f"adminright_{i}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{i}")
        ]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def admin_delete_connection(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        return
    index = int(query.data.split("_")[-1])
    if 0 <= index < len(connection_requests):
        connection_requests[index]["status"] = "deleted"
        await query.edit_message_text("✅ ተሰርዟል።")

async def handle_user_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    name = update.effective_user.full_name
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📨 **TANA CARGO — አዲስ መልዕክት**\n👤 {name} ({user_id})\n💬 {text}"
            )
        except Exception:
            pass
    await update.message.reply_text("✅ መልዕክትዎ ለ Admin ተልኳል!", reply_markup=main_menu(user_id))

async def handle_user_photo(update, context):
    user_id = update.effective_user.id
    name = update.effective_user.full_name
    file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=f"📸 **ፎቶ ከ {name} ({user_id})**")
        except Exception:
            pass
    await update.message.reply_text("✅ ፎቶዎ ለ Admin ተልኳል!", reply_markup=main_menu(user_id))

async def _start_admin_message_mode(update, context):
    user_id = update.effective_user.id
    await update.message.reply_text("📨 መረጃ ወይም ፎቶ ይላኩ።", reply_markup=main_menu(user_id))
    context.user_data["sending_admin_message"] = True

# ==================================================
# TRACKING MENU & GPS ROUTER
# ==================================================

async def tracking_menu(update, context):
    context.user_data.pop("tracking_update_code", None)
    context.user_data.pop("tracking_lookup_waiting", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚛 የመኪና ቦታ ላክ (ሹፌር)", callback_data="tracking_driver")],
        [InlineKeyboardButton("📦 ጭነቴን በኮድ መከታተል", callback_data="tracking_customer")],
    ])
    await update.effective_message.reply_text(
        "📍 **TANA CARGO — የጭነት ክትትል**\n\n"
        "🚛 ሹፌር፦ የተፈቀደ ጉዞ ይምረጡና ቦታ ይላኩ።\n"
        "📦 ደንበኛ፦ የክትትል ኮድ ያስገቡ።",
        reply_markup=keyboard
    )

async def tracking_driver_choose(update, context):
    query = update.callback_query
    await query.answer()
    jobs = db_get_driver_tracking(query.from_user.id)
    if not jobs:
        await query.message.reply_text("🚛 ንቁ የክትትል ጉዞ አልተገኘም።")
        return
    buttons = []
    for job in jobs[:20]:
        route = f"{job.get('origin', '')} ➡️ {job.get('destination', '')}"
        buttons.append([InlineKeyboardButton(f"📍 {job['tracking_code']} — {route}"[:60], callback_data=f"tracking_update:{job['tracking_code']}")])
        buttons.append([InlineKeyboardButton(f"🏁 {job['tracking_code']} — ጉዞ አቁም", callback_data=f"tracking_done:{job['tracking_code']}")])
    await query.message.reply_text("ቦታ ለመላክ የጉዞ ኮድ ይምረጡ፦", reply_markup=InlineKeyboardMarkup(buttons))

async def tracking_customer_choose(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_lookup_waiting"] = True
    await query.message.reply_text("📦 የተሰጠዎትን የክትትል ኮድ ያስገቡ (ምሳሌ፦ TC1A2B3C)")

async def tracking_begin_location(update, context):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1].upper()
    record = db_get_tracking(code)
    if not record or int(record["driver_user_id"]) != query.from_user.id:
        await query.message.reply_text("❌ ፈቃድ የለዎትም።")
        return
    context.user_data["tracking_update_code"] = code
    await query.message.reply_text(
        f"📍 ለጉዞ **{code}** የአሁኑን ቦታ ይላኩ።",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)],
             [KeyboardButton("❌ ክትትሉን ሰርዝ")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )

async def tracking_finish_trip(update, context):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1].upper()
    db_finish_tracking(code)
    await query.message.reply_text(f"🏁 ጉዞ {code} ተጠናቋል።")

async def tracking_location_handler(update, context):
    code = context.user_data.get("tracking_update_code")
    if not code or not update.message or not update.message.location:
        return
    loc = update.message.location
    db_update_tracking_location(code, loc.latitude, loc.longitude)
    context.user_data.pop("tracking_update_code", None)
    await update.message.reply_text(
        f"✅ የጉዞ {code} ቦታ ተመዝግቧል!\n📍 ኬክሮስ፦ {loc.latitude:.6f}, ኬንትሮስ፦ {loc.longitude:.6f}",
        reply_markup=ReplyKeyboardRemove()
    )

async def tracking_show_result(update, context, record):
    code = record["tracking_code"]
    lat = record.get("latitude")
    lon = record.get("longitude")
    msg = f"📦 **የጭነት ክትትል**\n🔑 ኮድ፦ `{code}`\n📍 ሁኔታ፦ {record.get('status')}"
    if lat is not None and lon is not None:
        msg += f"\n📍 ቦታ፦ {lat}, {lon}\n🗺️ Google Maps: https://maps.google.com/?q={lat},{lon}"
    await update.effective_message.reply_text(msg, disable_web_page_preview=True)

async def tracking_text_handler(update, context):
    text = update.message.text.strip()
    if context.user_data.get("tracking_update_code") and text == "❌ ክትትሉን ሰርዝ":
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
    if not context.user_data.get("tracking_lookup_waiting"):
        return
    code = "".join(text.upper().split())
    record = db_get_tracking(code)
    if not record:
        await update.message.reply_text("❌ ኮዱ አልተገኘም።")
        return
    context.user_data.pop("tracking_lookup_waiting", None)
    await tracking_show_result(update, context, record)

async def share_full_information(index, context):
    req = connection_requests[index]
    if req.get("full_info_shared"):
        return
    try:
        tracking_code = db_create_tracking_session(req, index)
        req["tracking_code"] = tracking_code
    except Exception:
        tracking_code = None

    req["full_info_shared"] = True
    req["status"] = "completed"
    requester_id = req["requester_id"]
    owner_id = other_party_id(req)

    for uid in [requester_id, owner_id]:
        msg = "✅ **ግንኙነት ተሳክቷል!** መረጃው ተከፍቷል።"
        if tracking_code:
            msg += f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**"
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception:
            pass

async def text_router(update, context):
    user_id = update.effective_user.id
    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)

# ==================================================
# BASIC COMMANDS
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    try:
        db_save_user(user.id, user.full_name, user.username or "")
    except Exception:
        pass

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "📢 እባክዎ መጀመሪያ **@tanapage** ቻናል **Subscribe** ያድርጉ!",
        reply_markup=subscribe_keyboard()
    )

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})
    await update.message.reply_text(f"👤 ስም፦ {user['name']}\n🆔 ID፦ {user['id']}", reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(f"📞 Support: {SUPPORT_PHONE} / {SUPPORT_PHONE_2}", reply_markup=main_menu(update.effective_user.id))

async def about(update, context):
    await update.message.reply_text("🚚 TANA CARGO — ጭነትንና መኪናን እናገናኛለን።", reply_markup=main_menu(update.effective_user.id))

async def service_payment(update, context):
    await update.message.reply_text(payment_methods_text(), reply_markup=main_menu(update.effective_user.id))

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"ERROR: {context.error}")

# ==================================================
# HEALTH SERVER & GPS HOOK
# ==================================================

class HealthHandler(BaseHTTPRequestHandler):
    def _reply(self, status, message):
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/health"]:
            return self._reply(200, "TANA CARGO is running")
        if parsed.path != "/gps":
            return self._reply(404, "Not found")
        params = parse_qs(parsed.query)
        device_id = params.get("id", [""])[0].strip()
        try:
            lat = float(params.get("lat", [""])[0])
            lon = float(params.get("lon", [""])[0])
        except (TypeError, ValueError):
            return self._reply(400, "Invalid lat/lon")
        
        raw_map = os.getenv("GPS_DEVICE_MAP", "")
        device_map = {}
        for pair in raw_map.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                device_map[k.strip()] = v.strip().upper()
        tracking_code = device_map.get(device_id)
        if not tracking_code:
            return self._reply(403, "Unknown device")
        db_update_tracking_location(tracking_code, lat, lon)
        return self._reply(200, "OK")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Health server running on port {port}")

# ==================================================
# MENU ROUTER & MAIN
# ==================================================

MASTER_MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
    r"👤 የኔ መረጃ|📍 የጭነት ክትትል|🤝 የግንኙነት ጥያቄዎች|📋 የጭነት ደላሎች መዝገብ|"
    r"🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ|📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ|"
    r"💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"
)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    if not await check_subscription(uid, context):
        await update.message.reply_text("📢 መጀመሪያ @tanapage Subscribe ያድርጉ!", reply_markup=subscribe_keyboard())
        return ConversationHandler.END

    if text == "🚚 ጭነት መለጠፍ": return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ": await find_cargo(update, context); return ConversationHandler.END
    if text == "🚛 መኪና ማስመዝገብ": return await truck_start(update, context)
    if text == "🚛 መኪና መፈለግ": await find_truck(update, context); return ConversationHandler.END
    if text == "👤 የኔ መረጃ": await profile(update, context); return ConversationHandler.END
    if text == "📍 የጭነት ክትትል": await tracking_menu(update, context); return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች": await show_connection_requests(update, context); return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ": await show_broker_registry(update, context); return ConversationHandler.END
    if text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ": return await broker_start(update, context)
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ": await _start_admin_message_mode(update, context); return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም": await service_payment(update, context); return ConversationHandler.END
    if text == "📞 Support": await support_start(update, context); return ConversationHandler.END
    if text == "ℹ️ About": await about(update, context); return ConversationHandler.END
    return ConversationHandler.END

async def menu_interrupt(update, context):
    return await menu_router(update, context)

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    init_db()

    application = Application.builder().token(TOKEN).build()
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cargo", cargo_start))
    application.add_handler(CommandHandler("truck", truck_start))
    application.add_handler(CommandHandler("track", tracking_menu))

    master_conversation = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_router)],
        states={
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_CALENDAR: [CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ROUTE: [CallbackQueryHandler(truck_route_choice, pattern=r"^route_"), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_route)],
            TRUCK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
            BROKER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name)],
            BROKER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broker_phone)],
            BROKER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, broker_address)],
            CONNECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(master_conversation)

    application.add_handler(CallbackQueryHandler(tracking_driver_choose, pattern=r"^tracking_driver$"))
    application.add_handler(CallbackQueryHandler(tracking_customer_choose, pattern=r"^tracking_customer$"))
    application.add_handler(CallbackQueryHandler(tracking_begin_location, pattern=r"^tracking_update:[A-Z0-9]+$"))
    application.add_handler(CallbackQueryHandler(tracking_finish_trip, pattern=r"^tracking_done:[A-Z0-9]+$"))
    application.add_handler(MessageHandler(filters.LOCATION, tracking_location_handler), group=1)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_text_handler), group=1)

    application.add_handler(CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"))
    application.add_handler(CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"))
    application.add_handler(CallbackQueryHandler(already_selected_callback, pattern=r"^already_selected$"))
    application.add_handler(CallbackQueryHandler(subscribed_check_callback, pattern=r"^subscribed_check$"))
    application.add_handler(CallbackQueryHandler(broker_right_confirm, pattern=r"^brokerright_\d+$"))
    application.add_handler(CallbackQueryHandler(broker_x_delete, pattern=r"^brokerxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_\d+$"))
    application.add_handler(CallbackCodeQuery := CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_connection, pattern=r"^admindeleteconnection_\d+$"))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_user_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("TANA CARGO Bot is starting...")
    start_health_server()
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
