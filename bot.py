import os
import asyncio
import threading
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup,
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

NEGOTIATION_TIMEOUT_SECONDS = 300
CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
DB_PATH = "tana_cargo.db"

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
            timestamp TEXT, expiry_date TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS truck_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            truck_type TEXT, plate TEXT, capacity TEXT, route TEXT,
            address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1,
            timestamp TEXT, expiry_date TEXT
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
    CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE,
    CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE,
) = range(10)

(
    TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE,
    TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE,
) = range(10, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)
(
    CONNECT_NAME, CONNECT_TRUCK_TYPE, CONNECT_PLATE, CONNECT_PHONE,
) = range(19, 23)

# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
            ["📞 Support", "ℹ️ About"],
        ]
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
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
        [InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (ET)", callback_data="cal_et")],
        [InlineKeyboardButton("🌍 የአውሮፓ ካላንደር (GC)", callback_data="cal_gc")],
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

def get_user_username(user_id):
    user = users.get(user_id, {})
    uname = user.get("username", "")
    if uname:
        return "@" + uname
    return None

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
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]):
        return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

def parse_flexible_date(date_text):
    raw = date_text.strip()
    for sep in ["-", ",", ".", " "]:
        raw = raw.replace(sep, "/")
    parts = raw.split("/")
    if len(parts) != 3:
        return None, "የቀን ቅርጸቱ ትክክል አይደለም። ምሳሌ፦ 25/09/2026 ወይም 25-9-2026 ወይም 25,9,2026"
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2])
    except ValueError:
        return None, "ቀን/ወር/ዓመት በቁጥር መሆን አለበት።"
    if day < 1 or day > 31:
        return None, "ቀን 1 እና 31 መካከል መሆን አለበት።"
    if month < 1 or month > 12:
        return None, "ወር 1 እና 12 መካከል መሆን አለበት።"
    if year < 1900 or year > 2100:
        return None, "ዓመት ትክክል አይደለም።"
    try:
        date_obj = datetime(year, month, day)
    except ValueError:
        return None, "ያስገቡት ቀን ትክክል አይደለም።"
    return date_obj, ""

def convert_calendar(date_obj, calendar):
    if calendar == "ET":
        gc_year = date_obj.year + 7
        gc_month = date_obj.month + 8
        if gc_month > 12:
            gc_month -= 12
            gc_year += 1
        gc_date = f"{date_obj.day:02d}/{gc_month:02d}/{gc_year}"
        et_date = f"{date_obj.day:02d}/{date_obj.month:02d}/{date_obj.year}"
    else:
        et_year = date_obj.year - 7
        et_month = date_obj.month - 8
        if et_month <= 0:
            et_month += 12
            et_year -= 1
        et_date = f"{date_obj.day:02d}/{et_month:02d}/{et_year}"
        gc_date = f"{date_obj.day:02d}/{date_obj.month:02d}/{date_obj.year}"
    return et_date, gc_date, ""

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
    calendar = context.user_data["cargo"].get("calendar", "GC")

    date_obj, error = parse_flexible_date(date_text)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return CARGO_DATE

    et_date, gc_date, error = convert_calendar(date_obj, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return CARGO_DATE

    try:
        gc_obj = datetime.strptime(gc_date, "%d/%m/%Y")
    except Exception:
        await update.message.reply_text("❌ የቀን ስሌት ስህተት።")
        return CARGO_DATE

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if gc_obj < today:
        await update.message.reply_text(
            "❌ **ያለፈ ቀን አይቀበልም!**\n\n"
            "እባክዎ የወደፊት ቀን ያስገቡ።"
        )
        return CARGO_DATE

    max_date = today + timedelta(days=30)
    if gc_obj > max_date:
        await update.message.reply_text(
            "❌ **ከ1 ወር (30 ቀናት) በላይ የሆነ ቀን አይቀበልም!**\n\n"
            "እባክዎ በ30 ቀናት ውስጥ ያለ ቀን ያስገቡ።"
        )
        return CARGO_DATE

    context.user_data["cargo"]["date"] = date_text
    context.user_data["cargo"]["date_et"] = et_date
    context.user_data["cargo"]["date_gc"] = gc_date

    try:
        expiry_date = gc_obj + timedelta(hours=CARGO_EXPIRY_HOURS)
        context.user_data["cargo"]["expiry_date"] = expiry_date.isoformat()
    except Exception:
        context.user_data["cargo"]["expiry_date"] = ""

    await update.message.reply_text(
        f"📅 የተመረጠው ቀን፦\n🇪🇹 ET: {et_date}\n🌍 GC: {gc_date}\n\n"
        "8️⃣ ስልክ ቁጥር ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    phone = update.message.text.strip()
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
    raw = update.message.text.strip()
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

    cargo_posts.append(cargo.copy())
    try:
        db_save_cargo(cargo)
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n"
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
    plate = update.message.text.strip()
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
    capacity = update.message.text.strip()
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
    phone = update.message.text.strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nእባክዎ እንደገና ይጻፉ።\nምሳሌ፦ 0912345678")
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

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    route_display = truck.get("route", "በማንኛውም ቦታ")

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n"
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
# FIND CARGO (ለደንበኞች - የባለቤት መረጃ ድብቅ)
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

        owner_line = ""
        phone_line = ""
        if is_admin_user:
            owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
            owner_line = f"👤 የጭነት ባለቤት፦ {owner_name}\n"
            phone_line = f"📞 ስልክ፦ {cargo.get('phone', 'N/A')}\n"

        msg = (
            f"📦 **ጭነት {cargo_id}**\n"
            f"📍 መነሻ፦ {cargo['from']}\n"
            f"📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
            f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n"
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"{owner_line}"
            f"{phone_line}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"👉 ጭነት {cargo_id} ን መርጠዋል ይጫኑ",
                callback_data=f"connect_{cargo_posts.index(cargo)}"
            )
        ]])

        await update.message.reply_text(msg, reply_markup=kb)

    await update.message.reply_text(
        f"📋 ጠቅላላ {len(active_cargos)} ጭነቶች ተገኝተዋል።"
    )

