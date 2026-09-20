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

TOKEN = os.getenv("BOT_TOKEN")

SUPPORT_PHONE = "0960011010"
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

# Payment accounts will be added later in Render Environment Variables.
CBE_ACCOUNT = os.getenv("CBE_ACCOUNT", "")
ABAY_ACCOUNT = os.getenv("ABAY_ACCOUNT", "")
CBE_BIRR = os.getenv("CBE_BIRR", "")
TELEBIRR = os.getenv("TELEBIRR", "")

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []

# --------------------------------------------------
# STATES
# --------------------------------------------------

(
    CARGO_FROM,
    CARGO_TO,
    CARGO_TYPE,
    CARGO_VEHICLE,
    CARGO_SIZE,
    CARGO_WEIGHT,
    CARGO_DATE,
    CARGO_PHONE,
) = range(8)

(
    TRUCK_TYPE,
    TRUCK_PLATE,
    TRUCK_CAPACITY,
    TRUCK_FROM,
    TRUCK_ROUTE,
    TRUCK_PHONE,
) = range(8, 14)

OWNER_NAME, OWNER_PHONE = range(14, 16)

SUPPORT_MESSAGE = 16

NEGOTIATE_PRICE = 17
NEGOTIATE_CONFIRM = 18

PAYMENT_RECEIPT = 19


# --------------------------------------------------
# MAIN MENU
# --------------------------------------------------

def main_menu():
    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 መኪና ማስመዝገብ", "🚛 መኪና መፈለግ"],
        ["📦 የጭነት ባለቤት", "👤 የኔ መረጃ"],
        ["🤝 ግንኙነት ጥያቄዎች"],
        ["📞 Support", "ℹ️ About"],
    ]

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )


# --------------------------------------------------
# KEYBOARDS
# --------------------------------------------------

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


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def normalize(text):
    return text.strip().lower().replace(" ", "")


def route_points(text):
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

    return normalize(truck_type) == normalize(requested_vehicle)


def commission_amount(price):
    """
    Latest agreed business rule:
    1% from cargo/vehicle seeker
    1% from the other side.
    Total TANA CARGO commission = 2%.
    """

    price = float(price)

    each_side = price * 0.01
    total = each_side * 2

    return each_side, total


def payment_methods_text():
    text = "💳 የክፍያ መንገዶች፦\n\n"

    if CBE_ACCOUNT:
        text += f"🏦 CBE፦ {CBE_ACCOUNT}\n"

    if ABAY_ACCOUNT:
        text += f"🏦 Abay Bank፦ {ABAY_ACCOUNT}\n"

    if CBE_BIRR:
        text += f"💰 CBE Birr፦ {CBE_BIRR}\n"

    if TELEBIRR:
        text += f"📱 Telebirr፦ {TELEBIRR}\n"

    if text == "💳 የክፍያ መንገዶች፦\n\n":
        text += (
            "⚠️ የክፍያ መረጃ ገና አልተዘጋጀም።\n"
            "እባክዎ Admin ያግኙ።"
        )

    return text


# --------------------------------------------------
# START
# --------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        reply_markup=main_menu(),
    )


# --------------------------------------------------
# CARGO POSTING
# --------------------------------------------------

async def cargo_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"] = {}

    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n"
        "1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\n"
        "ምሳሌ፦ ባህር ዳር"
    )

    return CARGO_FROM


