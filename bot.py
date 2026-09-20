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
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

SUPPORT_PHONE = "0960011010"
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

users = {}
cargo_posts = []
truck_posts = []
connection_requests = []

(
    CARGO_FROM,
    CARGO_TO,
    CARGO_TYPE,
    CARGO_SIZE,
    CARGO_WEIGHT,
    CARGO_DATE,
    CARGO_PHONE,
) = range(7)

(
    TRUCK_TYPE,
    TRUCK_PLATE,
    TRUCK_CAPACITY,
    TRUCK_FROM,
    TRUCK_ROUTE,
    TRUCK_PHONE,
) = range(7, 13)

OWNER_NAME, OWNER_PHONE = range(13, 15)
SUPPORT_MESSAGE = 15


def main_menu():
    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 መኪና ማስመዝገብ", "📦 የጭነት ባለቤት"],
        ["👤 My Profile", "📞 Support"],
        ["ℹ️ About"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def size_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100"),
        ],
        [
            InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50"),
        ],
        [
            InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def normalize(text):
    return text.strip().lower().replace(" ", "")


def route_points(text):
    separators = [",", "→", ">", "/", "፣"]
    for sep in separators:
        text = text.replace(sep, ",")
    return [normalize(x) for x in text.split(",") if x.strip()]


def route_match(truck_from, truck_route, cargo_from, cargo_to):
    truck_start = normalize(truck_from)
    cargo_start = normalize(cargo_from)
    cargo_end = normalize(cargo_to)

    points = [truck_start] + route_points(truck_route)

    if cargo_start not in points or cargo_end not in points:
        return False

    start_index = points.index(cargo_start)
    end_index = points.index(cargo_end)

    return start_index < end_index


def commission_rate(size):
    if size == 100:
        return 0.01
    if size == 50:
        return 0.015
    if size == 25:
        return 0.02
    return 0.01


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    users[user.id] = {
        "name": user.full_name,
        "username": user.username or "",
        "id": user.id,
    }

    await update.message.reply_text(
        "👋 እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "ከታች ያለውን ምናሌ ይጠቀሙ።",
        reply_markup=main_menu(),
    )


async def cargo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"] = {}

    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n"
        "1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\n"
        "ምሳሌ፦ ባህር ዳር"
    )

    return CARGO_FROM


async def cargo_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["from"] = update.message.text

    await update.message.reply_text(
        "2️⃣ የሚደርስበትን ቦታ ይጻፉ።"
    )

    return CARGO_TO


async def cargo_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["to"] = update.message.text

    await update.message.reply_text(
        "3️⃣ የጭነቱን አይነት ይጻፉ።\n"
        "ምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ"
    )

    return CARGO_TYPE


async def cargo_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["type"] = update.message.text

    await update.message.reply_text(
        "4️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard(),
    )

    return CARGO_SIZE


async def cargo_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sizes = {
        "size_100": (100, "ሙሉ ጭነት"),
        "size_50": (50, "ግማሽ ጭነት"),
        "size_25": (25, "እሩብ ጭነት"),
    }

    size, size_name = sizes[query.data]

    context.user_data["cargo"]["size"] = size
    context.user_data["cargo"]["size_name"] = size_name

    await query.edit_message_text(
        f"📦 የተመረጠው፦ {size_name} ({size}%)\n\n"
        "5️⃣ የጭነቱን ክብደት ይጻፉ።\n"
        "ምሳሌ፦ 10 ቶን"
    )

    return CARGO_WEIGHT


async def cargo_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["weight"] = update.message.text

    await update.message.reply_text(
        "6️⃣ የሚጫንበትን ቀን ይጻፉ።\n"
        "ምሳሌ፦ 25/09/2026"
    )

    return CARGO_DATE


async def cargo_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["date"] = update.message.text

    await update.message.reply_text(
        "7️⃣ ስልክ ቁጥር ይጻፉ።"
    )

    return CARGO_PHONE


async def cargo_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["phone"] = update.message.text

    cargo = context.user_data["cargo"]
    cargo["user_id"] = update.effective_user.id

    cargo_posts.append(cargo.copy())

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን፦ {cargo['date']}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


