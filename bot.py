import os

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
SUPPORT_USERNAME = "@tanacargosuport"
SUPPORT_URL = "https://t.me/tanacargosuport"
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

CBE_ACCOUNT = os.getenv("CBE_ACCOUNT", "")
ABAY_ACCOUNT = os.getenv("ABAY_ACCOUNT", "")
CBE_BIRR = os.getenv("CBE_BIRR", "")
TELEBIRR = os.getenv("TELEBIRR", "")


# ==================================================
# DATABASE / MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []
relay_messages = {}


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


# ==================================================
# MAIN MENU
# ==================================================

def main_menu():

    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
        ["📦 የጭነት ባለቤት", "👤 የኔ መረጃ"],
        ["🤝 ግንኙነት ጥያቄዎች"],
        ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
        ["📞 Support", "ℹ️ About"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# ==================================================
# KEYBOARDS
# ==================================================

def size_keyboard():

    keyboard = [
        [
            InlineKeyboardButton(
                "🟢 ሙሉ ጭነት 100%",
                callback_data="size_100"
            )
        ],
        [
            InlineKeyboardButton(
                "🟡 ግማሽ ጭነት 50%",
                callback_data="size_50"
            )
        ],
        [
            InlineKeyboardButton(
                "🟠 እሩብ ጭነት 25%",
                callback_data="size_25"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def vehicle_keyboard(prefix="vehicle"):

    keyboard = [
        [
            InlineKeyboardButton(
                "🚛 ተሳቢ",
                callback_data=f"{prefix}_ተሳቢ"
            )
        ],
        [
            InlineKeyboardButton(
                "🚛 ካሶኒ",
                callback_data=f"{prefix}_ካሶኒ"
            )
        ],
        [
            InlineKeyboardButton(
                "🚛 ኦባማ",
                callback_data=f"{prefix}_ኦባማ"
            )
        ],
        [
            InlineKeyboardButton(
                "🚛 Isuzu",
                callback_data=f"{prefix}_isuzu"
            )
        ],
        [
            InlineKeyboardButton(
                "✍️ ሌላ",
                callback_data=f"{prefix}_other"
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# ==================================================
# HELPERS
# ==================================================

def normalize(text):

    if text is None:
        return ""

    return (
        str(text)
        .strip()
        .lower()
        .replace(" ", "")
    )


def route_points(text):

    if not text:
        return []

    separators = [",", "→", ">", "/", "፣"]

    for sep in separators:
        text = text.replace(sep, ",")

    return [
        normalize(x)
        for x in text.split(",")
        if x.strip()
    ]


def route_match(
    truck_from,
    truck_route,
    cargo_from,
    cargo_to
):

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

    return (
        normalize(truck_type)
        == normalize(requested_vehicle)
    )


def commission_amount(price):

    price = float(price)

    each_side = price * 0.01
    total = each_side * 2

    return each_side, total


def payment_methods_text():

    return (
        "💳 የአገልግሎት ክፍያ መረጃ፦\n\n"
        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000031098231\n"
        "👤 solomon\n\n"
        "🏦 Abay Bank\n"
        "🔢 2011011003938018\n"
        "👤 solomon\n\n"
        "💰 CBE Birr\n"
        "📱 0918132914\n"
        "👤 solomon\n\n"
        "📱 Telebirr\n"
        "📱 0918132914\n"
        "👤 solomon\n\n"
        "እኛን ስለመረጡን እናመሰግናለን 🙏"
    )


def other_party_id(req):

    if req.get("type") == "truck":
        return req["truck_owner_id"]

    return req["cargo_owner_id"]


def other_party_name(req):

    if req.get("type") == "truck":

        user = users.get(
            req["truck_owner_id"],
            {}
        )

        return user.get(
            "name",
            "የመኪና ባለቤት"
        )

    user = users.get(
        req["cargo_owner_id"],
        {}
    )

    return user.get(
        "name",
        "የጭነት ባለቤት"
    )


def get_user_phone(user_id):

    user = users.get(
        user_id,
        {}
    )

    if user.get("owner", {}).get("phone"):
        return user["owner"]["phone"]

    for cargo in reversed(cargo_posts):

        if cargo["user_id"] == user_id:
            return cargo.get("phone", "")

    for truck in reversed(truck_posts):

        if truck["user_id"] == user_id:
            return truck.get("phone", "")

    return ""


# ==================================================
# NEGOTIATION INDEX
# ==================================================

def get_negotiation_index(user_id):

    user = users.get(
        user_id,
        {}
    )

    saved = user.get(
        "negotiation_request"
    )

    if (
        saved is not None
        and 0 <= saved < len(connection_requests)
    ):

        req = connection_requests[saved]

        if (
            req["status"] == "negotiating"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):

            return saved

    for i in range(
        len(connection_requests) - 1,
        -1,
        -1
    ):

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
# PAYMENT INDEX
# ==================================================

def get_payment_index(user_id):

    user = users.get(
        user_id,
        {}
    )

    saved = user.get(
        "payment_request"
    )

    if (
        saved is not None
        and 0 <= saved < len(connection_requests)
    ):

        req = connection_requests[saved]

        if (
            req["status"] == "awaiting_payment"
            and (
                req["requester_id"] == user_id
                or other_party_id(req) == user_id
            )
        ):

            return saved

    for i in range(
        len(connection_requests) - 1,
        -1,
        -1
    ):

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
# REQUEST STATE HELPERS
# ==================================================

def set_negotiation_request(index):

    req = connection_requests[index]

    users.setdefault(
        req["requester_id"],
        {}
    )

    users.setdefault(
        other_party_id(req),
        {}
    )

    users[
        req["requester_id"]
    ]["negotiation_request"] = index

    users[
        other_party_id(req)
    ]["negotiation_request"] = index


def set_payment_request(index):

    req = connection_requests[index]

    users.setdefault(
        req["requester_id"],
        {}
    )

    users.setdefault(
        other_party_id(req),
        {}
    )

    users[
        req["requester_id"]
    ]["payment_request"] = index

    users[
        other_party_id(req)
    ]["payment_request"] = index


def clear_negotiation_request(req):

    for user_id in [
        req["requester_id"],
        other_party_id(req),
    ]:

        if user_id in users:

            users[user_id].pop(
                "negotiation_request",
                None
            )


def clear_payment_request(req):

    for user_id in [
        req["requester_id"],
        other_party_id(req),
    ]:

        if user_id in users:

            users[user_id].pop(
                "payment_request",
                None
            )


# ==================================================
# START
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Reset any stale per-user flow data when /start is used.
    context.user_data.clear()

    user = update.effective_user

    if user.id not in users:

        users[user.id] = {
            "name": user.full_name,
            "username": user.username or "",
            "id": user.id,
        }

    else:

        users[user.id]["name"] = (
            user.full_name
        )

        users[user.id]["username"] = (
            user.username or ""
        )

    await update.message.reply_text(

        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት "
        "በደህና መጡ!\n\n"

        "🚚 የጭነት ባለቤቶችንና "
        "ጫኝ መኪኖችን እናገናኛለን።\n\n"

        "ከታች ያለውን ምናሌ ይጠቀሙ።",

        reply_markup=main_menu()
    )


# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(
    update,
    context
):

    context.user_data["cargo"] = {}

    await update.message.reply_text(

        "🚚 ጭነት መለጠፍ\n\n"

        "1️⃣ ጭነቱ የሚነሳበትን "
        "ቦታ ይጻፉ።\n"

        "ምሳሌ፦ ባህር ዳር"
    )

    return CARGO_FROM


async def cargo_from(
    update,
    context
):

    context.user_data[
        "cargo"
    ]["from"] = update.message.text

    await update.message.reply_text(
        "2️⃣ የሚደርስበትን ቦታ ይጻፉ።"
    )

    return CARGO_TO


async def cargo_to(
    update,
    context
):

    context.user_data[
        "cargo"
    ]["to"] = update.message.text

    await update.message.reply_text(

        "3️⃣ የጭነቱን አይነት ይጻፉ።\n"

        "ምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ"
    )

    return CARGO_TYPE


async def cargo_type(
    update,
    context
):

    context.user_data[
        "cargo"
    ]["type"] = update.message.text

    await update.message.reply_text(

        "4️⃣ ለዚህ ጭነት የሚፈልጉትን "
        "የመኪና አይነት ይምረጡ።",

        reply_markup=vehicle_keyboard(
            "cargo_vehicle"
        )
    )

    return CARGO_VEHICLE


async def cargo_vehicle(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    value = query.data.replace(
        "cargo_vehicle_",
        ""
    )

    if value == "other":

        await query.edit_message_text(
            "✍️ የሚፈልጉትን "
            "የመኪና አይነት ይጻፉ።"
        )

        context.user_data[
            "cargo"
        ]["vehicle_waiting"] = True

        return CARGO_VEHICLE

    context.user_data[
        "cargo"
    ]["vehicle"] = value

    await query.edit_message_text(

        f"🚛 የተመረጠው መኪና፦ {value}\n\n"

        "5️⃣ የጭነቱን መጠን ይምረጡ።",

        reply_markup=size_keyboard()
    )

    return CARGO_SIZE


async def cargo_vehicle_text(
    update,
    context
):

    cargo = context.user_data[
        "cargo"
    ]

    if not cargo.get(
        "vehicle_waiting"
    ):
        return

    cargo["vehicle"] = (
        update.message.text
    )

    cargo["vehicle_waiting"] = False

    await update.message.reply_text(

        f"🚛 የተፈለገው መኪና፦ "
        f"{cargo['vehicle']}\n\n"

        "5️⃣ የጭነቱን መጠን ይምረጡ።",

        reply_markup=size_keyboard()
    )

    return CARGO_SIZE


async def cargo_size(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    sizes = {

        "size_100": (
            100,
            "ሙሉ ጭነት"
        ),

        "size_50": (
            50,
            "ግማሽ ጭነት"
        ),

        "size_25": (
            25,
            "እሩብ ጭነት"
        ),
    }

    if query.data not in sizes:
        return CARGO_SIZE

    size, size_name = sizes[
        query.data
    ]

    context.user_data[
        "cargo"
    ]["size"] = size

    context.user_data[
        "cargo"
    ]["size_name"] = size_name

    await query.edit_message_text(

        f"📦 የተመረጠው፦ "
        f"{size_name} ({size}%)\n\n"

        "6️⃣ የጭነቱን ክብደት ይጻፉ።\n"

        "ምሳሌ፦ 10 ቶን"
    )

    return CARGO_WEIGHT


async def cargo_weight(
    update,
    context
):

    context.user_data[
        "cargo"
    ]["weight"] = update.message.text

    await update.message.reply_text(

        "7️⃣ የሚጫንበትን ቀን ይጻፉ።\n"

        "ምሳሌ፦ 25/09/2026"
    )

    return CARGO_DATE


async def cargo_date(
    update,
    context
):

    context.user_data[
        "cargo"
    ]["date"] = update.message.text

    await update.message.reply_text(
        "8️⃣ ስልክ ቁጥር ይጻፉ።"
    )

    return CARGO_PHONE


async def cargo_phone(
    update,
    context
):
    context.user_data["cargo"]["phone"] = update.message.text
    await update.message.reply_text(
        "9️⃣ የመጫኛ የመጨረሻ ዋጋ ይጻፉ።\n\nምሳሌ፦ 50000"
    )
    return CARGO_PRICE


async def cargo_price(
    update,
    context
):
    raw = update.message.text.strip().replace(",", "").replace("ብር", "").strip()
    try:
        price = float(raw)
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ዋጋውን ቁጥር ብቻ ይጻፉ።\nምሳሌ፦ 50000")
        return CARGO_PRICE
    if price <= 0:
        await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
        return CARGO_PRICE
    cargo = context.user_data["cargo"]
    cargo["price"] = price
    cargo["user_id"] = update.effective_user.id
    cargo["active"] = True
    cargo_posts.append(cargo.copy())
    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን፦ {cargo['date']}\n"
        f"💰 የመጫኛ የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
    )
    for truck in truck_posts:
        if vehicle_match(truck.get("type"), cargo.get("vehicle")) and route_match(truck.get("from"), truck.get("route"), cargo.get("from"), cargo.get("to")):
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
                        f"💰 {price:,.2f} ብር\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass
    return ConversationHandler.END


# ==================================================
# FIND CARGO
# ==================================================

async def find_cargo(
    update,
    context
):

    active_cargos = [c for c in cargo_posts if c.get("active", True)]

    if not active_cargos:

        await update.message.reply_text(

            "📦 በአሁኑ ጊዜ "
            "ተስማሚ ጭነት የለም።\n\n"

            "🔎 ጭነት እያፈላለግን ነው።\n"

            "🔔 ተስማሚ ጭነት ሲገኝ "
            "እናሳውቅዎታለን።",

            reply_markup=main_menu()
        )

        return

    message = (
        "🔎 የተለጠፉ ጭነቶች፦\n\n"
    )

    buttons = []

    for i, cargo in enumerate(
        active_cargos,
        1
    ):

        message += (

            f"📦 ጭነት #{i}\n"

            f"📍 {cargo['from']} ➡️ "
            f"{cargo['to']}\n"

            f"📦 አይነት፦ "
            f"{cargo['type']}\n"

            f"🚛 መኪና፦ "
            f"{cargo['vehicle']}\n"

            f"📊 {cargo['size_name']} "
            f"({cargo['size']}%)\n"

            f"⚖️ {cargo['weight']}\n"

            f"📅 {cargo['date']}\n"

            "🔒 የባለቤቱ ስልክ "
            "ተደብቋል።\n\n"
        )

        buttons.append([

            InlineKeyboardButton(

                f"🔢 {i} — 🤝 ግንኙነት ጠይቅ",

                callback_data=(
                    f"connect_{i - 1}"
                )
            )
        ])

    message += "\n👉 እባክዎን የፈለጉትን የጭነት ዝርዝር በቁጥር ይምረጡ።"

    await update.message.reply_text(

        message,

        reply_markup=(
            InlineKeyboardMarkup(buttons)
        )
    )


# ==================================================
# CARGO CONNECTION
# ==================================================

async def connection_request(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    index = int(
        query.data.split("_")[1]
    )

    if (
        index < 0
        or index >= len(cargo_posts)
    ):

        await query.edit_message_text(
            "❌ ይህ ጭነት "
            "ከአሁን በኋላ አይገኝም።"
        )

        return

    cargo = cargo_posts[index]

    if not cargo.get("active", True):
        await query.edit_message_text("❌ ይህ ጭነት ቀድሞ ተስማምቶ ከፍርግርግ ዝርዝር ተወግዷል።")
        return

    requester = update.effective_user

    if cargo["user_id"] == requester.id:

        await query.answer(
            "❌ የራስዎን ጭነት "
            "መጠየቅ አይችሉም።",
            show_alert=True
        )

        return

    for req in connection_requests:

        if (
            req.get("type") == "cargo"
            and req["cargo_index"] == index
            and req["requester_id"]
            == requester.id
            and req["status"] in [
                "pending",
                "negotiating",
                "awaiting_payment",
            ]
        ):

            await query.answer(

                "⚠️ ይህን ጭነት "
                "አስቀድመው ጠይቀዋል።",

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

        "cargo_owner_id":
            cargo["user_id"],

        "requester_id":
            requester.id,

        "requester_name":
            requester.full_name,

        "status": "pending",

        "offers": [],

        "current_offer": None,

        "current_offer_by": None,

        # NEW
        "offer_version": 0,

        "final_price": None,

        "requester_confirmed": False,

        "other_confirmed": False,

        # PAYMENT
        "requester_payment_submitted": False,

        "other_payment_submitted": False,

        "requester_payment_approved": False,

        "other_payment_approved": False,

        "requester_payment_rejected": False,

        "other_payment_rejected": False,

        # NEW
        "requester_payment_version": 0,

        "other_payment_version": 0,

        "full_info_shared": False,
    }

    connection_requests.append(
        request
    )

    await query.edit_message_text(

        "✅ የግንኙነት ጥያቄዎ "
        "ተላክቷል።\n\n"

        "🔒 የግል ስልክ ቁጥሮች "
        "ተደብቀዋል።\n"

        "💬 የዋጋ ድርድሩ "
        "በTANA CARGO Bot ውስጥ "
        "ይካሄዳል።\n\n"

        "⏳ የጭነቱ ባለቤት "
        "ሲቀበል ይነገርዎታል።"
    )

    try:
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 TANA CARGO — አዲስ ግንኙነት ጥያቄ\n\n"
                    f"📌 Request ID፦ {len(connection_requests)-1}\n"
                    "📦 የጭነት ባለቤት ተጠይቋል።\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n"
                    f"📍 {cargo['from']} ➡️ {cargo['to']}\n\n"
                    "💬 ድርድሩ በ @tanacargosuport እንዲቀጥል ያስተባብሩ።"
                )
            )
    except Exception:
        pass

    try:

        await context.bot.send_message(

            chat_id=cargo["user_id"],

            text=(

                "🔔 አዲስ "
                "የግንኙነት ጥያቄ!\n\n"

                f"📍 {cargo['from']} ➡️ "
                f"{cargo['to']}\n"

                f"📦 {cargo['type']}\n"

                f"🚛 {cargo['vehicle']}\n"

                f"📊 {cargo['size_name']}\n\n"

                f"👤 ጠያቂ፦ "
                f"{requester.full_name}\n\n"

                "📦 ጭነትዎን የሚጭን የጭነት መኪና ደንበኛ ተገኝቷል!\n"
                "💬 ለዋጋና ዝርዝር ውይይት @tanacargosuport ይጠቀሙ።"
            ),

            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🤝 ግንኙነት", callback_data=f"accept_{len(connection_requests)-1}"),
                InlineKeyboardButton("❌ ውድቅ", callback_data=f"reject_{len(connection_requests)-1}")
            ]])
        )

    except Exception:
        pass


# ==================================================
# CONNECTION REQUESTS
# ==================================================

async def show_connection_requests(
    update,
    context
):

    user_id = update.effective_user.id

    received = []
    sent = []

    for i, req in enumerate(
        connection_requests
    ):

        owner_id = other_party_id(req)

        if owner_id == user_id:
            received.append(
                (i, req)
            )

        if req["requester_id"] == user_id:
            sent.append(
                (i, req)
            )

    if not received and not sent:

        await update.message.reply_text(

            "🤝 እስካሁን "
            "የግንኙነት ጥያቄ "
            "የለዎትም።",

            reply_markup=main_menu()
        )

        return

    text = (
        "🤝 የግንኙነት ጥያቄዎች\n\n"
    )

    buttons = []

    for i, req in received:

        if req.get("type") == "truck":

            truck = truck_posts[
                req["truck_index"]
            ]

            text += (

                f"📥 የመጣ #{i + 1}\n"

                f"🚛 {truck['type']}\n"

                f"📍 {truck['from']} ➡️ "
                f"{truck['route']}\n"

                f"👤 {req['requester_name']}\n"

                f"📌 ሁኔታ፦ "
                f"{req['status']}\n\n"
            )

        else:

            cargo = cargo_posts[
                req["cargo_index"]
            ]

            text += (

                f"📥 የመጣ #{i + 1}\n"

                f"📍 {cargo['from']} ➡️ "
                f"{cargo['to']}\n"

                f"📦 {cargo['type']}\n"

                f"👤 {req['requester_name']}\n"

                f"📌 ሁኔታ፦ "
                f"{req['status']}\n\n"
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

            f"📌 ሁኔታ፦ "
            f"{req['status']}\n"
        )

        if req.get("final_price"):

            text += (

                f"💰 የመጨረሻ ዋጋ፦ "

                f"{req['final_price']:,.2f} "
                f"ብር\n"
            )

        if (
            req["status"]
            == "awaiting_payment"
        ):

            text += (
                "💳 ክፍያ ይጠበቃል።\n"
            )

        if req["status"] == "completed":

            text += (
                "✅ ሁለቱም "
                "ክፍያዎች ተፈቅደዋል።\n"
            )

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
# ACCEPT CONNECTION
# ==================================================

async def accept_connection(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    index = int(
        query.data.split("_")[1]
    )

    if (
        index < 0
        or index >= len(connection_requests)
    ):
        return

    req = connection_requests[index]

    if (
        other_party_id(req)
        != update.effective_user.id
    ):

        await query.answer(
            "❌ ይህን ጥያቄ "
            "እርስዎ መቀበል አይችሉም።",
            show_alert=True
        )

        return

    if req["status"] != "pending":

        await query.answer(
            "⚠️ ይህ ጥያቄ "
            "ከዚህ በፊት "
            "ተስተናግዷል።",
            show_alert=True
        )

        return

    req["status"] = "negotiating"

    req["requester_confirmed"] = False
    req["other_confirmed"] = False

    req["current_offer"] = None
    req["current_offer_by"] = None
    req["final_price"] = None

    set_negotiation_request(index)

    await query.edit_message_text(

        "✅ የግንኙነት "
        "ጥያቄውን ተቀብለዋል።\n\n"

        "💬 አሁን የዋጋ ድርድር "
        "ይጀምራል።"
    )

    try:

        await context.bot.send_message(

            chat_id=req["requester_id"],

            text=(

                "✅ የግንኙነት "
                "ጥያቄዎ ተቀባ!\n\n"

                "💬 አሁን የመጓጓዣ "
                "ዋጋ ድርድር "
                "መጀመር ይችላሉ።\n\n"

                "💰 ለመጀመር "
                "የሚፈልጉትን "
                "ዋጋ በብር ይጻፉ።\n"

                "ምሳሌ፦ 55000"
            )
        )

    except Exception:
        pass


# ==================================================
# REJECT CONNECTION
# ==================================================

async def reject_connection(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    index = int(
        query.data.split("_")[1]
    )

    if (
        index < 0
        or index >= len(connection_requests)
    ):
        return

    req = connection_requests[index]

    if (
        other_party_id(req)
        != update.effective_user.id
    ):
        return

    req["status"] = "rejected"

    clear_negotiation_request(req)
    clear_payment_request(req)

    await query.edit_message_text(

        "❌ የግንኙነት "
        "ጥያቄው ውድቅ "
        "ተደርጓል።"
    )

    try:

        await context.bot.send_message(

            chat_id=req["requester_id"],

            text=(
                "❌ የግንኙነት "
                "ጥያቄዎ ውድቅ "
                "ተደርጓል።"
            )
        )

    except Exception:
        pass


# ==================================================
# ADMIN RELAY
# ==================================================

async def send_relay_to_admin(context, req_index, sender_id, text, buttons=None):
    if not ADMIN_USER_ID:
        return False
    req = connection_requests[req_index]
    receiver_id = other_party_id(req)
    relay_id = max(relay_messages.keys(), default=0) + 1
    relay_messages[relay_id] = {"req_index": req_index, "sender_id": sender_id, "receiver_id": receiver_id, "text": text}
    kb = [[InlineKeyboardButton("➡️ ወደ ሌላኛው ወገን ላክ", callback_data=f"relay_{relay_id}")]]
    if buttons: kb.extend(buttons)
    try:
        await context.bot.send_message(chat_id=int(ADMIN_USER_ID), text=f"📨 TANA CARGO Relay #{relay_id}\n\n{text}", reply_markup=InlineKeyboardMarkup(kb))
        return True
    except Exception:
        return False

async def relay_forward(update, context):
    query=update.callback_query; await query.answer()
    if not ADMIN_USER_ID or str(update.effective_user.id)!=str(ADMIN_USER_ID):
        await query.answer("❌ Admin ብቻ።", show_alert=True); return
    try:
        rid=int(query.data.split("_")[1]); item=relay_messages.get(rid)
        if not item: raise ValueError()
        await context.bot.send_message(chat_id=item["receiver_id"], text="📨 ከTANA CARGO የተላለፈ መልእክት፦\n\n"+item["text"])
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id=item["sender_id"], text="✅ መልእክትዎ Admin አስተላልፎልዎታል።")
    except Exception:
        await query.answer("❌ መልእክቱ ሊተላለፍ አልቻለም።", show_alert=True)

# ==================================================
# SUBMIT PRICE
# ==================================================

async def submit_price(
    update,
    context
):

    user_id = update.effective_user.id

    active_index = (
        get_negotiation_index(
            user_id
        )
    )

    if active_index is None:
        return

    req = connection_requests[
        active_index
    ]

    text = update.message.text.strip()

    try:

        price = float(
            text
            .replace(",", "")
            .replace("ብር", "")
            .strip()
        )

    except ValueError:

        await update.message.reply_text(

            "❌ የዋጋውን ቁጥር "
            "ብቻ ይጻፉ።\n"

            "ምሳሌ፦ 55000"
        )

        return

    if price <= 0:

        await update.message.reply_text(
            "❌ ዋጋው ከ0 በላይ "
            "መሆን አለበት።"
        )

        return

    # ----------------------------------------------
    # NEW OFFER VERSION
    # ----------------------------------------------

    req["offer_version"] = (
        req.get("offer_version", 0)
        + 1
    )

    version = req[
        "offer_version"
    ]

    # ----------------------------------------------
    # NEW OFFER CANCELS OLD AGREEMENT
    # ----------------------------------------------

    req["requester_confirmed"] = False
    req["other_confirmed"] = False

    req["final_price"] = None

    req["offers"].append({

        "user_id": user_id,

        "price": price,

        "version": version,
    })

    req["current_offer"] = price

    req["current_offer_by"] = user_id

    req["status"] = "negotiating"

    set_negotiation_request(
        active_index
    )

    other_user = other_party_id(
        req
    )

    await update.message.reply_text(

        f"💰 የላኩት ዋጋ፦ "
        f"{price:,.2f} ብር\n\n"

        "⏳ የሌላኛውን ወገን "
        "ምላሽ ይጠብቁ።"
    )

    buttons = [

        [

            InlineKeyboardButton(

                f"✅ እስማማለሁ "
                f"{price:,.2f} ብር",

                callback_data=(
                    f"agreeprice_"
                    f"{active_index}_"
                    f"{version}"
                )
            )
        ],

        [

            InlineKeyboardButton(

                "💬 ሌላ ዋጋ ላክ",

                callback_data=(
                    f"counterprice_"
                    f"{active_index}_"
                    f"{version}"
                )
            )
        ],
    ]

    relay_text = (
        "💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n"
        f"💵 የቀረበው ዋጋ፦ {price:,.2f} ብር\n\n"
        "ከዚህ ዋጋ ጋር ከተስማሙ ተስማምቻለሁ ይጫኑ። ወይም ሌላ ዋጋ ያቅርቡ።"
    )
    sent = await send_relay_to_admin(context, active_index, user_id, relay_text, buttons)
    if sent:
        await update.message.reply_text("⏳ የዋጋ መልእክትዎ መጀመሪያ Admin ዘንድ ተልኳል። Admin ሲያስተላልፈው ለሌላኛው ወገን ይደርሳል።")
    else:
        await update.message.reply_text("⚠️ Admin relay አልተገኘም።")


# ==================================================
# AGREE PRICE
# ==================================================

async def agree_price(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split("_")

    if len(parts) != 3:
        return

    index = int(parts[1])
    button_version = int(parts[2])

    if (
        index < 0
        or index >= len(connection_requests)
    ):
        return

    req = connection_requests[index]

    user_id = update.effective_user.id

    # ----------------------------------------------
    # SECURITY: ONLY CURRENT VERSION
    # ----------------------------------------------

    current_version = req.get(
        "offer_version",
        0
    )

    if button_version != current_version:

        await query.answer(

            "⚠️ ይህ የድሮ ዋጋ "
            "ነው። አዲሱን ዋጋ "
            "ይጠቀሙ።",

            show_alert=True
        )

        return

    if req["status"] != "negotiating":

        await query.answer(

            "⚠️ ይህ ድርድር "
            "አሁን ንቁ አይደለም።",

            show_alert=True
        )

        return

    if (
        user_id != req["requester_id"]
        and user_id != other_party_id(req)
    ):

        await query.answer(

            "❌ ይህ የእርስዎ "
            "ድርድር አይደለም።",

            show_alert=True
        )

        return

    price = req.get(
        "current_offer"
    )

    if price is None:

        await query.answer(

            "⚠️ አሁን "
            "የቀረበ ዋጋ የለም።",

            show_alert=True
        )

        return

    # ----------------------------------------------
    # CHECK CURRENT OFFER OWNER
    # ----------------------------------------------

    if user_id == req[
        "current_offer_by"
    ]:

        await query.answer(

            "⚠️ የራስዎን ዋጋ "
            "መቀበል አይችሉም።",

            show_alert=True
        )

        return

    # ----------------------------------------------
    # CONFIRM
    # ----------------------------------------------

    if user_id == req[
        "requester_id"
    ]:

        if req[
            "requester_confirmed"
        ]:

            await query.answer(

                "✅ እርስዎ ቀድሞ "
                "ተስማምተዋል።",

                show_alert=True
            )

            return

        req[
            "requester_confirmed"
        ] = True

    else:

        if req[
            "other_confirmed"
        ]:

            await query.answer(

                "✅ እርስዎ ቀድሞ "
                "ተስማምተዋል።",

                show_alert=True
            )

            return

        req[
            "other_confirmed"
        ] = True

    # ----------------------------------------------
    # ONLY FINAL WHEN BOTH AGREE
    # ----------------------------------------------

    if (
        req["requester_confirmed"]
        and req["other_confirmed"]
    ):

        req["final_price"] = price

        req["status"] = (
            "awaiting_payment"
        )

        # Reset payment states
        req[
            "requester_payment_submitted"
        ] = False

        req[
            "other_payment_submitted"
        ] = False

        req[
            "requester_payment_approved"
        ] = False

        req[
            "other_payment_approved"
        ] = False

        req[
            "requester_payment_rejected"
        ] = False

        req[
            "other_payment_rejected"
        ] = False

        clear_negotiation_request(
            req
        )

        set_payment_request(
            index
        )

        each_side, total = (
            commission_amount(price)
        )

        await query.edit_message_text(

            "✅ ሁለቱም ወገኖች "
            "በተናጠል ተስማምተዋል!\n\n"

            f"💰 የመጨረሻ ዋጋ፦ "
            f"{price:,.2f} ብር\n\n"

            f"📌 ከእያንዳንዱ "
            f"ወገን 1%፦ "
            f"{each_side:,.2f} ብር\n"

            f"💵 ጠቅላላ TANA CARGO "
            f"ኮሚሽን፦ "
            f"{total:,.2f} ብር\n\n"

            "💳 አሁን እያንዳንዱ "
            "ወገን የራሱን 1% "
            "ይከፍላል።"
        )

        await send_admin_agreement_request(context, index)

        payment_text = (

            "💳 TANA CARGO — ክፍያ\n\n"

            f"💰 የመጨረሻ ዋጋ፦ "
            f"{price:,.2f} ብር\n"

            f"📌 የእርስዎ 1%፦ "
            f"{each_side:,.2f} ብር\n\n"

            f"{payment_methods_text()}\n\n"

            "🧾 ክፍያ ካደረጉ በኋላ "
            "Receipt No. ወይም "
            "Screenshot ይላኩ።\n\n"

            "🔒 ሁለቱም ክፍያዎች "
            "Admin እስኪያረጋግጥ "
            "ድረስ የግል መረጃ "
            "አይጋራም።"
        )

        for user in [
            req["requester_id"],
            other_party_id(req),
        ]:

            try:

                await context.bot.send_message(

                    chat_id=user,

                    text=payment_text
                )

            except Exception:
                pass

        return

    # ----------------------------------------------
    # ONLY ONE SIDE AGREED
    # ----------------------------------------------

    other_user = (

        other_party_id(req)

        if user_id == req[
            "requester_id"
        ]

        else req[
            "requester_id"
        ]
    )

    buttons = [

        [

            InlineKeyboardButton(

                f"✅ እኔም እስማማለሁ "
                f"{price:,.2f} ብር",

                callback_data=(
                    f"agreeprice_"
                    f"{index}_"
                    f"{current_version}"
                )
            )
        ],

        [

            InlineKeyboardButton(

                "💬 ሌላ ዋጋ ላክ",

                callback_data=(
                    f"counterprice_"
                    f"{index}_"
                    f"{current_version}"
                )
            )
        ],
    ]

    await query.edit_message_text(

        f"✅ {price:,.2f} ብር ላይ "
        "ተስማምተዋል።\n\n"

        "⏳ አሁን የሌላኛው "
        "ወገን በተናጠል "
        "መስማማት አለበት።"
    )

    try:

        await context.bot.send_message(

            chat_id=other_user,

            text=(

                "🤝 የዋጋ "
                "ስምምነት ማረጋገጫ\n\n"

                f"💰 {price:,.2f} ብር\n\n"

                "ሌላኛው ወገን "
                "በዚህ ዋጋ "
                "ተስማምቷል።\n"

                "እርስዎም ከተስማሙ "
                "ከዚህ በታች "
                "ያለውን አዝራር "
                "ይጫኑ።"
            ),

            reply_markup=(
                InlineKeyboardMarkup(
                    buttons
                )
            )
        )

    except Exception:
        pass


# ==================================================
# COUNTER PRICE
# ==================================================

async def counter_price_button(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split("_")

    if len(parts) != 3:
        return

    index = int(parts[1])
    button_version = int(parts[2])

    if (
        index < 0
        or index >= len(connection_requests)
    ):
        return

    req = connection_requests[index]

    # ----------------------------------------------
    # SECURITY: CURRENT VERSION ONLY
    # ----------------------------------------------

    if button_version != req.get(
        "offer_version",
        0
    ):

        await query.answer(

            "⚠️ ይህ የድሮ ዋጋ "
            "ነው። አዲሱን ዋጋ "
            "ይጠቀሙ።",

            show_alert=True
        )

        return

    if req["status"] != "negotiating":

        await query.answer(

            "⚠️ ድርድሩ "
            "ንቁ አይደለም።",

            show_alert=True
        )

        return

    user_id = update.effective_user.id

    if (
        user_id != req["requester_id"]
        and user_id != other_party_id(req)
    ):

        await query.answer(

            "❌ ይህ የእርስዎ "
            "ድርድር አይደለም።",

            show_alert=True
        )

        return

    if user_id == req.get(
        "current_offer_by"
    ):

        await query.answer(

            "⚠️ የራስዎን "
            "ዋጋ እንደገና "
            "መቀየር ከፈለጉ "
            "አዲስ ዋጋ በቀጥታ "
            "ይጻፉ።",

            show_alert=True
        )

        return

    context.user_data[
        "negotiation_request"
    ] = index

    users.setdefault(
        user_id,
        {}
    )

    users[user_id][
        "negotiation_request"
    ] = index

    await query.message.reply_text(

        "💬 አዲስ ዋጋ ይጻፉ።\n"

        "ምሳሌ፦ 55000"
    )


# ==================================================
# ADMIN PAYMENT REQUEST
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

        side_name = (
            "ጠያቂ / Cargo or Service User"
        )

    else:

        if req.get("type") == "truck":

            side_name = (
                "የመኪና ባለቤት"
            )

        else:

            side_name = (
                "የጭነት ባለቤት"
            )

    if payment_version is None:

        payment_version = req.get(
            f"{side}_payment_version",
            0
        )

    buttons = InlineKeyboardMarkup([

        [

            InlineKeyboardButton(

                "✅ APPROVE",

                callback_data=(
                    f"adminapprove_"
                    f"{index}_"
                    f"{side}_"
                    f"{payment_version}"
                )
            ),

            InlineKeyboardButton(

                "❌ REJECT",

                callback_data=(
                    f"adminreject_"
                    f"{index}_"
                    f"{side}_"
                    f"{payment_version}"
                )
            ),
        ]
    ])

    user = users.get(
        user_id,
        {}
    )

    user_name = user.get(
        "name",
        "Unknown"
    )

    text = (

        "🧾 TANA CARGO — "
        "የክፍያ ማስረጃ\n\n"

        f"📌 Request ID፦ {index}\n"

        f"🔢 Payment Version፦ "
        f"{payment_version}\n"

        f"👤 ተጠቃሚ፦ "
        f"{user_name}\n"

        f"🆔 Telegram ID፦ "
        f"{user_id}\n"

        f"👥 ወገን፦ "
        f"{side_name}\n"

        f"💰 Final Price፦ "
        f"{req['final_price']:,.2f} ብር\n\n"
    )

    if receipt_text:

        text += (
            f"🧾 Receipt፦\n"
            f"{receipt_text}\n"
        )

    if photo_id:

        text += (
            "🖼️ Screenshot "
            "ከታች ተልኳል።\n"
        )

    text += (
        "\nእባክዎ ክፍያውን "
        "ይመርምሩ።"
    )

    try:

        if photo_id:

            await context.bot.send_photo(

                chat_id=int(
                    ADMIN_USER_ID
                ),

                photo=photo_id,

                caption=text,

                reply_markup=buttons
            )

        else:

            await context.bot.send_message(

                chat_id=int(
                    ADMIN_USER_ID
                ),

                text=text,

                reply_markup=buttons
            )

    except Exception:
        pass


# ==================================================
# RECEIPT MESSAGE
# ==================================================

async def receipt_message(
    update,
    context
):

    user_id = update.effective_user.id

    index = get_payment_index(
        user_id
    )

    if index is None:
        return

    req = connection_requests[index]

    receipt = update.message.text.strip()

    if user_id == req[
        "requester_id"
    ]:

        side = "requester"

    else:

        side = "other"

    # ----------------------------------------------
    # NEW PAYMENT VERSION
    # ----------------------------------------------

    version_key = (
        f"{side}_payment_version"
    )

    req[version_key] = (
        req.get(version_key, 0)
        + 1
    )

    payment_version = req[
        version_key
    ]

    submitted_key = (
        f"{side}_payment_submitted"
    )

    approved_key = (
        f"{side}_payment_approved"
    )

    rejected_key = (
        f"{side}_payment_rejected"
    )

    req[submitted_key] = True
    req[approved_key] = False
    req[rejected_key] = False

    req[
        f"{side}_receipt"
    ] = receipt

    req.pop(
        f"{side}_receipt_photo",
        None
    )

    req["status"] = (
        "awaiting_payment"
    )

    await update.message.reply_text(

        "🧾 የክፍያ Receipt "
        "ተቀብለናል።\n\n"

        "⏳ Admin ክፍያውን "
        "ይመረምራል።\n"

        "🔒 ሁለቱም ክፍያዎች "
        "Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ "
        "አይጋራም።"
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

async def receipt_photo(
    update,
    context
):

    user_id = update.effective_user.id

    index = get_payment_index(
        user_id
    )

    if index is None:
        return

    req = connection_requests[index]

    file_id = (
        update.message.photo[-1].file_id
    )

    if user_id == req[
        "requester_id"
    ]:

        side = "requester"

    else:

        side = "other"

    # ----------------------------------------------
    # NEW PAYMENT VERSION
    # ----------------------------------------------

    version_key = (
        f"{side}_payment_version"
    )

    req[version_key] = (
        req.get(version_key, 0)
        + 1
    )

    payment_version = req[
        version_key
    ]

    submitted_key = (
        f"{side}_payment_submitted"
    )

    approved_key = (
        f"{side}_payment_approved"
    )

    rejected_key = (
        f"{side}_payment_rejected"
    )

    req[submitted_key] = True
    req[approved_key] = False
    req[rejected_key] = False

    req[
        f"{side}_receipt_photo"
    ] = file_id

    req.pop(
        f"{side}_receipt",
        None
    )

    req["status"] = (
        "awaiting_payment"
    )

    await update.message.reply_text(

        "🧾 የክፍያ Screenshot "
        "ተቀብለናል።\n\n"

        "⏳ Admin ክፍያውን "
        "ይመረምራል።\n"

        "🔒 ሁለቱም ክፍያዎች "
        "Admin እስኪፈቀዱ "
        "ድረስ የግል መረጃ "
        "አይጋራም።"
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
# FINAL CONTACT SHARING
# ==================================================

async def share_full_information(
    index,
    context
):

    req = connection_requests[index]

    # ----------------------------------------------
    # SECURITY CHECK
    # ----------------------------------------------

    if req.get(
        "full_info_shared"
    ):
        return

    if not (
        req.get(
            "requester_payment_approved"
        )
        and req.get(
            "other_payment_approved"
        )
    ):
        return

    req[
        "full_info_shared"
    ] = True

    req["status"] = "completed"

    if req.get("type") == "truck":
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
    else:
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False

    requester_id = (
        req["requester_id"]
    )

    owner_id = other_party_id(
        req
    )

    requester_name = users.get(
        requester_id,
        {}
    ).get(
        "name",
        req.get(
            "requester_name",
            "የጠያቂው ስም"
        )
    )

    requester_phone = get_user_phone(
        requester_id
    )

    if req.get("type") == "truck":

        truck = truck_posts[
            req["truck_index"]
        ]

        truck_owner_name = users.get(
            owner_id,
            {}
        ).get(
            "name",
            "የመኪና ባለቤት"
        )

        truck_owner_phone = (
            truck.get("phone", "")
        )

        truck_plate = (
            truck.get("plate", "")
        )

        requester_message = (

            "✅ TANA CARGO — "
            "ሁለቱም ክፍያዎች "
            "ተፈቅደዋል!\n\n"

            "🔓 የግል መረጃ "
            "አሁን ተከፍቷል።\n\n"

            f"👤 የመኪና ባለቤት፦ "
            f"{truck_owner_name}\n"

            f"📞 ስልክ፦ "
            f"{truck_owner_phone or 'የለም'}\n"

            f"🚛 መኪና፦ "
            f"{truck['type']}\n"

            f"🔢 ታርጋ፦ "
            f"{truck_plate or 'የለም'}\n"

            f"⚖️ አቅም፦ "
            f"{truck['capacity']}\n"

            f"📍 መነሻ፦ "
            f"{truck['from']}\n"

            f"🛣️ መንገድ፦ "
            f"{truck['route']}\n\n"

            "🤝 እባክዎ ከአሁን "
            "በኋላ በቀጥታ "
            "ተገናኙ።"
        )

        owner_message = (

            "✅ TANA CARGO — "
            "ሁለቱም ክፍያዎች "
            "ተፈቅደዋል!\n\n"

            "🔓 የግል መረጃ "
            "አሁን ተከፍቷል።\n\n"

            f"👤 ጠያቂ፦ "
            f"{requester_name}\n"

            f"📞 ስልክ፦ "
            f"{requester_phone or 'የለም'}\n\n"

            "🤝 እባክዎ ከአሁን "
            "በኋላ በቀጥታ "
            "ተገናኙ።"
        )

    else:

        cargo = cargo_posts[
            req["cargo_index"]
        ]

        cargo_owner_name = users.get(
            owner_id,
            {}
        ).get(
            "name",
            "የጭነት ባለቤት"
        )

        cargo_owner_phone = (
            cargo.get("phone", "")
        )

        truck = None

        for item in reversed(
            truck_posts
        ):

            if item["user_id"] == requester_id:

                truck = item

                break

        if truck:

            truck_info = (

                f"🚛 መኪና፦ "
                f"{truck['type']}\n"

                f"🔢 ታርጋ፦ "
                f"{truck.get('plate', 'የለም')}\n"

                f"📞 ስልክ፦ "
                f"{truck.get('phone', 'የለም')}\n"

                f"⚖️ አቅም፦ "
                f"{truck.get('capacity', 'የለም')}\n"
            )

        else:

            truck_info = (

                f"📞 ስልክ፦ "
                f"{requester_phone or 'የለም'}\n"
            )

        requester_message = (

            "✅ TANA CARGO — "
            "ሁለቱም ክፍያዎች "
            "ተፈቅደዋል!\n\n"

            "🔓 የግል መረጃ "
            "አሁን ተከፍቷል።\n\n"

            f"👤 የጭነት ባለቤት፦ "
            f"{cargo_owner_name}\n"

            f"📞 ስልክ፦ "
            f"{cargo_owner_phone or 'የለም'}\n\n"

            f"📦 ጭነት፦ "
            f"{cargo['type']}\n"

            f"📍 {cargo['from']} ➡️ "
            f"{cargo['to']}\n"

            f"⚖️ {cargo['weight']}\n"

            f"📅 {cargo['date']}\n\n"

            "🤝 እባክዎ ከአሁን "
            "በኋላ በቀጥታ "
            "ተገናኙ።"
        )

        owner_message = (

            "✅ TANA CARGO — "
            "ሁለቱም ክፍያዎች "
            "ተፈቅደዋል!\n\n"

            "🔓 የግል መረጃ "
            "አሁን ተከፍቷል።\n\n"

            f"👤 የመኪና ጠያቂ፦ "
            f"{requester_name}\n"

            f"{truck_info}\n"

            f"📦 ጭነት፦ "
            f"{cargo['type']}\n"

            f"📍 {cargo['from']} ➡️ "
            f"{cargo['to']}\n"

            f"⚖️ {cargo['weight']}\n"

            f"📅 {cargo['date']}\n\n"

            "🤝 እባክዎ ከአሁን "
            "በኋላ በቀጥታ "
            "ተገናኙ።"
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

async def admin_payment_action(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    if not ADMIN_USER_ID:

        await query.answer(

            "❌ ADMIN_USER_ID "
            "አልተዘጋጀም።",

            show_alert=True
        )

        return

    if str(user_id) != str(
        ADMIN_USER_ID
    ):

        await query.answer(

            "❌ Admin ብቻ "
            "ይህን ተግባር "
            "ማድረግ ይችላል።",

            show_alert=True
        )

        return

    parts = query.data.split("_")

    # NEW FORMAT:
    # adminapprove_INDEX_SIDE_VERSION

    if len(parts) != 4:
        return

    action = parts[0]

    try:

        index = int(parts[1])
        button_version = int(parts[3])

    except ValueError:
        return

    side = parts[2]

    if (
        index < 0
        or index >= len(connection_requests)
    ):
        return

    req = connection_requests[index]

    if side not in [
        "requester",
        "other"
    ]:
        return

    current_version = req.get(
        f"{side}_payment_version",
        0
    )

    # ----------------------------------------------
    # SECURITY: OLD ADMIN BUTTON
    # ----------------------------------------------

    if button_version != current_version:

        await query.answer(

            "⚠️ ይህ የድሮ "
            "የክፍያ ማስረጃ "
            "ነው።",

            show_alert=True
        )

        return

    submitted_key = (
        f"{side}_payment_submitted"
    )

    approved_key = (
        f"{side}_payment_approved"
    )

    rejected_key = (
        f"{side}_payment_rejected"
    )

    if not req.get(
        submitted_key
    ):

        await query.answer(

            "⚠️ የክፍያ "
            "ማስረጃ "
            "አልተላከም።",

            show_alert=True
        )

        return

    user_to_notify = (

        req["requester_id"]

        if side == "requester"

        else other_party_id(req)
    )

    # ==================================================
    # APPROVE
    # ==================================================

    if action == "adminapprove":

        req[approved_key] = True
        req[rejected_key] = False

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        # Check both approvals FIRST
        if (
            req.get(
                "requester_payment_approved"
            )
            and req.get(
                "other_payment_approved"
            )
        ):

            await share_full_information(
                index,
                context
            )

        else:

            try:

                await context.bot.send_message(

                    chat_id=user_to_notify,

                    text=(

                        "✅ TANA CARGO — "
                        "የክፍያ ማረጋገጫ\n\n"

                        "Admin የላኩትን "
                        "የክፍያ ማስረጃ "
                        "አረጋግጧል።\n\n"

                        "⏳ የሁለተኛው "
                        "ወገን ክፍያ "
                        "ማረጋገጫ "
                        "ይጠበቃል።\n\n"

                        "🔒 የግል መረጃ "
                        "እስካሁን "
                        "አይጋራም።"
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

        req.pop(
            f"{side}_receipt",
            None
        )

        req.pop(
            f"{side}_receipt_photo",
            None
        )

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        try:

            await context.bot.send_message(

                chat_id=user_to_notify,

                text=(

                    "❌ TANA CARGO — "
                    "የክፍያ ማስረጃ "
                    "አልተፈቀደም።\n\n"

                    "እባክዎ ክፍያውን "
                    "እንደገና "
                    "ያረጋግጡ እና "
                    "Receipt No. ወይም "
                    "Screenshot "
                    "እንደገና ይላኩ።\n\n"

                    "🔒 የግል መረጃ "
                    "እስካሁን "
                    "ተደብቆ ይቆያል።"
                )
            )

        except Exception:
            pass


# ==================================================
# ADMIN AGREEMENT CONFIRMATION
# ==================================================

async def admin_agreement_confirm(update, context):
    query = update.callback_query
    await query.answer()

    if not ADMIN_USER_ID or str(update.effective_user.id) != str(ADMIN_USER_ID):
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

    notice = (
        "✅ TANA CARGO — Admin ማረጋገጫ\n\n"
        "የደንበኛውና የተመረጠው መዝገብ ላይ ያለው እቃ/መኪና ስምምነት በAdmin ተረጋግጧል።\n"
        "🔒 የግል መረጃ እስከ ተፈቀደበት ሂደት ድረስ ተጠብቆ ይቆያል።\n\n"
        "📞 ድርድር/ዝርዝር ለመቀጠል @tanacargosuport ይጠቀሙ።"
    )
    for uid in [req.get("requester_id"), other_party_id(req)]:
        if uid:
            try:
                await context.bot.send_message(chat_id=uid, text=notice)
            except Exception:
                pass


async def send_admin_agreement_request(context, index):
    if not ADMIN_USER_ID:
        return
    req = connection_requests[index]
    if req.get("admin_agreement_requested"):
        return
    req["admin_agreement_requested"] = True
    kind = "🚛 የጭነት መኪና" if req.get("type") == "truck" else "📦 ጭነት"
    button = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ስምምነቱን አረጋግጥ እና ከፍርግርግ አስወግድ", callback_data=f"adminagree_{index}")]])
    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_USER_ID),
            text=(
                "🤝 TANA CARGO — የስምምነት ማረጋገጫ\n\n"
                f"📌 Request ID፦ {index}\n"
                f"{kind}\n"
                f"💰 የተስማሙበት ዋጋ፦ {req.get('final_price', 0):,.2f} ብር\n\n"
                "እባክዎ ስምምነቱን ካረጋገጡ በኋላ ከመፈለጊያ ዝርዝር ለማስወገድ ከታች ያለውን ቁልፍ ይጫኑ።"
            ),
            reply_markup=button
        )
    except Exception:
        req["admin_agreement_requested"] = False


# ==================================================
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    context.user_data["truck"] = {}
    await update.message.reply_text("🚛 መኪና ማስመዝገብ\n\n1️⃣ የመኪናውን አይነት ይጻፉ።\nምሳሌ፦ Isuzu / ካሶኒ / ኦባማ")
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return TRUCK_PLATE

async def truck_plate(update, context):
    context.user_data["truck"]["plate"] = update.message.text
    await update.message.reply_text("3️⃣ የመጫን አቅሙን ይጻፉ።\nምሳሌ፦ 30 ቶን")
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    context.user_data["truck"]["capacity"] = update.message.text
    await update.message.reply_text("4️⃣ መነሻ ቦታ ይጻፉ።")
    return TRUCK_FROM

async def truck_from(update, context):
    context.user_data["truck"]["from"] = update.message.text
    await update.message.reply_text("5️⃣ የሚሄድበትን መንገድ ይጻፉ።\n\nምሳሌ፦ ደብረ ማርቆስ, አዲስ አበባ, አዳማ")
    return TRUCK_ROUTE

async def truck_route(update, context):
    context.user_data["truck"]["route"] = update.message.text
    await update.message.reply_text("6️⃣ የመኪናውን አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS

async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text
    await update.message.reply_text("7️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER

async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text
    await update.message.reply_text("8️⃣ የስልክ ቁጥር ይጻፉ።")
    return TRUCK_PHONE

async def truck_phone(update, context):
    truck = context.user_data["truck"]
    truck["phone"] = update.message.text
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck_posts.append(truck.copy())
    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"📍 መነሻ፦ {truck['from']}\n"
        f"🛣️ መንገድ፦ {truck['route']}\n"
        f"📍 አድራሻ፦ {truck['address']}\n"
        f"👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
    )
    for cargo in cargo_posts:
        if vehicle_match(truck.get("type"), cargo.get("vehicle")) and route_match(truck.get("from"), truck.get("route"), cargo.get("from"), cargo.get("to")):
            try:
                await context.bot.send_message(
                    chat_id=cargo["user_id"],
                    text=(
                        "🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n"
                        f"🚛 አይነት፦ {truck['type']}\n"
                        f"⚖️ አቅም፦ {truck['capacity']}\n"
                        f"📍 አድራሻ፦ {truck['address']}\n\n"
                        "🚛 መኪና መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass
    return ConversationHandler.END


# ==================================================
# FIND TRUCK
# ==================================================

async def find_truck(
    update,
    context
):

    active_trucks = [t for t in truck_posts if t.get("active", True)]

    if not active_trucks:

        await update.message.reply_text(

            "ይቅርታ ለጊዜው በምዝገባ ላይ ገቢ የሆነ የጭነት መኪና የለም።\n"
            "እባክዎን ከተወሰነ ጊዜ በኋላ ደግመው የጭነት መኪና ይፈልጉ።",

            reply_markup=main_menu()
        )

        return

    text = (
        "🚛 የተመዘገቡ "
        "መኪኖች፦\n\n"
    )

    buttons = []

    for i, truck in enumerate(
        active_trucks,
        1
    ):

        text += (

            f"🚛 መኪና #{i}\n"

            f"🔹 አይነት፦ "
            f"{truck['type']}\n"

            f"⚖️ አቅም፦ "
            f"{truck['capacity']}\n"

            f"📍 መነሻ፦ "
            f"{truck['from']}\n"

            f"🛣️ መንገድ፦ "
            f"{truck['route']}\n"

            "🔒 ታርጋና ስልክ "
            "ተደብቀዋል።\n\n"
        )

        buttons.append([

            InlineKeyboardButton(

f"🔢 {i} — 🤝 ግንኙነት ጠይቅ",

                callback_data=(
                    f"truckconnect_{i - 1}"
                )
            )
        ])

    text += "\n👉 እባክዎን የፈለጉትን የጭነት መኪና ዝርዝር በቁጥር ይምረጡ።"

    await update.message.reply_text(

        text,

        reply_markup=(
            InlineKeyboardMarkup(buttons)
        )
    )


# ==================================================
# TRUCK CONNECTION
# ==================================================

async def truck_connection_request(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    index = int(
        query.data.split("_")[1]
    )

    if (
        index < 0
        or index >= len(truck_posts)
    ):

        await query.edit_message_text(
            "❌ ይህ መኪና "
            "ከአሁን በኋላ "
            "አይገኝም።"
        )

        return

    truck = truck_posts[index]

    if not truck.get("active", True):
        await query.edit_message_text("❌ ይህ መኪና ቀድሞ ተስማምቶ ከመፈለጊያ ዝርዝር ተወግዷል።")
        return

    requester = update.effective_user

    if truck["user_id"] == requester.id:

        await query.answer(

            "❌ የራስዎን "
            "መኪና መጠየቅ "
            "አይችሉም።",

            show_alert=True
        )

        return

    for req in connection_requests:

        if (
            req.get("type") == "truck"
            and req["truck_index"] == index
            and req["requester_id"]
            == requester.id
            and req["status"] in [
                "pending",
                "negotiating",
                "awaiting_payment",
            ]
        ):

            await query.answer(

                "⚠️ ይህን መኪና "
                "አስቀድመው "
                "ጠይቀዋል።",

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

        "truck_owner_id":
            truck["user_id"],

        "requester_id":
            requester.id,

        "requester_name":
            requester.full_name,

        "status": "pending",

        "offers": [],

        "current_offer": None,

        "current_offer_by": None,

        # NEW
        "offer_version": 0,

        "final_price": None,

        "requester_confirmed": False,

        "other_confirmed": False,

        "requester_payment_submitted":
            False,

        "other_payment_submitted":
            False,

        "requester_payment_approved":
            False,

        "other_payment_approved":
            False,

        "requester_payment_rejected":
            False,

        "other_payment_rejected":
            False,

        # NEW
        "requester_payment_version":
            0,

        "other_payment_version":
            0,

        "full_info_shared": False,
    }

    connection_requests.append(
        request
    )

    await query.edit_message_text(

        "✅ የመኪና ግንኙነት "
        "ጥያቄዎ ተላክቷል።\n\n"

        "🔒 የግል መረጃዎች "
        "እስከ ማረጋገጫ ድረስ "
        "ተደብቀዋል።"
    )

    try:
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 TANA CARGO — አዲስ የመኪና ግንኙነት ጥያቄ\n\n"
                    f"📌 Request ID፦ {len(connection_requests)-1}\n"
                    f"🚛 መኪና፦ {truck['type']}\n"
                    f"📍 {truck['from']} ➡️ {truck['route']}\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                    "💬 ድርድሩ በ @tanacargosuport እንዲቀጥል ያስተባብሩ።"
                )
            )
    except Exception:
        pass

    try:

        await context.bot.send_message(

            chat_id=truck["user_id"],

            text=(

                "🔔 አዲስ "
                "የመኪና ግንኙነት "
                "ጥያቄ!\n\n"

                f"🚛 አይነት፦ "
                f"{truck['type']}\n"

                f"📍 መነሻ፦ "
                f"{truck['from']}\n"

                f"🛣️ መንገድ፦ "
                f"{truck['route']}\n\n"

                f"👤 ጠያቂ፦ "
                f"{requester.full_name}\n\n"

                "🚛 የእርሶን የጭነት መኪና የሚፈልግ ደንበኛ ተገኝቷል!\n"
                "💬 ለዋጋና ዝርዝር ውይይት @tanacargosuport ይጠቀሙ።"
            ),

            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🤝 ግንኙነት", callback_data=f"accept_{len(connection_requests)-1}"),
                InlineKeyboardButton("❌ ውድቅ", callback_data=f"reject_{len(connection_requests)-1}")
            ]])
        )

    except Exception:
        pass


# ==================================================
# OWNER REGISTRATION
# ==================================================

async def owner_start(
    update,
    context
):

    context.user_data["owner"] = {}

    await update.message.reply_text(

        "📦 የጭነት ባለቤት "
        "ምዝገባ\n\n"

        "1️⃣ ስምዎን ይጻፉ።"
    )

    return OWNER_NAME


async def owner_name(
    update,
    context
):

    context.user_data[
        "owner"
    ]["name"] = update.message.text

    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን "
        "ይጻፉ።"
    )

    return OWNER_PHONE


async def owner_phone(
    update,
    context
):

    context.user_data[
        "owner"
    ]["phone"] = update.message.text

    owner = context.user_data[
        "owner"
    ]

    owner["user_id"] = (
        update.effective_user.id
    )

    if (
        update.effective_user.id
        not in users
    ):

        users[
            update.effective_user.id
        ] = {

            "name":
                update.effective_user.full_name,

            "username":
                update.effective_user.username
                or "",

            "id":
                update.effective_user.id,
        }

    users[
        update.effective_user.id
    ]["owner"] = owner.copy()

    await update.message.reply_text(

        "✅ የጭነት ባለቤት "
        "ምዝገባዎ "
        "ተጠናቋል!",

        reply_markup=main_menu()
    )

    return ConversationHandler.END


# ==================================================
# PROFILE
# ==================================================

async def profile(
    update,
    context
):

    user_id = update.effective_user.id

    user = users.get(
        user_id
    )

    if not user:

        user = {

            "name":
                update.effective_user.full_name,

            "username":
                update.effective_user.username
                or "",

            "id":
                user_id,
        }

        users[user_id] = user

    message = (

        "👤 My Profile\n\n"

        f"👤 ስም፦ "
        f"{user['name']}\n"

        f"🔗 Username፦ "
        f"@{user['username'] if user['username'] else 'የለም'}\n"

        f"🆔 Telegram ID፦ "
        f"{user['id']}\n"
    )

    if "owner" in user:

        message += (
            "\n📦 የጭነት ባለቤት "
            "ምዝገባ፦ ✅\n"
        )

    my_cargo = [

        c for c in cargo_posts

        if c["user_id"] == user_id
    ]

    my_trucks = [

        t for t in truck_posts

        if t["user_id"] == user_id
    ]

    message += (

        f"\n🚚 የለጠፉት "
        f"ጭነት፦ {len(my_cargo)}\n"

        f"🚛 የተመዘገቡ "
        f"መኪኖች፦ {len(my_trucks)}"
    )

    await update.message.reply_text(

        message,

        reply_markup=main_menu()
    )


# ==================================================
# SUPPORT
# ==================================================

async def support_start(
    update,
    context
):
    """Show the Support contact information immediately."""

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 @tanacargosuport",
                url=SUPPORT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "☎️ 0960011010 ይደውሉ",
                url="tel:" + SUPPORT_PHONE
            )
        ],
    ])

    await update.message.reply_text(
        "ለማንኛውም እገዛ👉 @tanacargosuport ይጫኑ\n"
        "በዚህ ስልክ ቁጥር ይደውሉ 0960011010\n"
        "እናመሰግናለን",
        reply_markup=buttons
    )

    return ConversationHandler.END


async def support_message(update, context):
    await update.message.reply_text(
        "🙏 መልእክትዎን ለSupport ለመላክ @tanacargosuport ይጫኑ።",
        reply_markup=main_menu()
    )
    return ConversationHandler.END


# ==================================================
# ABOUT
# ==================================================

async def about(
    update,
    context
):

    await update.message.reply_text(
        "🚚 TANA CARGO | ጣና ጭነት\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት፣ "
        "የጭነት ማጓጓዣ ሂደትን ለማቀላጠፍ እና ቀላልና የተደራጀ አገልግሎት ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "📞 ለተጨማሪ እገዛ፦ @tanacargosuport\n"
        "☎️ 0960011010\n\n"
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

async def cancel(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(

        "❌ ሂደቱ ተሰርዟል።",

        reply_markup=main_menu()
    )

    return ConversationHandler.END


# ==================================================
# MENU ROUTER
# ==================================================

async def menu_router(
    update,
    context
):

    text = update.message.text

    if text == "🚚 ጭነት መለጠፍ":

        return await cargo_start(
            update,
            context
        )

    if text == "🔎 ጭነት መፈለግ":

        await find_cargo(
            update,
            context
        )

        return

    if text == "🚛 መኪና ማስመዝገብ":

        return await truck_start(
            update,
            context
        )

    if text == "🚛 መኪና መፈለግ":

        await find_truck(
            update,
            context
        )

        return

    if text == "📦 የጭነት ባለቤት":

        return await owner_start(
            update,
            context
        )

    if text == "👤 የኔ መረጃ":

        await profile(
            update,
            context
        )

        return

    if text == "🤝 ግንኙነት ጥያቄዎች":

        await show_connection_requests(
            update,
            context
        )

        return

    if text == "📞 Support":

        return await support_start(
            update,
            context
        )

    if text == "ℹ️ About":

        await about(
            update,
            context
        )

        return


# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(
    update,
    context
):

    user_id = update.effective_user.id

    payment_index = (
        get_payment_index(
            user_id
        )
    )

    if payment_index is not None:

        return await receipt_message(
            update,
            context
        )

    negotiation_index = (
        get_negotiation_index(
            user_id
        )
    )

    if negotiation_index is not None:

        return await submit_price(
            update,
            context
        )

    return await menu_router(
        update,
        context
    )


# ==================================================
# MENU INTERRUPT
# ==================================================

async def menu_interrupt(update, context):
    text=update.message.text; context.user_data.clear()
    if text=="🚚 ጭነት መለጠፍ": return await cargo_start(update,context)
    if text=="🚛 መኪና ማስመዝገብ": return await truck_start(update,context)
    if text=="📦 የጭነት ባለቤት": return await owner_start(update,context)
    if text=="💳 የአገልግሎት ክፍያ ለመፈፀም": await service_payment(update,context); return ConversationHandler.END
    if text=="📞 Support": return await support_start(update,context)
    if text=="🔎 ጭነት መፈለግ": await find_cargo(update,context); return ConversationHandler.END
    if text=="🚛 መኪና መፈለግ": await find_truck(update,context); return ConversationHandler.END
    if text=="👤 የኔ መረጃ": await profile(update,context); return ConversationHandler.END
    if text=="🤝 ግንኙነት ጥያቄዎች": await show_connection_requests(update,context); return ConversationHandler.END
    if text=="ℹ️ About": await about(update,context); return ConversationHandler.END
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"TANA CARGO ERROR: {context.error}")
    try:
        print(f"ERROR UPDATE: {update}")
    except Exception:
        pass


# ==================================================
# MAIN
# ==================================================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_error_handler(error_handler)

    # ==================================================
    # SIMPLE COMMANDS
    # ==================================================

    application.add_handler(
        CommandHandler("start", start)
    )

    # These commands are useful even when the user is not
    # inside a conversation.
    application.add_handler(
        CommandHandler("findcargo", find_cargo)
    )
    application.add_handler(
        CommandHandler("findtruck", find_truck)
    )
    application.add_handler(
        CommandHandler("profile", profile)
    )
    application.add_handler(
        CommandHandler("about", about)
    )
    application.add_handler(
        CommandHandler("connections", show_connection_requests)
    )

    # ==================================================
    # ONE MASTER CONVERSATION
    # ==================================================
    #
    # IMPORTANT FIX:
    # Previously cargo/truck/owner/support were four separate
    # ConversationHandlers. When one of them was active, its
    # state could capture the next menu button and the other
    # menu buttons could appear to do nothing.
    #
    # Putting all four flows into ONE ConversationHandler means:
    #   - every menu button can be used again while a flow is active
    #   - allow_reentry=True lets a new menu entry restart/switch flow
    #   - the existing cargo/truck/owner/support functions remain intact
    #
    master_conversation = ConversationHandler(

        entry_points=[

            CommandHandler("start", start),

            MessageHandler(
                filters.Regex(r"^🚚 ጭነት መለጠፍ$"),
                cargo_start,
            ),

            MessageHandler(
                filters.Regex(r"^🚛 መኪና ማስመዝገብ$"),
                truck_start,
            ),

            MessageHandler(
                filters.Regex(r"^📦 የጭነት ባለቤት$"),
                owner_start,
            ),

            MessageHandler(
                filters.Regex(r"^📞 Support$"),
                support_start,
            ),

            MessageHandler(
                filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"),
                service_payment,
            ),

            # Command entry points for the main registration flows.
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CommandHandler("owner", owner_start),
        ],

        states={

            # ---------------- CARGO ----------------
            CARGO_FROM: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_from,
                )
            ],

            CARGO_TO: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_to,
                )
            ],

            CARGO_TYPE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_type,
                )
            ],

            CARGO_VEHICLE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                CallbackQueryHandler(
                    cargo_vehicle,
                    pattern=r"^cargo_vehicle_",
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_vehicle_text,
                ),
            ],

            CARGO_SIZE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                CallbackQueryHandler(
                    cargo_size,
                    pattern=r"^size_",
                )
            ],

            CARGO_WEIGHT: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_weight,
                )
            ],

            CARGO_DATE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_date,
                )
            ],

            CARGO_PRICE: [
                MessageHandler(
                    filters.Regex(r"🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About"),
                    menu_interrupt,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price),
            ],

            CARGO_PHONE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_phone,
                )
            ],

            # ---------------- TRUCK ----------------
            TRUCK_TYPE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_type,
                )
            ],

            TRUCK_PLATE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_plate,
                )
            ],

            TRUCK_CAPACITY: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_capacity,
                )
            ],

            TRUCK_FROM: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_from,
                )
            ],

            TRUCK_ROUTE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_route,
                )
            ],

            TRUCK_ADDRESS: [
                MessageHandler(
                    filters.Regex(r"🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About"),
                    menu_interrupt,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address),
            ],

            TRUCK_DRIVER: [
                MessageHandler(
                    filters.Regex(r"🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About"),
                    menu_interrupt,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver),
            ],

            TRUCK_PHONE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_phone,
                )
            ],

            # ---------------- OWNER ----------------
            OWNER_NAME: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    owner_name,
                )
            ],

            OWNER_PHONE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    owner_phone,
                )
            ],

            # ---------------- SUPPORT ----------------
            SUPPORT_MESSAGE: [
                MessageHandler(
                    filters.Regex(r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 መኪና ማስመዝገብ|🚛 መኪና መፈለግ|📦 የጭነት ባለቤት|👤 የኔ መረጃ|🤝 ግንኙነት ጥያቄዎች|💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"),
                    menu_interrupt,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    support_message,
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel),
        ],

        # This is the key part of the fix. A menu entry can
        # start/restart another flow even if a previous flow is active.
        allow_reentry=True,
    )

    application.add_handler(master_conversation)

    # ==================================================
    # ADMIN RELAY
    # ==================================================
    application.add_handler(CallbackQueryHandler(relay_forward, pattern=r"^relay_\d+$"))

    # ==================================================
    # CONNECTION BUTTONS
    # ==================================================

    application.add_handler(
        CallbackQueryHandler(
            connection_request,
            pattern=r"^connect_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            truck_connection_request,
            pattern=r"^truckconnect_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            accept_connection,
            pattern=r"^accept_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            reject_connection,
            pattern=r"^reject_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_agreement_confirm,
            pattern=r"^adminagree_\d+$",
        )
    )

    # ==================================================
    # NEGOTIATION BUTTONS
    # ==================================================

    application.add_handler(
        CallbackQueryHandler(
            agree_price,
            pattern=r"^agreeprice_\d+_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            counter_price_button,
            pattern=r"^counterprice_\d+_\d+$",
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
        MessageHandler(
            filters.PHOTO,
            receipt_photo,
        )
    )

    # ==================================================
    # NON-CONVERSATION TEXT
    # ==================================================
    # This remains last so active conversation states get their
    # expected text first, while normal menu/search/profile text
    # is handled by text_router when no conversation is active.

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    print("TANA CARGO Bot is starting...")

    # Render Web Service Start Command:
    # python -m http.server $PORT & python bot.py
    application.run_polling(
        drop_pending_updates=True
    )


# ==================================================
# RUN
# ==================================================

if __name__ == "__main__":
    main()
# TANA CARGO latest update
