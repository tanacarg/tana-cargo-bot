import os
import random
import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# ZENBABA BINGO - DEMO / FAKE MONEY VERSION
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("ZENBABA_BINGO")

TOKEN = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))

# ------------------------------------------------------------
# GAME SETTINGS
# ------------------------------------------------------------

CARTELA_NAMES = list("ABCDEFGHIJKLMNOPQRST")  # A - T
MAX_CARTELAS_PER_PLAYER = 5

STARTING_BALANCE = 10
CARTELA_PRICE = 5
DEFAULT_PRIZE = 100

COUNTDOWN_SECONDS = 30
DRAW_INTERVAL = 2

BET_AMOUNTS = [10, 20, 30, 50, 100]

# ------------------------------------------------------------
# DATA CLASSES
# ------------------------------------------------------------


@dataclass
class Cartela:
    name: str
    numbers: List[int]
    owner_id: Optional[int] = None
    bot_owned: bool = False


@dataclass
class Player:
    user_id: int
    name: str
    balance: int = STARTING_BALANCE
    cartel_names: List[str] = field(default_factory=list)


@dataclass
class Round:
    active: bool = False
    countdown_task: Optional[asyncio.Task] = None
    game_task: Optional[asyncio.Task] = None

    auto_mode_locked: bool = False
    round_auto_mode: bool = True

    drawn_numbers: List[int] = field(default_factory=list)
    drawn_set: Set[int] = field(default_factory=set)

    winner_cartela: Optional[str] = None
    winner_user_id: Optional[int] = None

    selected_bets: Dict[int, int] = field(default_factory=dict)

    started_message_ids: Dict[int, int] = field(default_factory=dict)

    blinking: bool = False


# ------------------------------------------------------------
# GLOBAL STATE
# ------------------------------------------------------------

players: Dict[int, Player] = {}
cartelas: Dict[str, Cartela] = {}
round_state = Round()

# Global admin setting.
# Only affects the NEXT round.
AUTO_BINGO = True

# Lock prevents simultaneous cartela registration/winner processing.
game_lock = asyncio.Lock()


# ============================================================
# CARTELA GENERATION
# ============================================================

def generate_cartelas():
    """Generate A-T cartelas, each with 5 random unique numbers 1-100."""
    cartelas.clear()

    for name in CARTELA_NAMES:
        numbers = random.sample(range(1, 101), 5)
        cartelas[name] = Cartela(
            name=name,
            numbers=numbers,
            owner_id=None,
            bot_owned=False,
        )


def reset_cartela_owners():
    for c in cartelas.values():
        c.owner_id = None
        c.bot_owned = False


generate_cartelas()


# ============================================================
# HELPERS
# ============================================================

def get_player(user_id: int, name: str = "") -> Player:
    if user_id not in players:
        players[user_id] = Player(
            user_id=user_id,
            name=name or "Player",
            balance=STARTING_BALANCE,
        )
    elif name:
        players[user_id].name = name
    return players[user_id]


def money_text(amount: int) -> str:
    return f"{amount} Birr"


def cartela_completed(cartela: Cartela) -> bool:
    return all(n in round_state.drawn_set for n in cartela.numbers)


def player_cartelas_text(player: Player) -> str:
    if not player.cartel_names:
        return "None"
    return ", ".join(player.cartel_names)


def number_mark(number: int) -> str:
    if number in round_state.drawn_set:
        return f"🟩{number:02d}"
    return f"⬜{number:02d}"


def numbers_board() -> str:
    lines = []
    for start in range(1, 101, 10):
        row = [number_mark(n) for n in range(start, start + 10)]
        lines.append(" ".join(row))
    return "\n".join(lines)


def cartela_display(cartela: Cartela) -> str:
    nums = []
    for n in cartela.numbers:
        if n in round_state.drawn_set:
            nums.append(f"🟩{n}")
        else:
            nums.append(f"⬜{n}")
    return f"🎫 **{cartela.name}**  " + "  ".join(nums)


def all_cartelas_display() -> str:
    lines = []
    for name in CARTELA_NAMES:
        c = cartelas[name]
        lines.append(cartela_display(c))
    return "\n".join(lines)


