import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 የመኪና ባለቤት", "📦 የጭነት ባለቤት"],
        ["📞 Support"],
    ]

    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

    await update.message.reply_text(
        "👋 እንኳን ወደ Tana Cargo በደህና መጡ!\n\n"
        "🚚 የጭነት ባለቤቶችንና ጫኝ መኪኖችን እናገናኛለን።\n\n"
        "ከታች ያለውን ምናሌ ይጠቀሙ።",
        reply_markup=reply_markup
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🚚 ጭነት መለጠፍ":
        await update.message.reply_text(
            "📦 ጭነትዎን ለመለጠፍ የሚፈለገውን መረጃ በቅርቡ እንሞላለን።"
        )

    elif text == "🔎 ጭነት መፈለግ":
        await update.message.reply_text(
            "🔎 የሚፈልጉትን ጭነት በቅርቡ መፈለግ ይችላሉ።"
        )

    elif text == "🚛 የመኪና ባለቤት":
        await update.message.reply_text(
            "🚛 የመኪና ባለቤት ለመመዝገብ የሚፈለገውን መረጃ በቅርቡ እንሞላለን።"
        )

    elif text == "📦 የጭነት ባለቤት":
        await update.message.reply_text(
            "📦 የጭነት ባለቤት ለመመዝገብ የሚፈለገውን መረጃ በቅርቡ እንሞላለን።"
        )

    elif text == "📞 Support":
        await update.message.reply_text(
            "📞 Tana Cargo Support\n\n"
            "ስለ deposit, withdrawal ወይም ሌላ ችግር ካለዎት እባክዎ ያግኙን።"
        )

    else:
        await update.message.reply_text(
            "እባክዎ ከምናሌው ውስጥ አንዱን ይምረጡ።"
        )


def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN is not set")

    if not RENDER_URL:
        raise ValueError("RENDER_EXTERNAL_URL is not set")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    webhook_url = f"{RENDER_URL}/telegram"

    print("Tana Cargo Bot is starting with webhook...")
    print(f"Webhook URL: {webhook_url}")
    print(f"Listening on port: {PORT}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
