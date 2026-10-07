import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

# ==========================================
# ክፍል 1፦ ማዋቀሪያዎች እና የዳታቤዝ ሲምሌሽን
# ==========================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # እዚህ የቦትህን ቶከን አስገባ
ADMIN_IDS = [123456789, 987654321]  # የAdmin እና Super Admin የቴሌግራም IDዎች

# የዳታቤዝ ሲምሌሽን (በእውንተኛ ስራ ላይ SQLite ተጠቀም)
users_db = {}  # የተጠቃሚዎች መረጃ
posts_db = {}  # የጭነት እና የመኪና ልጥፎች መረጃ

# የሁኔታ ቁጥሮች (States)
NAME, PHONE, LOCATION = range(3)
CARGO_TYPE, CARGO_ORIGIN, CARGO_DEST, CARGO_DETAILS = range(3, 7)
TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_LOCATION = range(7, 11)

# ==========================================
# ክፍል 1፦ የተጠቃሚ ምዝገባ እና የAdmin ማረጋገጫ
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in users_db and users_db[user_id].get("status") == "Approved":
        await update.message.reply_text("እንኳን ደህና መጡ! የጣና ካርጎ አባል ነዎት።")
        await show_main_menu(update, context)
    else:
        keyboard = [
            [KeyboardButton("👤 እንደ ደንበኛ መመዝገብ (Customer)")],
            [KeyboardButton("🤝 እንደ ደላላ መመዝገብ (Broker)")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "እንኳን ወደ ጣና ካርጎ በደህና መጡ! እባክዎ የሚመዘገቡበትን ዓይነት ይምረጡ፦",
            reply_markup=reply_markup
        )

async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data["user_type"] = "Customer" if "ደንበኛ" in text else "Broker"
    await update.message.reply_text("1️⃣ እባክዎ ሙሉ ስምዎን ይጻፉ። (ምሳሌ፦ ረታ አበበ)")
    return NAME

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["full_name"] = update.message.text
    await update.message.reply_text("2️⃣ እባክዎ ስልክ ቁጥርዎን ይጻፉ። (በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው)")
    return PHONE

async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text
    if not (phone.startswith("09") or phone.startswith("07")) or len(phone) != 10 or not phone.isdigit():
        await update.message.reply_text("⚠️ ስህተት! እባክዎ ትክክለኛ ስልክ ቁጥር ያስገቡ (ምሳሌ፦ 0918113344)።")
        return PHONE
    context.user_data["phone"] = phone
    await update.message.reply_text("3️⃣ እባክዎ የሚሰሩበትን ቦታ/አድራሻ ይጻፉ። (ምሳሌ፦ አዲስ አበባ / ጎንደር)")
    return LOCATION

async def register_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["location"] = update.message.text
    user_id = update.effective_user.id
    users_db[user_id] = {
        "full_name": context.user_data["full_name"],
        "phone": context.user_data["phone"],
        "location": context.user_data["location"],
        "user_type": context.user_data["user_type"],
        "status": "Pending"
    }
    await update.message.reply_text(
        "✅ ምዝገባዎ ተጠናቋል!\n"
        "📌 Super Admin እና Admin የጭነት ደላላ/ደንበኛ መሆንዎን ካረጋገጡ በኋላ ይቀበሉዎታል።\n"
        "🙏 ስለተመዘገቡ እናመሰግናለን!"
    )
    admin_message = (
        f"🔔 **አዲስ የምዝገባ ጥያቄ!**\n\n"
        f"👤 **ዓይነት፦** {context.user_data['user_type']}\n"
        f"📝 **ሙሉ ስም፦** {context.user_data['full_name']}\n"
        f"📞 **ስልክ፦** {context.user_data['phone']}\n"
        f"📍 **አድራሻ፦** {context.user_data['location']}\n"
        f"🆔 **ID፦** `{user_id}`"
    )
    keyboard = [[
        InlineKeyboardButton("✅ አጽድቅ (Approve)", callback_data=f"approve_{user_id}"),
        InlineKeyboardButton("❌ ውድቅ አድርግ (Reject)", callback_data=f"reject_{user_id}")
    ]]
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Admin notify error: {e}")
    return ConversationHandler.END

async def admin_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, user_id_str = query.data.split("_")
    user_id = int(user_id_str)
    
    if user_id not in users_db:
        await query.edit_message_text("⚠️ ተጠቃሚው አልተገኘም።")
        return

    if action == "approve":
        users_db[user_id]["status"] = "Approved"
        await query.edit_message_text(f"✅ ተጠቃሚው (ID: {user_id}) ጸድቋል።")
        try:
            await context.bot.send_message(chat_id=user_id, text="🎉 ምዝገባዎ ጸድቋል! አሁን የጣና ካርጎን ሙሉ አገልግሎት መጠቀም ይችላሉ። /start ይበሉ።")
        except Exception as e:
            logging.error(f"User notify error: {e}")
    elif action == "reject":
        users_db[user_id]["status"] = "Rejected"
        await query.edit_message_text(f"❌ ተጠቃሚው (ID: {user_id}) ውድቅ ተደርጓል።")
        try:
            await context.bot.send_message(chat_id=user_id, text="⚠️ ይቅርታ፣ ምዝገባዎ ውድቅ ተደርጓል።")
        except Exception as e:
            logging.error(f"User notify error: {e}")

# ==========================================
# ክፍል 2፦ ጭነት/መኪና መለጠፍ እና መፈለግ
# ==========================================

async def post_cargo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🚚 እባክዎ የጭነቱን አይነት ይጻፉ። (ምሳሌ፦ ስንዴ፣ ሲሚንቶ)")
    return CARGO_TYPE

async def post_cargo_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cargo_type"] = update.message.text
    await update.message.reply_text("📍 ጭነቱ የሚነሳበትን ቦታ (መነሻ) ይጻፉ።")
    return CARGO_ORIGIN

async def post_cargo_origin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cargo_origin"] = update.message.text
    await update.message.reply_text("🎯 ጭነቱ የሚደርስበትን ቦታ (መድረሻ) ይጻፉ።")
    return CARGO_DEST

async def post_cargo_dest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cargo_dest"] = update.message.text
    await update.message.reply_text("📝 ተጨማሪ መረጃ ካለ ይጻፉ (ለምሳሌ፦ ክብደት)። ከሌለ 'የለም' ይበሉ።")
    return CARGO_DETAILS

async def post_cargo_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cargo_details"] = update.message.text
    user_id = update.effective_user.id
    post_id = f"CRG_{user_id}_{len(posts_db)+1}"
    posts_db[post_id] = {
        "user_id": user_id, "type": "cargo", "cargo_type": context.user_data["cargo_type"],
        "origin": context.user_data["cargo_origin"], "destination": context.user_data["cargo_dest"],
        "details": context.user_data["cargo_details"], "status": "available", "taken_by": None
    }
    await update.message.reply_text("✅ ጭነትዎ በተሳካ ሁኔታ ተለጥፏል! ደንበኞች ሲመርጡት ማሳወቂያ ይደርስዎታል።")
    await show_main_menu(update, context)
    return ConversationHandler.END

async def post_truck_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🚛 እባክዎ የመኪናውን አይነት ይጻፉ። (ምሳሌ፦ ኢሶዙ፣ ሲኖትራክ)")
    return TRUCK_TYPE

async def post_truck_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["truck_type"] = update.message.text
    await update.message.reply_text("🔢 የመኪናውን ታርጋ ቁጥር ይጻፉ። (ምሳሌ፦ 3-12345 AA)")
    return TRUCK_PLATE

async def post_truck_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["truck_plate"] = update.message.text
    await update.message.reply_text("⚖️ የመኪናውን የመጫን አቅም (ካፓሲቲ) ይጻፉ። (ምሳሌ፦ 20 ቶን)")
    return TRUCK_CAPACITY

async def post_truck_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["truck_capacity"] = update.message.text
    await update.message.reply_text("📍 መኪናው የሚገኝበትን ቦታ ይጻፉ።")
    return TRUCK_LOCATION

async def post_truck_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["truck_location"] = update.message.text
    user_id = update.effective_user.id
    post_id = f"TRK_{user_id}_{len(posts_db)+1}"
    posts_db[post_id] = {
        "user_id": user_id, "type": "truck", "truck_type": context.user_data["truck_type"],
        "plate": context.user_data["truck_plate"], "capacity": context.user_data["truck_capacity"],
        "location": context.user_data["truck_location"], "status": "available", "taken_by": None
    }
    await update.message.reply_text("✅ መኪናዎ በተሳካ ሁኔታ ተመዝግቧል! ደንበኞች ሲመርጡት ማሳወቂያ ይደርስዎታል።")
    await show_main_menu(update, context)
    return ConversationHandler.END

async def search_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    available = {k: v for k, v in posts_db.items() if v["type"] == "cargo" and v["status"] == "available"}
    if not available:
        await update.message.reply_text("⚠️ በአሁኑ ጊዜ ምንም ነፃ የጭነት ልጥፍ የለም።")
        return
    for post_id, cargo in available.items():
        text = (
            f"🚚 **የጭነት ዝርዝር**\n📦 **አይነት፦** {cargo['cargo_type']}\n"
            f"📍 **መነሻ፦** {cargo['origin']}\n🎯 **መድረሻ፦** {cargo['destination']}\n"
            f"📝 **ተጨማሪ፦** {cargo['details']}\n"
        )
        keyboard = InlineKeyboardButton("✅ መምረጥ (Select)", callback_data=f"select_post_{post_id}")
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def search_truck(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    available = {k: v for k, v in posts_db.items() if v["type"] == "truck" and v["status"] == "available"}
    if not available:
        await update.message.reply_text("⚠️ በአሁኑ ጊዜ ምንም ነፃ የመኪና ልጥፍ የለም።")
        return
    for post_id, truck in available.items():
        text = (
            f"🚛 **የመኪና ዝርዝር**\n🚚 **አይነት፦** {truck['truck_type']}\n"
            f"🔢 **ታርጋ፦** {truck['plate']}\n⚖️ **አቅም፦** {truck['capacity']}\n"
            f"📍 **ያለበት ቦታ፦** {truck['location']}\n"
        )
        keyboard = InlineKeyboardButton("✅ መምረጥ (Select)", callback_data=f"select_post_{post_id}")
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ==========================================
# ክፍል 3፦ ልጥፍ መምረጥ እና ለAdmin ማሳወቂያ መላክ
# ==========================================

async def select_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    post_id = query.data.split("_")[-1]
    user_id = update.effective_user.id

    if post_id not in posts_db:
        await query.edit_message_text("⚠️ ይህ ልጥፍ አልተገኘም።")
        return

    post = posts_db[post_id]
    if post["status"] != "available":
        await query.edit_message_text("❌ ይህ ልጥፍ ቀድሞ በሌላ ደንበኛ ተይዟል።")
        return

    # ልጥፉን "በመጠባበቅ ላይ" ማድረግ
    post["status"] = "pending"
    post["taken_by"] = user_id

    # የፈላጊውን መረጃ ማዘጋጀት
    user_info = users_db.get(user_id, {})
    customer_name = user_info.get("full_name", "ያልታወቀ")
    customer_phone = user_info.get("phone", "ያልታወቀ")
    user_type = user_info.get("user_type", "Customer")

    # ለፈላጊው ማረጋገጫ መላክ
    await query.edit_message_text(
        "✅ ልጥፉን መርጠዋል! መረጃዎ ለAdmin ተልኳል። በቅርቡ ይደውሉልዎታል።\n"
        "🙏 ስለተጠቀሙ እናመሰግናለን!"
    )

    # ለAdmin የሚላክ ማሳወቂያ
    post_details = ""
    if post["type"] == "cargo":
        post_details = f"📦 የጭነት አይነት: {post['cargo_type']}\n📍 መነሻ: {post['origin']}\n🎯 መድረሻ: {post['destination']}"
    else:
        post_details = f"🚚 የመኪና አይነት: {post['truck_type']}\n🔢 ታርጋ: {post['plate']}\n📍 ያለበት ቦታ: {post['location']}"

    admin_msg = (
        f"🔔 **አዲስ የግንኙነት ጥያቄ!**\n\n"
        f"👤 **የፈላጊው ዓይነት፦** {user_type}\n"
        f"📝 **ሙሉ ስም፦** {customer_name}\n"
        f"📞 **ስልክ፦** {customer_phone}\n"
        f"🆔 **የፈላጊው ID፦** `{user_id}`\n\n"
        f"--- **የተመረጠው ልጥፍ** ---\n"
        f"{post_details}\n"
        f"🆔 **የልጥፍ ID፦** `{post_id}`"
    )

    keyboard = [[
        InlineKeyboardButton("✅ ስምምነት ተጠናቋል", callback_data=f"complete_deal_{post_id}"),
        InlineKeyboardButton("❌ ስምምነት አልተፈጸመም", callback_data=f"cancel_deal_{post_id}")
    ]]

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Admin notify error: {e}")

# ==========================================
# ክፍል 4፦ የAdmin ማረጋገጫ እና ስምምነትን ማጠናቀቅ
# ==========================================

async def admin_deal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    # data ምሳሌ: complete_deal_CRG_12345_1
    parts = query.data.split("_")
    action = parts[0]  # complete ወይም cancel
    post_id = "_".join(parts[2:])  # CRG_12345_1
    
    if post_id not in posts_db:
        await query.edit_message_text("⚠️ ይህ ልጥፍ በዳታቤዝ ውስጥ አልተገኘም።")
        return

    post = posts_db[post_id]
    
    if action == "complete":
        # ስምምነቱ ተጠናቋል
        post["status"] = "completed"
        await query.edit_message_text(f"✅ ስምምነቱ በተሳካ ሁኔታ ተጠናቋል! (ልጥፍ ID: {post_id})")
        
        # ለደንበኛው ማሳወቂያ መላክ
        if post["taken_by"]:
            try:
                await context.bot.send_message(
                    chat_id=post["taken_by"],
                    text=f"🎉 ስምምነትዎ በተሳካ ሁኔታ ተጠናቋል! ስለተጠቀሙ እናመሰግናለን።\n(የልጥፍ መለያ: {post_id})"
                )
            except Exception as e:
                logging.error(f"User notify error: {e}")
                
    elif action == "cancel":
        # ስምምነቱ አልተፈጸመም - ልጥፉን ወደ ነፃ (available) መመለስ
        post["status"] = "available"
        post["taken_by"] = None
        await query.edit_message_text(f"❌ ስምምነቱ አልተፈጸመም። ልጥፉ እንደገና ለሌሎች ተጠቃሚዎች ክፍት ሆኗል። (ልጥፍ ID: {post_id})")
        
        # ለደንበኛው ማሳወቂያ መላክ
        if post["taken_by"]:
            try:
                await context.bot.send_message(
                    chat_id=post["taken_by"],
                    text=f"⚠️ ይቅርታ፣ ስምምነቱ አልተፈጸመም። ሌላ ልጥፍ መምረጥ ይችላሉ።\n(የልጥፍ መለያ: {post_id})"
                )
            except Exception as e:
                logging.error(f"User notify error: {e}")

# ==========================================
# ዋና ሜኑ እና ማስጀመሪያ (Main Menu & App)
# ==========================================

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [KeyboardButton("🚚 ጭነት መለጠፍ"), KeyboardButton("🚛 መኪና ማስመዝገብ")],
        [KeyboardButton("🔎 ጭነት መፈለግ"), KeyboardButton("🚛 መኪና መፈለግ")],
        [KeyboardButton("👤 የኔ መረጃ"), KeyboardButton("📞 Support")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    if update.message:
        await update.message.reply_text("ዋና ሜኑ፦ እባክዎ የሚፈልጉትን አገልግሎት ይምረጡ።", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("ዋና ሜኑ፦ እባክዎ የሚፈልጉትን አገልግሎት ይምረጡ።", reply_markup=reply_markup)

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል። ወደ ዋናው ሜኑ ተመልሰዋል።")
    await show_main_menu(update, context)
    return ConversationHandler.END

def main() -> None:
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation Handlers
    reg_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(👤 እንደ ደንበኛ መመዝገብ \\(Customer\\)|🤝 እንደ ደላላ መመዝገብ \\(Broker\\))$"), register_start)],
        states={NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)], PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_phone)], LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_location)]},
        fallbacks=[CommandHandler("start", start)]
    )
    cargo_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🚚 ጭነት መለጠፍ$"), post_cargo_start)],
        states={CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cargo_type)], CARGO_ORIGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cargo_origin)], CARGO_DEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cargo_dest)], CARGO_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cargo_details)]},
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel_conversation)]
    )
    truck_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🚛 መኪና ማስመዝገብ$"), post_truck_start)],
        states={TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_truck_type)], TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_truck_plate)], TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_truck_capacity)], TRUCK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_truck_location)]},
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel_conversation)]
    )

    # Add Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(reg_conv)
    application.add_handler(cargo_conv)
    application.add_handler(truck_conv)
    application.add_handler(MessageHandler(filters.Regex("^🔎 ጭነት መፈለግ$"), search_cargo))
    application.add_handler(MessageHandler(filters.Regex("^🚛 መኪና መፈለግ$"), search_truck))
    
    # Callback Query Handlers (በቅደም ተከተል መሆን አለባቸው)
    application.add_handler(CallbackQueryHandler(admin_approval_callback, pattern="^(approve|reject)_"))
    application.add_handler(CallbackQueryHandler(select_post_callback, pattern="^select_post_"))
    application.add_handler(CallbackQueryHandler(admin_deal_callback, pattern="^(complete|cancel)_deal_"))

    print("ቦቱ እየሰራ ነው...")
    application.run_polling()

if __name__ == "__main__":
    main()
