import os
import threading
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)


# ==================================================
# CONFIG
# ==================================================

TOKEN = os.getenv("BOT_TOKEN")

SUPPORT_PHONE = "0960011010"
SUPPORT_PHONE_2 = "0912991128"
SUPPORT_USERNAME = "@tanapage"
SUPPORT_URL = "https://t.me/tanapage"
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

CBE_ACCOUNT = os.getenv("CBE_ACCOUNT", "")
ABAY_ACCOUNT = os.getenv("ABAY_ACCOUNT", "")
CBE_BIRR = os.getenv("CBE_BIRR", "")
TELEBIRR = os.getenv("TELEBIRR", "")

NEGOTIATION_TIMEOUT_SECONDS = 300
CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72


# ==================================================
# DATABASE
# ==================================================

DB_PATH = "tana_cargo.db"


def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            owner_name TEXT,
            owner_phone TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cargo_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            from_location TEXT,
            to_location TEXT,
            cargo_type TEXT,
            vehicle TEXT,
            size INTEGER,
            size_name TEXT,
            weight TEXT,
            date_et TEXT,
            date_gc TEXT,
            phone TEXT,
            price REAL,
            price_display TEXT,
            price_status TEXT,
            active INTEGER DEFAULT 1,
            timestamp TEXT,
            expiry_date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS truck_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            truck_type TEXT,
            plate TEXT,
            capacity TEXT,
            route TEXT,
            address TEXT,
            driver TEXT,
            phone TEXT,
            active INTEGER DEFAULT 1,
            timestamp TEXT,
            expiry_date TEXT
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
            name = excluded.name,
            username = excluded.username,
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
            price_display, price_status, active, timestamp, expiry_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cargo["user_id"], cargo["from"], cargo["to"], cargo["type"],
        cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"],
        cargo.get("date_et", ""), cargo.get("date_gc", ""), cargo["phone"],
        cargo["price"], cargo["price_display"], cargo["price_status"],
        1, cargo["timestamp"], cargo.get("expiry_date", "")
    ))
    conn.commit()
    conn.close()


def db_save_truck(truck):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO truck_posts (
            user_id, truck_type, plate, capacity, route, address,
            driver, phone, active, timestamp, expiry_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        truck["user_id"], truck["type"], truck["plate"], truck["capacity"],
        truck["route"], truck["address"], truck["driver"], truck["phone"],
        1, truck["timestamp"], truck.get("expiry_date", "")
    ))
    conn.commit()
    conn.close()


# ==================================================
# MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []
negotiation_timeouts = {}


# ==================================================
# STATES
# ==================================================

(
    CARGO_FROM,
    CARGO_TO,
    CARGO_TYPE,
    CARGO_VEHICLE,
    CARGO_SIZE,
    CARGO_WEIGHT,
    CARGO_CALENDAR,
    CARGO_DATE,
    CARGO_PHONE,
    CARGO_PRICE,
) = range(10)

(
    TRUCK_TYPE,
    TRUCK_PLATE,
    TRUCK_CAPACITY,
    TRUCK_ROUTE,
    TRUCK_ADDRESS,
    TRUCK_DRIVER,
    TRUCK_PHONE,
) = range(10, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)


# ==================================================
# MAIN MENU
# ==================================================