async def find_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            f"📦 {cargo['type']}\n"
            f"📊 {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n"
            "🔒 የባለቤቱ ስልክ ተደብቋል።\n\n"
        )

        buttons.append(
            [InlineKeyboardButton(
                f"🤝 ግንኙነት ጠይቅ #{i}",
                callback_data=f"connect_{i-1}"
            )]
        )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def connection_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    request = {
        "cargo_index": index,
        "cargo_owner_id": cargo["user_id"],
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "status": "pending",
    }

    connection_requests.append(request)

    await query.edit_message_text(
        "✅ የግንኙነት ጥያቄዎ ተቀብለናል።\n\n"
        "🤝 TANA CARGO የሁለቱን ወገኖች ግንኙነት ያስተካክላል።\n"
        "🔒 የግል ስልክ ቁጥሮች እስከ ፍቃድ/ማረጋገጫ ድረስ ይደበቃሉ።"
    )

    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🔔 አዲስ የግንኙነት ጥያቄ\n\n"
                    f"📦 {cargo['from']} ➡️ {cargo['to']}\n"
                    f"📦 {cargo['type']}\n"
                    f"📊 {cargo['size_name']}\n"
                    f"👤 ጠያቂ፦ {requester.full_name}\n"
                    f"🆔 ID፦ {requester.id}"
                ),
            )
        except Exception:
            pass


async def truck_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"] = {}

    await update.message.reply_text(
        "🚛 መኪና ማስመዝገብ\n\n"
        "1️⃣ የመኪናውን አይነት ይጻፉ።\n"
        "ምሳሌ፦ Isuzu"
    )

    return TRUCK_TYPE


async def truck_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["type"] = update.message.text

    await update.message.reply_text(
        "2️⃣ የመኪናውን ታርጋ ይጻፉ።"
    )

    return TRUCK_PLATE


async def truck_plate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["plate"] = update.message.text

    await update.message.reply_text(
        "3️⃣ የመጫን አቅሙን ይጻፉ።\n"
        "ምሳሌ፦ 30 ቶን"
    )

    return TRUCK_CAPACITY


async def truck_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["capacity"] = update.message.text

    await update.message.reply_text(
        "4️⃣ መነሻ ቦታ ይጻፉ።"
    )

    return TRUCK_FROM


async def truck_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["from"] = update.message.text

    await update.message.reply_text(
        "5️⃣ የሚሄድበትን መንገድ ይጻፉ።\n\n"
        "ምሳሌ፦\n"
        "ዳንግላ, ደብረ ማርቆስ, አዲስ አበባ, አዳማ\n\n"
        "👉 መንገድ ላይ ያሉ ዋና ከተሞችን በኮማ (,) ይለያዩ።"
    )

    return TRUCK_ROUTE


async def truck_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["route"] = update.message.text

    await update.message.reply_text(
        "6️⃣ የስልክ ቁጥር ይጻፉ።"
    )

    return TRUCK_PHONE


async def truck_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


async def owner_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner"] = {}

    await update.message.reply_text(
        "📦 የጭነት ባለቤት ምዝገባ\n\n"
        "1️⃣ ስምዎን ይጻፉ።"
    )

    return OWNER_NAME


async def owner_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner"]["name"] = update.message.text

    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።"
    )

    return OWNER_PHONE


async def owner_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner"]["phone"] = update.message.text

    owner = context.user_data["owner"]
    owner["user_id"] = update.effective_user.id

    users[update.effective_user.id]["owner"] = owner.copy()

    await update.message.reply_text(
        "✅ የጭነት ባለቤት ምዝገባዎ ተጠናቋል!",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = users.get(update.effective_user.id)

    if not user:
        user = {
            "name": update.effective_user.full_name,
            "username": update.effective_user.username or "",
            "id": update.effective_user.id,
        }
        users[update.effective_user.id] = user

    message = (
        "👤 My Profile\n\n"
        f"👤 ስም፦ {user['name']}\n"
        f"🔗 Username፦ @{user['username'] if user['username'] else 'የለም'}\n"
        f"🆔 ID፦ {user['id']}\n"
    )

    if "owner" in user:
        message += "\n📦 የጭነት ባለቤት ምዝገባ አለ።"

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 TANA CARGO Support\n\n"
        "ችግር ወይም ጥያቄ ካለዎት ይደውሉ፦\n\n"
        f"📞 {SUPPORT_PHONE}\n\n"
        "ወይም መልዕክትዎን ከታች ይጻፉ።"
    )

    return SUPPORT_MESSAGE


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_USER_ID),
                text=(
                    "🆘 TANA CARGO Support\n\n"
                    f"👤 
