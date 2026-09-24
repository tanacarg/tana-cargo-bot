import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None

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

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "0960011010")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@tanacargosupport")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/tanacargosupport")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

CBE_ACCOUNT = os.getenv("CBE_ACCOUNT", "1000031098231")
ABAY_ACCOUNT = os.getenv("ABAY_ACCOUNT", "2011011003938018")
CBE_BIRR = os.getenv("CBE_BIRR", "0918132914")
TELEBIRR = os.getenv("TELEBIRR", "0918132914")
ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "solomon")

DATABASE_URL = os.getenv("DATABASE_URL", "")


# ==================================================
# DATABASE
# ==================================================

def get_db():
    if not DATABASE_URL or psycopg2 is None:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"DB error: {e}")
        return None


def init_db():
    if not DATABASE_URL or psycopg2 is None:
        print("⚠️  No DB — using memory storage.")
        return
    try:
        conn = get_db()
        if not conn:
            return
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                username TEXT,
                owner_name TEXT,
                owner_phone TEXT,
                negotiation_requests JSONB DEFAULT '[]'::jsonb,
                payment_requests JSONB DEFAULT '[]'::jsonb
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cargo_posts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                from_location TEXT,
                to_location TEXT,
                type TEXT,
                vehicle TEXT,
                size INTEGER,
                size_name TEXT,
                weight TEXT,
                date TEXT,
                phone TEXT,
                price DOUBLE PRECISION,
                price_status TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS truck_posts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                type TEXT,
                plate TEXT,
                capacity TEXT,
                from_location TEXT,
                route TEXT,
                address TEXT,
                driver TEXT,
                phone TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS connection_requests (
                id SERIAL PRIMARY KEY,
                type TEXT,
                cargo_index INTEGER,
                truck_index INTEGER,
                cargo_owner_id BIGINT,
                truck_owner_id BIGINT,
                requester_id BIGINT,
                requester_name TEXT,
                status TEXT DEFAULT 'pending',
                offers JSONB DEFAULT '[]'::jsonb,
                current_offer DOUBLE PRECISION,
                current_offer_by BIGINT,
                offer_version INTEGER DEFAULT 0,
                final_price DOUBLE PRECISION,
                requester_confirmed BOOLEAN DEFAULT FALSE,
                other_confirmed BOOLEAN DEFAULT FALSE,
                requester_payment_submitted BOOLEAN DEFAULT FALSE,
                other_payment_submitted BOOLEAN DEFAULT FALSE,
                requester_payment_approved BOOLEAN DEFAULT FALSE,
                other_payment_approved BOOLEAN DEFAULT FALSE,
                requester_payment_rejected BOOLEAN DEFAULT FALSE,
                other_payment_rejected BOOLEAN DEFAULT FALSE,
                requester_payment_version INTEGER DEFAULT 0,
                other_payment_version INTEGER DEFAULT 0,
                requester_receipt TEXT,
                other_receipt TEXT,
                requester_receipt_photo TEXT,
                other_receipt_photo TEXT,
                full_info_shared BOOLEAN DEFAULT FALSE,
                admin_agreement_requested BOOLEAN DEFAULT FALSE,
                admin_agreement_confirmed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("✅ DB initialized.")
    except Exception as e:
        print(f"❌ DB init error: {e}")


# ==================================================
# MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []
relay_messages = {}

USE_DB = bool(DATABASE_URL) and psycopg2 is not None


# ==================================================
# DB HELPERS
# ==================================================

def db_load_users():
    if not USE_DB:
        return
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users")
        for row in cur.fetchall():
            uid = row["user_id"]
            users[uid] = {
                "name": row["name"] or "",
                "username": row["username"] or "",
                "id": uid,
            }
            if row.get("owner_name"):
                users[uid]["owner"] = {
                    "name": row["owner_name"],
                    "phone": row["owner_phone"] or "",
                    "user_id": uid,
                }
            if row.get("negotiation_requests"):
                users[uid]["negotiation_requests"] = row["negotiation_requests"]
            if row.get("payment_requests"):
                users[uid]["payment_requests"] = row["payment_requests"]
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Load users error: {e}")


def db_save_user(user_id):
    if not USE_DB:
        return
    try:
        user = users.get(user_id, {})
        owner = user.get("owner", {})
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (user_id, name, username, owner_name, owner_phone,
                negotiation_requests, payment_requests)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                owner_name = EXCLUDED.owner_name,
                owner_phone = EXCLUDED.owner_phone,
                negotiation_requests = EXCLUDED.negotiation_requests,
                payment_requests = EXCLUDED.payment_requests
        """, (
            user_id, user.get("name", ""), user.get("username", ""),
            owner.get("name", ""), owner.get("phone", ""),
            json.dumps(user.get("negotiation_requests", [])),
            json.dumps(user.get("payment_requests", [])),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Save user error: {e}")


def db_load_cargo():
    if not USE_DB:
        return
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM cargo_posts WHERE active = TRUE ORDER BY id")
        cargo_posts.clear()
        for row in cur.fetchall():
            cargo_posts.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "from": row["from_location"],
                "to": row["to_location"],
                "type": row["type"],
                "vehicle": row["vehicle"],
                "size": row["size"],
                "size_name": row["size_name"],
                "weight": row["weight"],
                "date": row["date"],
                "phone": row["phone"],
                "price": row["price"],
                "price_status": row["price_status"] or "agreement",
                "active": row["active"],
            })
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Load cargo error: {e}")


def db_save_cargo(cargo):
    if not USE_DB:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cargo_posts (user_id, from_location, to_location, type,
                vehicle, size, size_name, weight, date, phone, price, price_status, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (
            cargo["user_id"], cargo["from"], cargo["to"], cargo["type"],
            cargo["vehicle"], cargo["size"], cargo["size_name"],
            cargo["weight"], cargo["date"], cargo["phone"],
            cargo.get("price"), cargo.get("price_status", "agreement")
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return new_id
    except Exception as e:
        print(f"❌ Save cargo error: {e}")
        return None


def db_deactivate_cargo(index):
    if not USE_DB:
        return
    try:
        cargo = cargo_posts[index] if 0 <= index < len(cargo_posts) else None
        if not cargo or "id" not in cargo:
            return
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE cargo_posts SET active = FALSE WHERE id = %s", (cargo["id"],))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Deactivate cargo error: {e}")


def db_load_trucks():
    if not USE_DB:
        return
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM truck_posts WHERE active = TRUE ORDER BY id")
        truck_posts.clear()
        for row in cur.fetchall():
            truck_posts.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "type": row["type"],
                "plate": row["plate"],
                "capacity": row["capacity"],
                "from": row["from_location"],
                "route": row["route"],
                "address": row["address"],
                "driver": row["driver"],
                "phone": row["phone"],
                "active": row["active"],
            })
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Load truck error: {e}")


def db_save_truck(truck):
    if not USE_DB:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO truck_posts (user_id, type, plate, capacity, from_location,
                route, address, driver, phone, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (
            truck["user_id"], truck["type"], truck["plate"],
            truck["capacity"], truck["from"], truck["route"],
            truck["address"], truck["driver"], truck["phone"]
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return new_id
    except Exception as e:
        print(f"❌ Save truck error: {e}")
        return None


def db_deactivate_truck(index):
    if not USE_DB:
        return
    try:
        truck = truck_posts[index] if 0 <= index < len(truck_posts) else None
        if not truck or "id" not in truck:
            return
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE truck_posts SET active = FALSE WHERE id = %s", (truck["id"],))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Deactivate truck error: {e}")


def db_save_request(req):
    if not USE_DB:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO connection_requests (type, cargo_index, truck_index,
                cargo_owner_id, truck_owner_id, requester_id, requester_name,
                status, offers, current_offer, current_offer_by, offer_version,
                final_price, requester_confirmed, other_confirmed,
                requester_payment_submitted, other_payment_submitted,
                requester_payment_approved, other_payment_approved,
                requester_payment_rejected, other_payment_rejected,
                requester_payment_version, other_payment_version,
                full_info_shared)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            req.get("type"), req.get("cargo_index"), req.get("truck_index"),
            req.get("cargo_owner_id"), req.get("truck_owner_id"),
            req.get("requester_id"), req.get("requester_name"),
            req.get("status", "pending"), json.dumps(req.get("offers", [])),
            req.get("current_offer"), req.get("current_offer_by"),
            req.get("offer_version", 0), req.get("final_price"),
            req.get("requester_confirmed", False), req.get("other_confirmed", False),
            req.get("requester_payment_submitted", False),
            req.get("other_payment_submitted", False),
            req.get("requester_payment_approved", False),
            req.get("other_payment_approved", False),
            req.get("requester_payment_rejected", False),
            req.get("other_payment_rejected", False),
            req.get("requester_payment_version", 0),
            req.get("other_payment_version", 0),
            req.get("full_info_shared", False),
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return new_id
    except Exception as e:
        print(f"❌ Save request error: {e}")
        return None


def db_update_request(req):
    if not USE_DB:
        return
    try:
        rid = req.get("id")
        if not rid:
            return
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE connection_requests SET
                status = %s, offers = %s::jsonb, current_offer = %s,
                current_offer_by = %s, offer_version = %s, final_price = %s,
                requester_confirmed = %s, other_confirmed = %s,
                requester_payment_submitted = %s, other_payment_submitted = %s,
                requester_payment_approved = %s, other_payment_approved = %s,
                requester_payment_rejected = %s, other_payment_rejected = %s,
                requester_payment_version = %s, other_payment_version = %s,
                requester_receipt = %s, other_receipt = %s,
                requester_receipt_photo = %s, other_receipt_photo = %s,
                full_info_shared = %s, admin_agreement_requested = %s,
                admin_agreement_confirmed = %s
            WHERE id = %s
        """, (
            req.get("status"), json.dumps(req.get("offers", [])),
            req.get("current_offer"), req.get("current_offer_by"),
            req.get("offer_version", 0), req.get("final_price"),
            req.get("requester_confirmed", False), req.get("other_confirmed", False),
            req.get("requester_payment_submitted", False),
            req.get("other_payment_submitted", False),
            req.get("requester_payment_approved", False),
            req.get("other_payment_approved", False),
            req.get("requester_payment_rejected", False),
            req.get("other_payment_rejected", False),
            req.get("requester_payment_version", 0),
            req.get("other_payment_version", 0),
            req.get("requester_receipt"), req.get("other_receipt"),
            req.get("requester_receipt_photo"), req.get("other_receipt_photo"),
            req.get("full_info_shared", False),
            req.get("admin_agreement_requested", False),
            req.get("admin_agreement_confirmed", False),
            rid,
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Update request error: {e}")


def db_load_requests():
    if not USE_DB:
        return
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM connection_requests ORDER BY id")
        connection_requests.clear()
        for row in cur.fetchall():
            req = dict(row)
            connection_requests.append(req)
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Load requests error: {e}")


# ==================================================
# STATES
# ==================================================

(
    CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE,
    CARGO_WEIGHT, CARGO_DATE, CARGO_PHONE, CARGO_PRICE,
) = range(9)

(
    TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_FROM, TRUCK_ROUTE,
    TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE,
) = range(9, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)
SUPPORT_MESSAGE = 19
NEGOTIATE_PRICE = 20
NEGOTIATE_CONFIRM = 21
PAYMENT_RECEIPT = 22


MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ"
    r"|🔎 ጭነት መፈለግ"
    r"|🚛 መኪና ማስመዝገብ"
    r"|🚛 መኪና መፈለግ"
    r"|👤 የኔ መረጃ"
    r"|🤝 ግንኙነት ጥያቄዎች"
    r"|💳 የአገልግሎት ክፍያ ለመፈፀም"
    r"|📞 Support"
    r"|ℹ️ About)$"
)


# ==================================================
# MENUS
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
    return points.index(cargo_start) < points.index(cargo_end)


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
        "💳 የአገልግሎት ክፍያ መረጃ፦\n\n"
        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        f"🔢 {CBE_ACCOUNT}\n"
        f"👤 {ACCOUNT_NAME}\n\n"
        "🏦 Abay Bank\n"
        f"🔢 {ABAY_ACCOUNT}\n"
        f"👤 {ACCOUNT_NAME}\n\n"
        "💰 CBE Birr\n"
        f"📱 {CBE_BIRR}\n"
        f"👤 {ACCOUNT_NAME}\n\n"
        "📱 Telebirr\n"
        f"📱 {TELEBIRR}\n"
        f"👤 {ACCOUNT_NAME}\n\n"
        "እኛን ስለመረጡን እናመሰግናለን 🙏"
    )