# ==================================================
# FIND TRUCK (ለደንበኞች - የባለቤት መረጃ ድብቅ)
# ==================================================

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

        owner_line = ""
        phone_line = ""
        plate_line = ""
        driver_line = ""
        if is_admin_user:
            owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
            owner_line = f"👤 ባለቤት፦ {owner_name}\n"
            phone_line = f"📞 ስልክ፦ {truck.get('phone', 'N/A')}\n"
            plate_line = f"🔢 ታርጋ፦ {truck.get('plate', 'N/A')}\n"
            driver_line = f"👨‍✈️ ሹፌር፦ {truck.get('driver', 'N/A')}\n"

        msg = (
            f"🚛 **የጭነት መኪና {truck_id}**\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {route_display}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"{driver_line}"
            f"{plate_line}"
            f"{owner_line}"
            f"{phone_line}"
            "━━━━━━━━━━━━━━━━━━━━"
        )

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"👉 የጭነት መኪና {truck_id} ን መርጠዋል ይጫኑ",
                callback_data=f"truckconnect_{truck_posts.index(truck)}"
            )
        ]])

        await update.message.reply_text(msg, reply_markup=kb)

    await update.message.reply_text(
        f"📋 ጠቅላላ {len(active_trucks)} መኪኖች ተገኝተዋል።"
    )