async def cargo_from(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["from"] = update.message.text

    await update.message.reply_text(
        "2️⃣ የሚደርስበትን ቦታ ይጻፉ።"
    )

    return CARGO_TO


async def cargo_to(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["to"] = update.message.text

    await update.message.reply_text(
        "3️⃣ የጭነቱን አይነት ይጻፉ።\n"
        "ምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ"
    )

    return CARGO_TYPE


async def cargo_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["type"] = update.message.text

    await update.message.reply_text(
        "4️⃣ ለዚህ ጭነት የሚፈልጉትን የመኪና አይነት ይምረጡ።",
        reply_markup=vehicle_keyboard("cargo_vehicle")
    )

    return CARGO_VEHICLE


async def cargo_vehicle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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
        reply_markup=size_keyboard(),
    )

    return CARGO_SIZE


async def cargo_vehicle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    cargo = context.user_data["cargo"]

    if not cargo.get("vehicle_waiting"):
        return

    cargo["vehicle"] = update.message.text
    cargo["vehicle_waiting"] = False

    await update.message.reply_text(
        f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n"
        "5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard(),
    )

    return CARGO_SIZE


async def cargo_size(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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


async def cargo_weight(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["weight"] = update.message.text

    await update.message.reply_text(
        "7️⃣ የሚጫንበትን ቀን ይጻፉ።\n"
        "ምሳሌ፦ 25/09/2026"
    )

    return CARGO_DATE


async def cargo_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["date"] = update.message.text

    await update.message.reply_text(
        "8️⃣ ስልክ ቁጥር ይጻፉ።"
    )

    return CARGO_PHONE


async def cargo_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["cargo"]["phone"] = update.message.text

    cargo = context.user_data["cargo"]
    cargo["user_id"] = update.effective_user.id

    cargo_posts.append(cargo.copy())

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} "
        f"({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን፦ {cargo['date']}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች "
        "ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu(),
    )

    # Notify matching truck owners
    for truck in truck_posts:
        if (
            vehicle_match(truck["type"], cargo["vehicle"])
            and route_match(
                truck["from"],
                truck["route"],
                cargo["from"],
                cargo["to"]
            )
        ):
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
                        f"📅 {cargo['date']}\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END


# --------------------------------------------------
# FIND CARGO
# --------------------------------------------------

async def find_cargo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not cargo_posts:
        await update.message.reply_text(
            "📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።\n\n"
            "🔎 ጭነት እያፈላለግን ነው።\n"
            "🔔 ተስማሚ ጭነት ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu(),
        )
        return

    message = "🔎 የተለጠፉ ጭነቶች፦\n\n"
    buttons = []

    for i, cargo in enumerate(cargo_posts, 1):

        message += (
            f"📦 ጭነት #{i}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 መኪና፦ {cargo['vehicle']}\n"
            f"📊 {cargo['size_name']} "
            f"({cargo['size']}%)\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n"
            "🔒 የባለቤቱ ስልክ ተደብቋል።\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🤝 ግንኙነት ጠይቅ #{i}",
                    callback_data=f"connect_{i-1}"
                )
            ]
        )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# --------------------------------------------------
# CONNECTION REQUEST
# --------------------------------------------------

async def connection_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(cargo_posts):
        await query.edit_message_text(
            "❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።"
        )
        return

    cargo = cargo_posts[index]
    requester = update.effective_user

    if cargo["user_id"] == requester.id:
        await query.answer(
            "❌ የራስዎን ጭነት መጠየቅ አይችሉም።",
            show_alert=True
        )
        return

    # Prevent duplicate pending request
    for req in connection_requests:
        if (
            req["cargo_index"] == index
            and req["requester_id"] == requester.id
            and req["status"] in [
                "pending",
                "negotiating",
                "agreed",
                "awaiting_payment",
                "payment_submitted",
            ]
        ):
            await query.answer(
                "⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።",
                show_alert=True
            )
            return

    request = {
        "cargo_index": index,
        "cargo_owner_id": cargo["user_id"],
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "status": "pending",
        "offers": [],
        "final_price": None,
        "cargo_owner_confirmed": False,
        "requester_confirmed": False,
        "cargo_owner_paid": False,
        "requester_paid": False,
        "cargo_owner_payment_submitted": False,
        "requester_payment_submitted": False,
    }

    connection_requests.append(request)

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄዎ ተላክቷል።\n\n"
        "🔒 የግል ስልክ ቁጥሮች እስካሁን ተደብቀዋል።\n"
        "💬 የዋጋ ድርድሩ በግል በTANA CARGO Bot ውስጥ ይካሄዳል።\n\n"
        "⏳ የጭነቱ ባለቤት ሲቀበል ይነገርዎታል።"
    )

    # Notify cargo owner
    try:
        await context.bot.send_message(
            chat_id=cargo["user_id"],
            text=(
                "🔔 አዲስ የግንኙነት ጥያቄ መጥቷል!\n\n"
                f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                f"📦 {cargo['type']}\n"
                f"🚛 {cargo['vehicle']}\n"
                f"📊 {cargo['size_name']}\n\n"
                f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                "🤝 ጥያቄዎችን ለማየት "
                "«🤝 ግንኙነት ጥያቄዎች» ይጫኑ።"
            ),
            reply_markup=main_menu()
        )
    except Exception:
        pass

    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 አዲስ የግንኙነት ጥያቄ\n\n"
                    f"📦 {cargo['from']} ➡️ {cargo['to']}\n"
                    f"📦 {cargo['type']}\n"
                    f"🚛 {cargo['vehicle']}\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n"
                    f"🆔 ID፦ {requester.id}"
                )
            )
        except Exception:
            pass