def other_party_id(req):
    if req.get("type") == "truck":
        return req.get("truck_owner_id")
    return req.get("cargo_owner_id")


def get_user_phone(user_id, phone_type=None):
    user = users.get(user_id, {})
    if phone_type == "owner":
        return user.get("owner", {}).get("phone", "")
    if phone_type == "cargo":
        for cargo in reversed(cargo_posts):
            if cargo["user_id"] == user_id:
                return cargo.get("phone", "")
        return ""
    if phone_type == "truck":
        for truck in reversed(truck_posts):
            if truck["user_id"] == user_id:
                return truck.get("phone", "")
        return ""
    if user.get("owner", {}).get("phone"):
        return user["owner"]["phone"]
    for cargo in reversed(cargo_posts):
        if cargo["user_id"] == user_id:
            return cargo.get("phone", "")
    for truck in reversed(truck_posts):
        if truck["user_id"] == user_id:
            return truck.get("phone", "")
    return ""


def _add_user_list(user_id, key, index):
    users.setdefault(user_id, {})
    lst = users[user_id].setdefault(key, [])
    if index not in lst:
        lst.append(index)
    db_save_user(user_id)


def _remove_user_list(user_id, key, index):
    if user_id in users and key in users[user_id]:
        try:
            users[user_id][key].remove(index)
        except ValueError:
            pass
        db_save_user(user_id)