def main_menu():
    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
        ["👤 የኔ መረጃ"],
        ["🤝 ግንኙነት ጥያቄዎች"],
        ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
        ["📞 Support", "ℹ️ About"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ==================================================
# KEYBOARDS
# ==================================================

def size_keyboard():
    keyboard = [
        [InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")],
        [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")],
        [InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25")],
    ]
    return InlineKeyboardMarkup(keyboard)


def vehicle_keyboard(prefix="vehicle"):
    keyboard = [
        [InlineKeyboardButton("🚛 ተሳቢ", callback_data=f"{prefix}_ተሳቢ")],
        [InlineKeyboardButton("🚛 ካሶኒ", callback_data=f"{prefix}_ካሶኒ")],
        [InlineKeyboardButton("🚛 ኦባማ", callback_data=f"{prefix}_ኦባማ")],
        [InlineKeyboardButton("🚛 Isuzu", callback_data=f"{prefix}_isuzu")],
        [InlineKeyboardButton("✍️ ሌላ", callback_data=f"{prefix}_other")],
    ]
    return InlineKeyboardMarkup(keyboard)


def calendar_keyboard():
    keyboard = [
        [InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (ET)", callback_data="cal_et")],
        [InlineKeyboardButton("🌍 የግሪጎሪያን ካላንደር (GC)", callback_data="cal_gc")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================================================
# HELPERS
# ==================================================

def normalize(text):
    if text is None:
        return ""
    return str(text).strip().lower().replace(" ", "")


def route_points(text):
    if not text:
        return []
    separators = [",", "→", ">", "/", "፣"]
    for sep in separators:
        text = text.replace(sep, ",")
    return [normalize(x) for x in text.split(",") if x.strip()]


def route_match(truck_from, truck_route, cargo_from, cargo_to):
    truck_start = normalize(truck_from)
    cargo_start = normalize(cargo_from)
    cargo_end = normalize(cargo_to)
    points = [truck_start] + route_points(truck_route)
    if cargo_start not in points:
        return False
    if cargo_end not in points:
        return False
    start_index = points.index(cargo_start)
    end_index = points.index(cargo_end)
    return start_index < end_index


def vehicle_match(truck_type, requested_vehicle):
    if not requested_vehicle:
        return True
    return normalize(truck_type) == normalize(requested_vehicle)


def commission_amount(price):
    price = float(price)
    each_side = price * 0.01
    total = each_side * 2
    return each_side, total


def payment_methods_text():
    return (
        "💳 የአገልግሎት ክፍያ መረጃ\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000031098231\n"
        "👤 Solomon Sisay\n\n"

        "📱 Telebirr\n"
        "📱 0918132914\n"
        "👤 Solomon Sisay\n\n"

        "💰 CBE Birr\n"
        "📱 0918132914\n"
        "👤 Solomon Sisay\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000139288697\n"
        "👤 Melak Gebeyhu\n\n"

        "📱 Telebirr\n"
        "📱 0918161179\n"
        "👤 Melak Gebeyhu\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "📌 ክፍያ ካደረጉ በኋላ ደረሰኙን ወይም "
        "ስክሪንሾቱን ወደ @tanapage ይላኩ።\n\n"

        "🙏 እኛን ስለመረጡን እናመሰግናለን!"
    )


def other_party_id(req):
    if req.get("type") == "truck":
        return req["truck_owner_id"]
    return req["cargo_owner_id"]


def other_party_name(req):
    if req.get("type") == "truck":
        user = users.get(req["truck_owner_id"], {})
        return user.get("name", "የመኪና ባለቤት")
    user = users.get(req["cargo_owner_id"], {})
    return user.get("name", "የጭነት ባለቤት")


def get_user_phone(user_id):
    user = users.get(user_id, {})
    if user.get("owner", {}).get("phone"):
        return user["owner"]["phone"]
    for cargo in reversed(cargo_posts):
        if cargo["user_id"] == user_id:
            return cargo.get("phone", "")
    for truck in reversed(truck_posts):
        if truck["user_id"] == user_id:
            return truck.get("phone", "")
    return ""


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
    valid_units = ["ቶን", "ቢያጆ", "ኩንታል"]
    if not any(unit in weight for unit in valid_units):
        return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""


def convert_calendar(date_text, calendar):
    try:
        date_obj = datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError:
        return None, None, "የቀን ቅርጸቱ ትክክል አይደለም። ቀን/ወር/ዓመት ይጠቀሙ።"

    if calendar == "ET":
        gc_year = date_obj.year + 7
        gc_month = date_obj.month + 8
        if gc_month > 12:
            gc_month -= 12
            gc_year += 1
        gc_date = f"{date_obj.day:02d}/{gc_month:02d}/{gc_year}"
        et_date = date_text
    else:
        et_year = date_obj.year - 7
        et_month = date_obj.month - 8
        if et_month <= 0:
            et_month += 12
            et_year -= 1
        et_date = f"{date_obj.day:02d}/{et_month:02d}/{et_year}"
        gc_date = date_text

    return et_date, gc_date, ""


def is_admin(user_id):
    return bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID)


def agreement_success_text(price, each_side):
    return (
        "🎉 እንኳን ደስ ያላችሁ! 🎉\n\n"
        "🤝 ሁለታችሁም ተስማምታችኋል!\n"
        "✅ ስምምነት ላይ ደርሳችኋል።\n\n"
        f"💰 የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💳 የአገልግሎት ክፍያ ለመፈፀም\n\n"
        f"📌 የእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n\n"
        "እባክዎ ከታች ያለውን "
        "💳 የአገልግሎት ክፍያ የሚለውን ቁልፍ ተጭነው "
        "በተሰጠው አካውንት ቁጥር ክፍያ ይፈፅሙ።\n\n"
        "🧾 ክፍያ ካደረጉ በኋላ ደረሰኙን ወይም "
        "ስክሪንሾቱን ወደ @tanapage ይላኩ።\n\n"
        "⚠️ ችግር ካጋጠመዎ ከታች "
        "📞 Support የሚለውን ቁልፍ ተጭነው "
        "በሚያገኙት አድራሻ ያናግሩን።\n\n"
        "🙏 እኛን ስለመረጡን እናመሰግናለን!"
    )


def timeout_warning_text():
    return (
        "⏳ ውድ ደንበኛችን፣\n\n"
        "የፈለጉት የጭነት/መኪና ባለቤት "
        "እስካሁን ምላሽ አልሰጡም።\n\n"
        "📌 እባክዎ ትንሽ ይጠብቁ።\n"
        "🔔 ባለቤቱ ምላሽ ሲሰጡ ወዲያውኑ "
        "እናሳውቅዎታለን።\n\n"
        "🙏 ስለትዕግስትዎ እናመሰግናለን።"
    )


def notification_text_cargo(cargo):
    return (
        "ጭነትዎን / የጭነት መኪናዎን የፈለገ ደንበኛ መስመር ላይ ነው።\n\n"
        "እባክዎን ይህንን ተጭነው 👉 @tanacargo ውስጥ በመግባት "
        "\"የርሶ ግንኙነት\" ወደሚለው ውስጥ ይግቡና "
        "የ Right (✅) ምልክቷን ነክተው የዋጋ ድርድር ይጀምሩ።\n\n"
        "Notification @tanacargo ግን አክቲቭ መሆን አለበት።"
    )


def notification_text_truck(truck):
    return (
        "የጭነት መኪናዎን የፈለገ ደንበኛ መስመር ላይ ነው።\n\n"
        "እባክዎን ይህንን ተጭነው 👉 @tanacargo ውስጥ በመግባት "
        "\"የርሶ ግንኙነት\" ወደሚለው ውስጥ ይግቡና "
        "የ Right (✅) ምልክቷን ነክተው የዋጋ ድርድር ይጀምሩ።\n\n"
        "Notification @tanacargo ግን አክቲቭ መሆን አለበት።"
    )


# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    context.user_data["cargo"] = {}
    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n"
        "1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\n"
        "ምሳሌ፦ ባህር ዳር"
    )
    return CARGO_FROM


async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ የሚደርስበትን ቦታ ይጻፉ።"
    )
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
        await query.edit_message_text(
            "✍️ የሚፈልጉትን የመኪና አይነት ይጻፉ።"
        )
        context.user_data["cargo"]["vehicle_waiting"] = True
        return CARGO_VEHICLE
    context.user_data["cargo"]["vehicle"] = value
    await query.edit_message_text(
        f"🚛 የተመረጠው መኪና፦ {value}\n\n"
        "5️⃣ የጭነቱን መጠን ይምረጡ።",
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
        f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n"
        "5️⃣ የጭነቱን መጠን ይምረጡ።",
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
        await update.message.reply_text(
            f"❌ {error}\n"
            "ምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
        )
        return CARGO_WEIGHT
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text(
        "7️⃣ የሚጫንበትን ቀን ይምረጡ።\n\n"
        "እባክዎ የሚጠቀሙበትን ካላንደር ይምረጡ፦",
        reply_markup=calendar_keyboard()
    )
    return CARGO_CALENDAR


async def cargo_calendar(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cal_et":
        context.user_data["cargo"]["calendar"] = "ET"
        await query.edit_message_text(
            "🇪🇹 የኢትዮጵያ ካላንደር ተመርጧል።\n\n"
            "እባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 01/01/2019"
        )
    elif query.data == "cal_gc":
        context.user_data["cargo"]["calendar"] = "GC"
        await query.edit_message_text(
            "🌍 የግሪጎሪያን ካላንደር ተመርጧል።\n\n"
            "እባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 25/09/2026"
        )
    else:
        return CARGO_CALENDAR
    return CARGO_DATE


async def cargo_date(update, context):
    date_text = update.message.text.strip()
    calendar = context.user_data["cargo"].get("calendar", "GC")

    try:
        date_obj = datetime.strptime(date_text, "%d/%m/%Y")
    except ValueError:
        await update.message.reply_text(
            "❌ የቀን ቅርጸቱ ትክክል አይደለም።\n"
            "እባክዎ በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 25/09/2026"
        )
        return CARGO_DATE

    if calendar == "GC":
        today = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if date_obj < today:
            await update.message.reply_text(
                "❌ ያስገቡት ቀን ያለፈ ቀን ነው።\n"
                "እባክዎ የወደፊት ቀን ያስገቡ።"
            )
            return CARGO_DATE

    et_date, gc_date, error = convert_calendar(date_text, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return CARGO_DATE

    context.user_data["cargo"]["date"] = date_text
    context.user_data["cargo"]["date_et"] = et_date
    context.user_data["cargo"]["date_gc"] = gc_date

    try:
        gc_date_obj = datetime.strptime(gc_date, "%d/%m/%Y")
        expiry_date = gc_date_obj + timedelta(hours=CARGO_EXPIRY_HOURS)
        context.user_data["cargo"]["expiry_date"] = expiry_date.isoformat()
    except Exception:
        context.user_data["cargo"]["expiry_date"] = ""

    await update.message.reply_text(
        f"📅 የተመረጠው ቀን፦\n"
        f"🇪🇹 ET: {et_date}\n"
        f"🌍 GC: {gc_date}\n\n"
        "8️⃣ ስልክ ቁጥር ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return CARGO_PHONE


async def cargo_phone(update, context):
    phone = update.message.text.strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {error}\n"
            "ምሳሌ፦ 0912345678"
        )
        return CARGO_PHONE
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n"
        "በብር ብቻ ወይም 'በስምምነት' ብለው ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት"
    )
    return CARGO_PRICE


async def cargo_price(update, context):
    raw = update.message.text.strip()
    if raw in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price = None
        price_status = "agreement"
        price_display = "በስምምነት"
    else:
        try:
            price = float(
                raw.replace(",", "").replace("ብር", "").strip()
            )
            price_status = "fixed"
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text(
                "❌ እባክዎ ዋጋውን በቁጥር ወይም "
                "'በስምምነት' ብለው ይጻፉ።\n"
                "ምሳሌ፦ 55000 ወይም በስምምነት"
            )
            return CARGO_PRICE
        if price <= 0:
            await update.message.reply_text(
                "❌ ዋጋው ከ0 በላይ መሆን አለበት።"
            )
            return CARGO_PRICE

    cargo = context.user_data["cargo"]
    cargo["price"] = price
    cargo["price_status"] = price_status
    cargo["price_display"] = price_display
    cargo["user_id"] = update.effective_user.id
    cargo["active"] = True
    cargo["timestamp"] = datetime.now().isoformat()

    cargo_posts.append(cargo.copy())

    try:
        db_save_cargo(cargo)
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
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
        reply_markup=main_menu()
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
        "2️⃣ የመኪናውን ታርጋ ይጻፉ።\n"
        "ቁጥር ብቻ መሆን አለበት።\n"
        "ምሳሌ፦ 12345"
    )
    return TRUCK_PLATE


async def truck_plate(update, context):
    plate = update.message.text.strip()
    is_valid, error = validate_plate(plate)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {error}\n"
            "እባክዎ እንደገና ይጻፉ።\n"
            "ምሳሌ፦ 12345"
        )
        return TRUCK_PLATE
    context.user_data["truck"]["plate"] = plate
    await update.message.reply_text(
        "3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።\n"
        "በቶን፣ በኩንታል፣ በቢያጆ\n"
        "ምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ"
    )
    return TRUCK_CAPACITY


async def truck_capacity(update, context):
    capacity = update.message.text.strip()
    is_valid, error = validate_weight(capacity)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {error}\n"
            "ምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ"
        )
        return TRUCK_CAPACITY
    context.user_data["truck"]["capacity"] = capacity
    await update.message.reply_text(
        "4️⃣ የመንገድ ዝርዝር ይጻፉ።\n"
        "ምሳሌ፦ ባህርዳር → ጎንደር → መቐለ"
    )
    return TRUCK_ROUTE


async def truck_route(update, context):
    context.user_data["truck"]["route"] = update.message.text.strip()
    await update.message.reply_text(
        "5️⃣ የመኪናውን አድራሻ ይጻፉ።"
    )
    return TRUCK_ADDRESS


async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    await update.message.reply_text(
        "6️⃣ የሹፌሩን ስም ይጻፉ።"
    )
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
    phone = update.message.text.strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {error}\n"
            "እባክዎ እንደገና ይጻፉ።\n"
            "ምሳሌ፦ 0912345678"
        )
        return TRUCK_PHONE

    truck = context.user_data["truck"]
    truck["phone"] = phone
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck["timestamp"] = datetime.now().isoformat()

    expiry_date = datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)
    truck["expiry_date"] = expiry_date.isoformat()

    truck_posts.append(truck.copy())

    try:
        db_save_truck(truck)
    except Exception as e:
        print(f"DB SAVE TRUCK ERROR: {e}")

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"🛣️ መንገድ፦ {truck['route']}\n"
        f"📍 አድራሻ፦ {truck['address']}\n"
        f"👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
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
                        f"🛣️ መንገድ፦ {truck['route']}\n"
                        f"📍 አድራሻ፦ {truck['address']}\n\n"
                        "🚛 መኪና መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END


# ==================================================
# FIND CARGO
# ==================================================

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]

    if not active_cargos:
        await update.message.reply_text(
            "📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።\n\n"
            "🔎 ጭነት እያፈላለግን ነው።\n"
            "🔔 ተስማሚ ጭነት ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu()
        )
        return

    message = "🔎 የተለጠፉ ጭነቶች\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n\n"

    buttons = []

    for i, cargo in enumerate(active_cargos, 1):
        cargo_id = f"{i:03d}"
        price_display = cargo.get("price_display", "በስምምነት")

        message += (
            f"📦 ጭነት {cargo_id}\n"
            f"📍 መነሻ፦ {cargo['from']}\n"
            f"📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
            f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n"
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"👤 ደንበኛችን\n"
            "─────────────────────\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"🔢 ጭነት {cargo_id} — 🤝 ግንኙነት ጠይቅ",
                callback_data=f"connect_{i - 1}"
            )
        ])

    message += (
        f"📋 ጠቅላላ {len(active_cargos)} ጭነቶች ተገኝተዋል።\n\n"
        "👉 እባክዎን የፈለጉትን ጭነት በቁጥር ይምረጡ።"
    )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==================================================