# --------------------------------------------------
# CONNECTION REQUESTS LIST
# --------------------------------------------------

async def show_connection_requests(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    received = []
    sent = []

    for i, req in enumerate(connection_requests):
        if req["cargo_owner_id"] == user_id:
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
        cargo = cargo_posts[req["cargo_index"]]

        text += (
            f"📥 የገባ #{i + 1}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"📦 {cargo['type']}\n"
            f"👤 {req['requester_name']}\n"
            f"📌 ሁኔታ፦ {req['status']}\n\n"
        )

        if req["status"] == "pending":
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"✅ ተቀበል #{i + 1}",
                        callback_data=f"accept_{i}"
                    ),
                    InlineKeyboardButton(
                        f"❌ ውድቅ #{i + 1}",
                        callback_data=f"reject_{i}"
                    ),
                ]
            )

    for i, req in sent:
        text += (
            f"📤 የላኩት #{i + 1}\n"
            f"📌 ሁኔታ፦ {req['status']}\n"
        )

        if req["final_price"]:
            text += f"💰 የመጨረሻ ዋጋ፦ {req['final_price']:,.2f} ብር\n"

        text += "\n"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
        if buttons else main_menu()
    )


# --------------------------------------------------
# ACCEPT / REJECT CONNECTION
# --------------------------------------------------

async def accept_connection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(connection_requests):
        return

    req = connection_requests[index]

    if req["cargo_owner_id"] != update.effective_user.id:
        await query.answer(
            "❌ ይህን ጥያቄ እርስዎ መቀበል አይችሉም።",
            show_alert=True
        )
        return

    req["status"] = "negotiating"

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

    context.user_data["negotiation_request"] = index


async def reject_connection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(connection_requests):
        return

    req = connection_requests[index]

    if req["cargo_owner_id"] != update.effective_user.id:
        return

    req["status"] = "rejected"

    await query.edit_message_text(
        "❌ የግንኙነት ጥያቄው ውድቅ ተደርጓል።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=(
                "❌ የግንኙነት ጥያቄዎ በጭነቱ ባለቤት ውድቅ ተደርጓል።"
            )
        )
    except Exception:
        pass


# --------------------------------------------------
# NEGOTIATION
# --------------------------------------------------

