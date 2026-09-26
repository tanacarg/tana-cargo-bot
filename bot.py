import os
import threading
from datetime import datetime
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

# Support
SUPPORT_URL = "https://t.me/tanapage"
SUPPORT_USERNAME = "@tanapage"
SUPPORT_PHONE_1 = "0960011010"
SUPPORT_PHONE_2 = "091 816 1179"
SUPPORT_PHONE_3 = "091 299 1128"

# Admin (Super Admin — አንተ ብቻ)
SUPER_ADMIN_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# Payment Accounts
CBE_ACCOUNT = "1000139288697"
CBE_BIRR = "0918161179"
TELEBIRR = "0918161179"
ABAY_ACCOUNT = "1000139288697"
ACCOUNT_NAME = "Melak Gebeyhu"

# Timeout
TIMEOUT_SECONDS = 300  # 5 minutes
ADMIN_TIMEOUT = 900  # 15 minutes (for admin confirmation)


# ==================================================
# DATABASE / MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []
relay_messages = {}

# Admin Management
admins = set()  # Regular admins (Super Admin is separate)


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
    CARGO_DATE,
    CARGO_PHONE,
    CARGO_PRICE,
) = range(9)

(
    TRUCK_TYPE,
    TRUCK_PLATE,
    TRUCK_CAPACITY,
    TRUCK_FROM,
    TRUCK_ROUTE,
    TRUCK_ADDRESS,
    TRUCK_DRIVER,
    TRUCK_PHONE,
) = range(9, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)
SUPPORT_MESSAGE = 19
NEGOTIATE_PRICE = 20
NEGOTIATE_CONFIRM = 21
PAYMENT_RECEIPT = 22

# Admin Management States
ADD_ADMIN_ID, ADD_ADMIN_CONFIRM = range(23, 25)
BROADCAST_MESSAGE = 25


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


# ==================================================
# ADMIN HELPERS
# ==================================================

def is_super_admin(user_id):
    """Super Admin (አንተ ብቻ)"""
    return int(user_id) == SUPER_ADMIN_ID


def is_admin(user_id):
    """Admin ወይም Super Admin"""
    return is_super_admin(user_id) or int(user_id) in admins


def is_admin_only(user_id):
    """Regular Admin (Super Admin አይደለም)"""
    return is_admin(user_id) and not is_super_admin(user_id)


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
        return req["truck_owner_id"]
    return req["cargo_owner_id"]


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


def get_user_full_info(user_id):
    user = users.get(user_id, {})
    return {
        "name": user.get("name", "Unknown"),
        "username": user.get("username", ""),
        "user_id": user_id,
        "phone": get_user_phone(user_id),
    }


def get_negotiation_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("negotiation_request")
    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (req["status"] == "negotiating"
                and (req["requester_id"] == user_id
                     or other_party_id(req) == user_id)):
            return saved
    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (req["status"] == "negotiating"
                and (req["requester_id"] == user_id
                     or other_party_id(req) == user_id)):
            return i
    return None


def get_payment_index(user_id):
    user = users.get(user_id, {})
    saved = user.get("payment_request")
    if saved is not None and 0 <= saved < len(connection_requests):
        req = connection_requests[saved]
        if (req["status"] == "awaiting_payment"
                and (req["requester_id"] == user_id
                     or other_party_id(req) == user_id)):
            return saved
    for i in range(len(connection_requests) - 1, -1, -1):
        req = connection_requests[i]
        if (req["status"] == "awaiting_payment"
                and (req["requester_id"] == user_id
                     or other_party_id(req) == user_id)):
            return i
    return None


def set_negotiation_request(index):
    req = connection_requests[index]
    users.setdefault(req["requester_id"], {})
    users.setdefault(other_party_id(req), {})
    users[req["requester_id"]]["negotiation_request"] = index
    users[other_party_id(req)]["negotiation_request"] = index


def set_payment_request(index):
    req = connection_requests[index]
    users.setdefault(req["requester_id"], {})
    users.setdefault(other_party_id(req), {})
    users[req["requester_id"]]["payment_request"] = index
    users[other_party_id(req)]["payment_request"] = index


def clear_negotiation_request(req):
    for user_id in [req["requester_id"], other_party_id(req)]:
        if user_id in users:
            users[user_id].pop("negotiation_request", None)


def clear_payment_request(req):
    for user_id in [req["requester_id"], other_party_id(req)]:
        if user_id in users:
            users[user_id].pop("payment_request", None)


# ==================================================
# PHONE NUMBER DETECTION
# ==================================================

import re

def contains_phone_number(text):
    """
    የስልክ ቁጥር ካለ ይመልሳል:
    - 09... / 07... / +251... / 0960...
    - ዜሮ ዘጠኝ / ዜሮ ሰባት
    """
    if not text:
        return False
    
    text_lower = text.lower().replace(" ", "").replace("-", "")
    
    # የቁጥር ፓተርኖች
    patterns = [
        r"\+?251[97]\d{8}",       # +2519... / +2517...
        r"0[97]\d{8}",             # 09... / 07...
        r"0[97]\s?\d{3}\s?\d{4}",  # 09 xxx xxxx
    ]
    
    for pattern in patterns:
        if re.search(pattern, text_lower):
            return True
    
    # የፅሁፍ ፓተርኖች
    text_clean = text.lower().replace(" ", "")
    word_patterns = [
        "ዜሮዘጠኝ", "ዜሮሰባት",
        "ዜሮ ዘጠኝ", "ዜሮ ሰባት",
        "zero nine", "zero seven",
    ]
    
    for wp in word_patterns:
        if wp.replace(" ", "") in text_clean:
            return True
    
    return False


# ==================================================
# TIMEOUT SYSTEM
# ==================================================