def set_negotiation_request(index):
    req = connection_requests[index]
    _add_user_list(req["requester_id"], "negotiation_requests", index)
    _add_user_list(other_party_id(req), "negotiation_requests", index)


def set_payment_request(index):
    req = connection_requests[index]
    _add_user_list(req["requester_id"], "payment_requests", index)
    _add_user_list(other_party_id(req), "payment_requests", index)


def clear_negotiation_request(req):
    try:
        idx = connection_requests.index(req)
    except ValueError:
        return
    for user_id in [req["requester_id"], other_party_id(req)]:
        _remove_user_list(user_id, "negotiation_requests", idx)


def clear_payment_request(req):
    try:
        idx = connection_requests.index(req)
    except ValueError:
        return
    for user_id in [req["requester_id"], other_party_id(req)]:
        _remove_user_list(user_id, "payment_requests", idx)


def get_negotiation_index(user_id):
    saved_list = users.get(user_id, {}).get("negotiation_requests", [])
    for saved in reversed(saved_list):
        if 0 <= saved < len(connection_requests):
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


def get_payment_index(user_id):
    saved_list = users.get(user_id, {}).get("payment_requests", [])
    for saved in reversed(saved_list):
        if 0 <= saved < len(connection_requests):
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
    db_save_user(user.id)

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "ከታች ያለውን ምናሌ ይጠቀሙ።",
        reply_markup=main_menu()
    )