async def submit_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    active_request = None
    active_index = None

    for i, req in enumerate(connection_requests):
        if (
            req["status"] == "negotiating"
            and (
                req["requester_id"] == user_id
                or req["cargo_owner_id"] == user_id
            )
        ):
            active_request = req
            active_index = i
            break

    if active_request is None:
        return

    text = update.message.text.strip()

    try:
        price = float(
            text.replace(",", "")
            .replace("ብር", "")
            .strip()
        )
    except ValueError:
        await update.message.reply_text(
            "❌ የዋጋውን ቁጥር ብቻ ይጻፉ።\n"
            "ምሳሌ፦ 55000"
        )
        return NEGOTIATE_PRICE

    if price <= 0:
        await update.message.reply_text(
            "❌ ዋጋው ከ0 በላይ መሆን አለበት።"
        )
        return NEGOTIATE_PRICE

    active_request["offers"].append({
        "user_id": user_id,
        "price": price,
    })

    active_request["current_offer"] = price
    active_request["current_offer_by"] = user_id

    other_user = (
        active_request["cargo_owner_id"]
        if user_id == active_request["requester_id"]
        else active_request["requester_id"]
    )

    await update.message.reply_text(
        f"💰 የላኩት ዋጋ፦ {price:,.2f} ብር\n\n"
        "⏳ የሌላኛውን ወገን ምላሽ ይጠብቁ።"
    )

    buttons = [
        [
            InlineKeyboardButton(
                f"✅ ይስማሙ {price:,.2f} ብር",
                callback_data=f"agreeprice_{active_index}"
            )
        ],
        [
            InlineKeyboardButton(
                "💬 ሌላ ዋጋ ላክ",
                callback_data=f"counterprice_{active_index}"
            )
        ],
    ]

    try:
        await context.bot.send_message(
            chat_id=other_user,
            text=(
                "💰 አዲስ የዋጋ ጥያቄ መጥቷል።\n\n"
                f"💵 የቀረበው ዋጋ፦ {price:,.2f} ብር\n\n"
                "ይስማሙ ወይም ሌላ ዋጋ ያቅርቡ።"
            ),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception:
        pass

    return NEGOTIATE_PRICE


async def agree_price(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(connection_requests):
        return

    req = connection_requests[index]

    if req["status"] != "negotiating":
        return

    price = req.get("current_offer")

    if not price:
        return

    req["final_price"] = price
    req["cargo_owner_confirmed"] = True
    req["requester_confirmed"] = True
    req["status"] = "awaiting_payment"

    each_side, total = commission_amount(price)

    await query.edit_message_text(
        "✅ ሁለቱም ወገኖች ተስማምተዋል!\n\n"
        f"💰 የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n\n"
        f"📌 ከእያንዳንዱ ወገን 1%፦ "
        f"{each_side:,.2f} ብር\n"
        f"💵 TANA CARGO ጠቅላላ ኮሚሽን፦ "
        f"{total:,.2f} ብር\n\n"
        "🧾 አሁን ክፍያውን ያድርጉ።"
    )

    payment_text = (
        "💳 TANA CARGO ክፍያ\n\n"
        f"💰 የመጨረሻ ዋጋ፦ {price:,.2f} ብር\n"
        f"📌 የእርስዎ 1%፦ {each_side:,.2f} ብር\n\n"
        f"{payment_methods_text()}\n\n"
        "🧾 ክፍያ ካደረጉ በኋላ Receipt No. "
        "ወይም Screenshot ይላኩ።"
    )

    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=payment_text
        )
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=req["cargo_owner_id"],
            text=payment_text
        )
    except Exception:
        pass


async def counter_price_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(connection_requests):
        return

    context.user_data["negotiation_request"] = index

    await query.message.reply_text(
        "💬 አዲስ ዋጋ ይጻፉ።\n"
        "ምሳሌ፦ 55000"
    )


# --------------------------------------------------
# PAYMENT / RECEIPT
# --------------------------------------------------

async def receipt_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    active = None
    active_index = None

    for i, req in enumerate(connection_requests):
        if (
            req["status"] == "awaiting_payment"
            and (
                req["requester_id"] == user_id
                or req["cargo_owner_id"] == user_id
            )
        ):
            active = req
            active_index = i
            break

    if active is None:
        return

    receipt = update.message.text

    if user_id == active["requester_id"]:
        active["requester_payment_submitted"] = True
        active["requester_receipt"] = receipt
    else:
        active["cargo_owner_payment_submitted"] = True
        active["cargo_owner_receipt"] = receipt

    active["status"] = "payment_submitted"

    await update.message.reply_text(
        "🧾 የክፍያ መረጃዎ ተቀብለናል።\n\n"
        "⏳ Admin ክፍያውን ካረጋገጠ በኋላ "
        "የቀጣይ ደረጃ መረጃ ይላክልዎታል።"
    )

    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🧾 TANA CARGO — አዲስ የክፍያ ማስረጃ\n\n"
                    f"👤 User፦ {update.effective_user.full_name}\n"
                    f"🆔 ID፦ {user_id}\n"
                    f"💰 Final Price፦ "
                    f"{active['final_price']:,.2f} ብር\n"
                    f"🧾 Receipt፦ {receipt}\n\n"
                    f"📌 Request ID፦ {active_index}\n\n"
                    "እባክዎ ክፍያውን ያረጋግጡ።"
                )
            )
        except Exception:
            pass