async def check_timeout(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    
    for i, req in enumerate(connection_requests):
        if req.get("status") != "pending":
            continue
        if req.get("timeout_alerted"):
            continue
        
        created_at = req.get("created_at")
        if not created_at:
            req["created_at"] = now
            continue
        
        elapsed = (now - created_at).total_seconds()
        if elapsed >= TIMEOUT_SECONDS:
            req["timeout_alerted"] = True
            try:
                await send_timeout_notifications(context, i)
            except Exception as e:
                print(f"Timeout error: {e}")


async def send_timeout_notifications(context, index):
    req = connection_requests[index]
    owner_id = other_party_id(req)
    owner_info = get_user_full_info(owner_id)
    requester_id = req["requester_id"]
    requester_info = get_user_full_info(requester_id)

    # 1. SMS TO OWNER
    if owner_info["phone"]:
        try:
            sms_text = (
                "TANA CARGO\n\n"
                "ውድ ደንበኛ!\n\n"
                "የእርስዎን ጭነት/መኪና የፈለገ ደንበኛ "
                "በድርድር ይጠብቅዎታል!\n\n"
                "እባክዎ ትንሽ ይጠብቁን ወይም "
                "በውስጥ ይደራደሩ\n\n"
                "https://t.me/tanapage\n"
                f"{SUPPORT_PHONE_1}\n"
                f"{SUPPORT_PHONE_2}"
            )
            await send_sms_to_phone(owner_info["phone"], sms_text)
        except Exception as e:
            print(f"Owner SMS error: {e}")

    # 2. TELEGRAM TO REQUESTER
    try:
        requester_msg = (
            "⏳ TANA CARGO\n\n"
            "ውድ ደንበኛ!\n\n"
            "የፈለጉት ጭነት/መኪና ባለቤት "
            "ገና አልተገናኙም\n\n"
            "⏰ 5 ደቂቃ አልፏል\n\n"
            "🙏 እባክዎ ትንሽ ይጠብቁን\n\n"
            "📞 አድሚን ያገናኛችኋል\n\n"
            "https://t.me/tanapage"
        )
        await context.bot.send_message(
            chat_id=requester_id,
            text=requester_msg
        )
    except Exception as e:
        print(f"Requester msg error: {e}")

    # 3. TELEGRAM TO ADMIN
    if not SUPER_ADMIN_ID:
        return

    text = (
        "🚨 TANA CARGO — Timeout Alert!\n\n"
        "⏰ 5 ደቂቃ አልፏል!\n\n"
    )

    if req.get("type") == "cargo":
        text += "📋 የጭነት ባለቤት ምላሽ አልሰጠም\n\n"
    else:
        text += "🚛 የመኪና ባለቤት ምላሽ አልሰጠም\n\n"

    text += (
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📌 የተጠየቀው ሰው (ባለቤት):\n\n"
        f"📛 ስም: {owner_info['name']}\n"
        f"🆔 Telegram ID: {owner_info['user_id']}\n"
    )

    if owner_info["username"]:
        text += f"🔗 Username: @{owner_info['username']}\n"
    if owner_info["phone"]:
        text += f"📞 ስልክ: {owner_info['phone']}\n"

    text += "\n━━━━━━━━━━━━━━━━━━\n\n"

    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        text += (
            "📦 ጭነት ዝርዝር:\n\n"
            f"📍 መነሻ: {cargo['from']}\n"
            f"📍 መድረሻ: {cargo['to']}\n"
            f"📦 አይነት: {cargo['type']}\n"
            f"🚛 መኪና: {cargo['vehicle']}\n"
            f"📊 መጠን: {cargo['size_name']}\n"
            f"⚖️ ክብደት: {cargo['weight']}\n"
            f"📅 ቀን: {cargo['date']}\n"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        text += (
            "🚛 መኪና ዝርዝር:\n\n"
            f"🔹 አይነት: {truck['type']}\n"
            f"🔢 ታርጋ: {truck.get('plate', 'የለም')}\n"
            f"⚖️ አቅም: {truck['capacity']}\n"
            f"📍 አድራሻ: {truck['address']}\n"
        )

    text += (
        "\n━━━━━━━━━━━━━━━━━━\n\n"
        "📌 ጠያቂ (ደንበኛ):\n\n"
        f"📛 ስም: {requester_info['name']}\n"
        f"🆔 Telegram ID: {requester_info['user_id']}\n"
    )

    if requester_info["username"]:
        text += f"🔗 Username: @{requester_info['username']}\n"
    if requester_info["phone"]:
        text += f"📞 ስልክ: {requester_info['phone']}\n"

    text += (
        "\n━━━━━━━━━━━━━━━━━━\n\n"
        "📞 እባክዎ ተጠቃሚውን ያገኙ!\n\n"
        f"📱 ስልክ 1: {SUPPORT_PHONE_1}\n"
        f"📱 ስልክ 2: {SUPPORT_PHONE_2}\n"
        f"📱 ስልክ 3: {SUPPORT_PHONE_3}\n\n"
        "🙏 TANA CARGO Support"
    )

    try:
        await context.bot.send_message(
            chat_id=int(SUPER_ADMIN_ID),
            text=text
        )
    except Exception as e:
        print(f"Admin timeout error: {e}")


async def send_sms_to_phone(phone, message):
    """SMS placeholder — Termux ሲዘጋጅ ይሠራል."""
    try:
        print(f"[SMS] To: {phone}")
        print(f"[SMS] Msg: {message[:80]}...")
    except Exception as e:
        print(f"SMS error: {e}")


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

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "ከታች ያለውን ምናሌ ይጠቀሙ።",
        reply_markup=main_menu()
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
        cargo_id = cargo.get("id", f"C{i:03d}")
        message += (
            f"📦 ጭነት #{cargo_id}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 መኪና፦ {cargo['vehicle']}\n"
            f"📊 {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n"
            "🔒 የባለቤቱ ስልክ ተደብቋል።\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"🔢 {cargo_id} — 🤝 ግንኙነት ጠይቅ",
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
        await query.edit_message_text("❌ ይህ ጭነት ቀድሞ ተስማምቶ ተወግዷል።")
        return

    requester = update.effective_user

    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return

    # Check if already requested
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

    # Order ID
    order_id = f"CG-{len(connection_requests) + 1:04d}"

    request = {
        "type": "cargo",
        "order_id": order_id,
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
        "created_at": datetime.now(),
        "timeout_alerted": False,
        "admin_confirmed": False,
    }

    connection_requests.append(request)

    await query.edit_message_text(
        f"✅ የግንኙነት ጥያቄዎ ተላክቷል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "🔒 የግል ስልክ ቁጥሮች ተደብቀዋል።\n"
        "💬 የዋጋ ድርድሩ በTANA CARGO Bot ውስጥ ይካሄዳል።\n\n"
        "⏳ የጭነቱ ባለቤት ሲቀበል ይነገርዎታል።"
    )

    # Admin notification
    try:
        if SUPER_ADMIN_ID:
            await context.bot.send_message(
                chat_id=int(SUPER_ADMIN_ID),
                text=(
                    f"🔔 TANA CARGO — አዲስ ግንኙነት ጥያቄ\n\n"
                    f"🆔 Order ID: {order_id}\n"
                    f"👤 ጠያቂ: {requester.full_name}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}"
                )
            )
    except Exception:
        pass

    # Notify cargo owner
    try:
        await context.bot.send_message(
            chat_id=cargo["user_id"],
            text=(
                f"🔔 አዲስ የግንኙነት ጥያቄ!\n\n"
                f"🆔 Order ID: {order_id}\n\n"
                f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                f"📦 {cargo['type']}\n"
                f"🚛 {cargo['vehicle']}\n"
                f"📊 {cargo['size_name']}\n\n"
                f"👤 ጠያቂ: {requester.full_name}\n\n"
                "⏰ እባክዎ በ5 ደቂቃ ውስጥ ምላሽ ይስጡ!\n\n"
                "🔒 የግል መረጃ ለመለዋወጥ ግንኙነት ይጫኑ።"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"🤝 ግንኙነት ({order_id})",
                    callback_data=f"accept_{len(connection_requests)-1}"
                ),
                InlineKeyboardButton(
                    "❌ ውድቅ",
                    callback_data=f"reject_{len(connection_requests)-1}"
                )
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
        order_id = req.get("order_id", f"#{i+1}")
        
        if req.get("type") == "truck":
            try:
                truck = truck_posts[req["truck_index"]]
                text += (
                    f"📥 Order: {order_id}\n"
                    f"🚛 {truck['type']}\n"
                    f"📍 {truck['from']} ➡️ {truck['route']}\n"
                )
            except (IndexError, KeyError):
                pass
        else:
            try:
                cargo = cargo_posts[req["cargo_index"]]
                text += (
                    f"📥 Order: {order_id}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                    f"📦 {cargo['type']}\n"
                )
            except (IndexError, KeyError):
                pass

        text += (
            f"👤 {req['requester_name']}\n"
            f"📌 ሁኔታ: {req['status']}\n\n"
        )

        if req["status"] == "pending":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ ተቀበል {order_id}",
                    callback_data=f"accept_{i}"
                ),
                InlineKeyboardButton(
                    f"❌ ውድቅ {order_id}",
                    callback_data=f"reject_{i}"
                ),
            ])

    for i, req in sent:
        order_id = req.get("order_id", f"#{i+1}")
        text += f"📤 Order: {order_id}\n📌 ሁኔታ: {req['status']}\n"
        if req.get("final_price"):
            text += f"💰 የመጨረሻ ዋጋ: {req['final_price']:,.2f} ብር\n"
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
    req["timeout_alerted"] = True

    set_negotiation_request(index)

    order_id = req.get("order_id", f"#{index+1}")

    await query.edit_message_text(
        f"✅ የግንኙነት ጥያቄውን ተቀብለዋል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "💬 አሁን የዋጋ ድርድር ይጀምራል።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=(
                f"✅ የግንኙነት ጥያቄዎ ተቀባ!\n\n"
                f"🆔 Order ID: {order_id}\n\n"
                "💬 አሁን የመጓጓዣ ዋጋ ድርድር መጀመር ይችላሉ።\n\n"
                "💰 ለመጀመር የሚፈልጉትን ዋጋ በብር ይጻፉ።\n"
                "ምሳሌ: 55000\n\n"
                "🔒 ስልክ ቁጥር መለዋወጥ ክልክል ነው።"
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
    req["timeout_alerted"] = True

    clear_negotiation_request(req)
    clear_payment_request(req)

    order_id = req.get("order_id", f"#{index+1}")

    await query.edit_message_text(f"❌ {order_id} ውድቅ ተደርጓል።")

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=f"❌ የግንኙነት ጥያቄዎ ({order_id}) ውድቅ ተደርጓል።"
        )
    except Exception:
        pass


# ==================================================
# ADMIN MANAGEMENT SYSTEM
# ==================================================

async def addadmin_start(update, context):
    """Super Admin only — Add new admin"""
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ ይህ ተግባር Super Admin ብቻ ነው!\n\n"
            "🔒 Admin ሌላ Admin መጨመር አይችልም።"
        )
        return ConversationHandler.END

    context.user_data["add_admin"] = {}
    await update.message.reply_text(
        "➕ አዲስ Admin መጨመር\n\n"
        "👤 የ Admin User ID ጻፍ።\n"
        "ምሳሌ: 1234567890\n\n"
        "⚠️ User ID ለማግኘት @userinfobot ተጠቀም።"
    )
    return ADD_ADMIN_ID


async def addadmin_id(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END

    try:
        admin_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ቁጥር ብቻ ይጻፉ።")
        return ADD_ADMIN_ID

    if admin_id == SUPER_ADMIN_ID:
        await update.message.reply_text("⚠️ አንተ ቀድሞ Super Admin ነህ!")
        return ConversationHandler.END

    if admin_id in admins:
        await update.message.reply_text("⚠️ ይህ ሰው ቀድሞ Admin ነው!")
        return ConversationHandler.END

    context.user_data["add_admin"]["id"] = admin_id

    admin_user = users.get(admin_id, {})
    admin_name = admin_user.get("name", "Unknown")
    admin_username = admin_user.get("username", "")

    await update.message.reply_text(
        f"👤 Admin መረጃ:\n\n"
        f"📛 ስም: {admin_name}\n"
        f"🆔 ID: {admin_id}\n"
        f"🔗 Username: @{admin_username or 'የለም'}\n\n"
        f"✅ አረጋግጥ?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ አረጋግጥ", callback_data="addadmin_confirm")],
            [InlineKeyboardButton("❌ ሰርዝ", callback_data="addadmin_cancel")],
        ])
    )
    return ADD_ADMIN_CONFIRM


