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

users = {}
cargo_posts = []
truck_posts = []

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


async def find_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not cargo_posts:
        await update.message.reply_text(
            "🔎 በአሁኑ ጊዜ የተለጠፈ ጭነት የለም።",
            reply_markup=main_menu(),
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

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


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
        "ምሳሌ፦ 10 ቶን"
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
        "5️⃣ የሚሄድበትን ቦታ ይጻፉ።"
    )

    return TRUCK_TO


async def truck_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["to"] = update.message.text

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
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"📍 መነሻ፦ {truck['from']}\n"
        f"📍 መድረሻ፦ {truck['to']}\n"
        f"📞 ስልክ፦ {truck['phone']}",
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
        "✅ የጭነት ባለቤት ምዝገባዎ ተጠናቋል!\n\n"
        f"👤 ስም፦ {owner['name']}\n"
        f"📞 ስልክ፦ {owner['phone']}",
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
        message += (
            f"\n📦 የጭነት ባለቤት\n"
            f"📞 {user['owner']['phone']}\n"
        )

    await update.message.reply_text(
        message,
        reply_markup=main_menu(),
    )


async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 TANA CARGO Support\n\n"
        "እባክዎ ጥያቄዎን ወይም ችግርዎን ይጻፉ።"
    )

    return SUPPORT_MESSAGE


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ መልዕክትዎ ተቀብለናል።\n"
        "የSupport ቡድናችን በቅርቡ ያገኝዎታል።",
        reply_markup=main_menu(),
    )

    return ConversationHandler.END


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ TANA CARGO\n\n"
        "TANA CARGO የጭነት ባለቤቶችንና "
        "የመኪና ባለቤቶችን ለማገናኘት የተዘጋጀ አገልግሎት ነው።\n\n"
        "🚚 ጭነት ይለጥፉ\n"
        "🔎 ጭነት ይፈልጉ\n"
        "🚛 መኪና ያስመዝግቡ\n"
        "📦 የጭነት ባለቤት ይመዝገቡ",
        reply_markup=main_menu(),
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)

    if text == "🔎 ጭነት መፈለግ":
        return await find_cargo(update, context)

    if text == "🚛 መኪና ማስመዝገብ":
        return await truck_start(update, context)

    if text == "📦 የጭነት ባለቤት":
        return await owner_start(update, context)

    if text == "👤 My Profile":
        return await profile(update, context)

    if text == "📞 Support":
        return await support_start(update, context)

    if text == "ℹ️ About":
        return await about(update, context)

    await update.message.reply_text(
        "እባክዎ ከምናሌው ይምረጡ።",
        reply_markup=main_menu(),
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(TOKEN).build()

    cargo_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🚚 ጭነት መለጠፍ$"),
                cargo_start,
            )
        ],
        states={
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
        },
        fallbacks=[],
    )

    truck_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🚛 መኪና ማስመዝገብ$"),
                truck_start,
            )
        ],
        states={
            TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_from)],
            TRUCK_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_to)],
            TRUCK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
        },
        fallbacks=[],
    )

    owner_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📦 የጭነት ባለቤት$"),
                owner_start,
            )
        ],
        states={
            OWNER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name)],
            OWNER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, owner_phone)],
        },
        fallbacks=[],
    )

    support_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📞 Support$"),
                support_start,
            )
        ],
        states={
            SUPPORT_MESSAGE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    support_message,
                )
            ],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(cargo_conv)
    app.add_handler(truck_conv)
    app.add_handler(owner_conv)
    app.add_handler(support_conv)

    app.add_handler(
        MessageHandler(
            filters.Regex("^(🔎 ጭነት መፈለግ|👤 My Profile|ℹ️ About)$"),
            menu_handler,
        )
    )

    if RENDER_URL:
        webhook_url = RENDER_URL.rstrip("/") + "/telegram"

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="telegram",
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