async def receipt_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    active = None
    active_index = None

    for i, req in enumerate(connection_requests):
        if (
            req["status"] == "awaiting_payment"
            and (
                req["requester_id"] == user_id
                or req["cargo_owner_id"] == user_id
            )
        ):
            active = req
            active_index = i
            break

    if active is None:
        return

    file_id = update.message.photo[-1].file_id

    if user_id == active["requester_id"]:
        active["requester_payment_submitted"] = True
        active["requester_receipt_photo"] = file_id
    else:
        active["cargo_owner_payment_submitted"] = True
        active["cargo_owner_receipt_photo"] = file_id

    active["status"] = "payment_submitted"

    await update.message.reply_text(
        "🧾 Screenshot ተቀብለናል።\n\n"
        "⏳ Admin ክፍያውን ካረጋገጠ በኋላ "
        "የቀጣይ ደረጃ መረጃ ይላክልዎታል።"
    )

    if ADMIN_USER_ID:
        try:
            await context.bot.send_photo(
                chat_id=int(ADMIN_USER_ID),
                photo=file_id,
                caption=(
                    "🧾 TANA CARGO — የክፍያ Screenshot\n\n"
                    f"👤 {update.effective_user.full_name}\n"
                    f"🆔 {user_id}\n"
                    f"💰 Final Price፦ "
                    f"{active['final_price']:,.2f} ብር\n"
                    f"📌 Request ID፦ {active_index}"
                )
            )
        except Exception:
            pass


# --------------------------------------------------
# TRUCK REGISTRATION
# --------------------------------------------------

async def truck_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"] = {}

    await update.message.reply_text(
        "🚛 መኪና ማስመዝገብ\n\n"
        "1️⃣ የመኪናውን አይነት ይጻፉ።\n"
        "ምሳሌ፦ Isuzu / ካሶኒ / ኦባማ"
    )

    return TRUCK_TYPE


async def truck_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["type"] = update.message.text

    await update.message.reply_text(
        "2️⃣ የመኪናውን ታርጋ ይጻፉ።"
    )

    return TRUCK_PLATE


async def truck_plate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["plate"] = update.message.text

    await update.message.reply_text(
        "3️⃣ የመጫን አቅሙን ይጻፉ።\n"
        "ምሳሌ፦ 30 ቶን"
    )

    return TRUCK_CAPACITY


async def truck_capacity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["capacity"] = update.message.text

    await update.message.reply_text(
        "4️⃣ መነሻ ቦታ ይጻፉ።"
    )

    return TRUCK_FROM


async def truck_from(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["from"] = update.message.text

    await update.message.reply_text(
        "5️⃣ የሚሄድበትን መንገድ ይጻፉ።\n\n"
        "ምሳሌ፦\n"
        "ዳንግላ, ደብረ ማርቆስ, "
        "አዲስ አበባ, አዳማ\n\n"
        "👉 መንገድ ላይ ያሉ ዋና ከተሞችን "
        "በኮማ (,) ይለያዩ።"
    )

    return TRUCK_ROUTE


async def truck_route(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["route"] = update.message.text

    await update.message.reply_text(
        "6️⃣ የስልክ ቁጥር ይጻፉ።"
    )

    return TRUCK_PHONE


async def truck_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["truck"]["phone"] = update.message.text

    truck = context.user_data["truck"]
    truck["user_id"] = update.effective_user.id

    truck_posts.append(truck.copy())

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"📍 መነሻ፦ {truck['from']}\n"
        f"🛣️ መንገድ፦ {truck['route']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር "
        "ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu()
    )

    return ConversationHandler.END


# --------------------------------------------------
# FIND TRUCK
# --------------------------------------------------

async def find_truck(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not truck_posts:
        await update.message.reply_text(
            "🚛 በአሁኑ ጊዜ ተስማሚ መኪና የለም።\n\n"
            "🔎 መኪና እያፈላለግን ነው።\n"
            "🔔 ተስማሚ መኪና ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu()
        )
        return

    text = "🚛 የተመዘገቡ መኪኖች፦\n\n"
    buttons = []

    for i, truck in enumerate(truck_posts, 1):
        text += (
            f"🚛 መኪና #{i}\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"📍 መነሻ፦ {truck['from']}\n"
            f"🛣️ መንገድ፦ {truck['route']}\n"
            "🔒 ታርጋና ስልክ ተደብቀዋል።\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🤝 ግንኙነት ጠይቅ #{i}",
                    callback_data=f"truckconnect_{i-1}"
                )
            ]
        )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# --------------------------------------------------
# TRUCK CONNECTION REQUEST
# --------------------------------------------------