async def addadmin_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.edit_message_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END

    if query.data == "addadmin_cancel":
        await query.edit_message_text("❌ ተሰርዟል።")
        context.user_data.pop("add_admin", None)
        return ConversationHandler.END

    admin_id = context.user_data["add_admin"]["id"]
    admins.add(admin_id)

    await query.edit_message_text(
        f"✅ Admin ተጨምሯል!\n\n"
        f"🆔 ID: {admin_id}\n\n"
        f"📊 ጠቅላላ Admins: {len(admins)}"
    )

    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🎉 እንኳን ደስ አለህ!\n\n"
                "✅ አሁን TANA CARGO Admin ሆነሃል!\n\n"
                "📋 የምትችለው:\n"
                "• ግንኙነቶችን ማረጋገጥ\n"
                "• ክፍያዎችን ማጽደቅ\n"
                "• ደንበኞችን ማየት\n\n"
                "❌ የማትችለው:\n"
                "• ሌላ Admin መጨመር\n"
                "• ሌላ Admin ማስወገድ\n\n"
                "👉 /adminpanel ተጠቀም"
            )
        )
    except Exception:
        pass

    context.user_data.pop("add_admin", None)
    return ConversationHandler.END


async def removeadmin_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ ይህ ተግባር Super Admin ብቻ ነው!\n\n"
            "🔒 Admin ሌላ Admin ማስወገድ አይችልም።"
        )
        return

    if not admins:
        await update.message.reply_text("ℹ️ ምንም Admin የለም።")
        return

    buttons = []
    for admin_id in admins:
        admin_user = users.get(admin_id, {})
        name = admin_user.get("name", "Unknown")
        buttons.append([
            InlineKeyboardButton(
                f"👤 {name} ({admin_id})",
                callback_data=f"removeadmin_{admin_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("❌ ዝጋ", callback_data="removeadmin_cancel")
    ])

    await update.message.reply_text(
        "🗑️ Admin ማስወገድ\n\n"
        "ማንን ማስወገድ ትፈልጋለህ?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def removeadmin_action(update, context):
    query = update.callback_query
    await query.answer()

    if not is_super_admin(update.effective_user.id):
        await query.edit_message_text("❌ Super Admin ብቻ!")
        return

    if query.data == "removeadmin_cancel":
        await query.edit_message_text("❌ ተሰርዟል።")
        return

    admin_id = int(query.data.replace("removeadmin_", ""))

    if admin_id in admins:
        admins.remove(admin_id)

        await query.edit_message_text(
            f"✅ Admin ተወግዷል!\n\n"
            f"🆔 ID: {admin_id}\n\n"
            f"📊 ቀሪ Admins: {len(admins)}"
        )

        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text="❌ ከ TANA CARGO Admin ተወግደሃል።"
            )
        except Exception:
            pass


async def listadmins(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return

    text = "👥 TANA CARGO Admins\n\n━━━━━━━━━━━━━━━━━━\n\n"

    super_user = users.get(SUPER_ADMIN_ID, {})
    text += (
        f"1️⃣ Super Admin (አንተ)\n"
        f"   👤 {super_user.get('name', 'Unknown')}\n"
        f"   🆔 {SUPER_ADMIN_ID}\n"
        f"   🔗 @{super_user.get('username', 'የለም')}\n\n"
    )

    if admins:
        for i, admin_id in enumerate(admins, 2):
            admin_user = users.get(admin_id, {})
            text += (
                f"{i}️⃣ Admin\n"
                f"   👤 {admin_user.get('name', 'Unknown')}\n"
                f"   🆔 {admin_id}\n"
                f"   🔗 @{admin_user.get('username', 'የለም')}\n\n"
            )
    else:
        text += "ℹ️ ሌላ Admin የለም።\n\n"

    text += f"━━━━━━━━━━━━━━━━━━\n\n📊 ጠቅላላ: {1 + len(admins)}"

    await update.message.reply_text(text)


async def adminpanel(update, context):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ!")
        return

    is_super = is_super_admin(user_id)

    stats = (
        f"📊 Statistics:\n"
        f"👥 ደንበኞች: {len(users)}\n"
        f"🚚 ጭነቶች: {len(cargo_posts)}\n"
        f"🚛 መኪኖች: {len(truck_posts)}\n"
        f"🤝 ግንኙነቶች: {len(connection_requests)}\n"
    )

    text = (
        "🔧 TANA CARGO — Admin Panel\n\n"
        f"👤 ሚና: {'Super Admin' if is_super else 'Admin'}\n"
        f"🆔 ID: {user_id}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{stats}\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    buttons = []

    if is_super:
        buttons = [
            [InlineKeyboardButton("👥 Admins ዝርዝር", callback_data="admin_list")],
            [InlineKeyboardButton("➕ Admin ጨምር", callback_data="admin_add")],
            [InlineKeyboardButton("🗑️ Admin አስወግድ", callback_data="admin_remove")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        ]
        text += "\n🔑 አንተ Super Admin ነህ — ሁሉንም ማድረግ ትችላለህ!"
    else:
        buttons = [
            [InlineKeyboardButton("💳 Pending Payments", callback_data="admin_payments")],
            [InlineKeyboardButton("🤝 Pending Connections", callback_data="admin_connections")],
            [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        ]
        text += "\n⚠️ አንተ Admin ነህ — ሌላ Admin መጨመር አትችልም!"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_panel_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await query.answer("❌ Admin ብቻ!", show_alert=True)
        return

    data = query.data

    if data == "admin_list":
        if not is_super_admin(user_id):
            await query.answer("❌ Super Admin ብቻ!", show_alert=True)
            return
        await listadmins(update, context)

    elif data == "admin_add":
        if not is_super_admin(user_id):
            await query.answer("❌ Super Admin ብቻ!", show_alert=True)
            return
        await query.edit_message_text(
            "➕ Admin መጨመር\n\n"
            "ይህን ለማድረግ /addadmin ይጻፉ።"
        )

    elif data == "admin_remove":
        if not is_super_admin(user_id):
            await query.answer("❌ Super Admin ብቻ!", show_alert=True)
            return
        await query.edit_message_text(
            "🗑️ Admin ማስወገድ\n\n"
            "ይህን ለማድረግ /removeadmin ይጻፉ።"
        )

    elif data == "admin_broadcast":
        if not is_super_admin(user_id):
            await query.answer("❌ Super Admin ብቻ!", show_alert=True)
            return
        await query.edit_message_text(
            "📢 Broadcast\n\n"
            "ይህን ለማድረግ /broadcast ይጻፉ።"
        )

    elif data == "admin_payments":
        await query.edit_message_text(
            "💳 Pending Payments\n\n"
            "ይህን ለማየት /pendingpayments ይጻፉ።"
        )

    elif data == "admin_connections":
        await query.edit_message_text(
            "🤝 Pending Connections\n\n"
            "ይህን ለማየት /pendingconnections ይጻፉ።"
        )

    elif data == "admin_users":
        await query.edit_message_text(
            "👥 Users\n\n"
            "ይህን ለማየት /adminusers ይጻፉ።"
        )


# ==================================================
# ADMIN RELAY
# ==================================================

async def send_relay_to_admin(context, req_index, sender_id, text, buttons=None):
    if not SUPER_ADMIN_ID:
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
    kb = [[InlineKeyboardButton(
        "➡️ ወደ ሌላኛው ወገን ላክ",
        callback_data=f"relay_{relay_id}"
    )]]
    if buttons:
        kb.extend(buttons)
    try:
        await context.bot.send_message(
            chat_id=int(SUPER_ADMIN_ID),
            text=f"📨 TANA CARGO Relay #{relay_id}\n\n{text}",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return True
    except Exception:
        return False


async def relay_forward(update, context):
    query = update.callback_query
    await query.answer()
    if not SUPER_ADMIN_ID or str(update.effective_user.id) != str(SUPER_ADMIN_ID):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        rid = int(query.data.split("_")[1])
        item = relay_messages.get(rid)
        if not item:
            raise ValueError()
        await context.bot.send_message(
            chat_id=item["receiver_id"],
            text="📨 ከTANA CARGO የተላለፈ መልእክት:\n\n" + item["text"]
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

    # Phone detection
    if contains_phone_number(text):
        await update.message.reply_text(
            "⚠️ ስልክ ቁጥር መለዋወጥ ክልክል ነው!\n\n"
            "📋 ይህ ከ TANA CARGO መመሪያ ውጭ ነው።\n\n"
            "💬 እባክዎ በ TANA CARGO Bot ውስጥ ብቻ ይደራደሩ።"
        )
        return

    try:
        price = float(text.replace(",", "").replace("ብር", "").strip())
    except ValueError:
        await update.message.reply_text(
            "❌ የዋጋውን ቁጥር ብቻ ይጻፉ።\n"
            "ምሳሌ: 55000"
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

    order_id = req.get("order_id", f"#{active_index+1}")

    await update.message.reply_text(
        f"💰 የላኩት ዋጋ: {price:,.2f} ብር\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    buttons = [
        [InlineKeyboardButton(
            f"✅ እስማማለሁ {price:,.2f} ብር",
            callback_data=f"agreeprice_{active_index}_{version}"
        )],
        [InlineKeyboardButton(
            "❌ አልስማማም",
            callback_data=f"disagreeprice_{active_index}_{version}"
        )],
    ]

    relay_text = (
        f"💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n"
        f"🆔 Order ID: {order_id}\n"
        f"💵 የቀረበው ዋጋ: {price:,.2f} ብር\n\n"
        "ከዚህ ዋጋ ጋር ከተስማሙ ተስማምቻለሁ ይጫኑ። ወይም አልስማማም ይጫኑ።"
    )
    sent = await send_relay_to_admin(context, active_index, user_id, relay_text, buttons)

    if sent:
        await update.message.reply_text(
            "⏳ የዋጋ መልእክትዎ መጀመሪያ Admin ዘንድ ተልኳል።"
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

    # Mark confirmation
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

    order_id = req.get("order_id", f"#{index+1}")

    # Both agreed → Move to payment
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

        each_side, total = commission_amount(price)

        # Success message
        success_text = (
            f"🎉 እንኳን ደስ አላችሁ!\n\n"
            f"✅ በ TANA CARGO | ጣና ጭነት በኩል "
            f"ስምምነት ላይ ደርሳችኋል!\n\n"
            f"🆔 Order ID: {order_id}\n"
            f"💰 የመጨረሻ ዋጋ: {price:,.2f} ብር\n\n"
            f"📌 ከእያንዳንዱ ወገን 1%: {each_side:,.2f} ብር\n"
            f"💵 ጠቅላላ TANA CARGO ኮሚሽን: {total:,.2f} ብር\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💬 ሙሉ ስምና ስልክ አድራሻ "
            "ለመለዋወጥ እባክዎ ከታች ያለውን "
            "የአገልግሎት ክፍያ ለመፈፀም የሚለውን ቁልፍ "
            "ተጭነው ካሉት አካውንቶች መርጠው "
            "ክፍያ ይፈፅሙ።\n\n"
            "🧾 ሲጨርሱ የክፍያ ቁጥር (Receipt No.) "
            "ወይም የክፍያ ፎቶ ስክሪንሾት "
            "አድርገው ያስገቡ።\n\n"
            "🔒 የክፍያ ደረሰኝ ከላኩ በኋላ "
            "አድማይኖች አረጋግጠው "
            "«ግንኙነት» የሚለው ቁልፍ ውስጥ ብቻ "
            "የሚገኝ የሁለቱ ደንበኞች ሙሉ መረጃ "
            "ይለዋወጣሉ።"
        )

        await query.edit_message_text(success_text)

        # Send to both parties
        for user in [req["requester_id"], other_party_id(req)]:
            try:
                await context.bot.send_message(chat_id=user, text=success_text)
            except Exception:
                pass

        # Send admin confirmation request
        await send_admin_agreement_request(context, index)

        return

    # Only one agreed
    other_user = other_party_id(req) if user_id == req["requester_id"] else req["requester_id"]

    buttons = [
        [InlineKeyboardButton(
            f"✅ እኔም እስማማለሁ {price:,.2f} ብር",
            callback_data=f"agreeprice_{index}_{current_version}"
        )],
        [InlineKeyboardButton(
            "❌ አልስማማም",
            callback_data=f"disagreeprice_{index}_{current_version}"
        )],
    ]

    await query.edit_message_text(
        f"✅ {price:,.2f} ብር ላይ ተስማምተዋል።\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "⏳ አሁን የሌላኛው ወገን በተናጠል መስማማት አለበት።"
    )

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                f"🤝 የዋጋ ስምምነት ማረጋገጫ\n\n"
                f"🆔 Order ID: {order_id}\n"
                f"💰 {price:,.2f} ብር\n\n"
                "ሌላኛው ወገን በዚህ ዋጋ ተስማምቷል።\n"
                "እርስዎም ከተስማሙ ከዚህ በታች "
                "ያለውን አዝራር ይጫኑ።"
            ),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception:
        pass


# ==================================================
# DISAGREE PRICE
# ==================================================

async def disagree_price(update, context):
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
        await query.answer("⚠️ ይህ የድሮ ዋጋ ነው።", show_alert=True)
        return

    if req["status"] != "negotiating":
        await query.answer("⚠️ ይህ ድርድር አሁን ንቁ አይደለም።", show_alert=True)
        return

    if user_id != req["requester_id"] and user_id != other_party_id(req):
        await query.answer("❌ ይህ የእርስዎ ድርድር አይደለም።", show_alert=True)
        return

    # Reset confirmations
    req["requester_confirmed"] = False
    req["other_confirmed"] = False

    order_id = req.get("order_id", f"#{index+1}")

    await query.edit_message_text(
        f"❌ አልተስማማም ብለዋል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "💬 አዲስ ዋጋ ለማቅረብ ወይም "
        "ለመደራደር በቀጥታ ይጻፉ።\n\n"
        "📝 ምሳሌ: 55000"
    )

    other_user = other_party_id(req) if user_id == req["requester_id"] else req["requester_id"]

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                f"❌ ሌላኛው ወገን በዚህ ዋጋ አልተስማማም!\n\n"
                f"🆔 Order ID: {order_id}\n\n"
                "💬 አዲስ ዋጋ ይላኩ ወይም ይጠብቁ።"
            )
        )
    except Exception:
        pass


# ==================================================
# COUNTER PRICE
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
        await query.answer("⚠️ ይህ የድሮ ዋጋ ነው።", show_alert=True)
        return

    if req["status"] != "negotiating":
        await query.answer("⚠️ ድርድሩ ንቁ አይደለም።", show_alert=True)
        return

    user_id = update.effective_user.id

    if user_id != req["requester_id"] and user_id != other_party_id(req):
        await query.answer("❌ ይህ የእርስዎ ድርድር አይደለም።", show_alert=True)
        return

    if user_id == req.get("current_offer_by"):
        await query.answer(
            "⚠️ የራስዎን ዋጋ እንደገና መቀየር ከፈለጉ "
            "አዲስ ዋጋ በቀጥታ ይጻፉ።",
            show_alert=True
        )
        return

    set_negotiation_request(index)

    await query.message.reply_text(
        "💬 አዲስ ዋጋ ይጻፉ።\n"
        "ምሳሌ: 55000"
    )


# ==================================================
# ADMIN AGREEMENT REQUEST
# ==================================================

async def send_admin_agreement_request(context, index):
    if not SUPER_ADMIN_ID:
        return
    req = connection_requests[index]
    if req.get("admin_agreement_requested"):
        return
    req["admin_agreement_requested"] = True

    kind = "🚛 የጭነት መኪና" if req.get("type") == "truck" else "📦 ጭነት"
    order_id = req.get("order_id", f"#{index+1}")

    button = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ ስምምነቱን አረጋግጥ እና ከፍርግርግ አስወግድ",
            callback_data=f"adminagree_{index}"
        )
    ]])

    try:
        await context.bot.send_message(
            chat_id=int(SUPER_ADMIN_ID),
            text=(
                f"🤝 TANA CARGO — የስምምነት ማረጋገጫ\n\n"
                f"🆔 Order ID: {order_id}\n"
                f"📌 Request: {index}\n"
                f"{kind}\n"
                f"💰 የተስማሙበት ዋጋ: {req.get('final_price', 0):,.2f} ብር\n\n"
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

    if not SUPER_ADMIN_ID or str(update.effective_user.id) != str(SUPER_ADMIN_ID):
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
    req["status"] = req.get("status", "negotiating")

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

    order_id = req.get("order_id", f"#{index+1}")

    notice = (
        f"✅ TANA CARGO — Admin ማረጋገጫ\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "የደንበኛውና የተመረጠው መዝገብ ላይ "
        "ያለው እቃ/መኪና ስምምነት በAdmin ተረጋግጧል።\n"
        "🔒 የግል መረጃ እስከ ተፈቀደበት ሂደት ድረስ "
        "ተጠብቆ ይቆያል።\n\n"
        f"📞 ለድጋፍ: {SUPPORT_URL}"
    )

    for uid in [req.get("requester_id"), other_party_id(req)]:
        if uid:
            try:
                await context.bot.send_message(chat_id=uid, text=notice)
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

    # Phone detection
    if contains_phone_number(receipt):
        await update.message.reply_text(
            "⚠️ ስልክ ቁጥር መለዋወጥ ክልክል ነው!\n\n"
            "📋 ይህ ከ TANA CARGO መመሪያ ውጭ ነው።"
        )
        return

    if user_id == req["requester_id"]:
        side = "requester"
    else:
        side = "other"

    version_key = f"{side}_payment_version"
    req[version_key] = req.get(version_key, 0) + 1
    payment_version = req[version_key]

    req[f"{side}_payment_submitted"] = True
    req[f"{side}_payment_approved"] = False
    req[f"{side}_payment_rejected"] = False
    req[f"{side}_receipt"] = receipt
    req.pop(f"{side}_receipt_photo", None)
    req["status"] = "awaiting_payment"

    order_id = req.get("order_id", f"#{index+1}")

    await update.message.reply_text(
        f"🧾 የክፍያ Receipt ተቀብለናል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "⏳ Admin ክፍያውን ይመረምራል።\n"
        "🔒 ሁለቱም ክፍያዎች Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ አይጋራም።"
    )

    await send_admin_payment_request(
        context, index, side, user_id,
        receipt_text=receipt,
        payment_version=payment_version
    )


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

    req[f"{side}_payment_submitted"] = True
    req[f"{side}_payment_approved"] = False
    req[f"{side}_payment_rejected"] = False
    req[f"{side}_receipt_photo"] = file_id
    req.pop(f"{side}_receipt", None)
    req["status"] = "awaiting_payment"

    order_id = req.get("order_id", f"#{index+1}")

    await update.message.reply_text(
        f"🧾 የክፍያ Screenshot ተቀብለናል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "⏳ Admin ክፍያውን ይመረምራል።\n"
        "🔒 ሁለቱም ክፍያዎች Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ አይጋራም።"
    )

    await send_admin_payment_request(
        context, index, side, user_id,
        photo_id=file_id,
        payment_version=payment_version
    )


# ==================================================
# ADMIN PAYMENT REQUEST
# ==================================================

async def send_admin_payment_request(context, index, side, user_id,
                                       receipt_text=None, photo_id=None,
                                       payment_version=None):
    if not SUPER_ADMIN_ID:
        return

    req = connection_requests[index]

    if side == "requester":
        side_name = "ጠያቂ / Cargo User"
    else:
        if req.get("type") == "truck":
            side_name = "የመኪና ባለቤት"
        else:
            side_name = "የጭነት ባለቤት"

    if payment_version is None:
        payment_version = req.get(f"{side}_payment_version", 0)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ APPROVE",
            callback_data=f"adminapprove_{index}_{side}_{payment_version}"
        ), InlineKeyboardButton(
            "❌ REJECT",
            callback_data=f"adminreject_{index}_{side}_{payment_version}"
        )],
    ])

    user = users.get(user_id, {})
    user_name = user.get("name", "Unknown")
    order_id = req.get("order_id", f"#{index+1}")

    text = (
        f"🧾 TANA CARGO — የክፍያ ማስረጃ\n\n"
        f"🆔 Order ID: {order_id}\n"
        f"📌 Request: {index}\n"
        f"🔢 Version: {payment_version}\n"
        f"👤 ተጠቃሚ: {user_name}\n"
        f"🆔 Telegram ID: {user_id}\n"
        f"👥 ወገን: {side_name}\n"
        f"💰 Final Price: {req['final_price']:,.2f} ብር\n\n"
    )

    if receipt_text:
        text += f"🧾 Receipt:\n{receipt_text}\n"
    if photo_id:
        text += "🖼️ Screenshot ከታች ተልኳል።\n"

    text += "\nእባክዎ ክፍያውን ይመርምሩ።"

    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=int(SUPER_ADMIN_ID),
                photo=photo_id,
                caption=text,
                reply_markup=buttons
            )
        else:
            await context.bot.send_message(
                chat_id=int(SUPER_ADMIN_ID),
                text=text,
                reply_markup=buttons
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

    if not SUPER_ADMIN_ID:
        await query.answer("❌ ADMIN_USER_ID አልተዘጋጀም።", show_alert=True)
        return

    if str(user_id) != str(SUPER_ADMIN_ID):
        await query.answer("❌ Admin ብቻ!", show_alert=True)
        return

    parts = query.data.split("_")
    if len(parts) != 4:
        return

    action = parts[0]

    try:
        index = int(parts[1])
        button_version = int(parts[3])
    except ValueError:
        return

    side = parts[2]

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

    order_id = req.get("order_id", f"#{index+1}")

    # ==================================================
    # APPROVE
    # ==================================================

    if action == "adminapprove":
        req[approved_key] = True
        req[rejected_key] = False

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Check both approvals FIRST
        if (req.get("requester_payment_approved")
                and req.get("other_payment_approved")):
            await share_full_information(index, context)
        else:
            try:
                await context.bot.send_message(
                    chat_id=user_to_notify,
                    text=(
                        f"✅ TANA CARGO — የክፍያ ማረጋገጫ\n\n"
                        f"🆔 Order ID: {order_id}\n\n"
                        "Admin የላኩትን የክፍያ ማስረጃ አረጋግጧል።\n\n"
                        "⏳ የሁለተኛው ወገን ክፍያ "
                        "ማረጋገጫ ይጠበቃል።\n\n"
                        "🔒 የግል መረጃ እስካሁን አይጋራም።"
                    )
                )
            except Exception:
                pass

    # ==================================================
    # REJECT
    # ==================================================

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
                    f"❌ TANA CARGO — የክፍያ ማስረጃ አልተፈቀደም\n\n"
                    f"🆔 Order ID: {order_id}\n\n"
                    "እባክዎ ክፍያውን እንደገና ያረጋግጡ እና "
                    "Receipt No. ወይም Screenshot "
                    "እንደገና ይላኩ።\n\n"
                    "🔒 የግል መረጃ እስካሁን ተደብቆ ይቆያል።"
                )
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

    requester_name = users.get(requester_id, {}).get(
        "name", req.get("requester_name", "የጠያቂው ስም")
    )
    requester_phone = get_user_phone(requester_id)

    order_id = req.get("order_id", f"#{index+1}")

    if req.get("type") == "truck":
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(owner_id, {}).get("name", "የመኪና ባለቤት")
        truck_owner_phone = truck.get("phone", "")
        truck_plate = truck.get("plate", "")

        requester_message = (
            f"✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            f"🆔 Order ID: {order_id}\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 የመኪና ባለቤት: {truck_owner_name}\n"
            f"📞 ስልክ: {truck_owner_phone or 'የለም'}\n"
            f"🚛 መኪና: {truck['type']}\n"
            f"🔢 ታርጋ: {truck_plate or 'የለም'}\n"
            f"⚖️ አቅም: {truck['capacity']}\n"
            f"📍 መነሻ: {truck['from']}\n"
            f"🛣️ መንገድ: {truck['route']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።\n\n"
            f"📞 ለድጋፍ: {SUPPORT_URL}"
        )

        owner_message = (
            f"✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            f"🆔 Order ID: {order_id}\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 ጠያቂ: {requester_name}\n"
            f"📞 ስልክ: {requester_phone or 'የለም'}\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።\n\n"
            f"📞 ለድጋፍ: {SUPPORT_URL}"
        )

    else:
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(owner_id, {}).get("name", "የጭነት ባለቤት")
        cargo_owner_phone = cargo.get("phone", "")

        truck = None
        for item in reversed(truck_posts):
            if item["user_id"] == requester_id:
                truck = item
                break

        if truck:
            truck_info = (
                f"🚛 መኪና: {truck['type']}\n"
                f"🔢 ታርጋ: {truck.get('plate', 'የለም')}\n"
                f"📞 ስልክ: {truck.get('phone', 'የለም')}\n"
                f"⚖️ አቅም: {truck.get('capacity', 'የለም')}\n"
            )
        else:
            truck_info = f"📞 ስልክ: {requester_phone or 'የለም'}\n"

        requester_message = (
            f"✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            f"🆔 Order ID: {order_id}\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 የጭነት ባለቤት: {cargo_owner_name}\n"
            f"📞 ስልክ: {cargo_owner_phone or 'የለም'}\n\n"
            f"📦 ጭነት: {cargo['type']}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።\n\n"
            f"📞 ለድጋፍ: {SUPPORT_URL}"
        )

        owner_message = (
            f"✅ TANA CARGO — ሁለቱም ክፍያዎች ተፈቅደዋል!\n\n"
            f"🆔 Order ID: {order_id}\n\n"
            "🔓 የግል መረጃ አሁን ተከፍቷል።\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 የመኪና ጠያቂ: {requester_name}\n"
            f"{truck_info}\n"
            f"📦 ጭነት: {cargo['type']}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🤝 እባክዎ ከአሁን በኋላ በቀጥታ ተገናኙ።\n\n"
            f"📞 ለድጋፍ: {SUPPORT_URL}"
        )

    try:
        await context.bot.send_message(chat_id=requester_id, text=requester_message)
    except Exception:
        pass

    try:
        await context.bot.send_message(chat_id=owner_id, text=owner_message)
    except Exception:
        pass