def cartela_is_available(name: str) -> bool:
    return cartelas[name].owner_id is None and not cartelas[name].bot_owned


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 PLAY BINGO", callback_data="play")],
        [
            InlineKeyboardButton("🎫 CARTELAS", callback_data="cartelas"),
            InlineKeyboardButton("💰 BALANCE", callback_data="balance"),
        ],
        [InlineKeyboardButton("🏆 MY CARTELAS", callback_data="my_cartelas")],
        [InlineKeyboardButton("📜 GAME RULES", callback_data="rules")],
    ])


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player = get_player(user.id, user.full_name)

    text = (
        "🎉 **WELCOME TO ZENBABA BINGO** 🎉\n\n"
        f"👤 Player: {player.name}\n"
        f"💰 Demo Balance: **{money_text(player.balance)}**\n\n"
        "🎁 New player bonus: **10 Demo Birr**\n\n"
        "🎫 Choose a Cartela and play Bingo.\n"
        "🤖 The bot can play the other Cartelas automatically.\n\n"
        "⚠️ This is a DEMO game using fake balance only."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# ============================================================
# CARTELA LIST
# ============================================================

async def show_cartelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "🎫 **ZENBABA BINGO CARTELAS A–T**\n\n"
        "Each Cartela has 5 random numbers.\n"
        "Select a Cartela to register it.\n\n"
        f"💰 Price: **{CARTELA_PRICE} Demo Birr**\n\n"
    )

    keyboard = []
    for name in CARTELA_NAMES:
        c = cartelas[name]
        if c.owner_id is not None:
            status = "🟥"
        elif c.bot_owned:
            status = "🤖"
        else:
            status = "🟢"

        keyboard.append([
            InlineKeyboardButton(
                f"{status} {name}  {','.join(map(str, c.numbers))}",
                callback_data=f"select_{name}",
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 BACK", callback_data="back")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# SELECT CARTELA
# ============================================================

async def select_cartela(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    name = query.data.replace("select_", "")

    if name not in cartelas:
        return

    player = get_player(user.id, user.full_name)
    c = cartelas[name]

    if c.owner_id is not None:
        await query.answer("❌ This Cartela is already registered.", show_alert=True)
        return

    if c.bot_owned:
        await query.answer("🤖 This Cartela is being used by the Bot.", show_alert=True)
        return

    if name in player.cartel_names:
        await query.answer("You already have this Cartela.", show_alert=True)
        return

    if len(player.cartel_names) >= MAX_CARTELAS_PER_PLAYER:
        await query.answer("❌ Maximum 5 Cartelas allowed.", show_alert=True)
        return

    text = (
        f"🎫 **CARTELA {name}**\n\n"
        f"🔢 Numbers:\n**{', '.join(map(str, c.numbers))}**\n\n"
        f"💰 Price: **{CARTELA_PRICE} Demo Birr**\n"
        f"💳 Your Balance: **{player.balance} Demo Birr**\n\n"
        "Press REGISTER to take this Cartela."
    )

    keyboard = [
        [InlineKeyboardButton("🔴 REGISTER", callback_data=f"register_{name}")],
        [InlineKeyboardButton("🔙 BACK", callback_data="cartelas")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# REGISTER CARTELA
# ============================================================

async def register_cartela(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    name = query.data.replace("register_", "")

    async with game_lock:
        if name not in cartelas:
            await query.answer("Cartela not found.", show_alert=True)
            return

        player = get_player(user.id, user.full_name)
        c = cartelas[name]

        if round_state.active:
            await query.answer("❌ The round has already started.", show_alert=True)
            return

        if c.owner_id is not None or c.bot_owned:
            await query.answer("❌ Cartela is no longer available.", show_alert=True)
            return

        if len(player.cartel_names) >= MAX_CARTELAS_PER_PLAYER:
            await query.answer("❌ Maximum 5 Cartelas.", show_alert=True)
            return

        if player.balance < CARTELA_PRICE:
            await query.answer("❌ Insufficient Balance", show_alert=True)
            return

        player.balance -= CARTELA_PRICE
        c.owner_id = user.id
        player.cartel_names.append(name)

        await query.answer("✅ Cartela Registered!", show_alert=True)

        text = (
            f"🟢 **REGISTERED — CARTELA {name}**\n\n"
            f"🔢 **{', '.join(map(str, c.numbers))}**\n\n"
            "✅ Cartela registered successfully.\n"
            f"💰 Remaining Demo Balance: **{player.balance} Birr**\n\n"
            "⏳ The game will start when the round countdown begins."
        )

        keyboard = [
            [InlineKeyboardButton("🎫 CHOOSE ANOTHER", callback_data="cartelas")],
            [InlineKeyboardButton("🎮 VIEW GAME", callback_data="play")],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

        # First registered cartela starts round countdown
        if not round_state.active and round_state.countdown_task is None:
            round_state.countdown_task = asyncio.create_task(
                start_round_countdown(context)
            )


# ============================================================
# PLAY SCREEN
# ============================================================

async def play_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    player = get_player(user.id, user.full_name)

    text = (
        "🎮 **ZENBABA BINGO**\n\n"
        f"💰 Balance: **{player.balance} Demo Birr**\n"
        f"🎫 Your Cartelas: **{player_cartelas_text(player)}**\n\n"
        f"🤖 Auto Bingo: **{'ON' if AUTO_BINGO else 'OFF'}**\n\n"
    )

    if round_state.active:
        text += (
            f"🔢 Numbers Drawn: **{len(round_state.drawn_numbers)}**\n\n"
            f"{numbers_board()}"
        )
    else:
        text += (
            "⏳ **WAITING FOR PLAYERS**\n\n"
            "Choose a Cartela to start a new round.\n\n"
            f"{numbers_board()}"
        )

    keyboard = [
        [InlineKeyboardButton("🎫 CARTELAS", callback_data="cartelas")],
        [InlineKeyboardButton("🏆 MY CARTELAS", callback_data="my_cartelas")],
        [InlineKeyboardButton("🔄 REFRESH", callback_data="play")],
        [InlineKeyboardButton("🔙 MENU", callback_data="back")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# ROUND COUNTDOWN
# ============================================================

async def start_round_countdown(context: ContextTypes.DEFAULT_TYPE):
    try:
        for remaining in range(COUNTDOWN_SECONDS, 0, -1):
            registered = [
                c for c in cartelas.values() if c.owner_id is not None
            ]

            if not registered:
                round_state.countdown_task = None
                return

            if remaining == 25:
                await broadcast(
                    context,
                    "⚠️ **GAME IS ABOUT TO START!**\n\n"
                    "🎫 Cartela registration is still open until the round starts.\n"
                    f"⏱️ Starting in **{remaining} seconds**."
                )
            elif remaining in (20, 15, 10, 5, 4, 3, 2, 1):
                await broadcast(
                    context,
                    f"⏳ **BINGO STARTING IN {remaining} SECONDS...**"
                )

            await asyncio.sleep(1)

        round_state.round_auto_mode = AUTO_BINGO
        round_state.auto_mode_locked = True

        await begin_round(context)

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Countdown error")
    finally:
        round_state.countdown_task = None


# ============================================================
# BEGIN ROUND
# ============================================================

async def begin_round(context: ContextTypes.DEFAULT_TYPE):
    async with game_lock:
        round_state.active = True
        round_state.drawn_numbers.clear()
        round_state.drawn_set.clear()
        round_state.winner_cartela = None
        round_state.winner_user_id = None
        round_state.blinking = False

        # Bot takes all unused cartelas
        for name, c in cartelas.items():
            if c.owner_id is None:
                c.bot_owned = True

        mode = "AUTO ON 🤖" if round_state.round_auto_mode else "AUTO OFF 👤"

        await broadcast(
            context,
            "🚨 **BINGO STARTED!** 🚨\n\n"
            f"🤖 Round Auto Mode: **{mode}**\n"
            "🔒 Auto mode is LOCKED for this round.\n\n"
            "🔢 Numbers will be drawn every **2 seconds**.\n"
            "🏆 The first completed Cartela wins."
        )

    round_state.game_task = asyncio.create_task(run_game(context))


# ============================================================
# GAME LOOP
# ============================================================

async def run_game(context: ContextTypes.DEFAULT_TYPE):
    try:
        available_numbers = list(range(1, 101))
        random.shuffle(available_numbers)

        while round_state.active and available_numbers:
            number = available_numbers.pop()

            async with game_lock:
                round_state.drawn_numbers.append(number)
                round_state.drawn_set.add(number)

            # Show spinning effect
            await broadcast(
                context,
                "🎰 **DRAWING...**\n\n"
                "🔄 🔄 🔄"
            )

            await asyncio.sleep(0.5)

            await broadcast(
                context,
                f"🎰 **NUMBER DRAWN**\n\n"
                f"🟢 **{number}**\n\n"
                f"📊 Numbers drawn: {len(round_state.drawn_numbers)}"
            )

            # Auto Bingo detection
            if round_state.round_auto_mode:
                winner = find_completed_cartela()
                if winner:
                    await finish_game(context, winner.name, winner.owner_id)
                    return

            await asyncio.sleep(DRAW_INTERVAL - 0.5)

        if round_state.active:
            await finish_without_winner(context)

    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Game loop error")


# ============================================================
# FIND COMPLETED CARTELA
# ============================================================

def find_completed_cartela() -> Optional[Cartela]:
    completed = []
    for c in cartelas.values():
        if c.owner_id is None and not c.bot_owned:
            continue
        if cartela_completed(c):
            completed.append(c)

    if not completed:
        return None

    completed.sort(key=lambda x: CARTELA_NAMES.index(x.name))
    return completed[0]


# ============================================================
# MANUAL BINGO
# ============================================================

async def manual_bingo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    if not round_state.active:
        await query.answer("❌ No active game.", show_alert=True)
        return

    player = players.get(user.id)
    if not player:
        return

    if not player.cartel_names:
        await query.answer("❌ You don't have a Cartela.", show_alert=True)
        return

    for name in player.cartel_names:
        c = cartelas.get(name)
        if c and cartela_completed(c):
            await finish_game(context, c.name, user.id)
            return

    await query.answer("❌ NOT BINGO YET!", show_alert=True)


# ============================================================
# FINISH GAME
# ============================================================

async def finish_game(
    context: ContextTypes.DEFAULT_TYPE,
    winner_name: str,
    winner_user_id: Optional[int],
):
    async with game_lock:
        if not round_state.active:
            return

        round_state.active = False
        round_state.winner_cartela = winner_name
        round_state.winner_user_id = winner_user_id
        round_state.blinking = True

    winner = cartelas.get(winner_name)
    prize = DEFAULT_PRIZE

    if winner and winner.owner_id is not None:
        player = players.get(winner.owner_id)
        if player:
            player.balance += prize

    # Blinking celebration
    for i in range(4):
        await broadcast(
            context,
            (
                "🟩🟩🟩 **BINGO WINNER** 🟩🟩🟩\n\n"
                f"🏆 **Winning Cartela No: {winner_name}**\n\n"
                f"🎉 **GOOD BINGO!**\n\n"
                f"🔢 Winning numbers:\n"
                f"**{', '.join(map(str, winner.numbers if winner else []))}**"
            )
        )

        await asyncio.sleep(0.5)

        await broadcast(
            context,
            (
                "🏆 **BINGO WINNER**\n\n"
                f"🎫 Winning Cartela No: **{winner_name}**\n\n"
                f"🔢 Drawn numbers:\n"
                f"**{', '.join(map(str, round_state.drawn_numbers))}**"
            )
        )

        await asyncio.sleep(0.5)

    # Final result
    if winner and winner.owner_id is not None:
        player = players.get(winner.owner_id)
        if player:
            result = (
                "🎉 **GOOD BINGO!** 🎉\n\n"
                f"🏆 Winning Cartela No: **{winner_name}**\n\n"
                f"💰 Prize: **{prize} Demo Birr**\n"
                f"💳 New Balance: **{player.balance} Demo Birr**\n\n"
                "🔎 You can compare the winning Cartela "
                "with all drawn numbers."
            )
        else:
            result = (
                "🎉 **GOOD BINGO!** 🎉\n\n"
                f"🏆 Winning Cartela No: **{winner_name}**"
            )
    else:
        result = (
            "🤖 **BOT WON THE GAME**\n\n"
            f"🏆 Winning Cartela No: **{winner_name}**\n\n"
            "❌ Your Cartela did not win this round.\n\n"
            "🔎 You can compare the winning Cartela "
            "with the drawn numbers."
        )

    await broadcast(context, result)
    await prepare_next_round(context)


# ============================================================
# FINISH WITHOUT WINNER
# ============================================================

async def finish_without_winner(context: ContextTypes.DEFAULT_TYPE):
    round_state.active = False

    await broadcast(
        context,
        "🛑 **GAME FINISHED**\n\n"
        "No Cartela completed Bingo.\n\n"
        f"🔢 All drawn numbers:\n"
        f"**{', '.join(map(str, round_state.drawn_numbers))}**"
    )

    await prepare_next_round(context)


# ============================================================
# NEXT ROUND
# ============================================================

async def prepare_next_round(context: ContextTypes.DEFAULT_TYPE):
    # Reset
    for player in players.values():
        player.cartel_names.clear()

    reset_cartela_owners()
    generate_cartelas()

    round_state.auto_mode_locked = False
    round_state.round_auto_mode = AUTO_BINGO
    round_state.drawn_numbers.clear()
    round_state.drawn_set.clear()
    round_state.winner_cartela = None
    round_state.winner_user_id = None
    round_state.blinking = False
    round_state.game_task = None

    await broadcast(
        context,
        "🔄 **NEW ROUND READY!**\n\n"
        "🎫 Cartelas A–T have been regenerated with new random numbers.\n\n"
        f"🤖 Current Auto Bingo setting: **{'ON' if AUTO_BINGO else 'OFF'}**\n\n"
        "Players can choose Cartelas for the next game."
    )


# ============================================================
# MY CARTELAS
# ============================================================

async def my_cartelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    player = get_player(user.id, user.full_name)

    text = (
        "🎫 **MY CARTELAS**\n\n"
        f"👤 {player.name}\n"
        f"💰 Balance: **{player.balance} Demo Birr**\n\n"
    )

    if not player.cartel_names:
        text += "You have no Cartela yet."
    else:
        for name in player.cartel_names:
            c = cartelas.get(name)
            if c:
                text += (
                    f"🟢 **Cartela {name}**\n"
                    f"🔢 {', '.join(map(str, c.numbers))}\n\n"
                )

    keyboard = [
        [InlineKeyboardButton("🎫 CHOOSE CARTELA", callback_data="cartelas")],
        [InlineKeyboardButton("🔙 MENU", callback_data="back")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# BALANCE
# ============================================================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    player = get_player(user.id, user.full_name)

    text = (
        "💰 **DEMO BALANCE**\n\n"
        f"👤 {player.name}\n"
        f"💳 Balance: **{player.balance} Demo Birr**\n\n"
        "🎁 New player bonus: 10 Demo Birr\n"
        "🎫 Cartela price: 5 Demo Birr\n"
        "🏆 Bingo prize: 100 Demo Birr\n\n"
        "⚠️ This balance has no real monetary value."
    )

    keyboard = [
        [InlineKeyboardButton("🎮 PLAY", callback_data="play")],
        [InlineKeyboardButton("🔙 MENU", callback_data="back")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# RULES
# ============================================================

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "📜 **ZENBABA BINGO GAME RULES**\n\n"
        "1️⃣ There are 20 Cartelas: **A–T**.\n\n"
        "2️⃣ Every Cartela contains 5 random numbers from 1–100.\n\n"
        "3️⃣ One player can hold up to **5 Cartelas**.\n\n"
        "4️⃣ Each Cartela costs **5 Demo Birr**.\n\n"
        "5️⃣ When at least one Cartela is registered, a "
        "**30-second countdown** starts.\n\n"
        "6️⃣ At game start, unused Cartelas are controlled by the Bot.\n\n"
        "7️⃣ Numbers are drawn randomly from 1–100 every "
        "**2 seconds**.\n\n"
        "8️⃣ The first completed Cartela wins.\n\n"
        "9️⃣ **AUTO ON:** the Bot automatically detects Bingo.\n\n"
        "🔟 **AUTO OFF:** the player can press Bingo manually.\n\n"
        "🔒 Auto mode is locked when the round starts.\n\n"
        "🏆 Winner receives **100 Demo Birr**.\n\n"
        "⚠️ **DEMO ONLY:** all balances are fake/demo credits."
    )

    keyboard = InlineKeyboardButton("🔙 MENU", callback_data="back")

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🤖 AUTO {'ON' if AUTO_BINGO else 'OFF'}",
                callback_data="admin_toggle_auto",
            )
        ],
        [InlineKeyboardButton("📊 GAME STATUS", callback_data="admin_status")],
    ])


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Access denied.")
        return

    text = (
        "👑 **ZENBABA BINGO SUPER ADMIN**\n\n"
        f"🤖 Auto Bingo: **{'ON' if AUTO_BINGO else 'OFF'}**\n"
        f"🎮 Round Active: **{'YES' if round_state.active else 'NO'}**\n"
        f"🔒 Mode Locked: **{'YES' if round_state.auto_mode_locked else 'NO'}**\n\n"
    )

    if round_state.active:
        text += (
            f"🎯 Current Round Mode: "
            f"**{'AUTO ON' if round_state.round_auto_mode else 'AUTO OFF'}**\n"
        )
    else:
        text += "🎯 Current Round Mode: None\n"

    await update.message.reply_text(
        text,
        reply_markup=admin_keyboard(),
        parse_mode="Markdown",
    )


# ============================================================
# ADMIN TOGGLE
# ============================================================

async def admin_toggle_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_BINGO

    query = update.callback_query
    user = update.effective_user

    if user.id != SUPER_ADMIN_ID:
        await query.answer("❌ Super Admin only.", show_alert=True)
        return

    if round_state.active or round_state.auto_mode_locked:
        await query.answer(
            "🔒 Auto Mode is locked for the current round.",
            show_alert=True,
        )
        return

    AUTO_BINGO = not AUTO_BINGO

    await query.answer(
        f"Auto Bingo {'ON' if AUTO_BINGO else 'OFF'}",
        show_alert=True,
    )

    text = (
        "👑 **ZENBABA BINGO SUPER ADMIN**\n\n"
        f"🤖 Auto Bingo: **{'ON' if AUTO_BINGO else 'OFF'}**\n\n"
        "✅ This setting will be used for the NEXT round.\n"
        "🔒 Once a round starts, the mode becomes locked."
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_keyboard(),
        parse_mode="Markdown",
    )


# ============================================================
# ADMIN STATUS
# ============================================================

async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if user.id != SUPER_ADMIN_ID:
        await query.answer("❌ Super Admin only.", show_alert=True)
        return

    player_count = sum(
        1 for c in cartelas.values() if c.owner_id is not None
    )

    text = (
        "📊 **GAME STATUS**\n\n"
        f"🎮 Active: **{'YES' if round_state.active else 'NO'}**\n"
        f"👥 Player Cartelas: **{player_count}**\n"
        f"🔢 Numbers Drawn: **{len(round_state.drawn_numbers)}**\n"
        f"🤖 Global Auto: **{'ON' if AUTO_BINGO else 'OFF'}**\n"
        f"🔒 Mode Locked: **{'YES' if round_state.auto_mode_locked else 'NO'}**\n"
    )

    if round_state.active:
        text += (
            f"🎯 Round Auto: "
            f"**{'ON' if round_state.round_auto_mode else 'OFF'}**\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ADMIN", callback_data="admin_back")]
        ]),
        parse_mode="Markdown",
    )


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "👑 **ZENBABA BINGO SUPER ADMIN**\n\n"
        f"🤖 Auto Bingo: **{'ON' if AUTO_BINGO else 'OFF'}**\n"
        f"🎮 Round Active: **{'YES' if round_state.active else 'NO'}**\n"
        f"🔒 Mode Locked: **{'YES' if round_state.auto_mode_locked else 'NO'}**"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_keyboard(),
        parse_mode="Markdown",
    )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a message to all known players."""
    for user_id in list(players.keys()):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Broadcast failed for %s: %s", user_id, e)


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "play":
        await play_game(update, context)
    elif data == "cartelas":
        await show_cartelas(update, context)
    elif data.startswith("select_"):
        await select_cartela(update, context)
    elif data.startswith("register_"):
        await register_cartela(update, context)
    elif data == "my_cartelas":
        await my_cartelas(update, context)
    elif data == "balance":
        await balance(update, context)
    elif data == "rules":
        await rules(update, context)
    elif data == "manual_bingo":
        await manual_bingo(update, context)
    elif data == "admin_toggle_auto":
        await admin_toggle_auto(update, context)
    elif data == "admin_status":
        await admin_status(update, context)
    elif data == "admin_back":
        await admin_back(update, context)
    elif data == "back":
        await query.answer()
        user = update.effective_user
        player = get_player(user.id, user.full_name)
        await query.edit_message_text(
            "🎉 **ZENBABA BINGO**\n\n"
            f"💰 Demo Balance: **{player.balance} Birr**\n\n"
            "Choose an option:",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )
    else:
        await query.answer()


# ============================================================
# HEALTH SERVER (Render Port Binding)
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ZENBABA BINGO is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"ZENBABA BINGO health server listening on port {port}")
    return server


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Exception while handling update:", exc_info=context.error)


# ============================================================
# MAIN
# ============================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_error_handler(error_handler)

    logger.info("ZENBABA BINGO is starting...")

    # Start health server FIRST so Render detects the port
    start_health_server()

    # Then start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