async def truck_connection_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    index = int(query.data.split("_")[1])

    if index >= len(truck_posts):
        await query.edit_message_text(
            "❌ ይህ መኪና ከአሁን በኋላ አይገኝም።"
        )
        return

    truck = truck_posts[index]
    requester = update.effective_user

    if truck["user_id"] == requester.id:
        await query.answer(
            "❌ የራስዎን መኪና መጠየቅ አይችሉም።",
            show_alert=True
        )
        return

    request = {
        "truck_index": index,
        "truck_owner_id": truck["user_id"],
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "status": "pending",
        "type": "truck",
        "offers": [],
        "final_price": None,
        "requester_confirmed": False,
        "truck_owner_confirmed": False,
    }

    connection_requests.append(request)

    await query.edit_message_text(
        "✅ የመኪና ግንኙነት ጥያቄዎ ተላክቷል።\n\n"
        "🔒 የግል መረጃዎች እስከ ማረጋገጫ ድረስ ተደብቀዋል።"
    )

    try:
        await context.bot.send_message(
            chat_id=truck["user_id"],
            text=(
                "🔔 አዲስ የመኪና ግንኙነት ጥያቄ!\n\n"
                f"🚛 አይነት፦ {truck['type']}\n"
                f"📍 መነሻ፦ {truck['from']}\n"
                f"🛣️ መንገድ፦ {truck['route']}\n\n"
                f"👤 ጠያቂ፦ {requester.full_name}\n\n"
                "🤝 ግንኙነት ጥያቄዎችን ይክፈቱ።"
            ),
            reply_markup=main_menu()
        )
    except Exception:
        pass


# --------------------------------------------------
# OWNER REGISTRATION
# --------------------------------------------------

async def owner_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["owner"] = {}

    await update.message.reply_text(
        "📦 የጭነት ባለቤት ምዝገባ\n\n"
        "1️⃣ ስምዎን ይጻፉ።"
    )

    return OWNER_NAME


async def owner_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["owner"]["name"] = update.message.text

    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።"
    )

    return OWNER_PHONE


async def owner_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["owner"]["phone"] = update.message.text

    owner = context.user_data["owner"]
    owner["user_id"] = update.effective_user.id

    if update.effective_user.id not in users:
        users[update.effective_user.id] = {
            "name": update.effective_user.full_name,
            "username": update.effective_user.username or "",
            "id": update.effective_user.id,
        }

    users[update.effective_user.id]["owner"] = owner.copy()

    await update.message.reply_text(
        "✅ የጭነት ባለቤት ምዝገባዎ ተጠናቋል!",
        reply_markup=main_menu()
    )

    return ConversationHandler.END


# --------------------------------------------------
# PROFILE
# --------------------------------------------------

async def profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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
        f"👤 ስም፦ {user['name']}\n"
        f"🔗 Username፦ "
        f"@{user['username'] if user['username'] else 'የለም'}\n"
        f"🆔 Telegram ID፦ {user['id']}\n"
    )

    if "owner" in user:
        message += (
            "\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n"
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
        f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n"
        f"🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"
    )

    await update.message.reply_text(
        message,
        reply_markup=main_menu()
    )


# --------------------------------------------------
# SUPPORT
# --------------------------------------------------

async def support_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "📞 TANA CARGO Support\n\n"
        "ችግር ወይም ጥያቄ ካለዎት ይደውሉ፦\n\n"
        f"📞 {SUPPORT_PHONE}\n\n"
        "ወይም መልዕክትዎን ከታች ይጻፉ።"
    )

    return SUPPORT_MESSAGE


async def support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    message = update.message.text

    await update.message.reply_text(
        "✅ መልዕክትዎ ተቀብለናል።\n\n"
        "🙏 TANA CARGO Support በቅርቡ ያነጋግርዎታል።",
        reply_markup=main_menu()
    )

    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🆘 TANA CARGO Support\n\n"
                    f"👤 ስም፦ {user.full_name}\n"
                    f"🆔 ID፦ {user.id}\n"
                    f"🔗 Username፦ "
                    f"@{user.username if user.username else 'የለም'}\n\n"
                    f"💬 መልዕክት፦\n{message}"
                )
            )
        except Exception:
            pass

    return ConversationHandler.END


# --------------------------------------------------
# ABOUT
# --------------------------------------------------