# ==================================================
# CARGO
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
    context.user_data["cargo"]["from"] = update.message.text
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO


async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text
    await update.message.reply_text(
        "3️⃣ የጭነቱን አይነት ይጻፉ።\n"
        "ምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ"
    )
    return CARGO_TYPE


async def cargo_type(update, context):
    context.user_data["cargo"]["type"] = update.message.text
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
        f"🚛 የተመረጠው መኪና፦ {value}\n\n"
        "5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard()
    )
    return CARGO_SIZE


async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"):
        return
    cargo["vehicle"] = update.message.text
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
        "6️⃣ የጭነቱን ክብደት ይጻፉ።\n"
        "ምሳሌ፦ 10 ቶን"
    )
    return CARGO_WEIGHT


async def cargo_weight(update, context):
    context.user_data["cargo"]["weight"] = update.message.text
    await update.message.reply_text(
        "7️⃣ የሚጫንበትን ቀን ይጻፉ።\n"
        "ምሳሌ፦ 25/09/2026"
    )
    return CARGO_DATE


async def cargo_date(update, context):
    context.user_data["cargo"]["date"] = update.message.text
    await update.message.reply_text("8️⃣ ስልክ ቁጥር ይጻፉ።")
    return CARGO_PHONE


async def cargo_phone(update, context):
    cargo = context.user_data["cargo"]
    cargo["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n\n"
        "💰 በብር ብቻ ያስገቡ።\n"
        "ምሳሌ፦ 50000\n\n"
        "ወይም 'በስምምነት' ብለው ይጻፉ።"
    )
    return CARGO_PRICE