# FIND TRUCK
# ==================================================

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]

    if not active_trucks:
        await update.message.reply_text(
            "🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።\n\n"
            "🔎 መኪና እያፈላለግን ነው።\n"
            "🔔 ተስማሚ መኪና ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu()
        )
        return

    message = "🚛 የተመዘገቡ መኪኖች\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n\n"

    buttons = []

    for i, truck in enumerate(active_trucks, 1):
        truck_id = f"{i:03d}"

        message += (
            f"🚛 የጭነት መኪና {truck_id}\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"👤 ደንበኛችን\n"
            "─────────────────────\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                f"🔢 የጭነት መኪና {truck_id} — 🤝 ግንኙነት ጠይቅ",
                callback_data=f"truckconnect_{i - 1}"
            )
        ])

    message += (
        f"📋 ጠቅላላ {len(active_trucks)} መኪኖች ተገኝተዋል።\n\n"
        "👉 እባክዎን የፈለጉትን መኪና በቁጥር ይምረጡ።"
    )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==================================================
# CONNECTION REQUEST - CARGO
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(cargo_posts):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return

    cargo = cargo_posts[index]

    if not cargo.get("active", True):
        await query.edit_message_text(
            "❌ ይህ ጭነት ቀድሞ ተስማምቶ ከፍርግርግ ዝርዝር ተወግዷል።"
        )
        return

    requester = update.effective_user

    if cargo["user_id"] == requester.id:
        await query.answer(
            "❌ የራስዎን ጭነት መጠየቅ አይችሉም።",
            show_alert=True
        )
        return

    for req in connection_requests:
        if (
            req.get("type") == "cargo"
            and req["cargo_index"] == index
            and req["requester_id"] == requester.id
            and req["status"] in [
                "pending",
                "negotiating",
                "awaiting_payment",
            ]
        ):
            await query.answer(
                "⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።",
                show_alert=True
            )
            return

    users.setdefault(
        requester.id,
        {
            "name": requester.full_name,
            "username": requester.username or "",
            "id": requester.id,
        }
    )

    request = {
        "type": "cargo",
        "cargo_index": index,
        "cargo_owner_id": cargo["user_id"],
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "status": "pending",
        "offers": [],
        "current_offer": None,
        "current_offer_by": None,
        "offer_version": 0,
        "final_price": None,
        "requester_confirmed": False,
        "other_confirmed": False,
        "requester_payment_submitted": False,
        "other_payment_submitted": False,
        "requester_payment_approved": False,
        "other_payment_approved": False,
        "requester_payment_rejected": False,
        "other_payment_rejected": False,
        "requester_payment_version": 0,
        "other_payment_version": 0,
        "full_info_shared": False,
        "created_at": datetime.now().isoformat(),
    }

    connection_requests.append(request)
    req_index = len(connection_requests) - 1

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄዎ ተላክቷል።\n\n"
        "🔒 የግል ስልክ ቁጥሮች ተደብቀዋል።\n"
        "💬 የዋጋ ድርድሩ በ TANA CARGO Bot ውስጥ ይካሄዳል።\n\n"
        "⏳ የጭነቱ ባለቤት ሲቀበል ይነገርዎታል።"
    )

    try:
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 TANA CARGO — አዲስ ግንኙነት ጥያቄ\n\n"
                    f"📌 Request ID፦ {req_index}\n"
                    f"📦 የጭነት ባለቤት ተጠይቋል።\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}\n\n"
                    "💬 ድርድሩ በ @tanapage እንዲቀጥል ያስተባብሩ።"
                )
            )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=cargo["user_id"],
            text=(
                "🔔 አዲስ የግንኙነት ጥያቄ!\n\n"
                f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                f"📦 {cargo['type']}\n"
                f"🚛 {cargo['vehicle']}\n"
                f"📊 {cargo['size_name']}\n\n"
                f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                "📦 ጭነትዎን የሚጭን የጭነት መኪና ደንበኛ ተገኝቷል!\n"
                "💬 ለዋጋና ዝርዝር ውይይት @tanapage ይጠቀሙ።"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🤝 ግንኙነት",
                    callback_data=f"accept_{req_index}"
                ),
                InlineKeyboardButton(
                    "❌ ውድቅ",
                    callback_data=f"reject_{req_index}"
                )
            ]])
        )
    except Exception:
        pass


# ==================================================
# CONNECTION REQUEST - TRUCK
# ==================================================

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(truck_posts):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return

    truck = truck_posts[index]

    if not truck.get("active", True):
        await query.edit_message_text(
            "❌ ይህ መኪና ቀድሞ ተስማምቶ ከመፈለጊያ ዝርዝር ተወግዷል።"
        )
        return

    requester = update.effective_user

    if truck["user_id"] == requester.id:
        await query.answer(
            "❌ የራስዎን መኪና መጠየቅ አይችሉም።",
            show_alert=True
        )
        return

    for req in connection_requests:
        if (
            req.get("type") == "truck"
            and req["truck_index"] == index
            and req["requester_id"] == requester.id
            and req["status"] in [
                "pending",
                "negotiating",
                "awaiting_payment",
            ]
        ):
            await query.answer(
                "⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።",
                show_alert=True
            )
            return

    users.setdefault(
        requester.id,
        {
            "name": requester.full_name,
            "username": requester.username or "",
            "id": requester.id,
        }
    )

    request = {
        "type": "truck",
        "truck_index": index,
        "truck_owner_id": truck["user_id"],
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "status": "pending",
        "offers": [],
        "current_offer": None,
        "current_offer_by": None,
        "offer_version": 0,
        "final_price": None,
        "requester_confirmed": False,
        "other_confirmed": False,
        "requester_payment_submitted": False,
        "other_payment_submitted": False,
        "requester_payment_approved": False,
        "other_payment_approved": False,
        "requester_payment_rejected": False,
        "other_payment_rejected": False,
        "requester_payment_version": 0,
        "other_payment_version": 0,
        "full_info_shared": False,
        "created_at": datetime.now().isoformat(),
    }

    connection_requests.append(request)
    req_index = len(connection_requests) - 1

    await query.edit_message_text(
        "✅ የመኪና ግንኙነት ጥያቄዎ ተላክቷል።\n\n"
        "🔒 የግል መረጃዎች እስከ ማረጋገጫ ድረስ ተደብቀዋል።"
    )

    try:
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 TANA CARGO — አዲስ የመኪና ግንኙነት ጥያቄ\n\n"
                    f"📌 Request ID፦ {req_index}\n"
                    f"🚛 መኪና፦ {truck['type']}\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                    "💬 ድርድሩ በ @tanapage እንዲቀጥል ያስተባብሩ።"
                )
            )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=truck["user_id"],
            text=(
                "🔔 አዲስ የመኪና ግንኙነት ጥያቄ!\n\n"
                f"🚛 አይነት፦ {truck['type']}\n"
                f"📍 መነሻ፦ {truck.get('address', 'N/A')}\n"
                f"🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n\n"
                f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                "🚛 የእርሶን የጭነት መኪና የሚፈልግ ደንበኛ ተገኝቷል!\n"
                "💬 ለዋጋና ዝርዝር ውይይት @tanapage ይጠቀሙ።"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🤝 ግንኙነት",
                    callback_data=f"accept_{req_index}"
                ),
                InlineKeyboardButton(
                    "❌ ውድቅ",
                    callback_data=f"reject_{req_index}"
                )
            ]])
        )
    except Exception:
        pass


