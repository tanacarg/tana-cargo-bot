# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    clear_stale_user_state(context)
    context.user_data["cargo"] = {}
    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር"
    )
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።\nምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ")
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
        "በቶን፣ በቢያጆ፣ በኩንታል\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
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
        "6.5️⃣ ለዚህ ጭነት **ስንት መኪና** ይፈልጋሉ? (ቁጥር ብቻ ይጻፉ)\n"
        "ምሳሌ፦ 1 ወይም 2 ወይም 3"
    )
    return CARGO_TRUCKS_NEEDED

async def cargo_trucks_needed(update, context):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ እባክዎ ከ1 በላይ የሆነ ትክክለኛ ቁጥር ይጻፉ።")
        return CARGO_TRUCKS_NEEDED
    
    context.user_data["cargo"]["trucks_needed"] = int(text)
    
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
    else:
        return CARGO_CALENDAR
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    calendar = context.user_data.get("cargo", {}).get("calendar", "ET")
    date_parts, error = parse_flexible_date(date_text)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ቀኑን በቀን/ወር/ዓመት ቅርጽ እንደገና ያስገቡ።")
        return CARGO_DATE
    et_date, gc_date, error = convert_calendar(date_parts, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ትክክለኛውን ቀን ያስገቡ።")
        return CARGO_DATE
    try:
        gc_obj = datetime.strptime(gc_date, "%d/%m/%Y")
        today = datetime.now(ZoneInfo("Africa/Addis_Ababa")).date()
        given_date = gc_obj.date()
    except (ValueError, TypeError):
        await update.message.reply_text("❌ የቀን ስሌት ስህተት። እባክዎ ቀኑን እንደገና ያስገቡ።")
        return CARGO_DATE
    if given_date < today:
        await update.message.reply_text(
            "❌ ያለፈ ቀን አይቀበልም።\n\n"
            "ዛሬን ወይም ወደፊት ያለ ቀን በኢትዮጵያ ካላንደር (ቀን/ወር/ዓ.ም.) ያስገቡ።\n"
            "ምሳሌ፦ 11/02/2019"
        )
        return CARGO_DATE
    max_date = today + timedelta(days=30)
    if given_date > max_date:
        await update.message.reply_text(
            "❌ ከ30 ቀናት በላይ የሆነ ቀን አይቀበልም።\n\n"
            "እባክዎ ዛሬን ወይም በሚቀጥሉት 30 ቀናት ውስጥ ያለ ቀን ያስገቡ።"
        )
        return CARGO_DATE
    cargo = context.user_data.setdefault("cargo", {})
    cargo["date"] = date_text
    cargo["date_et"] = et_date
    cargo["date_gc"] = gc_date
    cargo["expiry_date"] = (gc_obj + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    await update.message.reply_text(
        "📅 የተመረጠው ቀን፦\n"
        f"🇪🇹 ET: {et_date}\n"
        f"🌍 GC: {gc_date}\n\n"
        "8️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = result
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n"
        "በብር ብቻ ወይም **በስምምነት / በድርድር** ብለው ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት",
        reply_markup=ReplyKeyboardRemove()
    )
    return CARGO_PRICE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
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
    cargo["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"

    if cargo["poster_type"] == "broker":
        if is_duplicate_broker_post(cargo, is_truck=False,
                                     broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን ጭነት ሌላ ደላላ (broker) ለጥፎታል።**\n\n"
                "እባክዎ ሌላ ጭነት ካለዎት ይለጥፉ።",
                reply_markup=main_menu(update.effective_user.id)
            )
            return ConversationHandler.END

    cargo_posts.append(cargo.copy())
    try:
        db_save_cargo(cargo)
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    poster_label = get_poster_label(update.effective_user.id)
    trucks_needed = cargo.get("trucks_needed", 1)

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"🚚 **የሚፈለጉ መኪናዎች፦ {trucks_needed}**\n"
        f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
        f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
        f"💰 የመጫኛ ዋጋ፦ {price_display}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉@tanapage join ያድርጉ።",
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
                        f"🚚 የሚፈለጉ መኪናዎች፦ {trucks_needed}\n"
                        f"📅 {cargo.get('date_et', 'N/A')} (ET)\n"
                        f"💰 {price_display}\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END

# ==================================================
# SUPER ADMIN BROADCAST
# ==================================================

async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END
        
    await update.message.reply_text(
        "📢 **ለደንበኞች መረጃ መላኪያ**\n\n"
        "እባክዎ ለደንበኞች ለማጋራት የፈለጉትን መልእክት ይፃፉ።\n\n"
        "📌 እኔ ፅፌ እልካለሁ ማለት ነው።",
        reply_markup=ReplyKeyboardRemove()
    )
    return ADMIN_BROADCAST_TEXT

async def broadcast_message(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text
    await update.message.reply_text("⏳ በመላክ ላይ ነው... እባክዎ ይጠብቁ።")
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        users_list = conn.execute("SELECT user_id FROM users").fetchall()
    finally:
        conn.close()
        
    success = 0
    failed = 0
    for u in users_list:
        uid = u[0]
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📢 **TANA CARGO | ጣና ጭነት**\n\n{text}"
            )
            success += 1
            await asyncio.sleep(0.05) 
        except Exception:
            failed += 1
            
    await update.message.reply_text(
        f"✅ **መልእክቱ ተልኳል!**\n\n"
        f"✔️ የደረሰው፦ {success}\n"
        f"❌ ያልደረሰው (ቦቱን የታገዱ ወይም የሌሉ)፦ {failed}",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END
# ==================================================
# MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
brokers = []
connection_requests = []
pending_deletes = []
gmaps_share_requests = []
tracking_stops = []
pending_gmaps_shares = []

# ==================================================
# STATES
# ==================================================

(
    CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE,
    CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE,
    CARGO_TRUCKS_NEEDED
) = range(11)

(
    TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE,
    TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE,
) = range(11, 18)

OWNER_NAME, OWNER_PHONE = range(18, 20)
(
    CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE,
) = range(20, 23)

(
    BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS,
) = range(23, 26)

(
    TRACK_REQ_PHONE, TRACK_REQ_ROUTE, TRACK_REQ_LOCATION,
) = range(26, 29)

(
    TRACK_STOP_NAME, TRACK_STOP_PHONE
) = range(29, 31)

START_PHONE = 31
VEHICLE_TRACK_PHONE = 32
ADMIN_BROADCAST_TEXT = 33

# ==================================================
# MENUS
# ==================================================

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        if is_super_admin(user_id):
            keyboard = [
                ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
                ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
                ["👤 የኔ መረጃ"],
                ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"],
                ["📊 የክትትል መዝገብ (Admin)"],
                ["🤝 የግንኙነት ጥያቄዎች"],
                ["📋 የጭነት ደላሎች መዝገብ"],
                ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
                ["📢 ለደንበኞች መረጃ መላኪያ"],
                ["📞 Support"],
            ]
        else:
            keyboard = [
                ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
                ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
                ["👤 የኔ መረጃ"],
                ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"],
                ["📊 የክትትል መዝገብ (Admin)"],
                ["🤝 የግንኙነት ጥያቄዎች"],
                ["📋 የጭነት ደላሎች መዝገብ"],
                ["💳 የአገልግሎት ክፍያ ለመፈፀም"],
                ["📞 Support", "ℹ️ About"],
            ]
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["👤 የኔ መረጃ"],
            ["📍 የጭነት ክትትል", "🚚 የመኪና ክትትል"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
            ["🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ"],
            ["💳 የአገልግሎት ክፍያ ለመፈጸም"],
            ["📞 Support", "ℹ️ About"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def phone_request_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

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
        [InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (EC)", callback_data="cal_et")],
    ])

def route_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 በማንኛውም ቦታ", callback_data="route_anywhere")],
        [InlineKeyboardButton("📍 በርሶ ምርጫ", callback_data="route_custom")],
    ])

def tracking_action_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ክትትል ጀምር", callback_data="track_start")],
        [InlineKeyboardButton("🔴 ክትትል አቁም", callback_data="track_stop")],
    ])

# ==================================================
# HELPERS
# ==================================================

def is_user_registered(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]:
            return True
    finally:
        conn.close()
    return False

def normalize(text):
    if text is None:
        return ""
    return str(text).strip().lower().replace(" ", "")

def vehicle_match(truck_type, requested_vehicle):
    if not requested_vehicle:
        return True
    return normalize(truck_type) == normalize(requested_vehicle)

def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"):
            return True
    return False

def get_poster_label(user_id):
    if is_broker(user_id):
        return "🏷️ Broker"
    return "🏷️ Customer"

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

def validate_phone(phone):
    phone = phone.strip()
    if phone.startswith("+251"):
        phone = "0" + phone[4:]
    elif phone.startswith("251"):
        phone = "0" + phone[3:]
        
    if not (phone.startswith("09") or phone.startswith("07")):
        return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10:
        return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    if not phone.isdigit():
        return False, "ስልክ ቁጥር ቁጥር ብቻ መሆን አለበት።"
    return True, phone

def validate_plate(plate):
    plate = plate.strip()
    if not plate.isdigit():
        return False, "ታርጋ ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_weight(weight):
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]):
        return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

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

