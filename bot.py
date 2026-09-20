import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# Temporary storage
users = {}
cargo_posts = []
truck_posts = []

# Conversation states
CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_WEIGHT, CARGO_DATE, CARGO_PHONE = range(6)
TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_FROM, TRUCK_TO, TRUCK_PHONE = range(6, 12)
OWNER_NAME, OWNER_PHONE = range(12, 14)
SUPPORT_MESSAGE = 14


def main_menu():
    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 መኪና ማስመዝገብ", "📦 የጭነት ባለቤት"],
        ["👤 My Profile", "📞 Support"],
        ["ℹ️ About"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


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


# =========================
# CARGO POSTING
# =========================

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
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
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
        "4️⃣ የጭነቱን ክብደት ይጻፉ።\n"
        "ምሳሌ፦ 10 ቶን"
    )
    return CARGO_WEIGHT


async def cargo_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["weight"] = update.message.text
    await update.message.reply_text(
        "5️⃣ የሚጫንበትን ቀን ይጻፉ።\n"
        "ምሳሌ፦ 25/09/2026"
    )
    return CARGO_DATE


async def cargo_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cargo"]["date"] = update.message.text
    await update.message.reply_text(
        "6️⃣ ስልክ ቁጥር ይጻፉ።"
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
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን፦ {cargo['date']}\n"
        f"📞 ስልክ፦ {cargo['phone']}",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# =========================
# FIND CARGO
# =========================

async def find_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not cargo_posts:
        await update.message.reply_text(
            "🔎 በአሁኑ ጊዜ የተለጠፈ ጭነት የለም።"
        )
        return

    message = "🔎 የተለጠፉ ጭነቶች፦\n\n"

    for i, cargo in enumerate(cargo_posts, 1):
        message += (
            f"🚚 ጭነት #{i}\n"
            f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
            f"📦 {cargo['type']}\n"
            f"⚖️ {cargo['weight']}\n"
            f"📅 {cargo['date']}\n"
            f"📞 {cargo['phone']}\n\n"
        )

    await update.message.reply_text(message)


# =========================
# TRUCK REGISTRATION
# =========================

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
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return TRUCK_PLATE


async def truck_plate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["plate"] = update.message.text
    await update.message.reply_text(
        "3️⃣ የመጫን አቅሙን ይጻፉ።\n"
        "ምሳሌ፦ 10 ቶን"
    )
    return TRUCK_CAPACITY


async def truck_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["capacity"] = update.message.text
    await update.message.reply_text("4️⃣ መነሻ ቦታ ይጻፉ።")
    return TRUCK_FROM


async def truck_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["from"] = update.message.text
    await update.message.reply_text("5️⃣ የሚሄድበትን ቦታ ይጻፉ።")
    return TRUCK_TO


async def truck_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["to"] = update.message.text
    await update.message.reply_text("6️⃣ የስልክ ቁጥር ይጻፉ።")
    return TRUCK_PHONE


async def truck_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["phone"] = update.message.text

    truck = context.user_data["truck"]
    truck["user_id"] = update.effective_user.id
    truck_posts.append(truck.copy())

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"📍 መነሻ፦ {truck['from']}\n"
        f"📍 መድረሻ፦ {truck['to']}\n"
        f"📞 ስልክ፦ {truck['phone']}",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# =========================
# CARGO OWNER
# =========================

async def owner_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner"] = {}
    await update.message.reply_text(
        "📦 የጭነት ባለቤት ምዝገባ\n\n"
        "1️⃣ ሙሉ ስምዎን ይጻፉ።"
    )
    return OWNER_NAME


async def owner_name(update: Update