# ==================================================
# ACCEPT CONNECTION
# ==================================================

async def accept_connection(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]

    if other_party_id(req) != update.effective_user.id:
        await query.answer(
            "❌ ይህን ጥያቄ እርስዎ መቀበል አይችሉም።",
            show_alert=True
        )
        return

    if req["status"] != "pending":
        await query.answer(
            "⚠️ ይህ ጥያቄ ከዚህ በፊት ተስተናግዷል።",
            show_alert=True
        )
        return

    req["status"] = "negotiating"
    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["current_offer"] = None
    req["current_offer_by"] = None
    req["final_price"] = None

    users.setdefault(req["requester_id"], {})
    users.setdefault(other_party_id(req), {})
    users[req["requester_id"]]["negotiation_request"] = index
    users[other_party_id(req)]["negotiation_request"] = index

    negotiation_timeouts[index] = datetime.now()

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄውን ተቀብለዋል።\n\n"
        "💬 አሁን የዋጋ ድርድር ይጀምራል።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=(
                "✅ የግንኙነት ጥያቄዎ ተቀባ!\n\n"
                "💬 አሁን የመጓጓዣ ዋጋ ድርድር መጀመር ይችላሉ።\n\n"
                "💰 ለመጀመር የሚፈልጉትን ዋጋ በብር ይጻፉ።\n"
                "ምሳሌ፦ 55000"
            )
        )
    except Exception:
        pass


# ==================================================
# REJECT CONNECTION
# ==================================================

async def reject_connection(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]

    if other_party_id(req) != update.effective_user.id:
        return

    req["status"] = "rejected"

    for uid in [req["requester_id"], other_party_id(req)]:
        if uid in users:
            users[uid].pop("negotiation_request", None)
            users[uid].pop("payment_request", None)

    negotiation_timeouts.pop(index, None)

    await query.edit_message_text(
        "❌ የግንኙነት ጥያቄው ውድቅ ተደርጓል።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text="❌ የግንኙነት ጥያቄዎ ውድቅ ተደርጓል።"
        )
    except Exception:
        pass


# ==================================================
# SHOW CONNECTION REQUESTS
# ==================================================

async def show_connection_requests(update, context):
    user_id = update.effective_user.id

    received = []
    sent = []

    for i, req in enumerate(connection_requests):
        owner_id = other_party_id(req)
        if owner_id == user_id:
            received.append((i, req))
        if req["requester_id"] == user_id:
            sent.append((i, req))

    if not received and not sent:
        await update.message.reply_text(
            "🤝 እስካሁን የግንኙነት ጥያቄ የለዎትም።",
            reply_markup=main_menu()
        )
        return

    text = "🤝 የግንኙነት ጥያቄዎች\n\n"
    buttons = []

    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            text += (
                f"📥 የመጣ #{i + 1}\n"
                f"🚛 {truck['type']}\n"
                f"📍 {truck.get('address', 'N/A')} ➡️ "
                f"{truck.get('route', 'N/A')}\n"
                f"👤 {req['requester_name']}\n"
                f"📌 ሁኔታ፦ {req['status']}\n\n"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            text += (
                f"📥 የመጣ #{i + 1}\n"
                f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                f"📦 {cargo['type']}\n"
                f"👤 {req['requester_name']}\n"
                f"📌 ሁኔታ፦ {req['status']}\n\n"
            )

        if req["status"] == "pending":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ ተቀበል #{i + 1}",
                    callback_data=f"accept_{i}"
                ),
                InlineKeyboardButton(
                    f"❌ ውድቅ #{i + 1}",
                    callback_data=f"reject_{i}"
                ),
            ])

    for i, req in sent:
        text += (
            f"📤 የላኩት #{i + 1}\n"
            f"📌 ሁኔታ፦ {req['status']}\n"
        )
        if req.get("final_price"):
            text += f"💰 የመጨረሻ ዋጋ፦ {req['final_price']:,.2f} ብር\n"
        if req["status"] == "awaiting_payment":
            text += "💳 ክፍያ ይጠበቃል።\n"
        if req["status"] == "completed":
            text += "✅ ሁለቱም ክፍያዎች ተፈቅደዋል።\n"
        text += "\n"

    await update.message.reply_text(
        text,
        reply_markup=(
            InlineKeyboardMarkup(buttons)
            if buttons
            else main_menu()
        )
    )


# ==================================================
# ADMIN CONNECTION REQUESTS
# ==================================================