async def auto_delete_messages(context):
    now = datetime.now()
    remaining = []
    for chat_id, msg_id, delete_at in pending_deletes:
        if now >= delete_at:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        else:
            remaining.append((chat_id, msg_id, delete_at))
    pending_deletes.clear()
    pending_deletes.extend(remaining)

def is_duplicate_broker_post(new_item, is_truck, broker_user_id):
    if is_truck:
        for t in truck_posts:
            if (t.get("active") and t.get("poster_type") == "broker"
                and t.get("plate") == new_item.get("plate")
                and t.get("user_id") != broker_user_id):
                return True
    else:
        for c in cargo_posts:
            if (c.get("active") and c.get("poster_type") == "broker"
                and normalize(c.get("from")) == normalize(new_item.get("from"))
                and normalize(c.get("to")) == normalize(new_item.get("to"))
                and c.get("user_id") != broker_user_id):
                return True
    return False

def is_already_selected(item_type, item_index):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("status") in ["pending", "admin_confirming"]):
                return True
    return False

def is_selected_by_other(item_type, item_index, requester_id):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("requester_id") != requester_id
                and req.get("status") in ["pending", "admin_confirming"]):
                return True
    return False

async def subscribed_check_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    context.user_data["subscribed"] = True
    await query.answer("✅ አመሰግናለሁ! Subscribe ማድረግዎን አረጋግጠናል።", show_alert=True)
    try:
        await query.edit_message_text("✅ **አመሰግናለሁ!**\n\nSubscribe ማድረግዎን አረጋግጠናል።\n\n👉 አሁን የፈለጉትን ምናሌ በመንካት ይቀጥሉ!")
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=user_id,
        text="👇 አሁን የፈለጉትን ምናሌ ይንኩ፦",
        reply_markup=main_menu(user_id)
    )

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner",
                "connect_info", "sending_admin_message",
                "tracking_update_code", "tracking_lookup_waiting",
                "track_new", "cust_track_phone_only",
                "gmaps_share", "tracking_stop", "vehicle_track"):
        if key not in keep:
            context.user_data.pop(key, None)
# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    clear_stale_user_state(context)
    context.user_data["cargo"] = {}
    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር"
    )
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።\nምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ")
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
        "በቶን፣ በቢያጆ፣ በኩንታል\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
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
        "6.5️⃣ ለዚህ ጭነት **ስንት መኪና** ይፈልጋሉ? (ቁጥር ብቻ ይጻፉ)\n"
        "ምሳሌ፦ 1 ወይም 2 ወይም 3"
    )
    return CARGO_TRUCKS_NEEDED