async def about(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "ℹ️ TANA CARGO\n\n"
        "TANA CARGO የጭነት ባለቤቶችንና "
        "የመኪና ባለቤቶችን ለማገናኘት "
        "የተዘጋጀ አገልግሎት ነው።\n\n"
        "🤝 የጭነት ባለቤትና የመኪና ባለቤት "
        "የመጓጓዣ ዋጋውን ራሳቸው ይደራደራሉ።\n\n"
        "💰 TANA CARGO የመጨረሻው የተስማሙበት "
        "ዋጋን አይወስንም።\n\n"
        "📌 ከተስማሙበት የመጨረሻ ዋጋ "
        "ከእያንዳንዱ ወገን 1% ኮሚሽን ይከፈላል።\n\n"
        "🔒 የግል ስልክና የታርጋ መረጃ "
        "እስከ ተገቢው ማረጋገጫ ድረስ ይጠበቃል።\n\n"
        "📞 Support፦ 0960011010",
        reply_markup=main_menu()
    )


# --------------------------------------------------
# CANCEL
# --------------------------------------------------

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ ሂደቱ ተሰርዟል።",
        reply_markup=main_menu()
    )

    return ConversationHandler.END


# --------------------------------------------------
# SIMPLE MENU ROUTER
# --------------------------------------------------

async def menu_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
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

    if text == "📦 የጭነት ባለቤት":
        return await owner_start(update, context)

    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return

    if text == "🤝 ግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return

    if text == "📞 Support":
        return await support_start(update, context)

    if text == "ℹ️ About":
        await about(update, context)
        return


# --------------------------------------------------
# MAIN
# --------------------------------------------------

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

    # -----------------------------
    # Cargo conversation
    # -----------------------------

    cargo_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🚚 ጭነት መለጠፍ$"),
                cargo_start
            )
        ],

        states={

            CARGO_FROM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_from
                )
            ],

            CARGO_TO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_to
                )
            ],

            CARGO_TYPE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_type
                )
            ],

            CARGO_VEHICLE: [
                CallbackQueryHandler(
                    cargo_vehicle,
                    pattern=r"^cargo_vehicle_"
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_vehicle_text
                ),
            ],

            CARGO_SIZE: [
                CallbackQueryHandler(
                    cargo_size,
                    pattern=r"^size_"
                )
            ],

            CARGO_WEIGHT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_weight
                )
            ],

            CARGO_DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_date
                )
            ],

            CARGO_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    cargo_phone
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ],

        allow_reentry=True,
    )

    application.add_handler(cargo_conversation)

    # -----------------------------
    # Truck conversation
    # -----------------------------

    truck_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🚛 መኪና ማስመዝገብ$"),
                truck_start
            )
        ],

        states={

            TRUCK_TYPE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_type
                )
            ],

            TRUCK_PLATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_plate
                )
            ],

            TRUCK_CAPACITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_capacity
                )
            ],

            TRUCK_FROM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_from
                )
            ],

            TRUCK_ROUTE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_route
                )
            ],

            TRUCK_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    truck_phone
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ],

        allow_reentry=True,
    )

    application.add_handler(truck_conversation)

    # -----------------------------
    # Owner conversation
    # -----------------------------

    owner_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📦 የጭነት ባለቤት$"),
                owner_start
            )
        ],

        states={

            OWNER_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    owner_name
                )
            ],

            OWNER_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    owner_phone
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ],

        allow_reentry=True,
    )

    application.add_handler(owner_conversation)

    # -----------------------------
    # Support conversation
    # -----------------------------

    support_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📞 Support$"),
                support_start
            )
        ],

        states={

            SUPPORT_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    support_message
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel)
        ],

        allow_reentry=True,
    )

    application.add_handler(support_conversation)

    # -----------------------------
    # Callback handlers
    # -----------------------------

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
            agree_price,
            pattern=r"^agreeprice_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            counter_price_button,
            pattern=r"^counterprice_\d+$"
        )
    )

    # -----------------------------
    # Payment screenshot
    # -----------------------------

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receipt_photo
        )
    )

    # -----------------------------
    # Price / receipt text
    # -----------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            submit_price
        )
    )

    # -----------------------------
    # Start
    # -----------------------------

    application.add_handler(
        CommandHandler("start", start)
    )

    # -----------------------------
    # Menu
    # -----------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            menu_router
        )
    )

    print("TANA CARGO Bot is starting...")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