# ==================================================
# CARGO CONNECTION REQUEST
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])
    active_cargos = [c for c in cargo_posts if c.get("active", True)]

    if index < 0 or index >= len(active_cargos):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END

    cargo = active_cargos[index]
    real_index = cargo_posts.index(cargo)
    requester = update.effective_user

    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    for req in connection_requests:
        if (req.get("type") == "cargo" and req.get("cargo_index") == real_index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END

    context.user_data["connect_info"] = {
        "type": "cargo",
        "item_index": real_index,
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
    info["name"] = update.message.text.strip()
    context.user_data["connect_info"] = info

    if info.get("type") == "cargo":
        await update.message.reply_text(
            "2️⃣ ጭነቱን ለማጓጓዝ የሚጠቀሙበትን **የመኪና አይነት** ይጻፉ።\n"
            "ምሳሌ፦ Isuzu / ካሶኒ / ኦባማ / ተሳቢ"
        )
        return CONNECT_TRUCK_TYPE
    else:
        await update.message.reply_text(
            "2️⃣ **የጭነት አይነት** እና **የጭነት አድራሻ** ይጻፉ።\n"
            "ምሳሌ፦ ሲሚንቶ ባህርዳር"
        )
        return CONNECT_TRUCK_TYPE

async def connect_truck_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip()

    if info.get("type") == "cargo":
        info["truck_type"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text(
            "3️⃣ የመኪናውን **ታርጋ ቁጥር** ይጻፉ።\nምሳሌ፦ 12345"
        )
        return CONNECT_PLATE
    else:
        info["cargo_info"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text(
            "3️⃣ **የመንገድ ዝርዝር** ይጻፉ።\nምሳሌ፦ ባህርዳር → አዲስ አበባ"
        )
        return CONNECT_PLATE

async def connect_plate(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip()

    if info.get("type") == "cargo":
        is_valid, error = validate_plate(text)
        if not is_valid:
            await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 12345")
            return CONNECT_PLATE
        info["plate"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text(
            "4️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\n"
            "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
            "ምሳሌ፦ 0912345678"
        )
        return CONNECT_PHONE
    else:
        info["route"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text(
            "4️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\n"
            "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
            "ምሳሌ፦ 0912345678"
        )
        return CONNECT_PHONE

async def connect_phone(update, context):
    phone = update.message.text.strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CONNECT_PHONE

    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
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
            "requester_phone": phone,
            "requester_truck_type": info.get("truck_type", ""),
            "requester_plate": info.get("plate", ""),
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
            "requester_phone": phone,
            "requester_cargo_info": info.get("cargo_info", ""),
            "requester_route": info.get("route", ""),
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
        "✅ **መረጃዎ ተቀብለናል!**\n\n"
        "📌 አስተዳዳሪው ሲያረጋግጥ ወዲያውኑ እናሳውቅዎታለን።\n"
        "🙏 እናመሰግናለን!",
        reply_markup=main_menu(requester.id)
    )

    await send_admin_notification(context, req_index)

    return ConversationHandler.END

# ==================================================
# TRUCK CONNECTION REQUEST
# ==================================================

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])
    active_trucks = [t for t in truck_posts if t.get("active", True)]

    if index < 0 or index >= len(active_trucks):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END

    truck = active_trucks[index]
    real_index = truck_posts.index(truck)
    requester = update.effective_user

    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END

    for req in connection_requests:
        if (req.get("type") == "truck" and req.get("truck_index") == real_index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "negotiating", "awaiting_payment", "admin_confirming"]):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END

    context.user_data["connect_info"] = {
        "type": "truck",
        "item_index": real_index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }

    await query.edit_message_text(
        "📝 **መረጃዎን ያስገቡ**\n\n"
        "1️⃣ ሙሉ ስምዎን ይጻፉ።"
    )
    return CONNECT_NAME

# ==================================================
# SEND ADMIN NOTIFICATION (1 ጊዜ ብቻ)
# ==================================================

async def send_admin_notification(context, req_index):
    req = connection_requests[req_index]

    if req.get("admin_notified"):
        return

    req["admin_notified"] = True

    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
        cargo_owner_phone = cargo.get("phone", "N/A")

        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"📦 **የጭነት ዝርዝር:**\n"
            f"   • መነሻ: {cargo['from']}\n"
            f"   • መድረሻ: {cargo['to']}\n"
            f"   • አይነት: {cargo['type']}\n"
            f"   • ክብደት: {cargo['weight']}\n"
            f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
            f"👤 **የጭነት ባለቤት:**\n"
            f"   • ስም: {cargo_owner_name}\n"
            f"   • ስልክ: {cargo_owner_phone}\n\n"
            f"👤 **ጠያቂ (የመኪና ባለቤት):**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • የመኪና አይነት: {req.get('requester_truck_type', 'N/A')}\n"
            f"   • ታርጋ: {req.get('requester_plate', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **ጭነት ፈላጊ ደንበኛ ጭነት መርጧል!**\n\n"
            "📌 1 ጊዜ ብቻ ግንኙነት የጠየቁ ደንበኞች አሉ። "
            "እባክዎን **የግንኙነት ጥያቄዎች** ውስጥ ገብተው "
            "ደንበኞችን ያስተናግዷቸው።"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
        truck_owner_phone = truck.get("phone", "N/A")

        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"🚛 **የመኪና ዝርዝር:**\n"
            f"   • አይነት: {truck['type']}\n"
            f"   • አቅም: {truck['capacity']}\n"
            f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
            f"   • መንገድ: {truck.get('route', 'N/A')}\n\n"
            f"👤 **የመኪና ባለቤት:**\n"
            f"   • ስም: {truck_owner_name}\n"
            f"   • ስልክ: {truck_owner_phone}\n\n"
            f"👤 **ጠያቂ (የጭነት ባለቤት):**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • የጭነት አይነት: {req.get('requester_cargo_info', 'N/A')}\n"
            f"   • መንገድ: {req.get('requester_route', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የመኪና ፈላጊ ደንበኛ መኪና መርጧል!**\n\n"
            "📌 1 ጊዜ ብቻ ግንኙነት የጠየቁ ደንበኞች አሉ። "
            "እባክዎን **የግንኙነት ጥያቄዎች** ውስጥ ገብተው "
            "ደንበኞችን ያስተናግዷቸው።"
        )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Right", callback_data=f"adminright_{req_index}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{req_index}")
        ],
        [
            InlineKeyboardButton("🗑️ Clear", callback_data=f"adminclear_{req_index}")
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
# ADMIN RIGHT CONFIRM (2 ጊዜ ብቻ ለባለቤቱ)
# ==================================================

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return

    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]

    if req.get("admin_right_confirmed"):
        await query.answer("⚠️ ይህ ቀድሞ ተረጋግጧል!", show_alert=True)
        return

    req["admin_right_confirmed"] = True
    req["status"] = "negotiating"

    await query.edit_message_text(
        "✅ **Right ተረጋግጧል!**\n\n"
        "ለሁለቱም ወገኖች ማሳወቂያ ተልኳል።"
    )

    # ==========================================
    # 1. ለፈላጊው (Seeker) - 2 ጊዜ ብቻ
    # ==========================================
    seeker_id = req["requester_id"]
    item_type = "የጭነት መኪና" if req.get("type") == "truck" else "የጭነት"

    seeker_text = (
        f"ውድ ደንበኛችን፣\n\n"
        f"የፈለጉት {item_type} ባለቤት "
        f"ምላሽ እንደሰጡ እናሳውቃለን።\n\n"
        "📌 እባክዎ ትንሽ ይጠብቁ።\n\n"
        "🙏 እናመሰግናለን!"
    )

    for i in range(2):
        try:
            await context.bot.send_message(chat_id=seeker_id, text=seeker_text)
            req["seeker_notified"] = True
        except Exception:
            pass
        if i == 0:
            await asyncio.sleep(10 * 60)

    # ==========================================
    # 2. ለባለቤቱ (Owner) - 2 ጊዜ ብቻ
    # ==========================================
    owner_id = other_party_id(req)

    if req.get("type") == "truck":
        owner_text = (
            "ውድ ደንበኛችን፣\n\n"
            "የለጠፉትን የጭነት መኪና የሚፈልግ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n"
            f"📞 {SUPPORT_PHONE_2}\n\n"
            "ወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" "
            "የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n"
            "🙏 እናመሰግናለን!"
        )
    else:
        owner_text = (
            "ውድ ደንበኛችን፣\n\n"
            "የለጠፉትን ጭነት የሚወስድ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n"
            f"📞 {SUPPORT_PHONE_2}\n\n"
            "ወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" "
            "የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n"
            "🙏 እናመሰግናለን!"
        )

    for i in range(2):
        try:
            await context.bot.send_message(chat_id=owner_id, text=owner_text)
            req["owner_notified"] = True
        except Exception:
            pass
        if i == 0:
            await asyncio.sleep(10 * 60)

# ==================================================
# ADMIN X DELETE (Super Admin ብቻ)
# ==================================================

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ ማጥፋት ይችላል!", show_alert=True)
        return

    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(connection_requests):
        return

    req = connection_requests[index]
    req["status"] = "deleted"

    negotiation_timeouts.pop(index, None)

    await query.edit_message_text(
        "❌ **ተሰርዟል!**\n\nይህ ጥያቄ በ Super Admin ተሰርዟል።"
    )

# ==================================================
# ADMIN CLEAR (ማሳወቂያ ብቻ ማጥፋት)
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
# SHOW CONNECTION REQUESTS (Admin Menu - ሙሉ መረጃ)
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

    received = []
    for i, req in enumerate(connection_requests):
        if req["status"] not in ["deleted"]:
            received.append((i, req))

    if not received:
        await update.message.reply_text(
            "🤝 እስካሁን የግንኙነት ጥያቄ የለም።",
            reply_markup=main_menu(user_id)
        )
        return

    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            truck_owner_name = users.get(req["truck_owner_id"], {}).get("name", "N/A")
            truck_owner_phone = truck.get("phone", "N/A")

            text = (
                f"📥 **ጥያቄ #{i + 1}**\n\n"
                f"🚛 **የመኪና ዝርዝር:**\n"
                f"   • አይነት: {truck['type']}\n"
                f"   • አቅም: {truck['capacity']}\n"
                f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"   • መንገድ: {truck.get('route', 'N/A')}\n"
                f"   • ሹፌር: {truck.get('driver', 'N/A')}\n\n"
                f"👤 **የመኪና ባለቤት:**\n"
                f"   • ስም: {truck_owner_name}\n"
                f"   • ስልክ: {truck_owner_phone}\n\n"
                f"👤 **ጠያቂ (የጭነት ባለቤት):**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • የጭነት አይነት: {req.get('requester_cargo_info', 'N/A')}\n"
                f"   • መንገድ: {req.get('requester_route', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            cargo_owner_name = users.get(req["cargo_owner_id"], {}).get("name", "N/A")
            cargo_owner_phone = cargo.get("phone", "N/A")

            text = (
                f"📥 **ጥያቄ #{i + 1}**\n\n"
                f"📦 **የጭነት ዝርዝር:**\n"
                f"   • መነሻ: {cargo['from']}\n"
                f"   • መድረሻ: {cargo['to']}\n"
                f"   • አይነት: {cargo['type']}\n"
                f"   • ክብደት: {cargo['weight']}\n"
                f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
                f"👤 **የጭነት ባለቤት:**\n"
                f"   • ስም: {cargo_owner_name}\n"
                f"   • ስልክ: {cargo_owner_phone}\n\n"
                f"👤 **ጠያቂ (የመኪና ባለቤት):**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • የመኪና አይነት: {req.get('requester_truck_type', 'N/A')}\n"
                f"   • ታርጋ: {req.get('requester_plate', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )

        buttons = []
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{i + 1} አጥፋ (Super)",
                    callback_data=f"admindeleteconnection_{i}"
                )
            ])

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
        )

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
        "✅ መልዕክትዎ ለ Admin/Super Admin ተልኳል።\n\n"
        "📸 ፎቶም መላክ ይችላሉ!",
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
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=text
                )
            else:
                await context.bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=text
                )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ ፎቶ/ስክሪንሾትዎ ለ Admin/Super Admin ተልኳል።\n\n"
        "📌 ሌላ መልዕክት ወይም ፎቶ መላክ ይችላሉ።",
        reply_markup=main_menu(user_id)
    )