async def admin_connection_requests(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return

    if not connection_requests:
        await update.message.reply_text("ℹ️ እስካሁን የግንኙነት ጥያቄ የለም።")
        return

    text = "🤝 የአድሚን የግንኙነት ጥያቄዎች\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    buttons = []

    for i, req in enumerate(connection_requests):
        if req["status"] == "completed":
            status_icon = "✅"
        elif req["status"] in ["pending", "negotiating"]:
            status_icon = "⏳"
        elif req["status"] == "rejected":
            status_icon = "❌"
        elif req["status"] == "awaiting_payment":
            status_icon = "💳"
        else:
            status_icon = "📌"

        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            text += (
                f"{status_icon} #{i + 1}\n"
                f"🚛 {truck['type']}\n"
                f"📍 {truck.get('address', 'N/A')} ➡️ "
                f"{truck.get('route', 'N/A')}\n"
                f"👤 ጠያቂ፦ {req['requester_name']}\n"
                f"👤 ባለቤት፦ {users.get(req['truck_owner_id'], {}).get('name', 'N/A')}\n"
                f"📌 ሁኔታ፦ {req['status']}\n"
                f"💰 ዋጋ፦ {req.get('final_price', 0):,.2f} ብር\n\n"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            text += (
                f"{status_icon} #{i + 1}\n"
                f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                f"📦 {cargo['type']}\n"
                f"👤 ጠያቂ፦ {req['requester_name']}\n"
                f"👤 ባለቤት፦ {users.get(req['cargo_owner_id'], {}).get('name', 'N/A')}\n"
                f"📌 ሁኔታ፦ {req['status']}\n"
                f"💰 ዋጋ፦ {req.get('final_price', 0):,.2f} ብር\n\n"
            )

        buttons.append([
            InlineKeyboardButton(
                f"🗑️ አጥፋ #{i + 1}",
                callback_data=f"admindeleteconnection_{i}"
            )
        ])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_delete_connection(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        index = int(query.data.split("_")[-1])
    except ValueError:
        return

    if index < 0 or index >= len(connection_requests):
        return

    connection_requests[index]["status"] = "deleted"

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄው ተሰርዟል።"
    )


# ==================================================
# GET NEGOTIATION INDEX
# ==================================================

def get_negotiation_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("negotiation_request")

    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (
            req["status"] == "negotiating"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):
            return saved

    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (
            req["status"] == "negotiating"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):
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
    text = update.message.text.strip()

    if text in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price = None
        price_display = "በስምምነት"
    else:
        try:
            price = float(
                text.replace(",", "").replace("ብር", "").strip()
            )
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text(
                "❌ የዋጋውን ቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\n"
                "ምሳሌ፦ 55000 ወይም በስምምነት"
            )
            return

        if price <= 0:
            await update.message.reply_text(
                "❌ ዋጋው ከ0 በላይ መሆን አለበት።"
            )
            return

    req["offer_version"] = req.get("offer_version", 0) + 1
    version = req["offer_version"]

    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None

    req["offers"].append({
        "user_id": user_id,
        "price": price,
        "display": price_display,
        "version": version,
    })

    req["current_offer"] = price
    req["current_offer_by"] = user_id
    req["status"] = "negotiating"

    negotiation_timeouts[active_index] = datetime.now()

    await update.message.reply_text(
        f"💰 የላኩት ዋጋ፦ {price_display}\n\n"
        "⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    buttons = [
        [
            InlineKeyboardButton(
                f"✅ ተስማማለሁ {price_display}",
                callback_data=f"agreeprice_{active_index}_{version}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ አልስማማሁም",
                callback_data=f"rejectprice_{active_index}_{version}"
            )
        ],
    ]

    other_user = other_party_id(req)

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                "💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n"
                f"💵 የቀረበው ዋጋ፦ {price_display}\n\n"
                "ከዚህ ዋጋ ጋር ከተስማሙ ✅ ይጫኑ።\n"
                "ወይም ❌ አልስማማሁም ይጫኑ።"
            ),
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

    current_version = req.get("offer_version", 0)
    if button_version != current_version:
        await query.answer(
            "⚠️ ይህ የድሮ ዋጋ ነው። አዲሱን ዋጋ ይጠቀሙ።",
            show_alert=True
        )
        return

    if req["status"] != "negotiating":
        await query.answer(
            "⚠️ ይህ ድርድር አሁን ንቁ አይደለም።",
            show_alert=True
        )
        return

    if (
        user_id != req["requester_id"]
        and user_id != other_party_id(req)
    ):
        await query.answer(
            "❌ ይህ የእርስዎ ድርድር አይደለም።",
            show_alert=True
        )
        return

    price = req.get("current_offer")
    price_display = "በስምምነት"

    if price is not None:
        price_display = f"{price:,.2f} ብር"

    if user_id == req.get("current_offer_by"):
        await query.answer(
            "⚠️ የራስዎን ዋጋ መቀበል አይችሉም።",
            show_alert=True
        )
        return

    if user_id == req["requester_id"]:
        if req["requester_confirmed"]:
            await query.answer(
                "✅ እርስዎ ቀድሞ ተስማምተዋል።",
                show_alert=True
            )
            return
        req["requester_confirmed"] = True
    else:
        if req["other_confirmed"]:
            await query.answer(
                "✅ እርስዎ ቀድሞ ተስማምተዋል።",
                show_alert=True
            )
            return
        req["other_confirmed"] = True

    if req["requester_confirmed"] and req["other_confirmed"]:
        req["final_price"] = price
        req["status"] = "awaiting_payment"

        req["requester_payment_submitted"] = False
        req["other_payment_submitted"] = False
        req["requester_payment_approved"] = False
        req["other_payment_approved"] = False
        req["requester_payment_rejected"] = False
        req["other_payment_rejected"] = False

        for uid in [req["requester_id"], other_party_id(req)]:
            if uid in users:
                users[uid].pop("negotiation_request", None)
                users[uid]["payment_request"] = index

        negotiation_timeouts.pop(index, None)

        if price is not None:
            each_side, total = commission_amount(price)
        else:
            each_side = 0
            total = 0

        await query.edit_message_text(
            "✅ ሁለቱም ወገኖች በተናጠል ተስማምተዋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price_display}\n\n"
            f"📌 ከእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n"
            f"💵 ጠቅላላ TANA CARGO ኮሚሽን፦ {total:,.2f} ብር\n\n"
            "💳 አሁን እያንዳንዱ ወገን የራሱን 1% ይከፍላል።"
        )

        payment_text = agreement_success_text(
            price if price else 0,
            each_side
        )

        for user in [req["requester_id"], other_party_id(req)]:
            try:
                await context.bot.send_message(
                    chat_id=user,
                    text=payment_text
                )
            except Exception:
                pass

        return

    other_user = (
        other_party_id(req)
        if user_id == req["requester_id"]
        else req["requester_id"]
    )

    buttons = [
        [
            InlineKeyboardButton(
                f"✅ እኔም እስማማለሁ {price_display}",
                callback_data=f"agreeprice_{index}_{current_version}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ አልስማማሁም",
                callback_data=f"rejectprice_{index}_{current_version}"
            )
        ],
    ]

    await query.edit_message_text(
        f"✅ {price_display} ላይ ተስማምተዋል።\n\n"
        "⏳ አሁን የሌላኛው ወገን በተናጠል መስማማት አለበት።"
    )

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                "🤝 የዋጋ ስምምነት ማረጋገጫ\n\n"
                f"💰 {price_display}\n\n"
                "ሌላኛው ወገን በዚህ ዋጋ ተስማምቷል።\n"
                "እርስዎም ከተስማሙ ✅ ይጫኑ።\n"
                "ወይም ❌ አልስማማሁም ይጫኑ።"
            ),
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

    if (
        user_id != req["requester_id"]
        and user_id != other_party_id(req)
    ):
        await query.answer("❌ የእርስዎ ድርድር አይደለም።", show_alert=True)
        return

    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None

    await query.edit_message_text(
        "❌ አልተስማማሁም ብለዋል።\n\n"
        "💬 አዲስ ዋጋ ለማቅረብ ይጻፉ።"
    )

    other_user = (
        other_party_id(req)
        if user_id == req["requester_id"]
        else req["requester_id"]
    )

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                "❌ ሌላኛው ወገን በዚህ ዋጋ አልተስማማም።\n\n"
                "💬 አዲስ ዋጋ ያቅርቡ።"
            )
        )
    except Exception:
        pass


# ==================================================
# COUNTER PRICE BUTTON
# ==================================================

async def counter_price_button(update, context):
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
        await query.answer(
            "⚠️ ይህ የድሮ ዋጋ ነው።",
            show_alert=True
        )
        return

    if req["status"] != "negotiating":
        await query.answer(
            "⚠️ ድርድሩ ንቁ አይደለም።",
            show_alert=True
        )
        return

    user_id = update.effective_user.id

    if (
        user_id != req["requester_id"]
        and user_id != other_party_id(req)
    ):
        await query.answer(
            "❌ ይህ የእርስዎ ድርድር አይደለም።",
            show_alert=True
        )
        return

    if user_id == req.get("current_offer_by"):
        await query.answer(
            "⚠️ የራስዎን ዋጋ እንደገና መቀየር ከፈለጉ አዲስ ዋጋ በቀጥታ ይጻፉ።",
            show_alert=True
        )
        return

    users.setdefault(user_id, {})
    users[user_id]["negotiation_request"] = index

    await query.message.reply_text(
        "💬 አዲስ ዋጋ ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት"
    )


# ==================================================
# OWNER REGISTRATION
# ==================================================

async def owner_start(update, context):
    context.user_data["owner"] = {}
    await update.message.reply_text(
        "📦 የጭነት ባለቤት ምዝገባ\n\n"
        "1️⃣ ስምዎን ይጻፉ።"
    )
    return OWNER_NAME


async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return OWNER_PHONE


async def owner_phone(update, context):
    phone = update.message.text.strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(
            f"❌ {error}\n"
            "ምሳሌ፦ 0912345678"
        )
        return OWNER_PHONE

    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id

    user = update.effective_user
    users.setdefault(
        user.id,
        {
            "name": user.full_name,
            "username": user.username or "",
            "id": user.id,
        }
    )
    users[user.id]["owner"] = owner.copy()

    try:
        db_save_user(
            user.id,
            user.full_name,
            user.username or "",
            owner_name=owner["name"],
            owner_phone=owner["phone"]
        )
    except Exception as e:
        print(f"DB SAVE OWNER ERROR: {e}")

    await update.message.reply_text(
        "✅ የጭነት ባለቤት ምዝገባዎ ተጠናቋል!\n\n"
        f"👤 ስም፦ {owner['name']}\n"
        f"📞 ስልክ፦ {owner['phone']}",
        reply_markup=main_menu()
    )

    return ConversationHandler.END


# ==================================================
# GET PAYMENT INDEX
# ==================================================

def get_payment_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("payment_request")

    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (
            req["status"] == "awaiting_payment"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):
            return saved

    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (
            req["status"] == "awaiting_payment"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):
            return i

    return None


# ==================================================
# SEND ADMIN PAYMENT REQUEST
# ==================================================