# ==================================================
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    context.user_data["truck"] = {}
    await update.message.reply_text(
        "🚛 መኪና ማስመዝገብ\n\n"
        "1️⃣ የመኪናውን አይነት ይጻፉ።\n"
        "ምሳሌ: Isuzu / ካሶኒ / ኦባማ"
    )
    return TRUCK_TYPE


async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return TRUCK_PLATE


async def truck_plate(update, context):
    context.user_data["truck"]["plate"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የመጫን አቅሙን ይጻፉ።\nምሳሌ: 30 ቶን")
    return TRUCK_CAPACITY


async def truck_capacity(update, context):
    context.user_data["truck"]["capacity"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የመኪናውን መነሻ ቦታ ይጻፉ።")
    return TRUCK_FROM


async def truck_from(update, context):
    context.user_data["truck"]["from"] = update.message.text.strip()
    await update.message.reply_text(
        "5️⃣ የመንገዱን ዝርዝር ይጻፉ።\n"
        "ምሳሌ: ባህር ዳር, ጎንደር, መቐለ"
    )
    return TRUCK_ROUTE


async def truck_route(update, context):
    context.user_data["truck"]["route"] = update.message.text.strip()
    await update.message.reply_text("6️⃣ የመኪናውን አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS


async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    await update.message.reply_text("7️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER


async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text.strip()
    await update.message.reply_text("8️⃣ የስልክ ቁጥር ይጻፉ።")
    return TRUCK_PHONE


async def truck_phone(update, context):
    truck = context.user_data["truck"]
    truck["phone"] = update.message.text.strip()
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck["id"] = f"TR{len(truck_posts)+1:03d}"
    truck_posts.append(truck.copy())

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"🆔 ID: {truck['id']}\n"
        f"🚛 አይነት: {truck['type']}\n"
        f"⚖️ አቅም: {truck['capacity']}\n"
        f"📍 መነሻ: {truck['from']}\n"
        f"🛣️ መንገድ: {truck['route']}\n"
        f"📍 አድራሻ: {truck['address']}\n"
        f"👨‍✈️ ሹፌር: {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
    )

    for cargo in cargo_posts:
        if cargo.get("active", True) and vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=cargo["user_id"],
                    text=(
                        "🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n"
                        f"🚛 አይነት: {truck['type']}\n"
                        f"⚖️ አቅም: {truck['capacity']}\n"
                        f"📍 መነሻ: {truck['from']}\n"
                        f"🛣️ መንገድ: {truck['route']}\n\n"
                        "🚛 መኪና መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END


# ==================================================
# FIND TRUCK
# ==================================================

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]

    if not active_trucks:
        await update.message.reply_text(
            "ይቅርታ ለጊዜው በምዝገባ ላይ ገቢ የሆነ የጭነት መኪና የለም።\n"
            "እባክዎን ከተወሰነ ጊዜ በኋላ ደግመው ይፈልጉ።",
            reply_markup=main_menu()
        )
        return

    text = "🚛 የተመዘገቡ መኪኖች፦\n\n"
    buttons = []

    for i, truck in enumerate(active_trucks, 1):
        truck_id = truck.get("id", f"TR{i:03d}")
        text += (
            f"🚛 መኪና #{truck_id}\n"
            f"🔹 አይነት: {truck['type']}\n"
            f"⚖️ አቅም: {truck['capacity']}\n"
            f"📍 መነሻ: {truck['from']}\n"
            f"🛣️ መንገድ: {truck['route']}\n"
            "🔒 ታርጋና ስልክ ተደብቀዋል።\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                f"🔢 {truck_id} — 🤝 ግንኙነት ጠይቅ",
                callback_data=f"truckconnect_{i - 1}"
            )
        ])

    text += "\n👉 እባክዎን የፈለጉትን የጭነት መኪና በቁጥር ይምረጡ።"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ==================================================
# TRUCK CONNECTION
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
        await query.edit_message_text("❌ ይህ መኪና ቀድሞ ተስማምቶ ተወግዷል።")
        return

    requester = update.effective_user

    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return

    for req in connection_requests:
        if (req.get("type") == "truck"
                and req["truck_index"] == index
                and req["requester_id"] == requester.id
                and req["status"] in ["pending", "negotiating", "awaiting_payment"]):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return

    users.setdefault(requester.id, {
        "name": requester.full_name,
        "username": requester.username or "",
        "id": requester.id,
    })

    order_id = f"TK-{len(connection_requests) + 1:04d}"

    request = {
        "type": "truck",
        "order_id": order_id,
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
        "created_at": datetime.now(),
        "timeout_alerted": False,
        "admin_confirmed": False,
    }

    connection_requests.append(request)

    await query.edit_message_text(
        f"✅ የመኪና ግንኙነት ጥያቄዎ ተላክቷል!\n\n"
        f"🆔 Order ID: {order_id}\n\n"
        "🔒 የግል መረጃዎች እስከ ማረጋገጫ ድረስ ተደብቀዋል።"
    )

    try:
        if SUPER_ADMIN_ID:
            await context.bot.send_message(
                chat_id=int(SUPER_ADMIN_ID),
                text=(
                    f"🔔 TANA CARGO — አዲስ የመኪና ግንኙነት ጥያቄ\n\n"
                    f"🆔 Order ID: {order_id}\n"
                    f"🚛 መኪና: {truck['type']}\n"
                    f"👤 ጠያቂ: {requester.full_name}"
                )
            )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=truck["user_id"],
            text=(
                f"🔔 አዲስ የመኪና ግንኙነት ጥያቄ!\n\n"
                f"🆔 Order ID: {order_id}\n\n"
                f"🚛 አይነት: {truck['type']}\n"
                f"📍 መነሻ: {truck['from']}\n"
                f"🛣️ መንገድ: {truck['route']}\n\n"
                f"👤 ጠያቂ: {requester.full_name}\n\n"
                "🚛 የእርሶን የጭነት መኪና የሚፈልግ ደንበኛ ተገኝቷል!\n"
                "⏰ እባክዎ በ5 ደቂቃ ውስጥ ምላሽ ይስጡ!"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"🤝 ግንኙነት ({order_id})",
                    callback_data=f"accept_{len(connection_requests)-1}"
                ),
                InlineKeyboardButton(
                    "❌ ውድቅ",
                    callback_data=f"reject_{len(connection_requests)-1}"
                )
            ]])
        )
    except Exception:
        pass


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
        "👤 My Profile\n\n"
        f"👤 ስም: {user['name']}\n"
        f"🔗 Username: @{user['username'] if user['username'] else 'የለም'}\n"
        f"🆔 Telegram ID: {user['id']}\n"
    )

    if "owner" in user:
        message += "\n📦 የጭነት ባለቤት ምዝገባ: ✅\n"

    if is_super_admin(user_id):
        message += "\n🔑 ሚና: Super Admin\n"
    elif is_admin(user_id):
        message += "\n🔑 ሚና: Admin\n"

    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]

    message += (
        f"\n🚚 የለጠፉት ጭነት: {len(my_cargo)}\n"
        f"🚛 የተመዘገቡ መኪኖች: {len(my_trucks)}"
    )

    await update.message.reply_text(message, reply_markup=main_menu())