# ==================================================
# OWNER REGISTRATION
# ==================================================

async def owner_start(update, context):
    context.user_data["owner"] = {}
    await update.message.reply_text("📦 የጭነት ባለቤት ምዝገባ\n\n1️⃣ ስምዎን ይጻፉ።")
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
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return OWNER_PHONE

    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id

    user = update.effective_user
    users.setdefault(user.id, {
        "name": user.full_name, "username": user.username or "", "id": user.id,
    })
    users[user.id]["owner"] = owner.copy()

    try:
        db_save_user(user.id, user.full_name, user.username or "",
                     owner_name=owner["name"], owner_phone=owner["phone"])
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
    text = update.message.text.strip()

    if text in ["በስምምነት", "በድርድር", "ድርድር", "ስምምነት"]:
        price = None
        price_display = "በስምምነት"
    else:
        try:
            price = float(text.replace(",", "").replace("ብር", "").strip())
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text(
                "❌ የዋጋውን ቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\n"
                "ምሳሌ፦ 55000 ወይም በስምምነት"
            )
            return
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return

    req["offer_version"] = req.get("offer_version", 0) + 1
    version = req["offer_version"]
    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None
    req["offers"].append({
        "user_id": user_id, "price": price,
        "display": price_display, "version": version,
    })
    req["current_offer"] = price
    req["current_offer_by"] = user_id
    req["status"] = "negotiating"

    await update.message.reply_text(
        f"💰 የላኩት ዋጋ፦ {price_display}\n\n"
        "⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    buttons = [
        [InlineKeyboardButton(
            f"✅ ተስማማለሁ {price_display}",
            callback_data=f"agreeprice_{active_index}_{version}"
        )],
        [InlineKeyboardButton(
            "❌ አልስማማሁም",
            callback_data=f"rejectprice_{active_index}_{version}"
        )],
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
            "🎉 **Congratulations!** 🎉\n\n"
            "✅ ሁለታችሁም ተስማምታችኋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price_display}\n\n"
            f"📌 ከእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n\n"
            "💳 አሁን እያንዳንዱ ወገን የራሱን 1% ይከፍላል።\n"
            "📸 ደረሰኙን ወደዚህ ቻት ይላኩ።"
        )

        success_text = (
            "🎉 **እንኳን ደስ ያላችሁ!** 🎉\n\n"
            "🤝 ሁለታችሁም ተስማምታችኋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price_display}\n\n"
            f"📌 የእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n\n"
            "💳 ደረሰኙን አሁን ወደዚህ ቻት ይላኩ።\n"
            "🔒 ሁለቱም ክፍያዎች Admin ሲያረጋግጥ ሙሉ መረጃ ይከፈታል።"
        )

        for uid in [req["requester_id"], other_party_id(req)]:
            try:
                await context.bot.send_message(chat_id=uid, text=success_text)
            except Exception:
                pass
        return

    other_user = (other_party_id(req) if user_id == req["requester_id"]
                  else req["requester_id"])
    buttons = [
        [InlineKeyboardButton(
            f"✅ እኔም እስማማለሁ {price_display}",
            callback_data=f"agreeprice_{index}_{req.get('offer_version', 0)}"
        )],
        [InlineKeyboardButton(
            "❌ አልስማማሁም",
            callback_data=f"rejectprice_{index}_{req.get('offer_version', 0)}"
        )],
    ]

    await query.edit_message_text(
        f"✅ {price_display} ላይ ተስማምተዋል።\n\n⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                "🤝 የዋጋ ስምምነት ማረጋገጫ\n\n"
                f"💰 {price_display}\n\n"
                "ሌላኛው ወገን ተስማምቷል።\n"
                "እርስዎም ከተስማሙ ✅ ይጫኑ።"
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
    if user_id != req["requester_id"] and user_id != other_party_id(req):
        return

    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["final_price"] = None

    await query.edit_message_text(
        "❌ **ድርድራችሁ አልተሳካም**\n\n"
        "በድጋሚ መደራደር ይቻላል።\n\n"
        "💬 አዲስ ዋጋ ይጻፉ።"
    )

    other_user = (other_party_id(req) if user_id == req["requester_id"]
                  else req["requester_id"])
    try:
        await context.bot.send_message(
            chat_id=other_user,
            text="❌ ሌላኛው ወገን አልተስማማም።\n\n💬 አዲስ ዋጋ ያቅርቡ።"
        )
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
        if (req["status"] == "awaiting_payment"
            and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
            return saved
    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (req["status"] == "awaiting_payment"
            and (req["requester_id"] == user_id or other_party_id(req) == user_id)):
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

    await update.message.reply_text(
        "🧾 የክፍያ Receipt ተቀብለናል።\n\n⏳ Admin ይመረምራል።"
    )

    await send_admin_payment_request(
        context, index, side, user_id,
        receipt_text=receipt, payment_version=payment_version
    )

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

    await update.message.reply_text(
        "🧾 የክፍያ Screenshot ተቀብለናል።\n\n⏳ Admin ይመረምራል።"
    )

    await send_admin_payment_request(
        context, index, side, user_id,
        photo_id=file_id, payment_version=payment_version
    )

# ==================================================
# SEND ADMIN PAYMENT REQUEST
# ==================================================

async def send_admin_payment_request(context, index, side, user_id,
                                     receipt_text=None, photo_id=None,
                                     payment_version=None):
    req = connection_requests[index]
    side_name = "ጠያቂ" if side == "requester" else (
        "የመኪና ባለቤት" if req.get("type") == "truck" else "የጭነት ባለቤት"
    )

    if payment_version is None:
        payment_version = req.get(f"{side}_payment_version", 0)

    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ APPROVE",
            callback_data=f"adminapprove_{index}_{side}_{payment_version}"
        ),
        InlineKeyboardButton(
            "❌ REJECT",
            callback_data=f"adminreject_{index}_{side}_{payment_version}"
        ),
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
                await context.bot.send_photo(
                    chat_id=admin_id, photo=photo_id,
                    caption=text, reply_markup=buttons
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id, text=text, reply_markup=buttons
                )
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

    action, index, side, button_version = (
        parts[0], int(parts[1]), parts[2], int(parts[3])
    )
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

    user_to_notify = (req["requester_id"] if side == "requester"
                      else other_party_id(req))

    if action == "adminapprove":
        req[f"{side}_payment_approved"] = True
        req[f"{side}_payment_rejected"] = False
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        if (req.get("requester_payment_approved")
            and req.get("other_payment_approved")):
            await share_full_information(index, context)
        else:
            try:
                await context.bot.send_message(
                    chat_id=user_to_notify,
                    text="✅ ክፍያዎ ተረጋግጧል።\n\n⏳ የሁለተኛው ወገን ማረጋገጫ ይጠበቃል።"
                )
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
            await context.bot.send_message(
                chat_id=user_to_notify,
                text="❌ ክፍያዎ አልተፈቀደም።\n\nእባክዎ እንደገና ይላኩ።"
            )
        except Exception:
            pass

# ==================================================
# SHARE FULL INFORMATION
# ==================================================

async def share_full_information(index, context):
    req = connection_requests[index]
    if req.get("full_info_shared"):
        return
    if not (req.get("requester_payment_approved")
            and req.get("other_payment_approved")):
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
    requester_name = req.get("requester_name", "N/A")
    requester_phone = req.get("requester_phone", "N/A")

    if req.get("type") == "truck":
        truck = truck_posts[req["truck_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የመኪና ባለቤት")
        owner_phone = truck.get("phone", "")

        requester_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n"
            "🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ባለቤት፦ {owner_name}\n"
            f"📞 ስልክ፦ {owner_phone or 'የለም'}\n"
            f"🚛 መኪና፦ {truck['type']}\n"
            f"🔢 ታርጋ፦ {truck.get('plate', 'የለም')}\n\n"
            "🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n"
            "🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 ጠያቂ፦ {requester_name}\n"
            f"📞 ስልክ፦ {requester_phone}\n\n"
            "🤝 አሁን በቀጥታ ተገናኙ።"
        )
    else:
        cargo = cargo_posts[req["cargo_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የጭነት ባለቤት")
        owner_phone = cargo.get("phone", "")

        requester_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n"
            "🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የጭነት ባለቤት፦ {owner_name}\n"
            f"📞 ስልክ፦ {owner_phone or 'የለም'}\n\n"
            f"📦 {cargo['type']}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n\n"
            "🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO — ተጠናቋል!**\n\n"
            "🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ጠያቂ፦ {requester_name}\n"
            f"📞 ስልክ፦ {requester_phone}\n\n"
            "🤝 አሁን በቀጥታ ተገናኙ።"
        )

    for uid, msg in [(requester_id, requester_msg), (owner_id, owner_msg)]:
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception:
            pass

# ==================================================
# MENU ROUTER
# ==================================================

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context); return
    if text == "🚛 መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 መኪና መፈለግ":
        await find_truck(update, context); return
    if text == "👤 የኔ መረጃ":
        await profile(update, context); return
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context); return
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\n"
            "ማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n"
            "📌 Admin ወይም Super Admin ብቻ ያየዋል።",
            reply_markup=main_menu(uid)
        )
        context.user_data["sending_admin_message"] = True
        return
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context); return
    if text == "📞 Support":
        return await support_start(update, context)
    if text == "ℹ️ About":
        await about(update, context); return

# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(update, context):
    if update.effective_user is None:
        return

    user_id = update.effective_user.id

    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)

    if context.user_data.get("connect_info"):
        return

    if get_payment_index(user_id) is not None:
        return await receipt_message(update, context)

    if get_negotiation_index(user_id) is not None:
        return await submit_price(update, context)

    return await menu_router(update, context)

# ==================================================
# START / PROFILE / SUPPORT / ABOUT / PAYMENT / CANCEL
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    users.setdefault(user.id, {
        "name": user.full_name, "username": user.username or "", "id": user.id,
    })
    try:
        db_save_user(user.id, user.full_name, user.username or "")
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(user.id)
    )

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {
        "name": update.effective_user.full_name,
        "username": update.effective_user.username or "",
        "id": user_id,
    })

    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user['name']}\n"
        f"🔗 Username፦ @{user['username'] or 'የለም'}\n"
        f"🆔 Telegram ID፦ {user['id']}\n"
    )

    if "owner" in user:
        owner = user["owner"]
        message += (
            f"\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n"
            f"👤 {owner.get('name', 'N/A')}\n"
            f"📞 {owner.get('phone', 'N/A')}\n"
        )

    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]

    message += (
        f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n"
        f"🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"
    )

    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        "📞 TANA CARGO የጣና ጭነት እገዛ\n\n"
        "ለእገዛ ለማግኘት @tanapage ይጠቀሙ።\n\n"
        f"☎️ {SUPPORT_PHONE}\n"
        f"☎️ {SUPPORT_PHONE_2}\n\n"
        "🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text(
        "🚚 **TANA CARGO | ጣና ጭነት**\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን "
        "ለማገናኘት፣ የጭነት ማጓጓዣ ሂደትን "
        "ለማቀላጠፍ እና ቀላልና የተደራጀ አገልግሎት "
        "ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "❤️ TANA CARGO — ጭነትንና መኪናን እናገናኛለን።\n\n"
        "🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu(update.effective_user.id)
    )