async def send_admin_payment_request(
    context,
    index,
    side,
    user_id,
    receipt_text=None,
    photo_id=None,
    payment_version=None
):
    if not ADMIN_USER_ID:
        return

    req = connection_requests[index]

    if side == "requester":
        side_name = "ጠያቂ / Cargo or Service User"
    else:
        if req.get("type") == "truck":
            side_name = "የመኪና ባለቤት"
        else:
            side_name = "የጭነት ባለቤት"

    if payment_version is None:
        payment_version = req.get(f"{side}_payment_version", 0)

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ APPROVE",
                callback_data=(
                    f"adminapprove_{index}_{side}_{payment_version}"
                )
            ),
            InlineKeyboardButton(
                "❌ REJECT",
                callback_data=(
                    f"adminreject_{index}_{side}_{payment_version}"
                )
            ),
        ]
    ])

    user = users.get(user_id, {})
    user_name = user.get("name", "Unknown")

    price = req.get("final_price") or 0

    text = (
        "🧾 TANA CARGO — የክፍያ ማስረጃ\n\n"
        f"📌 Request ID፦ {index}\n"
        f"🔢 Payment Version፦ {payment_version}\n"
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

    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=int(ADMIN_USER_ID),
                photo=photo_id,
                caption=text,
                reply_markup=buttons
            )
        else:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=text,
                reply_markup=buttons
            )
    except Exception:
        pass


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

    if user_id == req["requester_id"]:
        side = "requester"
    else:
        side = "other"

    version_key = f"{side}_payment_version"
    req[version_key] = req.get(version_key, 0) + 1
    payment_version = req[version_key]

    submitted_key = f"{side}_payment_submitted"
    approved_key = f"{side}_payment_approved"
    rejected_key = f"{side}_payment_rejected"

    req[submitted_key] = True
    req[approved_key] = False
    req[rejected_key] = False
    req[f"{side}_receipt"] = receipt
    req.pop(f"{side}_receipt_photo", None)
    req["status"] = "awaiting_payment"

    await update.message.reply_text(
        "🧾 የክፍያ Receipt ተቀብለናል።\n\n"
        "⏳ Admin ክፍያውን ይመረምራል።\n"
        "🔒 ሁለቱም ክፍያዎች Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ አይጋራም።"
    )

    await send_admin_payment_request(
        context,
        index,
        side,
        user_id,
        receipt_text=receipt,
        payment_version=payment_version
    )


# ==================================================
# RECEIPT PHOTO
# ==================================================

async def receipt_photo(update, context):
    user_id = update.effective_user.id
    index = get_payment_index(user_id)

    if index is None:
        return

    req = connection_requests[index]
    file_id = update.message.photo[-1].file_id

    if user_id == req["requester_id"]:
        side = "requester"
    else:
        side = "other"

    version_key = f"{side}_payment_version"
    req[version_key] = req.get(version_key, 0) + 1
    payment_version = req[version_key]

    submitted_key = f"{side}_payment_submitted"
    approved_key = f"{side}_payment_approved"
    rejected_key = f"{side}_payment_rejected"

    req[submitted_key] = True
    req[approved_key] = False
    req[rejected_key] = False
    req[f"{side}_receipt_photo"] = file_id
    req.pop(f"{side}_receipt", None)
    req["status"] = "awaiting_payment"

    await update.message.reply_text(
        "🧾 የክፍያ Screenshot ተቀብለናል።\n\n"
        "⏳ Admin ክፍያውን ይመረምራል።\n"
        "🔒 ሁለቱም ክፍያዎች Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ አይጋራም።"
    )

    await send_admin_payment_request(
        context,
        index,
        side,
        user_id,
        photo_id=file_id,
        payment_version=payment_version
    )


# ==================================================
# SHARE FULL INFORMATION
# ==================================================

async def share_full_information(index, context):
    req = connection_requests[index]

    if req.get("full_info_shared"):
        return

    if not (
        req.get("requester_payment_approved")
        and req.get("other_payment_approved")
    ):
        return

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

    requester_name = users.get(requester_id, {}).get(
        "name",
        req.get("requester_name", "የጠያቂው ስም")
    )
    requester_phone = get_user_phone(requester_id)

    if req.get("type") == "truck":
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(owner_id, {}).get(
            "name", "የመኪና ባለቤት"
        )
        truck_owner_phone = truck.get("phone", "")
        truck_plate = truck.get("plate", "")

        requester_message = (
            "✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            f"👤 የመኪና ባለቤት፦ {truck_owner_name}\n"
            f"📞 ስልክ፦ {truck_owner_phone or 'የለም'}\n"
            f"🚛 መኪና፦ {truck['type']}\n"
            f"🔢 ታርጋ፦ {truck_plate or 'የለም'}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {truck.get('route', 'N/A')}\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።"
        )

        owner_message = (
            "✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            f"👤 ጠያቂ፦ {requester_name}\n"
            f"📞 ስልክ፦ {requester_phone or 'የለም'}\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።"
        )

    else:
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(owner_id, {}).get(
            "name", "የጭነት ባለቤት"
        )
        cargo_owner_phone = cargo.get("phone", "")

        truck = None
        for item in reversed(truck_posts):
            if item["user_id"] == requester_id:
                truck = item
                break

        if truck:
            truck_info = (
                f"🚛 መኪና፦ {truck['type']}\n"
                f"🔢 ታርጋ፦ {truck.get('plate', 'የለም')}\n"
                f"📞 ስልክ፦ {truck.get('phone', 'የለም')}\n"
                f"⚖️ አቅም፦ {truck.get('capacity', 'የለም')}\n"
            )
        else:
            truck_info = f"📞 ስልክ፦ {requester_phone or 'የለም'}\n"

        requester_message = (
            "✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            f"👤 የጭነት ባለቤት፦ {cargo_owner_name}\n"
            f"📞 ስልክ፦ {cargo_owner_phone or 'የለም'}\n\n"
            f"📦 ጭነት፦ {cargo['type']}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo.get('date_et', 'N/A')} (ET)\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።"
        )

        owner_message = (
            "✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            f"👤 የመኪና ጠያቂ፦ {requester_name}\n"
            f"{truck_info}\n"
            f"📦 ጭነት፦ {cargo['type']}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo.get('date_et', 'N/A')} (ET)\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።"
        )

    try:
        await context.bot.send_message(
            chat_id=requester_id,
            text=requester_message
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=owner_message
        )
    except Exception:
        pass


# ==================================================
# ADMIN PAYMENT ACTION
# ==================================================

async def admin_payment_action(update, context):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    if not ADMIN_USER_ID:
        await query.answer("❌ ADMIN_USER_ID አልተዘጋጀም።", show_alert=True)
        return

    if str(user_id) != str(ADMIN_USER_ID):
        await query.answer(
            "❌ Admin ብቻ ይህን ተግባር ማድረግ ይችላል።",
            show_alert=True
        )
        return

    parts = query.data.split("_")

    if len(parts) != 4:
        return

    action = parts[0]
    index = int(parts[1])
    side = parts[2]
    button_version = int(parts[3])

    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]

    if side not in ["requester", "other"]:
        return

    current_version = req.get(f"{side}_payment_version", 0)

    if button_version != current_version:
        await query.answer("⚠️ ይህ የድሮ የክፍያ ማስረጃ ነው።", show_alert=True)
        return

    submitted_key = f"{side}_payment_submitted"
    approved_key = f"{side}_payment_approved"
    rejected_key = f"{side}_payment_rejected"

    if not req.get(submitted_key):
        await query.answer("⚠️ የክፍያ ማስረጃ አልተላከም።", show_alert=True)
        return

    user_to_notify = (
        req["requester_id"]
        if side == "requester"
        else other_party_id(req)
    )

    if action == "adminapprove":
        req[approved_key] = True
        req[rejected_key] = False

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        if (
            req.get("requester_payment_approved")
            and req.get("other_payment_approved")
        ):
            await share_full_information(index, context)
        else:
            try:
                await context.bot.send_message(
                    chat_id=user_to_notify,
                    text=(
                        "✅ TANA CARGO — የክፍያ ማረጋገጫ\n\n"
                        "Admin የላኩትን የክፍያ ማስረጃ አረጋግጧል።\n\n"
                        "⏳ የሁለተኛው ወገን ክፍያ ማረጋገጫ ይጠበቃል።\n\n"
                        "🔒 የግል መረጃ እስካሁን አይጋራም።"
                    )
                )
            except Exception:
                pass

    elif action == "adminreject":
        req[approved_key] = False
        req[rejected_key] = True
        req[submitted_key] = False
        req.pop(f"{side}_receipt", None)
        req.pop(f"{side}_receipt_photo", None)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=user_to_notify,
                text=(
                    "❌ TANA CARGO — የክፍያ ማስረጃ አልተፈቀደም።\n\n"
                    "እባክዎ ክፍያውን እንደገና ያረጋግጡ እና "
                    "Receipt No. ወይም Screenshot እንደገና ይላኩ።\n\n"
                    "🔒 የግል መረጃ እስካሁን ተደብቆ ይቆያል።"
                )
            )
        except Exception:
            pass


# ==================================================
# ADMIN AGREEMENT CONFIRMATION
# ==================================================