async def cargo_trucks_needed(update, context):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("❌ እባክዎ ከ1 በላይ የሆነ ትክክለኛ ቁጥር ይጻፉ።")
        return CARGO_TRUCKS_NEEDED
    
    context.user_data["cargo"]["trucks_needed"] = int(text)
    
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
    else:
        return CARGO_CALENDAR
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    calendar = context.user_data.get("cargo", {}).get("calendar", "ET")
    date_parts, error = parse_flexible_date(date_text)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ቀኑን በቀን/ወር/ዓመት ቅርጽ እንደገና ያስገቡ።")
        return CARGO_DATE
    et_date, gc_date, error = convert_calendar(date_parts, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ትክክለኛውን ቀን ያስገቡ።")
        return CARGO_DATE
    try:
        gc_obj = datetime.strptime(gc_date, "%d/%m/%Y")
        today = datetime.now(ZoneInfo("Africa/Addis_Ababa")).date()
        given_date = gc_obj.date()
    except (ValueError, TypeError):
        await update.message.reply_text("❌ የቀን ስሌት ስህተት። እባክዎ ቀኑን እንደገና ያስገቡ።")
        return CARGO_DATE
    if given_date < today:
        await update.message.reply_text(
            "❌ ያለፈ ቀን አይቀበልም።\n\n"
            "ዛሬን ወይም ወደፊት ያለ ቀን በኢትዮጵያ ካላንደር (ቀን/ወር/ዓ.ም.) ያስገቡ።\n"
            "ምሳሌ፦ 11/02/2019"
        )
        return CARGO_DATE
    max_date = today + timedelta(days=30)
    if given_date > max_date:
        await update.message.reply_text(
            "❌ ከ30 ቀናት በላይ የሆነ ቀን አይቀበልም።\n\n"
            "እባክዎ ዛሬን ወይም በሚቀጥሉት 30 ቀናት ውስጥ ያለ ቀን ያስገቡ።"
        )
        return CARGO_DATE
    cargo = context.user_data.setdefault("cargo", {})
    cargo["date"] = date_text
    cargo["date_et"] = et_date
    cargo["date_gc"] = gc_date
    cargo["expiry_date"] = (gc_obj + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    await update.message.reply_text(
        "📅 የተመረጠው ቀን፦\n"
        f"🇪🇹 ET: {et_date}\n"
        f"🌍 GC: {gc_date}\n\n"
        "8️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return CARGO_PHONE

async def cargo_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return CARGO_PHONE
    phone = result
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n"
        "በብር ብቻ ወይም **በስምምነት / በድርድር** ብለው ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት",
        reply_markup=ReplyKeyboardRemove()
    )
    return CARGO_PRICE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
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
    cargo["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"

    if cargo["poster_type"] == "broker":
        if is_duplicate_broker_post(cargo, is_truck=False,
                                     broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን ጭነት ሌላ ደላላ (broker) ለጥፎታል።**\n\n"
                "እባክዎ ሌላ ጭነት ካለዎት ይለጥፉ።",
                reply_markup=main_menu(update.effective_user.id)
            )
            return ConversationHandler.END

    cargo_posts.append(cargo.copy())
    try:
        db_save_cargo(cargo)
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    poster_label = get_poster_label(update.effective_user.id)
    trucks_needed = cargo.get("trucks_needed", 1)

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"🚚 **የሚፈለጉ መኪናዎች፦ {trucks_needed}**\n"
        f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
        f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
        f"💰 የመጫኛ ዋጋ፦ {price_display}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉@tanapage join ያድርጉ።",
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
                        f"🚚 የሚፈለጉ መኪናዎች፦ {trucks_needed}\n"
                        f"📅 {cargo.get('date_et', 'N/A')} (ET)\n"
                        f"💰 {price_display}\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END

# ==================================================
# SUPER ADMIN BROADCAST
# ==================================================

async def broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ!")
        return ConversationHandler.END
        
    await update.message.reply_text(
        "📢 **ለደንበኞች መረጃ መላኪያ**\n\n"
        "እባክዎ ለደንበኞች ለማጋራት የፈለጉትን መልእክት ይፃፉ።\n\n"
        "📌 እኔ ፅፌ እልካለሁ ማለት ነው።",
        reply_markup=ReplyKeyboardRemove()
    )
    return ADMIN_BROADCAST_TEXT

async def broadcast_message(update, context):
    if not is_super_admin(update.effective_user.id):
        return ConversationHandler.END
        
    text = update.message.text
    await update.message.reply_text("⏳ በመላክ ላይ ነው... እባክዎ ይጠብቁ።")
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        users_list = conn.execute("SELECT user_id FROM users").fetchall()
    finally:
        conn.close()
        
    success = 0
    failed = 0
    for u in users_list:
        uid = u[0]
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📢 **TANA CARGO | ጣና ጭነት**\n\n{text}"
            )
            success += 1
            await asyncio.sleep(0.05) 
        except Exception:
            failed += 1
            
    await update.message.reply_text(
        f"✅ **መልእክቱ ተልኳል!**\n\n"
        f"✔️ የደረሰው፦ {success}\n"
        f"❌ ያልደረሰው (ቦቱን የታገዱ ወይም የሌሉ)፦ {failed}",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END
# ==================================================
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    clear_stale_user_state(context)
    context.user_data["truck"] = {}
    await update.message.reply_text(
        "🚛 የጭነት መኪና ማስመዝገብ\n\n1️⃣ የመኪናውን አይነት ይጻፉ።\nምሳሌ፦ Isuzu / ካሶኒ / ኦባማ"
    )
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።\nቁጥር ብቻ መሆን አለበት።\nምሳሌ፦ 12345")
    return TRUCK_PLATE

async def truck_plate(update, context):
    plate = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_plate(plate)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 12345")
        return TRUCK_PLATE
    context.user_data["truck"]["plate"] = plate
    await update.message.reply_text("3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    capacity = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_weight(capacity)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
        return TRUCK_CAPACITY
    context.user_data["truck"]["capacity"] = capacity
    await update.message.reply_text(
        "4️⃣ የመንገድ ዝርዝር ይጻፉ።\n\nእባክዎ የሚጓዙበትን መንገድ ዓይነት ይምረጡ፦",
        reply_markup=route_choice_keyboard()
    )
    return TRUCK_ROUTE

async def truck_route_choice(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "route_anywhere":
        context.user_data["truck"]["route"] = "በማንኛውም ቦታ"
        context.user_data["truck"]["route_type"] = "anywhere"
        await query.edit_message_text("✅ **በማንኛውም ቦታ** ተመርጧል።\n\n5️⃣ የመኪናውን አድራሻ ይጻፉ።")
        return TRUCK_ADDRESS
    elif query.data == "route_custom":
        context.user_data["truck"]["route_type"] = "custom"
        await query.edit_message_text("📍 **በርሶ ምርጫ** ተመርጧል።\n\nለምሳሌ፦ ጅማ → ባህርዳር → አዲስ አበባ")
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
        "7️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return TRUCK_PHONE

async def truck_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return TRUCK_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return TRUCK_PHONE
    phone = result

    truck = context.user_data["truck"]
    truck["phone"] = phone
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck["timestamp"] = datetime.now().isoformat()
    truck["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"
    expiry_date = datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)
    truck["expiry_date"] = expiry_date.isoformat()

    if truck["poster_type"] == "broker":
        if is_duplicate_broker_post(truck, is_truck=True,
                                     broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን መኪና ሌላ ደላላ (broker) አስመዝግቦታል።**\n\n"
                "እባክዎ ሌላ የጭነት መኪና ካለዎት ያስመዝግቡ።",
                reply_markup=main_menu(update.effective_user.id)
            )
            return ConversationHandler.END

    truck_posts.append(truck.copy())
    try:
        db_save_truck(truck)
    except Exception as e:
        print(f"DB SAVE TRUCK ERROR: {e}")

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    route_display = truck.get("route", "በማንኛውም ቦታ")
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"🚛 አይነት፦ {truck['type']}\n"
        f"🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"🛣️ መንገድ፦ {route_display}\n"
        f"📍 አድራሻ፦ {truck['address']}\n"
        f"👨‍✈️ ሹፌር፦ {truck['driver']}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛችን ጭነት የሚያስጭንዎት ደንበኛ ጋር ስምምነት ላይ ደርሰዉ ጭነት ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነቱ ጭነቱን መከታተል እንዲችል የስልክዎን location on አድርገው የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ በመንካት አዲስ ጉዞ ክፈት ሹፌር የምትለዋን ነክተው ያስመዝግቡ። የጉዞ መዳረሻ ላይ ሲደርሱ location ያጥፉ እናመሰግናለን የሚል notfication ይጨመር። ለበለጠ መረጃ👉 @tanapage ነክተው join ያድርጉ።",
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
# BROKER REGISTRATION
# ==================================================

async def broker_start(update, context):
    clear_stale_user_state(context)
    context.user_data["broker"] = {}
    await update.message.reply_text(
        "🆕 **የጭነት ደላላ ምዝገባ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።",
        reply_markup=ReplyKeyboardRemove()
    )
    return BROKER_NAME

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return BROKER_PHONE

async def broker_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return BROKER_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return BROKER_PHONE
    phone = result
    context.user_data["broker"]["phone"] = phone
    await update.message.reply_text(
        "3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር",
        reply_markup=ReplyKeyboardRemove()
    )
    return BROKER_ADDRESS

async def broker_address(update, context):
    address = update.message.text.strip()
    user = update.effective_user
    context.user_data["broker"]["address"] = address
    broker = {
        "user_id": user.id,
        "name": context.user_data["broker"]["name"],
        "username": user.username or "",
        "phone": context.user_data["broker"]["phone"],
        "address": address,
        "timestamp": datetime.now().isoformat(),
    }
    brokers.append(broker)
    try:
        db_save_broker(broker)
    except Exception as e:
        print(f"DB SAVE BROKER ERROR: {e}")
    await update.message.reply_text(
        "✅ **ስለተመዘገቡ እናመሰግናለን!**\n\n"
        "የጭነት ደላላ መሆንዎን አረጋግጠን "
        "አብረን በጋራ እንሰራለን።\n\n"
        "📌 ማረጋገጫ ሲደረግ ይነገርዎታል።",
        reply_markup=main_menu(user.id)
    )
    await send_admin_broker_notification(context, broker)
    return ConversationHandler.END

async def send_admin_broker_notification(context, broker):
    notification_text = (
        "🆕 **TANA CARGO — አዲስ የጭነት ደላላ ተመዝግቧል!**\n\n"
        f"👤 **ስም:** {broker['name']}\n"
        f"🔗 **Username:** @{broker['username'] or 'የለም'}\n"
        f"🆔 **Telegram ID:** {broker['user_id']}\n"
        f"📞 **ስልክ:** {broker['phone']}\n"
        f"📍 **አድራሻ:** {broker['address']}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 **የጭነት ደላላ መሆኑን ያረጋግጡ።**\n"
        "✅ Right = አረጋግጥ\n"
        "❌ X = ውድቅ አድርግ"
    )
    broker_idx = len(brokers) - 1
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Right", callback_data=f"brokerright_{broker_idx}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"brokerxdelete_{broker_idx}"),
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

async def broker_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(brokers):
        return
    broker = brokers[index]
    if broker.get("approved"):
        await query.answer("⚠️ ይህ ቀድሞ ጸድቋል!", show_alert=True)
        return
    broker["approved"] = True
    broker["active"] = True
    await query.edit_message_text("✅ **የጭነት ደላላው ጸድቋል!**\n\nለደላላው ማሳወቂያ ተልኳል።")
    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            approval_text = (
                "🎉 **እንኳን ደስ አለዎት!** 🎉\n\n"
                "**የጣና ጭነት አባል ሆነዋል!**\n\n"
                "📦 **ጭነት ካለዎት** ወይም 🚛 **የጭነት መኪና ካለዎት**\n"
                "✅ **በነፃ ይለጥፉ!**\n\n"
                "🔒 መረጃዎ **100% የተጠበቀ** ነው።\n\n"
                "🙏 እኛን ስለመረጡ እናመሰግናለን!"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=approval_text)
        except Exception:
            pass

async def broker_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(brokers):
        return
    broker = brokers[index]
    broker["active"] = False
    broker["approved"] = False
    await query.edit_message_text(
        "❌ **ውድቅ ተደርጓል!**\n\n"
        "ይህ የጭነት ደላላ በ Super Admin ውድቅ ተደርጓል።"
    )
    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            rejection_text = (
                "❌ **የጭነት ደላላ ምዝገባ ውድቅ ተደርጓል።**\n\n"
                "የጣና ካርጎ አባላቶች ባገኙት መረጃ መሰረት "
                "እርሶ **የጭነት ደላላ አይደሉም**።\n\n"
                "ገፃችንን ስለጎበኙ እናመሰግናለን! 🙏"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=rejection_text)
        except Exception:
            pass

async def show_broker_registry(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return
    if not brokers:
        await update.message.reply_text("📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።")
        return
    for i, broker in enumerate(brokers):
        if not broker.get("approved"):
            status = "⏳ በመጠበቅ"
        elif broker.get("active"):
            status = "✅ ጸድቋል"
        else:
            status = "❌ ውድቅ"
        text = (
            f"📋 **የጭነት ደላላ #{i + 1}**\n\n"
            f"👤 ስም: {broker.get('name', 'N/A')}\n"
            f"🔗 Username: @{broker.get('username') or 'የለም'}\n"
            f"🆔 Telegram ID: {broker.get('user_id', 'N/A')}\n"
            f"📞 ስልክ: {broker.get('phone', 'N/A')}\n"
            f"📍 አድራሻ: {broker.get('address', 'N/A')}\n"
            f"📌 ሁኔታ: {status}\n"
        )
        buttons = []
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{i + 1} አጥፋ (Super)",
                    callback_data=f"admindeletebroker_{i}"
                )
            ])
        if buttons:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await update.message.reply_text(text)

async def admin_delete_broker(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[-1])
    except ValueError:
        return
    if index < 0 or index >= len(brokers):
        return
    brokers[index]["active"] = False
    brokers[index]["approved"] = False
    await query.edit_message_text("✅ **የጭነት ደላላው ተሰርዟል።**")
    # ==================================================
# FIND CARGO / TRUCK
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
        real_index = cargo_posts.index(cargo)
        is_selected = is_already_selected("cargo", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user:
            owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
            owner_username = users.get(cargo["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {cargo['user_id']}\n"
                f"📞 ስልክ: {cargo.get('phone', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
        trucks_needed = cargo.get("trucks_needed", 1)
        trucks_matched = cargo.get("trucks_matched", 0)
        
        msg = (
            f"{status_mark}📦 **ጭነት {cargo_id}**\n"
            f"{poster_label}\n"
            f"📍 መነሻ፦ {cargo['from']}\n"
            f"📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
            f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n"
            f"🚚 **የሚፈለጉ መኪናዎች፦ {trucks_needed}**\n"
            f"✅ **የተመደቡ፦ {trucks_matched}**\n"
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ ጭነት {cargo_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት ጭነት {cargo_id} ከሆነ እዚጋ ያጫኑ 👈",
                    callback_data=f"connect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

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
        real_index = truck_posts.index(truck)
        is_selected = is_already_selected("truck", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user:
            owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
            owner_username = users.get(truck["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {truck['user_id']}\n"
                f"📞 ስልክ: {truck.get('phone', 'N/A')}\n"
                f"🔢 ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"👨‍✈️ ሹፌር: {truck.get('driver', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
        msg = (
            f"{status_mark}🚛 **የጭነት መኪና {truck_id}**\n"
            f"{poster_label}\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"🛣️ መንገድ፦ {route_display}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ መኪና {truck_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት የጭነት መኪና {truck_id} ከሆነ ይጫኑ 👈",
                    callback_data=f"truckconnect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

async def already_selected_callback(update, context):
    query = update.callback_query
    await query.answer(
        "❌ ይቅርታ የመረጡት ጭነት/የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።\n\n"
        "እባክዎ ሌላ ተስማሚ ይምረጡ።",
        show_alert=True
    )

# ==================================================
# CONNECTION REQUEST
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(cargo_posts):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    cargo = cargo_posts[index]
    if not cargo.get("active", True):
        await query.edit_message_text("❌ ይህ ጭነት ተወግዷል።")
        return ConversationHandler.END
    requester = update.effective_user
    if is_selected_by_other("cargo", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት ጭነት በሌላ ደንበኛችን በድርድር ላይ ነው።", show_alert=True)
        return ConversationHandler.END
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "cargo" and req.get("cargo_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "admin_confirming"]):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "cargo",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(truck_posts):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    truck = truck_posts[index]
    if not truck.get("active", True):
        await query.edit_message_text("❌ ይህ መኪና ተወግዷል።")
        return ConversationHandler.END
    requester = update.effective_user
    if is_selected_by_other("truck", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት የጭነት መኪና በሌላ ደንበኛችን በድርድር ላይ ነው።", show_alert=True)
        return ConversationHandler.END
    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "truck" and req.get("truck_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "admin_confirming"]):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "truck",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info
    await update.message.reply_text(
        "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return CONNECT_PHONE

async def connect_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return CONNECT_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return CONNECT_PHONE
    phone = result
    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
    context.user_data["connect_info"] = info
    await update.message.reply_text(
        "3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር",
        reply_markup=ReplyKeyboardRemove()
    )
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip().split("\n")[0].strip()
    info["requester_address"] = text
    context.user_data["connect_info"] = info
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
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_plate": "",
            "status": "admin_confirming",
            "full_info_shared": False, "admin_right_confirmed": False,
            "created_at": datetime.now().isoformat(),
        }
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ አድሚን አጽድቆ መረጃውን ይልክልዎታል።\n\n"
            "🙏 እናመሰግናለን!"
        )
    else:
        truck = truck_posts[info["item_index"]]
        request = {
            "type": "truck",
            "truck_index": info["item_index"],
            "truck_owner_id": truck["user_id"],
            "requester_id": requester.id,
            "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""),
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_route": "",
            "status": "admin_confirming",
            "full_info_shared": False, "admin_right_confirmed": False,
            "created_at": datetime.now().isoformat(),
        }
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ አድሚን አጽድቆ መረጃውን ይልክልዎታል።\n\n"
            "🙏 እናመሰግናለን!"
        )
    connection_requests.append(request)
    req_index = len(connection_requests) - 1
    context.user_data.pop("connect_info", None)
    await update.message.reply_text(msg_text, reply_markup=main_menu(requester.id))
    await send_admin_notification(context, req_index)
    return ConversationHandler.END
async def send_admin_notification(context, req_index):
    req = connection_requests[req_index]
    if req.get("admin_notified"):
        return
    req["admin_notified"] = True
    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
        cargo_owner_username = users.get(cargo["user_id"], {}).get("username", "")
        cargo_owner_phone = cargo.get("phone", "N/A")
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"📦 **የጭነት ዝርዝር:** {poster_label}\n"
            f"   • መነሻ: {cargo['from']}\n"
            f"   • መድረሻ: {cargo['to']}\n"
            f"   • አይነት: {cargo['type']}\n"
            f"   • ክብደት: {cargo['weight']}\n"
            f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
            f"👤 **የጭነት ባለቤት:**\n"
            f"   • ስም: {cargo_owner_name}\n"
            f"   • Username: @{cargo_owner_username or 'የለም'}\n"
            f"   • ስልክ: {cargo_owner_phone}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
        truck_owner_username = users.get(truck["user_id"], {}).get("username", "")
        truck_owner_phone = truck.get("phone", "N/A")
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
        notification_text = (
            "🔔 **TANA CARGO — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"🚛 **የመኪና ዝርዝር:** {poster_label}\n"
            f"   • አይነት: {truck['type']}\n"
            f"   • አቅም: {truck['capacity']}\n"
            f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
            f"   • መንገድ: {truck.get('route', 'N/A')}\n\n"
            f"👤 **የመኪና ባለቤት:**\n"
            f"   • ስም: {truck_owner_name}\n"
            f"   • Username: @{truck_owner_username or 'የለም'}\n"
            f"   • ስልክ: {truck_owner_phone}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A')}\n"
            f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text)
        except Exception:
            pass

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    if req.get("admin_right_confirmed"):
        await query.answer("⚠️ ይህ ቀድሞ ተረጋግጧል!", show_alert=True)
        return
    req["admin_right_confirmed"] = True
    req["status"] = "completed"
    
    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo = cargo_posts[ci]
            cargo["trucks_matched"] = cargo.get("trucks_matched", 0) + 1
            needed = cargo.get("trucks_needed", 1)
            if cargo["trucks_matched"] >= needed:
                cargo["active"] = False
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
            
    await query.edit_message_text(
        "✅ **Right ተረጋግጧል!**\n\n"
        "📌 ጭነቱ/መኪናው ከገበያ ተወግዷል።\n"
        "📌 ለሁለቱም ወገኖች የስልክ ቁጥር መልእክት ተልኳል።"
    )
    seeker_id = req["requester_id"]
    item_type = "የጭነት መኪና" if req.get("type") == "truck" else "የጭነት"
    seeker_text = f"ውድ ደንበኛችን፣\n\nየፈለጉት {item_type} ባለቤት ምላሽ እንደሰጡ እናሳውቃለን።\n\n📌 እባክዎ ትንሽ ይጠብቁ።\n\n🙏 እናመሰግናለን!"
    try:
        await context.bot.send_message(chat_id=seeker_id, text=seeker_text)
        req["seeker_notified"] = True
    except Exception:
        pass
    owner_id = other_party_id(req)
    if req.get("type") == "truck":
        owner_text = (
            "ውድ ደንበኛችን፣\n\nየለጠፉትን የጭነት መኪና የሚፈልግ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n📞 {SUPPORT_PHONE_2}\n\nወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n🙏 እናመሰግናለን!"
        )
    else:
        owner_text = (
            "ውድ ደንበኛችን፣\n\nየለጠፉትን ጭነት የሚወስድ ደንበኛ አግኝተናል!\n\n"
            "እባክዎ መልዕክቱን እንዳዩ በሚከተሉት ቁጥሮች ይደውሉ፦\n\n"
            f"📞 {SUPPORT_PHONE}\n📞 {SUPPORT_PHONE_2}\n\nወይም\n\n"
            f"👉 @{BOT_USERNAME} ተጭነው ከገቡ በኋላ\n"
            "\"📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ\" የሚለውን ተጭነው መልዕክት ያስቀምጡልን።\n\n🙏 እናመሰግናለን!"
        )
    try:
        await context.bot.send_message(chat_id=owner_id, text=owner_text)
        req["owner_notified"] = True
    except Exception:
        pass

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ ማጥፋት ይችላል!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    req["status"] = "deleted"
    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = True
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = True
    await query.edit_message_text("❌ **ተሰርዟል!**\n\nይህ ጥያቄ በ Super Admin ተሰርዟል።\n\n📌 ጭነት/መኪናው ነፃ ሆኗል።")
    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text="❌ **ይቅርታ**\n\nየጠየቁት ጭነት/መኪና ውድቅ ሆኗል።\n\nእባክዎ ሌላ ተስማሚ ጭነት/መኪና ይምረጡ።"
        )
    except Exception:
        pass

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

async def show_connection_requests(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
            reply_markup=main_menu(user_id)
        )
        context.user_data["sending_admin_message"] = True
        return
    received = []
    for i, req in enumerate(connection_requests):
        if req["status"] not in ["deleted", "completed"]:
            received.append((i, req))
    if not received:
        await update.message.reply_text("🤝 እስካሁን የግንኙነት ጥያቄ የለም።", reply_markup=main_menu(user_id))
        return
    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            truck_owner_name = users.get(req["truck_owner_id"], {}).get("name", "N/A")
            truck_owner_username = users.get(req["truck_owner_id"], {}).get("username", "")
            truck_owner_phone = truck.get("phone", "N/A")
            truck_poster = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🚛 **የመኪና ባለቤት:** {truck_poster}\n"
                f"   • ስም: {truck_owner_name}\n"
                f"   • Username: @{truck_owner_username or 'የለም'}\n"
                f"   • ስልክ: {truck_owner_phone}\n"
                f"   • አይነት: {truck['type']}\n"
                f"   • አቅም: {truck['capacity']}\n"
                f"   • ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"   • መንገድ: {truck.get('route', 'N/A')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የጭነት ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            cargo_owner_name = users.get(req["cargo_owner_id"], {}).get("name", "N/A")
            cargo_owner_username = users.get(req["cargo_owner_id"], {}).get("username", "")
            cargo_owner_phone = cargo.get("phone", "N/A")
            cargo_poster = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **የጭነት ባለቤት:** {cargo_poster}\n"
                f"   • ስም: {cargo_owner_name}\n"
                f"   • Username: @{cargo_owner_username or 'የለም'}\n"
                f"   • ስልክ: {cargo_owner_phone}\n"
                f"   • መነሻ: {cargo['from']}\n"
                f"   • መድረሻ: {cargo['to']}\n"
                f"   • አይነት: {cargo['type']}\n"
                f"   • ክብደት: {cargo['weight']}\n"
                f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የመኪና ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A')}\n"
                f"   • Username: @{req.get('requester_username', '') or 'የለም'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A')}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A')}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        buttons = [[
            InlineKeyboardButton("✅ Right", callback_data=f"adminright_{i}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{i}"),
        ]]
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(f"🗑️ ሙሉ በሙሉ አጥፋ #{i + 1} (Super)", callback_data=f"admindeleteconnection_{i}")
            ])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

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
# ADMIN MESSAGE HANDLING
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
        "✅ መልዕክትዎ ለ Admin/Super Admin ተልኳል።\n\n📸 ፎቶም መላክ ይችላሉ!",
        reply_markup=main_menu(user_id)
    )

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
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=text)
            else:
                await context.bot.send_document(chat_id=admin_id, document=file_id, caption=text)
        except Exception:
            pass
    await update.message.reply_text(
        "✅ ፎቶ/ስክሪንሾትዎ ለ Admin/Super Admin ተልኳል።\n\n📌 ሌላ መልዕክት ወይም ፎቶ መላክ ይችላሉ።",
        reply_markup=main_menu(user_id)
    )

async def _start_admin_message_mode(update, context):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
        reply_markup=main_menu(user_id)
    )
    context.user_data["sending_admin_message"] = True

# ==================================================
# OWNER
# ==================================================

async def owner_start(update, context):
    clear_stale_user_state(context)
    context.user_data["owner"] = {}
    await update.message.reply_text("📦 የጭነት ባለቤት ምዝገባ\n\n1️⃣ ስምዎን ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return OWNER_NAME

async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=phone_request_keyboard()
    )
    return OWNER_PHONE

async def owner_phone(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return OWNER_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return OWNER_PHONE
    phone = result
    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = owner.copy()
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=owner["name"], owner_phone=owner["phone"])
    except Exception as e:
        print(f"DB SAVE OWNER ERROR: {e}")
    await update.message.reply_text(
        f"✅ ምዝገባዎ ተጠናቋል!\n\n👤 {owner['name']}\n📞 {owner['phone']}",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END
# ==================================================
# TRACKING — MAIN MENU & REGISTRY
# ==================================================

async def tracking_menu(update, context):
    clear_stale_user_state(context)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ክትትል ጀምር", callback_data="track_start")],
        [InlineKeyboardButton("🔴 ክትትል አቁም", callback_data="track_stop")],
    ])
    await update.effective_message.reply_text(
        "📍 **TANA CARGO — የጭነት ክትትል**\n\n"
        "⚠️ **እባክዎን የስልክዎን location on አድርገው Live Location ያጋሩ። ሲጨርሱ off ያድርጉ።**\n\n"
        "🟢 ክትትል ጀምርን በመንካት አዲስ የጉዞ መረጃ ያስመዝግቡ።\n"
        "🔴 ክትትል አቁምን በመንካት ክትትል ማቆም ይችላሉ።",
        reply_markup=keyboard
    )

async def vehicle_tracking_menu(update, context):
    clear_stale_user_state(context)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ክትትል ጀምር", callback_data="vehicle_track_start")],
        [InlineKeyboardButton("🔴 ክትትል አቁም", callback_data="track_stop")],
    ])
    await update.effective_message.reply_text(
        "🚚 **TANA CARGO — የመኪና ክትትል**\n\n"
        "⚠️ **እባክዎን የስልክዎን location on አድርገው Live Location ያጋሩ። ሲጨርሱ off ያድርጉ።**\n\n"
        "🟢 ክትትል ጀምርን በመንካት አዲስ የጉዞ መረጃ ያስመዝግቡ።\n"
        "🔴 ክትትል አቁምን በመንካት ክትትል ማቆም ይችላሉ።",
        reply_markup=keyboard
    )

async def tracking_registry(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM tracking_sessions ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
        
    if not rows:
        await update.message.reply_text("📊 እስካሁን ምንም የክትትል መዝገብ የለም።")
        return
        
    for i, row in enumerate(rows):
        record = dict(row)
        code = record["tracking_code"]
        driver_id = record["driver_user_id"]
        customer_id = record["customer_user_id"]
        
        msg = (
            f"📊 **የክትትል መዝገብ #{i+1}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 ኮድ፦ `{code}`\n"
            f"📌 ሁኔታ፦ {record['status']}\n"
            f"🚛 ሹፌር መለያ: {driver_id}\n"
            f"👤 ደንበኛ መለያ: {customer_id}\n"
            f"📞 የደንበኛው ስልክ: {record.get('customer_phone') or 'N/A'}\n"
            f"📍 አሁኑ ቦታ: {record.get('latitude') or 'N/A'}, {record.get('longitude') or 'N/A'}\n"
        )
        
        buttons = []
        if record.get('status') != 'delivered':
            buttons.append([InlineKeyboardButton("🗺️ Live ቦታ አሳይ", callback_data=f"admin_view_live:{code}")])
            if is_super_admin(user_id):
                buttons.append([InlineKeyboardButton("🛑 ክትትል አቁም (Super)", callback_data=f"admin_force_stop:{code}")])
                
        if buttons:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
        else:
            await update.message.reply_text(msg, disable_web_page_preview=True)

async def admin_view_live(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    code = query.data.split(":")[1]
    record = db_get_tracking(code)
    if record and record.get("latitude") and record.get("longitude"):
        try:
            await context.bot.send_location(
                chat_id=update.effective_user.id,
                latitude=float(record["latitude"]),
                longitude=float(record["longitude"]),
                live_period=3600
            )
        except Exception:
            await query.message.reply_text(f"📍 {record['latitude']}, {record['longitude']}")
    else:
        await query.message.reply_text("⚠️ ምንም የተላከ ቦታ የለም።")

async def admin_force_stop(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    code = query.data.split(":")[1]
    db_finish_tracking(code)
    await query.edit_message_text(f"✅ ክትትል ኮድ {code} በራስዎ ተዘግቷል።")

# --- START TRACKING (CARGO) ---
async def tracking_new_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_new"] = {"type": "cargo"}
    await query.message.reply_text(
        "📝 **አዲስ ጭነት ክትትል መክፈት**\n\n"
        "1️⃣ የባለጭነቱን (Customer) **ስልክ ቁጥር** ያስገቡ።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=ReplyKeyboardRemove()
    )
    return TRACK_REQ_PHONE

# --- START TRACKING (VEHICLE) ---
async def vehicle_tracking_new_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["track_new"] = {"type": "vehicle"}
    await query.message.reply_text(
        "📝 **አዲስ የመኪና ክትትል መክፈት**\n\n"
        "1️⃣ የባለመኪናው (እንዲከታተሎት የሚፈልጉት) (Customer) **ስልክ ቁጥር** ያስገቡ።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=ReplyKeyboardRemove()
    )
    return VEHICLE_TRACK_PHONE

async def tracking_req_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nምሳሌ፦ 0912345678")
        return TRACK_REQ_PHONE
    
    target_id = find_user_by_phone(result)
    if not target_id:
        await update.message.reply_text(
            "⚠️ **ማሳሰቢያ፦** የፈለጉት ደንበኛ በዚህ ስልክ ቁጥር በTANA CARGO አልተመዘገበም።\n\n"
            "ነገር ግን መረጃውን መቀጠል ይችላሉ። በኋላ ላይ የGoogle Maps ሊንኩን በመጠቀም ለደንበኛው ማጋራት ይችላሉ።"
        )
        target_id = 0 
        
    context.user_data["track_new"]["customer_phone"] = result
    context.user_data["track_new"]["target_user_id"] = target_id
    
    await update.message.reply_text(
        "2️⃣ **የመንገዱን ዝርዝር** ይጻፉ።\n"
        "ምሳሌ፦ ባህር ዳር ➡️ አዲስ አበባ",
        reply_markup=ReplyKeyboardRemove()
    )
    if context.user_data["track_new"]["type"] == "cargo":
        return TRACK_REQ_ROUTE
    else:
        return TRACK_REQ_ROUTE 

async def tracking_req_route(update, context):
    route = update.message.text.strip()
    context.user_data["track_new"]["route"] = route
    await update.message.reply_text(
        "3️⃣ **የአሁኑን የመኪና ቦታ** ይላኩ።\n\n"
        "ከታች ያለውን ቁልፍ ተጭነው GPS ይላኩ።",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📍 የአሁኑን ቦታ ላክ", request_location=True)],
             [KeyboardButton("❌ ሰርዝ")]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    return TRACK_REQ_LOCATION

async def tracking_req_location(update, context):
    if not update.message.location:
        await update.message.reply_text("❌ እባክዎ ቦታውን በGPS ይላኩ።", reply_markup=ReplyKeyboardRemove())
        return TRACK_REQ_LOCATION
    loc = update.message.location
    req = context.user_data.get("track_new", {})
    req["driver_id"] = update.effective_user.id
    req["driver_name"] = update.effective_user.full_name
    req["lat"] = loc.latitude
    req["lon"] = loc.longitude
    
    code = "TC" + secrets.token_hex(4).upper()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tracking_sessions (
                tracking_code, request_type, driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                status, latitude, longitude, location_updated_at, created_at,
                customer_phone, driver_phone
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', 'in_transit', ?, ?, ?, ?, ?, ?)
        """, (
            code, req["type"], req["driver_id"], req["target_user_id"], 
            req["route"], req["route"],
            req["lat"], req["lon"], datetime.now().astimezone().isoformat(),
            datetime.now().astimezone().isoformat(),
            req["customer_phone"], ""
        ))
        conn.commit()
    finally:
        conn.close()
        
    await update.message.reply_text(
        "✅ ቦታው ተቀብለናል። ኮዱ ተፈጥሯል።",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.pop("track_new", None)
    
    try:
        await context.bot.send_message(
            chat_id=req["driver_id"],
            text=f"🎉 **ጉዞዎ ተጀምሯል!**\n\n🔑 **የክትትል ኮድ፦** `{code}`\n\n"
                 f"📞 ይህንን ኮድ ለባለጭነቱ ({req['customer_phone']}) ያስተላልፉ።"
        )
    except Exception:
        pass
        
    if req["target_user_id"] > 0:
        try:
            await context.bot.send_location(
                chat_id=req["target_user_id"],
                latitude=float(req["lat"]),
                longitude=float(req["lon"]),
                live_period=3600
            )
            await context.bot.send_message(
                chat_id=req["target_user_id"],
                text=f"📦 **TANA CARGO — የጭነት ክትትል**\n\n"
                     f"ሹፌሩ ጉዞ ጀምሯል! የክትትል ኮድዎ ይህ ነው፦\n\n"
                     f"🔑 `{code}`\n\n"
                     f"👉 ወደ '📍 የጭነት ክትትል' ሄደው '📦 ጭነቴን በኮድ መከታተል' ላይ ይህንን ኮድ ያስገቡ።\n"
                     f"🗺️ Live ቦታው ከላይ ተልኳል።"
            )
        except Exception:
            pass
    else:
        map_url = f"https://maps.google.com/?q={req['lat']},{req['lon']}"
        try:
            await context.bot.send_message(
                chat_id=req["driver_id"],
                text=f"⚠️ **ደንበኛው በTANA CARGO አልተመዘገበም!**\n\n"
                     f"እባክዎ ይህንን የGoogle Map ሊንክ ኮፒ አድርገው ለደንበኛው ይላኩ፦\n\n"
                     f"🗺️ {map_url}\n\n"
                     f"እንዲሁም የክትትል ኮዱን `{code}` ለደንበኛው ማሳወቅ ይችላሉ።"
            )
        except Exception:
            pass
        
    return ConversationHandler.END

# --- LOOKUP ---
async def tracking_customer_choose(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_lookup_waiting"] = True
    await query.message.reply_text(
        "📦 የተሰጠዎትን የክትትል ኮድ ያስገቡ።\nምሳሌ፦ TC1A2B3C4"
    )

async def tracking_text_handler(update, context):
    if not update.effective_user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if context.user_data.get("track_new") and text == "❌ ሰርዝ":
        context.user_data.pop("track_new", None)
        await update.message.reply_text("ጉዞ መክፈቱ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
    if not context.user_data.get("tracking_lookup_waiting"):
        return
    code = "".join(text.upper().split())
    if len(code) < 6 or len(code) > 20:
        await update.message.reply_text("❌ የክትትል ኮዱ ትክክል አይመስልም።")
        return
    record = db_get_tracking(code)
    if not record:
        await update.message.reply_text("❌ ይህ የክትትል ኮድ አልተገኘም።")
        return
    context.user_data.pop("tracking_lookup_waiting", None)
    await tracking_show_result(update, context, record)

async def tracking_show_result(update, context, record):
    code = record["tracking_code"]
    status_labels = {"ready": "ጉዞ ለመጀመር ዝግጁ", "in_transit": "በመንገድ ላይ", "delivered": "ጉዞው ተጠናቋል"}
    msg = (
        f"📦 **TANA CARGO — የቀጥታ ክትትል**\n\n"
        f"🔑 ኮድ፦ `{code}`\n"
        f"📍 መነሻ/መረጃ፦ {record.get('origin') or 'አልተገለጸም'}\n"
        f"🏁 መድረሻ/መንገድ፦ {record.get('destination') or 'አልተገለጸም'}\n"
        f"📊 ሁኔታ፦ {status_labels.get(record.get('status'), record.get('status'))}\n"
    )
    if record.get("latitude") is not None:
        lat = float(record["latitude"])
        lon = float(record["longitude"])
        msg += f"\n📍 የመጨረሻ የተላከ ቦታ፦ {lat:.6f}, {lon:.6f}"
        try:
            await update.effective_message.reply_location(latitude=lat, longitude=lon, live_period=3600)
        except Exception:
            pass
    else:
        msg += "\n📍 ሹፌሩ እስካሁን የGPS ቦታ አልላከም።"
        
    await update.effective_message.reply_text(msg, disable_web_page_preview=True)

# ==================================================
# STOP TRACKING FLOW
# ==================================================

async def tracking_stop_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_stop"] = {}
    await query.message.reply_text(
        "🛑 **ክትትል ማቆም**\n\n"
        "1️⃣ እባክዎ **የሚከታተለው ሰው ስም** ይጻፉ።\n"
        "ምሳሌ፦ አበበ ከበደ",
        reply_markup=ReplyKeyboardRemove()
    )
    return TRACK_STOP_NAME

async def tracking_stop_name(update, context):
    name = update.message.text.strip().split("\n")[0].strip()
    if not name:
        await update.message.reply_text("❌ እባክዎ ስም ይጻፉ።")
        return TRACK_STOP_NAME
    context.user_data["tracking_stop"]["name"] = name
    await update.message.reply_text(
        "2️⃣ **የሚከታተለው ሰው ስልክ ቁጥር** ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678",
        reply_markup=ReplyKeyboardRemove()
    )
    return TRACK_STOP_PHONE

async def tracking_stop_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nምሳሌ፦ 0912345678")
        return TRACK_STOP_PHONE

    info = context.user_data.get("tracking_stop", {})
    info["phone"] = result
    info["requester_id"] = update.effective_user.id
    info["requester_name"] = update.effective_user.full_name
    info["timestamp"] = datetime.now().astimezone().isoformat()
    tracking_stops.append(info)
    idx = len(tracking_stops) - 1
    context.user_data.pop("tracking_stop", None)

    await update.message.reply_text(
        "✅ **የክትትል ማቆሚያ ጥያቄዎ ለአድሚን ተልኳል።**\n\n"
        "⏳ አድሚን ሲያጸድቅ የክትትሉ ባለቤት ማሳወቂያ ይደርሰዋል።",
        reply_markup=main_menu(update.effective_user.id)
    )

    text = (
        "🛑 **TANA CARGO — አዲስ የክትትል ማቆሚያ ጥያቄ**\n\n"
        f"👤 ጠያቂ: {info['requester_name']} (ID: {info['requester_id']})\n"
        f"📋 የሚከታተለው ሰው ስም: {info['name']}\n"
        f"📞 ስልክ: {info['phone']}\n\n"
        "📌 አጽድቀው ክትትሉ እንዲቆም ይፈልጋሉ?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ክትትል አቁም (Right)", callback_data=f"approvestop_{idx}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejectstop_{idx}")
    ]])
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception:
            pass
    return ConversationHandler.END

async def admin_approve_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tracking_sessions WHERE customer_phone = ? AND status IN ('ready', 'in_transit') ORDER BY created_at DESC LIMIT 1", (info["phone"],)).fetchone()
    finally:
        conn.close()
        
    if row:
        record = dict(row)
        db_finish_tracking(record["tracking_code"])
        driver_id = int(record["driver_user_id"])
        try:
            await context.bot.send_message(
                chat_id=driver_id,
                text=f"🛑 **ክትትል ቁማል**\n\n🔑 ኮድ: {record['tracking_code']}\n👤 የተከታተለው ሰው: {info['name']}\n📞 ስልክ: {info['phone']}\n\nጭነትዎ መዳረሻው ላይ ደርሷል።"
            )
        except Exception:
            pass
        await query.edit_message_text(
            f"✅ **ክትትል ተቆሟል!**\n\n🔑 ኮድ: {record['tracking_code']}\nለሹፌሩ ማሳወቂያ ተልኳል።"
        )
    else:
        await query.edit_message_text("⚠️ በዚህ ስልክ ቁጥር ንቁ የክትትል ጉዞ አልተገኘም።")

    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text=f"✅ **ክትትል ተቆሟል!**\n\n📞 {info['phone']} ለሚለው ስልክ ክትትል ተዘግቷል።"
        )
    except Exception:
        pass

async def admin_reject_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]
    await query.edit_message_text("❌ የክትትል ማቆሚያ ጥያቄው ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text="❌ የክትትል ማቆሚያ ጥያቄዎ በአድሚን ውድቅ ተደርጓል።"
        )
    except Exception:
        pass

# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(update, context):
    if update.effective_user is None:
        return
    user_id = update.effective_user.id
    
    if not is_user_registered(user_id):
        await update.message.reply_text("⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**")
        return

    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)
    if context.user_data.get("tracking_lookup_waiting") or context.user_data.get("track_new"):
        return await tracking_text_handler(update, context)
    if context.user_data.get("connect_info"):
        return await connect_fallback_router(update, context)

async def connect_fallback_router(update, context):
    info = context.user_data.get("connect_info")
    if not info:
        return
    if not info.get("name") and update.message.text:
        info["name"] = update.message.text.strip()
        context.user_data["connect_info"] = info
        await update.message.reply_text("2️⃣ እባክዎ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return

# ==================================================
# BASIC COMMANDS
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    
    if is_user_registered(user.id):
        users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
        total_users = get_total_users_count()
        try:
            await update.message.reply_photo(
                photo=START_IMAGE_ID,
                caption=(
                    "👋 **እንኳን ደህና መጡ ወደ TANA CARGO!**\n\n"
                    f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
                    "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦"
                ),
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text(
                "👋 **እንኳን ደህና መጡ ወደ TANA CARGO!**\n\n"
                f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
                "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦",
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
        return ConversationHandler.END
    
    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "🚛 **TANA CARGO | ጣና ጭነት** 📦\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "👋 **እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!**\n\n"
                "📱 **እባክዎ ለመጀመር የራስዎን ስልክ ቁጥር ያጋሩ።**\n\n"
                "📢 ለበለጠ መረጃ 👉 @tanapage Join ያድርጉ\n\n"
                "⚠️ **ማሳሰቢያ፦** ይህ ስልክ ቁጥርዎ በTANA CARGO ለሚደረጉ ግንኙነቶች ይጠቅማል።"
            ),
            reply_markup=phone_request_keyboard(),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "🚛 **TANA CARGO | ጣና ጭነት** 📦\n\n"
            "👋 **እንኳን ደህና መጡ!**\n\n"
            "📱 **እባክዎ ለመጀመር የራስዎን ስልክ ቁጥር ያጋሩ።**\n"
            "📢 ለበለጠ መረጃ 👉 @tanapage Join ያድርጉ",
            reply_markup=phone_request_keyboard(),
            parse_mode="Markdown"
        )
        
    return START_PHONE

async def start_phone_input(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ ከታች ያለውን '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return START_PHONE
        
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return START_PHONE
    phone = result
    
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = {"phone": phone, "name": user.full_name, "user_id": user.id}
    
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=phone)
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")
        
    total_users = get_total_users_count()
    
    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "✅ **ስልክ ቁጥርዎ በትክክል ተመዝግቧል!**\n\n"
                f"📞 **{phone}**\n\n"
                "👋 አሁን እንኳን ደህና መጡ ወደ TANA CARGO ጣና ጭነት!\n"
                f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
                "📢 ስለጣና ካርጎ ሙሉ መረጃ እንዲኖርዎት 👉 @tanapage ነክተው Join ያድርጉ።\n\n"
                "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦"
            ),
            reply_markup=main_menu(user.id),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "✅ **ስልክ ቁጥርዎ በትክክል ተመዝግቧል!**\n\n"
            "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦",
            reply_markup=main_menu(user.id)
        )
        
    return ConversationHandler.END

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})
    
    total_users = get_total_users_count()
    
    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user['name']}\n🔗 Username፦ @{user['username'] or 'የለም'}\n🆔 Telegram ID፦ {user['id']}\n"
        f"\n👥 **ጠቅላላ ተጠቃሚዎች፦** {total_users:,}\n"
    )
    if "owner" in user:
        owner = user["owner"]
        message += f"\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n👤 {owner.get('name', 'N/A')}\n📞 {owner.get('phone', 'N/A')}\n"
    if is_broker(user_id):
        message += "\n🏷️ የጭነት ደላላ: ✅ ጸድቋል\n"
    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]
    message += f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"
    
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        "📞 TANA CARGO የጣና ጭነት እገዛ\n\nለእገዛ ለማግኘት @tanapage ይጠቀሙ።\n\n"
        f"☎️ {SUPPORT_PHONE}\n☎️ {SUPPORT_PHONE_2}\n\n🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text(
        "🚚 **TANA CARGO | ጣና ጭነት**\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት፣ የጭነት ማጓጓዣ ሂደትን "
        "ለማቀላጠፍ እና ቀላልና የተደራጁ አገልግሎት ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "❤️ TANA CARGO — ጭነትንና መኪናን እናገናኛለን።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu(update.effective_user.id)
    )

async def service_payment(update, context):
    await update.message.reply_text(payment_methods_text(), reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# ==================================================
# ERROR / HEALTH / EXPIRY
# ==================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"TANA CARGO ERROR: {context.error}")
    try:
        print(f"ERROR UPDATE: {update}")
    except Exception:
        pass

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
    print(f"✅ TANA CARGO health server listening on port {port}")
    return server

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
            await context.bot.send_message(chat_id=cargo_posts[i]["user_id"], text=f"⏰ **የጭነት ጊዜ ገደብ**\n\n📍 {cargo_posts[i]['from']} ➡️ {cargo_posts[i]['to']}\n📦 {cargo_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass
    for i in expired_trucks:
        truck_posts[i]["active"] = False
        try:
            await context.bot.send_message(chat_id=truck_posts[i]["user_id"], text=f"⏰ **የመኪና ጊዜ ገደብ**\n\n🚛 {truck_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass

# ==================================================
# MENU ROUTER & INTERRUPT
# ==================================================

MASTER_MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 የጭነት መኪና ማስመዝገብ|🚛 የጭነት መኪና መፈለግ|"
    r"👤 የኔ መረጃ|📍 የጭነት ክትትል|🚚 የመኪና ክትትል|📊 የክትትል መዝገብ \(Admin\)|📢 ለደንበኞች መረጃ መላኪያ|🤝 የግንኙነት ጥያቄዎች|📋 የጭነት ደላሎች መዝገብ|"
    r"🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ|📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ|"
    r"💳 የአገልግሎት ክፍያ ለመፈፀም|📞 Support|ℹ️ About)$"
)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    
    if not is_user_registered(uid):
        await update.message.reply_text("⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**")
        return ConversationHandler.END

    context.user_data["subscribed"] = True
    clear_stale_user_state(context)
    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "📍 የጭነት ክትትል":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "🚚 የመኪና ክትትል":
        await vehicle_tracking_menu(update, context)
        return ConversationHandler.END
    if text == "📊 የክትትል መዝገብ (Admin)":
        await tracking_registry(update, context)
        return ConversationHandler.END
    if text == "📢 ለደንበኞች መረጃ መላኪያ":
        if not is_super_admin(uid):
            await update.message.reply_text("❌ Super Admin ብቻ!")
            return ConversationHandler.END
        return await broadcast_start(update, context)
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ":
        await show_broker_registry(update, context)
        return ConversationHandler.END
    if text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ":
        return await broker_start(update, context)
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await _start_admin_message_mode(update, context)
        return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

async def menu_interrupt(update, context):
    clear_stale_user_state(context)
    text = update.message.text
    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "📍 የጭነት ክትትል":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "🚚 የመኪና ክትትል":
        await vehicle_tracking_menu(update, context)
        return ConversationHandler.END
    if text == "📊 የክትትል መዝገብ (Admin)":
        await tracking_registry(update, context)
        return ConversationHandler.END
    if text == "📢 ለደንበኞች መረጃ መላኪያ":
        if not is_super_admin(update.effective_user.id):
            await update.message.reply_text("❌ Super Admin ብቻ!")
            return ConversationHandler.END
        return await broadcast_start(update, context)
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📋 የጭነት ደላሎች መዝገብ":
        await show_broker_registry(update, context)
        return ConversationHandler.END
    if text == "🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ":
        return await broker_start(update, context)
    if text == "📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ":
        await _start_admin_message_mode(update, context)
        return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ ለመፈፀም":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    start_health_server()
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")

    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("cargo", cargo_start))
    application.add_handler(CommandHandler("truck", truck_start))
    application.add_handler(CommandHandler("findcargo", find_cargo))
    application.add_handler(CommandHandler("findtruck", find_truck))
    application.add_handler(CommandHandler("myprofile", profile))
    application.add_handler(CommandHandler("support", support_start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("owner", owner_start))
    application.add_handler(CommandHandler("broker", broker_start))
    application.add_handler(CommandHandler("track", tracking_menu))

    master_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የጭነት ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^🚚 የመኪና ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^📊 የክትትል መዝገብ \(Admin\)$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 ለደንበኞች መረጃ መላኪያ$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^📋 የጭነት ደላሎች መዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🆕 የጭነት ደላላ ከሆኑ ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ ለመፈፀም$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),
            CommandHandler("owner", owner_start),
            CommandHandler("broker", broker_start),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"),
            CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"),
            CallbackQueryHandler(tracking_new_start, pattern=r"^track_start$"),
            CallbackQueryHandler(vehicle_tracking_new_start, pattern=r"^vehicle_track_start$"),
            CallbackQueryHandler(tracking_stop_start, pattern=r"^track_stop$"),
        ],
        states={
            CARGO_FROM: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_TRUCKS_NEEDED: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_trucks_needed)],
            CARGO_CALENDAR: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [MessageHandler(filters.CONTACT, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ROUTE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(truck_route_choice, pattern=r"^route_"), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_route)],
            TRUCK_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [MessageHandler(filters.CONTACT, truck_phone)],
            OWNER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name)],
            OWNER_PHONE: [MessageHandler(filters.CONTACT, owner_phone)],
            BROKER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name)],
            BROKER_PHONE: [MessageHandler(filters.CONTACT, broker_phone)],
            BROKER_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_address)],
            CONNECT_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [MessageHandler(filters.CONTACT, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
            TRACK_REQ_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_phone)],
            VEHICLE_TRACK_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_phone)],
            TRACK_REQ_ROUTE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_req_route)],
            TRACK_REQ_LOCATION: [MessageHandler(filters.LOCATION, tracking_req_location), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_text_handler)],
            TRACK_STOP_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_name)],
            TRACK_STOP_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_phone)],
            START_PHONE: [MessageHandler(filters.CONTACT, start_phone_input)],
            ADMIN_BROADCAST_TEXT: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )
    application.add_handler(master_conversation)

    # Extra Callbacks
    application.add_handler(CallbackQueryHandler(admin_approve_stop_request, pattern=^approvestop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_stop_request, pattern=r"^rejectstop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_connection, pattern=r"^admindeleteconnection_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_clear_notification, pattern=r"^adminclear_\d+$"))
    application.add_handler(CallbackQueryHandler(already_selected_callback, pattern=r"^already_selected$"))
    application.add_handler(CallbackQueryHandler(admin_view_live, pattern=r"^admin_view_live:"))
    application.add_handler(CallbackQueryHandler(admin_force_stop, pattern=r"^admin_force_stop:"))
    application.add_handler(CallbackQueryHandler(tracking_customer_choose, pattern=r"^track_customer_choose$"))

    # Message Handlers (ለፎቶ እና ለጽሑፍ)
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_user_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Job Queue (የጊዜ ገደብ ማስተዳደሪያ)
    if application.job_queue:
        application.job_queue.run_repeating(clean_expired_posts, interval=3600, first=15)
        print("✅ Expiry cleanup job scheduled (every 1 hour)")

    # BOT START (ማስጀመሪያ)
    print("🚀 TANA CARGO Bot is starting...")
    print(f"📍 Admins: {get_admin_ids()}")
    
    # ቦቱን ማስጀመር
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