async def cargo_price(update, context):
    raw = update.message.text.strip().replace(",", "").replace("ብር", "").strip()
    cargo = context.user_data["cargo"]

    if normalize(raw) in ["በስምምነት", "agreement", "negotiable", "none"]:
        cargo["price"] = None
        cargo["price_status"] = "agreement"
    else:
        try:
            price = float(raw)
        except ValueError:
            await update.message.reply_text(
                "❌ እባክዎ ዋጋውን ቁጥር ብቻ ይጻፉ።\n"
                "ምሳሌ፦ 50000\n"
                "ወይም 'በስምምነት' ይጻፉ።"
            )
            return CARGO_PRICE
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return CARGO_PRICE
        cargo["price"] = price
        cargo["price_status"] = "fixed"

    cargo["user_id"] = update.effective_user.id
    cargo["active"] = True
    new_id = db_save_cargo(cargo)
    if new_id:
        cargo["id"] = new_id
    cargo_posts.append(cargo.copy())

    if cargo.get("price"):
        price_line = f"💰 የመጫኛ ዋጋ፦ {cargo['price']:,.2f} ብር"
    else:
        price_line = "💰 የመጫኛ ዋጋ፦ በስምምነት"

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን፦ {cargo['date']}\n"
        f"{price_line}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
    )

    for truck in truck_posts:
        if not truck.get("active", True):
            continue
        if not vehicle_match(truck.get("type"), cargo.get("vehicle")):
            continue
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
                    f"📅 {cargo['date']}\n"
                    f"{price_line}\n\n"
                    "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
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

    message = "🔎 የተለጠፉ ጭነቶች፦\n\n"
    buttons = []

    for i, cargo in enumerate(active_cargos, 1):
        if cargo.get("price"):
            price_text = f"{cargo['price']:,.2f} ብር"
        else:
            price_text = "በስምምነት"
        message += (
            f"📦 ጭነት #{i}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 መኪና፦ {cargo['vehicle']}\n"
            f"📊 {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n"
            f"💰 {price_text}\n"
            "🔒 የባለቤቱ ስልክ ተደብቋል።\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"🔢 {i} — 🤝 ግንኙነት ጠይቅ",
                callback_data=f"connect_{i - 1}"
            )
        ])

    message += "\n👉 እባክዎን የፈለጉትን የጭነት ዝርዝር በቁጥር ይምረጡ።"
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==================================================
# CARGO CONNECTION
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
        await query.edit_message_text("❌ ይህ ጭነት ቀድሞ ተስማምቶ ከፍርግርግ ዝርዝር ተወግዷል።")
        return

    requester = update.effective_user
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return

    for req in connection_requests:
        if (req.get("type") == "cargo"
            and req["cargo_index"] == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "negotiating", "awaiting_payment"]):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return

    users.setdefault(requester.id, {
        "name": requester.full_name,
        "username": requester.username or "",
        "id": requester.id,
    })

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
    }

    new_id = db_save_request(request)
    if new_id:
        request["id"] = new_id
    connection_requests.append(request)

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄዎ ተላክቷል።\n\n"
        "🔒 የግል ስልክ ቁጥሮች ተደብቀዋል።\n"
        "💬 የዋጋ ድርድሩ በTANA CARGO Bot ውስጥ ይካሄዳል።\n\n"
        "⏳ የጭነቱ ባለቤት ሲቀበል ይነገርዎታል።"
    )

    try:
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 TANA CARGO — አዲስ ግንኙነት ጥያቄ\n\n"
                    f"📌 Request ID፦ {len(connection_requests)-1}\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}"
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
                f"👤 ጠያቂ፦ {requester.full_name}"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🤝 ግንኙነት", callback_data=f"accept_{len(connection_requests)-1}"),
                InlineKeyboardButton("❌ ውድቅ", callback_data=f"reject_{len(connection_requests)-1}")
            ]])
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
        if other_party_id(req) == user_id:
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
            try:
                truck = truck_posts[req["truck_index"]]
                text += (
                    f"📥 የመጣ #{i + 1}\n"
                    f"🚛 {truck['type']}\n"
                    f"📍 {truck['from']} ➡️ {truck['route']}\n"
                )
            except (IndexError, KeyError):
                pass
        else:
            try:
                cargo = cargo_posts[req["cargo_index"]]
                text += (
                    f"📥 የመጣ #{i + 1}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                    f"📦 {cargo['type']}\n"
                )
            except (IndexError, KeyError):
                pass
        text += (
            f"👤 {req['requester_name']}\n"
            f"📌 ሁኔታ፦ {req['status']}\n\n"
        )
        if req["status"] == "pending":
            buttons.append([
                InlineKeyboardButton(f"✅ ተቀበል #{i + 1}", callback_data=f"accept_{i}"),
                InlineKeyboardButton(f"❌ ውድቅ #{i + 1}", callback_data=f"reject_{i}"),
            ])

    for i, req in sent:
        text += f"📤 የላኩት #{i + 1}\n📌 ሁኔታ፦ {req['status']}\n"
        if req.get("final_price"):
            text += f"💰 የመጨረሻ ዋጋ፦ {req['final_price']:,.2f} ብር\n"
        if req["status"] == "awaiting_payment":
            text += "💳 ክፍያ ይጠበቃል።\n"
        if req["status"] == "completed":
            text += "✅ ሁለቱም ክፍያዎች ተፈቅደዋል።\n"
        text += "\n"

    await update.message.reply_text(
        text,
        reply_markup=(InlineKeyboardMarkup(buttons) if buttons else main_menu())
    )