async def send_admin_agreement_request(context, index):
    if not ADMIN_USER_ID:
        return

    req = connection_requests[index]

    if req.get("admin_agreement_requested"):
        return

    req["admin_agreement_requested"] = True

    kind = "🚛 የጭነት መኪና" if req.get("type") == "truck" else "📦 ጭነት"

    button = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ ስምምነቱን አረጋግጥ እና ከፍርግርግ አስወግድ",
            callback_data=f"adminagree_{index}"
        )
    ]])

    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_USER_ID),
            text=(
                "🤝 TANA CARGO — የስምምነት ማረጋገጫ\n\n"
                f"📌 Request ID፦ {index}\n"
                f"{kind}\n"
                f"💰 የተስማሙበት ዋጋ፦ "
                f"{req.get('final_price', 0):,.2f} ብር\n\n"
                "እባክዎ ስምምነቱን ካረጋገጡ በኋላ "
                "ከመፈለጊያ ዝርዝር ለማስወገድ "
                "ከታች ያለውን ቁልፍ ይጫኑ።"
            ),
            reply_markup=button
        )
    except Exception:
        req["admin_agreement_requested"] = False


async def admin_agreement_confirm(update, context):
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
    req["admin_agreement_confirmed"] = True

    if req.get("type") == "truck":
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
    else:
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    notice = (
        "✅ TANA CARGO — Admin ማረጋገጫ\n\n"
        "የደንበኛውና የተመረጠው መዝገብ ላይ ያለው "
        "እቃ/መኪና ስምምነት በAdmin ተረጋግጧል።\n"
        "🔒 የግል መረጃ እስከ ተፈቀደበት ሂደት ድረስ "
        "ተጠብቆ ይቆያል።\n\n"
        "📞 ድርድር/ዝርዝር ለመቀጠል @tanapage ይጠቀሙ።"
    )

    for uid in [req.get("requester_id"), other_party_id(req)]:
        if uid:
            try:
                await context.bot.send_message(chat_id=uid, text=notice)
            except Exception:
                pass


# ==================================================
# ADMIN USER MANAGEMENT
# ==================================================

async def admin_users(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return

    if not users:
        await update.message.reply_text("ℹ️ የተመዘገበ ደንበኛ የለም።")
        return

    for uid, user in users.items():
        text = (
            f"👤 {user.get('name', '')}\n"
            f"🆔 Telegram ID፦ {uid}\n"
            f"🔗 Username፦ @{user.get('username') or 'የለም'}\n"
            f"📦 Cargo፦ "
            f"{sum(1 for c in cargo_posts if c.get('user_id') == uid)}\n"
            f"🚛 Truck፦ "
            f"{sum(1 for t in truck_posts if t.get('user_id') == uid)}"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🗑 Delete User",
                callback_data=f"admindeleteuser_{uid}"
            )
        ]])
        await update.message.reply_text(text, reply_markup=kb)


async def admin_delete_user(update, context):
    q = update.callback_query
    await q.answer()

    if not is_admin(update.effective_user.id):
        await q.answer("❌ Admin ብቻ።", show_alert=True)
        return

    try:
        uid = int(q.data.split("_")[-1])
    except ValueError:
        return

    users.pop(uid, None)

    for c in cargo_posts:
        if c.get("user_id") == uid:
            c["active"] = False
    for t in truck_posts:
        if t.get("user_id") == uid:
            t["active"] = False

    await q.edit_message_text(
        "✅ የደንበኛው መረጃ ከአሁኑ bot memory ተሰርዟል።"
    )


# ==================================================
# TIMEOUT CHECKER
# ==================================================

async def check_negotiation_timeouts(context):
    now = datetime.now()
    expired = []

    for req_index, started_at in list(negotiation_timeouts.items()):
        if req_index < 0 or req_index >= len(connection_requests):
            negotiation_timeouts.pop(req_index, None)
            continue

        req = connection_requests[req_index]

        if req.get("status") != "negotiating":
            negotiation_timeouts.pop(req_index, None)
            continue

        elapsed = (now - started_at).total_seconds()

        if elapsed >= NEGOTIATION_TIMEOUT_SECONDS:
            expired.append(req_index)

    for req_index in expired:
        req = connection_requests[req_index]

        requester_id = req["requester_id"]

        try:
            await context.bot.send_message(
                chat_id=requester_id,
                text=timeout_warning_text()
            )
        except Exception:
            pass

        if ADMIN_USER_ID:
            try:
                await context.bot.send_message(
                    chat_id=int(ADMIN_USER_ID),
                    text=(
                        "⚠️ TANA CARGO — Timeout\n\n"
                        f"📌 Request ID፦ {req_index}\n"
                        "የድርድር ጊዜ ገደብ አልፏል።"
                    )
                )
            except Exception:
                pass

        negotiation_timeouts[req_index] = now


# ==================================================
# EXPIRED CARGO/TRUCK CLEANER (72 HOURS)
# ==================================================

async def clean_expired_posts(context):
    now = datetime.now()
    expired_cargos = []
    expired_trucks = []

    for i, cargo in enumerate(cargo_posts):
        if not cargo.get("active", True):
            continue
        expiry_date = cargo.get("expiry_date")
        if expiry_date:
            try:
                expiry = datetime.fromisoformat(expiry_date)
                if now >= expiry:
                    expired_cargos.append(i)
            except Exception:
                pass

    for i, truck in enumerate(truck_posts):
        if not truck.get("active", True):
            continue
        expiry_date = truck.get("expiry_date")
        if expiry_date:
            try:
                expiry = datetime.fromisoformat(expiry_date)
                if now >= expiry:
                    expired_trucks.append(i)
            except Exception:
                pass

    for i in expired_cargos:
        cargo_posts[i]["active"] = False
        try:
            await context.bot.send_message(
                chat_id=cargo_posts[i]["user_id"],
                text=(
                    "⏰ TANA CARGO — የጭነት ጊዜ ገደብ\n\n"
                    f"📍 {cargo_posts[i]['from']} ➡️ {cargo_posts[i]['to']}\n"
                    f"📦 {cargo_posts[i]['type']}\n\n"
                    "የመጫኛ ቀኑ ካለፈ 72 ሰዓት ስለሞላ "
                    "ጭነትዎ ከፍርግርግ ዝርዝር ተወግዷል።\n\n"
                    "🔄 አዲስ ጭነት ለመመዝገብ እንኳን ደህና መጡ።"
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
                    "⏰ TANA CARGO — የመኪና ጊዜ ገደብ\n\n"
                    f"🚛 {truck_posts[i]['type']}\n"
                    f"📍 {truck_posts[i].get('address', 'N/A')}\n\n"
                    "የመኪናዎ ምዝገባ ጊዜ ካለፈ 72 ሰዓት ስለሞላ "
                    "ከፍርግርግ ዝርዝር ተወግዷል።\n\n"
                    "🔄 እንደገና ለመመዝገብ እንኳን ደህና መጡ።"
                )
            )
        except Exception:
            pass

    if expired_cargos or expired_trucks:
        print(
            f"🧹 Cleaned {len(expired_cargos)} expired cargos "
            f"and {len(expired_trucks)} expired trucks"
        )


# ==================================================
# START
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    user = update.effective_user

    if user.id not in users:
        users[user.id] = {
            "name": user.full_name,
            "username": user.username or "",
            "id": user.id,
        }
    else:
        users[user.id]["name"] = user.full_name
        users[user.id]["username"] = user.username or ""

    try:
        db_save_user(user.id, user.full_name, user.username or "")
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።\n\n"
        "ከታች ያለውን ምናሌ ይጠቀሙ።",
        reply_markup=main_menu()
    )


# ==================================================
# PROFILE
# ==================================================

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id)

    if not user:
        user = {
            "name": update.effective_user.full_name,
            "username": update.effective_user.username or "",
            "id": user_id,
        }
        users[user_id] = user

    message = (
        "👤 የኔ መረጃ\n\n"
        f"👤 ስም፦ {user['name']}\n"
        f"🔗 Username፦ "
        f"@{user['username'] if user['username'] else 'የለም'}\n"
        f"🆔 Telegram ID፦ {user['id']}\n"
    )

    if "owner" in user:
        owner = user["owner"]
        message += (
            "\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n"
            f"👤 የባለቤት ስም፦ {owner.get('name', 'N/A')}\n"
            f"📞 ስልክ፦ {owner.get('phone', 'N/A')}\n"
        )

    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]

    message += (
        f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n"
        f"🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"
    )

    await update.message.reply_text(
        message,
        reply_markup=main_menu()
    )


# ==================================================
# SUPPORT (FIXED - NO tel: URL)
# ==================================================

async def support_start(update, context):
    await update.message.reply_text(
        "📞 TANA CARGO የጣና ጭነት እገዛ\n\n"
        "ለእገዛ ለማግኘት @tanapage ይጠቀሙ።\n\n"
        "☎️ 0960011010\n"
        "☎️ 0912991128\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።"
    )
    return ConversationHandler.END


# ==================================================
# ABOUT
# ==================================================

async def about(update, context):
    await update.message.reply_text(
        "🚚 TANA CARGO | ጣና ጭነት\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን "
        "ለማገናኘት፣ የጭነት ማጓጓዣ ሂደትን "
        "ለማቀላጠፍ እና ቀላልና የተደራጀ አገልግሎት "
        "ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "📞 ለተጨማሪ እገዛ፦ @tanapage\n"
        "☎️ 0960011010\n"
        "☎️ 0912991128\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።\n\n"
        "❤️ TANA CARGO — ጭነትንና መኪናን እናገናኛለን።\n\n"
        "🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu()
    )


# ==================================================
# SERVICE PAYMENT MENU
# ==================================================

async def service_payment(update, context):
    await update.message.reply_text(
        payment_methods_text(),
        reply_markup=main_menu()
    )
    return ConversationHandler.END


# ==================================================
# CANCEL
# ==================================================

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ ሂደቱ ተሰርዟል።",
        reply_markup=main_menu()
    )
    return ConversationHandler.END


# ==================================================
# MENU ROUTER
# ==================================================

async def menu_router(update, context):
    text = update.message.text

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)

    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return

    if text == "🚛 መኪና ማስመዝገብ":
        return await truck_start(update, context)

    if text == "🚛 መኪና መፈለግ":
        await find_truck(update, context)
        return

    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return

    if text == "🤝 ግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return

    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return

    if text == "📞 Support":
        return await support_start(update, context)

    if text == "ℹ️ About":
        await about(update, context)
        return


# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(update, context):
    if update.effective_user is None:
        return

    user_id = update.effective_user.id

    payment_index = get_payment_index(user_id)
    if payment_index is not None:
        return await receipt_message(update, context)

    negotiation_index = get_negotiation_index(user_id)
    if negotiation_index is not None:
        return await submit_price(update, context)

    return await menu_router(update, context)


# ==================================================
# MENU INTERRUPT
# ==================================================

async def menu_interrupt(update, context):
    text = update.message.text
    context.user_data.clear()

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)

    if text == "🚛 መኪና ማስመዝገብ":
        return await truck_start(update, context)

    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END

    if text == "📞 Support":
        return await support_start(update, context)

    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END

    if text == "🚛 መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END

    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END

    if text == "🤝 ግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END

    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END

    return ConversationHandler.END


# ==================================================
# ERROR HANDLER
# ==================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"TANA CARGO ERROR: {context.error}")
    try:
        print(f"ERROR UPDATE: {update}")
    except Exception:
        pass


# ==================================================
# RENDER HEALTH SERVER
# ==================================================

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
    print(f"TANA CARGO health server listening on port {port}")
    return server


# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    # DELETE OLD DATABASE ON STARTUP
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print(f"🗑️ አሮጌው ዳታቤዝ ({DB_PATH}) ተሰርዟል!")
        except Exception as e:
            print(f"⚠️ ዳታቤዙን ማጥፋት አልተቻለም: {e}")

    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

    application = Application.builder().token(TOKEN).build()

    application.add_error_handler(error_handler)

    # ==================================================
    # SIMPLE COMMANDS
    # ==================================================

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cargo", cargo_start))
    application.add_handler(CommandHandler("truck", truck_start))
    application.add_handler(CommandHandler("findcargo", find_cargo))
    application.add_handler(CommandHandler("findtruck", find_truck))
    application.add_handler(CommandHandler("myprofile", profile))
    application.add_handler(CommandHandler("support", support_start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("owner", owner_start))
    application.add_handler(
        CommandHandler("connections", show_connection_requests)
    )
    application.add_handler(
        CommandHandler("adminconnections", admin_connection_requests)
    )

    # ==================================================
    # MASTER CONVERSATION
    # ==================================================

    master_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^🚚 ጭነት መለጠፍ$"), cargo_start
            ),
            MessageHandler(
                filters.Regex(r"^🚛 መኪና ማስመዝገብ$"), truck_start
            ),
            MessageHandler(
                filters.Regex(r"^📞 Support$"), support_start
            ),
            MessageHandler(
                filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"),
                service_payment
            ),
            MessageHandler(
                filters.Regex(r"^🔎 ጭነት መፈለግ$"), find_cargo
            ),
            MessageHandler(
                filters.Regex(r"^🚛 መኪና መፈለግ$"), find_truck
            ),
            MessageHandler(
                filters.Regex(r"^👤 የኔ መረጃ$"), profile
            ),
            MessageHandler(
                filters.Regex(r"^🤝 ግንኙነት ጥያቄዎች$"),
                show_connection_requests
            ),
            MessageHandler(
                filters.Regex(r"^ℹ️ About$"), about
            ),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CommandHandler("owner", owner_start),
        ],

        states={
            CARGO_FROM: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_from
                ),
            ],

            CARGO_TO: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_to
                ),
            ],

            CARGO_TYPE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_type
                ),
            ],

            CARGO_VEHICLE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                CallbackQueryHandler(
                    cargo_vehicle, pattern=r"^cargo_vehicle_"
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_vehicle_text
                ),
            ],

            CARGO_SIZE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                CallbackQueryHandler(cargo_size, pattern=r"^size_"),
            ],

            CARGO_WEIGHT: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_weight
                ),
            ],

            CARGO_CALENDAR: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                CallbackQueryHandler(
                    cargo_calendar, pattern=r"^cal_"
                ),
            ],

            CARGO_DATE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_date
                ),
            ],

            CARGO_PHONE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_phone
                ),
            ],

            CARGO_PRICE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, cargo_price
                ),
            ],

            TRUCK_TYPE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_type
                ),
            ],

            TRUCK_PLATE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_plate
                ),
            ],

            TRUCK_CAPACITY: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_capacity
                ),
            ],

            TRUCK_ROUTE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_route
                ),
            ],

            TRUCK_ADDRESS: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_address
                ),
            ],

            TRUCK_DRIVER: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_driver
                ),
            ],

            TRUCK_PHONE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, truck_phone
                ),
            ],

            OWNER_NAME: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, owner_name
                ),
            ],

            OWNER_PHONE: [
                MessageHandler(
                    filters.Regex(
                        r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|"
                        r"🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|"
                        r"👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|"
                        r"💳 የአገልግሎት ክፍያ ለመፈፀም|"
                        r"📞 Support|ℹ️ About)$"
                    ),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, owner_phone
                ),
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel),
        ],

        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )

    application.add_handler(master_conversation)

    # ==================================================
    # ADMIN COMMANDS
    # ==================================================

    application.add_handler(CommandHandler("adminusers", admin_users))
    application.add_handler(
        CallbackQueryHandler(
            admin_delete_user,
            pattern=r"^admindeleteuser_\d+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_delete_connection,
            pattern=r"^admindeleteconnection_\d+$"
        )
    )

    # ==================================================
    # CONNECTION BUTTONS
    # ==================================================

    application.add_handler(
        CallbackQueryHandler(
            connection_request,
            pattern=r"^connect_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            truck_connection_request,
            pattern=r"^truckconnect_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            accept_connection,
            pattern=r"^accept_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            reject_connection,
            pattern=r"^reject_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_agreement_confirm,
            pattern=r"^adminagree_\d+$"
        )
    )

    # ==================================================
    # NEGOTIATION BUTTONS
    # ==================================================

    application.add_handler(
        CallbackQueryHandler(
            agree_price,
            pattern=r"^agreeprice_\d+_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            counter_price_button,
            pattern=r"^counterprice_\d+_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            reject_price_button,
            pattern=r"^rejectprice_\d+_\d+$"
        )
    )

    # ==================================================
    # ADMIN PAYMENT BUTTONS
    # ==================================================

    application.add_handler(
        CallbackQueryHandler(
            admin_payment_action,
            pattern=(
                r"^admin(approve|reject)"
                r"_\d+_"
                r"(requester|other)"
                r"_\d+$"
            ),
        )
    )

    # ==================================================
    # PHOTO RECEIPT
    # ==================================================

    application.add_handler(
        MessageHandler(filters.PHOTO, receipt_photo)
    )

    # ==================================================
    # NON-CONVERSATION TEXT
    # ==================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router
        )
    )

    # ==================================================
    # JOB QUEUE — TIMEOUT CHECKER & EXPIRY CLEANER
    # ==================================================

    if application.job_queue:
        application.job_queue.run_repeating(
            check_negotiation_timeouts,
            interval=30,
            first=10
        )
        print("✅ Timeout checker started (every 30s)")

        application.job_queue.run_repeating(
            clean_expired_posts,
            interval=3600,
            first=60
        )
        print("✅ Expiry cleaner started (every 1 hour)")
    else:
        print("⚠️ JobQueue not available")

    print("TANA CARGO Bot is starting...")
    start_health_server()

    application.run_polling(drop_pending_updates=True)


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":
    main()