async def service_payment(update, context):
    await update.message.reply_text(
        payment_methods_text(),
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ ሂደቱ ተሰርዟል።",
        reply_markup=main_menu(update.effective_user.id)
    )
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
# HEALTH SERVER
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
# EXPIRED CLEANER (72 HOURS) - የተስተካከለ
# ==================================================

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

# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("🗑️ አሮጌው ዳታቤዝ ተሰርዟል!")
        except Exception as e:
            print(f"⚠️ {e}")

    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

    application = Application.builder().token(TOKEN).build()
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

    # Master Conversation
    master_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), cargo_start),
            MessageHandler(filters.Regex(r"^🚛 መኪና ማስመዝገብ$"), truck_start),
            MessageHandler(filters.Regex(r"^📞 Support$"), support_start),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), service_payment),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), find_cargo),
            MessageHandler(filters.Regex(r"^🚛 መኪና መፈለግ$"), find_truck),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), profile),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), show_connection_requests),
            MessageHandler(filters.Regex(r"^📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), about),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CommandHandler("owner", owner_start),
        ],
        states={
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [
                CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text),
            ],
            CARGO_SIZE: [CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_CALENDAR: [CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ROUTE: [
                CallbackQueryHandler(truck_route_choice, pattern=r"^route_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_route),
            ],
            TRUCK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
            OWNER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name)],
            OWNER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_phone)],
            CONNECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_truck_type)],
            CONNECT_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_plate)],
            CONNECT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )
    application.add_handler(master_conversation)

    # Connection callbacks
    application.add_handler(CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"))
    application.add_handler(CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"))

    # Admin right/X/Clear
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_clear_notification, pattern=r"^adminclear_\d+$"))

    # Negotiation buttons
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

    # Photo receipt (የክፍያ ደረሰኝ + የAdmin መልዕክት ፎቶ)
    application.add_handler(MessageHandler(filters.PHOTO, receipt_photo))

    # Document (PDF, ስክሪንሾት ፋይሎች)
    application.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND,
        receipt_photo
    ))

    # Text router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Job queue - 72 hour cleaner
    if application.job_queue:
        application.job_queue.run_repeating(clean_expired_posts, interval=3600, first=60)
        print("✅ Expiry cleaner started (every 1 hour)")

    print("TANA CARGO Bot is starting...")
    start_health_server()
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