# ==================================================
# ACCEPT / REJECT
# ==================================================

async def accept_connection(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    if other_party_id(req) != update.effective_user.id:
        await query.answer("❌ ይህን ጥያቄ እርስዎ መቀበል አይችሉም።", show_alert=True)
        return
    if req["status"] != "pending":
        await query.answer("⚠️ ይህ ጥያቄ ከዚህ በፊት ተስተናግዷል።", show_alert=True)
        return

    req["status"] = "negotiating"
    req["requester_confirmed"] = False
    req["other_confirmed"] = False
    req["current_offer"] = None
    req["current_offer_by"] = None
    req["final_price"] = None
    set_negotiation_request(index)
    db_update_request(req)

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
    clear_negotiation_request(req)
    clear_payment_request(req)
    db_update_request(req)
    await query.edit_message_text("❌ የግንኙነት ጥያቄው ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text="❌ የግንኙነት ጥያቄዎ ውድቅ ተደርጓል።"
        )
    except Exception:
        pass


# ==================================================
# RELAY
# ==================================================

async def send_relay_to_admin(context, req_index, sender_id, text, buttons=None):
    if not ADMIN_USER_ID:
        return False
    req = connection_requests[req_index]
    receiver_id = other_party_id(req)
    relay_id = max(relay_messages.keys(), default=0) + 1
    relay_messages[relay_id] = {
        "req_index": req_index,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "text": text,
    }
    kb = [[InlineKeyboardButton("➡️ ወደ ሌላኛው ወገን ላክ", callback_data=f"relay_{relay_id}")]]
    if buttons:
        kb.extend(buttons)
    try:
        sender = users.get(sender_id, {}).get("name", "Unknown")
        await context.bot.send_message(
            chat_id=int(ADMIN_USER_ID),
            text=f"📨 TANA CARGO Relay #{relay_id}\n👤 ላኪ፦ {sender}\n\n{text}",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return True
    except Exception:
        return False


async def relay_forward(update, context):
    query = update.callback_query
    await query.answer()
    if not ADMIN_USER_ID or str(update.effective_user.id) != str(ADMIN_USER_ID):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        rid = int(query.data.split("_")[1])
        item = relay_messages.get(rid)
        if not item:
            raise ValueError()
        await context.bot.send_message(
            chat_id=item["receiver_id"],
            text=f"📨 ከTANA CARGO የተላለፈ መልእክት፦\n\n{item['text']}"
        )
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=item["sender_id"],
            text="✅ መልእክትዎ Admin አስተላልፎልዎታል።"
        )
    except Exception:
        await query.answer("❌ መልእክቱ ሊተላለፍ አልቻለም።", show_alert=True)


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
    try:
        price = float(text.replace(",", "").replace("ብር", "").strip())
    except ValueError:
        await update.message.reply_text(
            "❌ የዋጋውን ቁጥር ብቻ ይጻፉ።\n"
            "ምሳሌ፦ 55000"
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
    req["offers"].append({"user_id": user_id, "price": price, "version": version})
    req["current_offer"] = price
    req["current_offer_by"] = user_id
    req["status"] = "negotiating"
    set_negotiation_request(active_index)
    db_update_request(req)

    await update.message.reply_text(
        f"💰 የላኩት ዋጋ፦ {price:,.2f} ብር\n\n"
        "⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    buttons = [
        [InlineKeyboardButton(
            f"✅ እስማማለሁ {price:,.2f} ብር",
            callback_data=f"agreeprice_{active_index}_{version}"
        )],
        [InlineKeyboardButton(
            "💬 ሌላ ዋጋ ላክ",
            callback_data=f"counterprice_{active_index}_{version}"
        )],
    ]

    relay_text = (
        "💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n"
        f"💵 የቀረበው ዋጋ፦ {price:,.2f} ብር\n\n"
        "ከዚህ ዋጋ ጋር ከተስማሙ ተስማምቻለሁ ይጫኑ። ወይም ሌላ ዋጋ ያቅርቡ።"
    )
    sent = await send_relay_to_admin(context, active_index, user_id, relay_text, buttons)
    if sent:
        await update.message.reply_text(
            "⏳ የዋጋ መልእክትዎ መጀመሪያ Admin ዘንድ ተልኳል። Admin ሲያስተላልፈው ለሌላኛው ወገን ይደርሳል።"
        )
    else:
        await update.message.reply_text("⚠️ Admin relay አልተገኘም።")


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
        await query.answer("⚠️ ይህ የድሮ ዋጋ ነው። አዲሱን ዋጋ ይጠቀሙ።", show_alert=True)
        return
    if req["status"] != "negotiating":
        await query.answer("⚠️ ይህ ድርድር አሁን ንቁ አይደለም።", show_alert=True)
        return
    if user_id != req["requester_id"] and user_id != other_party_id(req):
        await query.answer("❌ ይህ የእርስዎ ድርድር አይደለም።", show_alert=True)
        return

    price = req.get("current_offer")
    if price is None:
        await query.answer("⚠️ አሁን የቀረበ ዋጋ የለም።", show_alert=True)
        return
    if user_id == req["current_offer_by"]:
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

    db_update_request(req)

    if req["requester_confirmed"] and req["other_confirmed"]:
        req["final_price"] = price
        req["status"] = "awaiting_payment"
        req["requester_payment_submitted"] = False
        req["other_payment_submitted"] = False
        req["requester_payment_approved"] = False
        req["other_payment_approved"] = False
        req["requester_payment_rejected"] = False
        req["other_payment_rejected"] = False
        clear_negotiation_request(req)
        set_payment_request(index)
        db_update_request(req)

        each_side, total = commission_amount(price)
        await query.edit_message_text(
            "✅ ሁለቱም ወገኖች በተናጠል ተስማምተዋል!\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n\n"
            f"📌 ከእያንዳንዱ ወገን 1%፦ {each_side:,.2f} ብር\n"
            f"💵 ጠቅላላ TANA CARGO ኮሚሽን፦ {total:,.2f} ብር\n\n"
            "💳 አሁን እያንዳንዱ ወገን የራሱን 1% ይከፍላል።"
        )
        await send_admin_agreement_request(context, index)

        payment_text = (
            "💳 TANA CARGO — ክፍያ\n\n"
            f"💰 የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n"
            f"📌 የእርስዎ 1%፦ {each_side:,.2f} ብር\n\n"
            f"{payment_methods_text()}\n\n"
            "🧾 ክፍያ ካደረጉ በኋላ Receipt No. ወይም Screenshot ይላኩ።\n\n"
            "🔒 ሁለቱም ክፍያዎች Admin እስኪያረጋግጥ ድረስ የግል መረጃ አይጋራም።"
        )
        for user in [req["requester_id"], other_party_id(req)]:
            try:
                await context.bot.send_message(chat_id=user, text=payment_text)
            except Exception:
                pass
        return

    other_user = other_party_id(req) if user_id == req["requester_id"] else req["requester_id"]
    buttons = [
        [InlineKeyboardButton(
            f"✅ እኔም እስማማለሁ {price:,.2f} ብር",
            callback_data=f"agreeprice_{index}_{current_version}"
        )],
        [InlineKeyboardButton(
            "💬 ሌላ ዋጋ ላክ",
            callback_data=f"counterprice_{index}_{current_version}"
        )],
    ]
    await query.edit_message_text(
        f"✅ {price:,.2f}