# ==================================================
# SUPPORT
# ==================================================

async def support_start(update, context):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 @tanapage ክፈት", url=SUPPORT_URL)],
    ])

    await update.message.reply_text(
        "📞 TANA CARGO — የድጋፍ ማዕከል\n\n"
        "🙏 ውድ ደንበኛ!\n\n"
        "የጣና ካርጎን እገዛ ከፈለጉ —\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💬 በ Telegram ድጋፍ:\n"
        f"👉 {SUPPORT_URL}\n\n"
        "📞 በስልክ ድጋፍ:\n"
        f"👉 {SUPPORT_PHONE_1}\n"
        f"👉 {SUPPORT_PHONE_2}\n"
        f"👉 {SUPPORT_PHONE_3}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⏰ የሥራ ሰዓት:\n"
        "ሰኞ - ቅዳሜ\n"
        "2:00 - 8:00 (ከሰዓት)\n\n"
        "🙏 እኛን ስለመረጡ እናመሰግናለን!",
        reply_markup=buttons
    )
    return ConversationHandler.END


async def support_message(update, context):
    await update.message.reply_text(
        f"🙏 መልእክትዎን ለSupport ለመላክ {SUPPORT_URL} ይጫኑ።",
        reply_markup=main_menu()
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
        "ለመስጠት የተዘጋጀ የጭነት ማገናኛ "
        "አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት "
        "መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን "
        "ጭነት ይፈልጉ።\n\n"
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
# ADMIN USER MANAGEMENT
# ==================================================

async def admin_users(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin ብቻ!")
        return

    if not users:
        await update.message.reply_text("ℹ️ የተመዘገበ ደንበኛ የለም።")
        return

    for uid, user in users.items():
        text = (
            f"👤 {user.get('name','')}\n"
            f"🆔 Telegram ID: {uid}\n"
            f"🔗 Username: @{user.get('username') or 'የለም'}\n"
            f"📦 Cargo: {sum(1 for c in cargo_posts if c.get('user_id') == uid)}\n"
            f"🚛 Truck: {sum(1 for t in truck_posts if t.get('user_id') == uid)}"
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

    await q.edit_message_text("✅ የደንበኛው መረጃ ተሰርዟል።")


# ==================================================
# BROADCAST
# ==================================================

async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 Broadcast Message\n\n"
        "ለሁሉም ተጠቃሚዎች የሚላክ መልእክት ጻፍ።\n"
        "ለመሰረዝ /cancel ጻፍ።"
    )
    return BROADCAST_MESSAGE


async def broadcast_send(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END

    message = update.message.text.strip()
    success = 0
    failed = 0

    await update.message.reply_text("📤 በመላክ ላይ...")

    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 TANA CARGO\n\n{message}"
            )
            success += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast ተጠናቋል!\n\n"
        f"✔️ ተሳክቷል: {success}\n"
        f"❌ አልተሳካም: {failed}"
    )
    return ConversationHandler.END


# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

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
    application.add_handler(CommandHandler("connections", show_connection_requests))
    application.add_handler(CommandHandler("adminpanel", adminpanel))
    application.add_handler(CommandHandler("listadmins", listadmins))
    application.add_handler(CommandHandler("adminusers", admin_users))

    # Master conversation
    master_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), cargo_start),
            MessageHandler(filters.Regex(r"^🚛 መኪና ማስመዝገብ$"), truck_start),
            MessageHandler(filters.Regex(r"^📞 Support$"), support_start),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), service_payment),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), find_cargo),
            MessageHandler(filters.Regex(r"^🚛 መኪና መፈለግ$"), find_truck),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), profile),
            MessageHandler(filters.Regex(r"^🤝 ግንኙነት ጥያቄዎች$"), show_connection_requests),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), about),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
        ],
        states={
            CARGO_FROM: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from),
            ],
            CARGO_TO: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to),
            ],
            CARGO_TYPE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type),
            ],
            CARGO_VEHICLE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text),
            ],
            CARGO_SIZE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                CallbackQueryHandler(cargo_size, pattern=r"^size_"),
            ],
            CARGO_WEIGHT: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight),
            ],
            CARGO_DATE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date),
            ],
            CARGO_PHONE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"), menu_interrupt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone),
            ],
            CARGO_PRICE: [
                MessageHandler(filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳
