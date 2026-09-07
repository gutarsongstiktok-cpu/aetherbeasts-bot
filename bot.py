import os
import random
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from html import escape
import hashlib
import hmac
import json

import psycopg
from psycopg.rows import dict_row

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Update, MenuButtonWebApp, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles


# ============================================================
# LOGGING / SETTINGS
# ============================================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aetherbeasts")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

START_AETHER = 100
SUMMON_COST = 25
MAX_LEVEL = 50
MAX_PET_LEVEL = 30
XP_SUMMON = 25
XP_MERGE = 100
XP_BATTLE_WIN = 60
XP_BATTLE_LOSS = 20
BATTLE_COOLDOWN_SECONDS = 35

# ============================================================
# PETS / GAME DATA
# ============================================================

PETS = {
    "Common": ["Ember Drake", "Frost Wolf", "Thunder Lynx", "Mystic Serpent", "Stone Golem"],
    "Uncommon": ["Flame Raptor", "Ice Panther", "Storm Hawk", "Shadow Viper"],
    "Rare": ["Inferno Dragon", "Frost Wyvern", "Thunder Beast", "Void Serpent"],
    "Epic": ["Ancient Phoenix", "Celestial Wolf", "Shadow Dragon"],
    "Legendary": ["Aether Dragon", "Eternal Phoenix"],
}

EVOLUTION_CHAINS = {
    "Ember Drake": {"Uncommon": "Flame Raptor", "Rare": "Inferno Dragon", "Epic": "Ancient Phoenix", "Legendary": "Aether Dragon"},
    "Flame Raptor": {"Rare": "Inferno Dragon", "Epic": "Ancient Phoenix", "Legendary": "Aether Dragon"},
    "Inferno Dragon": {"Epic": "Ancient Phoenix", "Legendary": "Aether Dragon"},
    "Ancient Phoenix": {"Legendary": "Aether Dragon"},
    "Frost Wolf": {"Uncommon": "Ice Panther", "Rare": "Frost Wyvern", "Epic": "Celestial Wolf", "Legendary": "Eternal Phoenix"},
    "Ice Panther": {"Rare": "Frost Wyvern", "Epic": "Celestial Wolf", "Legendary": "Eternal Phoenix"},
    "Frost Wyvern": {"Epic": "Celestial Wolf", "Legendary": "Eternal Phoenix"},
    "Celestial Wolf": {"Legendary": "Eternal Phoenix"},
    "Thunder Lynx": {"Uncommon": "Storm Hawk", "Rare": "Thunder Beast", "Epic": "Shadow Dragon", "Legendary": "Aether Dragon"},
    "Storm Hawk": {"Rare": "Thunder Beast", "Epic": "Shadow Dragon", "Legendary": "Aether Dragon"},
    "Thunder Beast": {"Epic": "Shadow Dragon", "Legendary": "Aether Dragon"},
    "Shadow Dragon": {"Legendary": "Aether Dragon"},
    "Mystic Serpent": {"Uncommon": "Shadow Viper", "Rare": "Void Serpent", "Epic": "Shadow Dragon", "Legendary": "Eternal Phoenix"},
    "Shadow Viper": {"Rare": "Void Serpent", "Epic": "Shadow Dragon", "Legendary": "Eternal Phoenix"},
    "Void Serpent": {"Epic": "Shadow Dragon", "Legendary": "Eternal Phoenix"},
    "Stone Golem": {"Uncommon": "Flame Raptor", "Rare": "Thunder Beast", "Epic": "Celestial Wolf", "Legendary": "Aether Dragon"},
}

RARITY_CHANCES = {"Common": 60, "Uncommon": 25, "Rare": 10, "Epic": 4, "Legendary": 1}
RARITY_EMOJI = {"Common": "⚪", "Uncommon": "🟢", "Rare": "🔵", "Epic": "🟣", "Legendary": "🟡"}
RARITY_POWER = {"Common": (10, 30), "Uncommon": (30, 60), "Rare": (60, 120), "Epic": (120, 220), "Legendary": (220, 400)}
RARITY_ORDER = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
ELEMENTS = {"Ember": "🔥", "Frost": "❄️", "Storm": "⚡", "Shadow": "🌑", "Stone": "🪨", "Aether": "✨"}

SHOP_ITEMS = {
    "lucky_charm": {"name": "🍀 Lucky Charm", "price": 80, "description": "+15% Legendary/⭐ шанс на следующий призыв", "kind": "consumable"},
    "xp_potion": {"name": "🧪 XP Potion", "price": 60, "description": "+100 XP игроку", "kind": "consumable"},
    "aether_crystal": {"name": "💎 Aether Crystal", "price": 120, "description": "+200 AETHER", "kind": "consumable"},
    "battle_elixir": {"name": "⚔️ Battle Elixir", "price": 100, "description": "+10% силы в следующем PvE-бою", "kind": "consumable"},
}

DAILY_REWARDS = [50, 75, 100, 125, 150, 200, 300]

ACHIEVEMENTS = [
    ("first_pet", "🐣 Первое существо", "Получить первое существо", 75, "aether"),
    ("collector_5", "🗃 Коллекционер", "Собрать 5 существ", 150, "aether"),
    ("collector_15", "🏛 Хранитель", "Собрать 15 существ", 300, "aether"),
    ("summon_10", "✨ Призыватель", "Сделать 10 призывов", 200, "aether"),
    ("merge_5", "🧬 Алхимик", "Сделать 5 merge", 300, "aether"),
    ("battle_10", "⚔️ Гладиатор", "Победить 10 боёв", 400, "aether"),
    ("battle_50", "👑 Чемпион", "Победить 50 боёв", 1000, "aether"),
    ("level_10", "⭐ Ветеран", "Достичь 10 уровня", 250, "aether"),
    ("legendary", "🌟 Легенда", "Получить Legendary", 1000, "aether"),
]

# ============================================================
# BOT / FASTAPI
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def db_one(sql, params=()):
    with get_db() as db:
        return db.execute(sql, params).fetchone()


def db_all(sql, params=()):
    with get_db() as db:
        return db.execute(sql, params).fetchall()


def db_exec(sql, params=()):
    with get_db() as db:
        db.execute(sql, params)


def init_db():
    # Existing players/pets are kept. Missing columns are added safely.
    statements = [
        """
        CREATE TABLE IF NOT EXISTS players (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            aether BIGINT NOT NULL DEFAULT 100,
            level INTEGER NOT NULL DEFAULT 1,
            xp INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            summons INTEGER NOT NULL DEFAULT 0,
            merges INTEGER NOT NULL DEFAULT 0,
            battles INTEGER NOT NULL DEFAULT 0,
            last_battle_at TIMESTAMPTZ,
            daily_date DATE,
            daily_streak INTEGER NOT NULL DEFAULT 0,
            quest_date DATE,
            quest_summons INTEGER NOT NULL DEFAULT 0,
            quest_wins INTEGER NOT NULL DEFAULT 0,
            quest_merges INTEGER NOT NULL DEFAULT 0,
            referral_code TEXT UNIQUE,
            referrer_id BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pets (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            power INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            xp INTEGER NOT NULL DEFAULT 0,
            attack INTEGER NOT NULL DEFAULT 0,
            defense INTEGER NOT NULL DEFAULT 0,
            hp INTEGER NOT NULL DEFAULT 0,
            element TEXT NOT NULL DEFAULT 'Aether',
            evolution_stage INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_pets_user_id ON pets(user_id)",
        """
        CREATE TABLE IF NOT EXISTS inventory (
            user_id BIGINT NOT NULL,
            item_key TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, item_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS achievements (
            user_id BIGINT NOT NULL,
            code TEXT NOT NULL,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(user_id, code)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT PRIMARY KEY,
            reward_given BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clan_members (
            clan_id BIGINT NOT NULL,
            user_id BIGINT PRIMARY KEY,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS clans (
            id BIGSERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            owner_id BIGINT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            xp INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS battle_logs (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            won BOOLEAN NOT NULL,
            player_power INTEGER NOT NULL,
            enemy_power INTEGER NOT NULL,
            reward BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]
    with get_db() as db:
        for sql in statements:
            db.execute(sql)
        # Migrate old versions from the original bot.
        existing = {r["column_name"] for r in db.execute("""
            SELECT column_name FROM information_schema.columns WHERE table_name='players'
        """).fetchall()}
        player_cols = {
            "wins": "INTEGER NOT NULL DEFAULT 0", "losses": "INTEGER NOT NULL DEFAULT 0",
            "summons": "INTEGER NOT NULL DEFAULT 0", "merges": "INTEGER NOT NULL DEFAULT 0",
            "battles": "INTEGER NOT NULL DEFAULT 0", "last_battle_at": "TIMESTAMPTZ",
            "daily_date": "DATE", "daily_streak": "INTEGER NOT NULL DEFAULT 0",
            "referral_code": "TEXT", "referrer_id": "BIGINT",
            "quest_date": "DATE", "quest_summons": "INTEGER NOT NULL DEFAULT 0",
            "quest_wins": "INTEGER NOT NULL DEFAULT 0", "quest_merges": "INTEGER NOT NULL DEFAULT 0",
        }
        for col, typ in player_cols.items():
            if col not in existing:
                db.execute(f"ALTER TABLE players ADD COLUMN {col} {typ}")
        # Existing pets get derived combat stats without losing data.
        pet_existing = {r["column_name"] for r in db.execute("""
            SELECT column_name FROM information_schema.columns WHERE table_name='pets'
        """).fetchall()}
        pet_cols = {
            "level": "INTEGER NOT NULL DEFAULT 1", "xp": "INTEGER NOT NULL DEFAULT 0",
            "attack": "INTEGER NOT NULL DEFAULT 0", "defense": "INTEGER NOT NULL DEFAULT 0",
            "hp": "INTEGER NOT NULL DEFAULT 0", "element": "TEXT NOT NULL DEFAULT 'Aether'",
            "evolution_stage": "INTEGER NOT NULL DEFAULT 1", "favorite": "BOOLEAN NOT NULL DEFAULT FALSE",
        }
        for col, typ in pet_cols.items():
            if col not in pet_existing:
                db.execute(f"ALTER TABLE pets ADD COLUMN {col} {typ}")
        db.execute("UPDATE players SET referral_code = COALESCE(referral_code, user_id::text) WHERE referral_code IS NULL")
        db.execute("ALTER TABLE players DROP CONSTRAINT IF EXISTS players_referral_code_key")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_players_referral_code ON players(referral_code)")
        db.execute("UPDATE pets SET attack = CASE WHEN attack=0 THEN GREATEST(1, power/2) ELSE attack END")
        db.execute("UPDATE pets SET defense = CASE WHEN defense=0 THEN GREATEST(1, power/4) ELSE defense END")
        db.execute("UPDATE pets SET hp = CASE WHEN hp=0 THEN GREATEST(10, power*2) ELSE hp END")


def now_utc():
    return datetime.now(timezone.utc)


def today_utc():
    return now_utc().date()


def ensure_player(user_id: int, username: str | None = None):
    username = username or ""
    with get_db() as db:
        p = db.execute("SELECT * FROM players WHERE user_id=%s", (user_id,)).fetchone()
        if not p:
            db.execute("INSERT INTO players(user_id, username, referral_code) VALUES(%s,%s,%s)", (user_id, username, str(user_id)))
            return db.execute("SELECT * FROM players WHERE user_id=%s", (user_id,)).fetchone()
        if username and p["username"] != username:
            db.execute("UPDATE players SET username=%s WHERE user_id=%s", (username, user_id))
            p["username"] = username
        return p


def xp_needed(level: int):
    if level >= MAX_LEVEL:
        return 0
    return 100 + (level - 1) * 75


def level_reward(level: int):
    return 50 + (level - 1) * 25


def add_player_xp(user_id: int, amount: int):
    level_ups = []
    with get_db() as db:
        row = db.execute("SELECT level,xp FROM players WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
        if not row or row["level"] >= MAX_LEVEL:
            return level_ups
        level, xp = row["level"], row["xp"] + amount
        while level < MAX_LEVEL and xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            reward = level_reward(level)
            db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s", (reward, user_id))
            level_ups.append((level, reward))
        if level >= MAX_LEVEL:
            xp = 0
        db.execute("UPDATE players SET level=%s,xp=%s WHERE user_id=%s", (level, xp, user_id))
    return level_ups


def random_element(name: str):
    n = name.lower()
    if any(x in n for x in ("ember", "flame", "inferno")):
        return "Ember"
    if any(x in n for x in ("frost", "ice")):
        return "Frost"
    if any(x in n for x in ("thunder", "storm")):
        return "Storm"
    if any(x in n for x in ("shadow", "void")):
        return "Shadow"
    if "stone" in n:
        return "Stone"
    return random.choice(list(ELEMENTS))


def generate_pet(rarity=None, name=None, power=None):
    rarity = rarity or random.choices(list(RARITY_CHANCES), weights=list(RARITY_CHANCES.values()))[0]
    name = name or random.choice(PETS[rarity])
    if power is None:
        lo, hi = RARITY_POWER[rarity]
        power = random.randint(lo, hi)
    attack = max(2, int(power * random.uniform(0.40, 0.58)))
    defense = max(2, int(power * random.uniform(0.20, 0.36)))
    hp = max(20, power * 2 + random.randint(0, power))
    return {"name": name, "rarity": rarity, "power": power, "attack": attack, "defense": defense, "hp": hp, "element": random_element(name)}


def save_pet(user_id, pet):
    with get_db() as db:
        db.execute("""
            INSERT INTO pets(user_id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage)
            VALUES(%s,%s,%s,%s,1,0,%s,%s,%s,%s,1)
        """, (user_id, pet["name"], pet["rarity"], pet["power"], pet["attack"], pet["defense"], pet["hp"], pet["element"]))


def get_aether(user_id):
    row = db_one("SELECT aether FROM players WHERE user_id=%s", (user_id,))
    return row["aether"] if row else 0


def reward_activity(user_id, activity: str):
    with get_db() as db:
        if activity == "summon":
            db.execute("UPDATE players SET summons=summons+1 WHERE user_id=%s", (user_id,))
        elif activity == "merge":
            db.execute("UPDATE players SET merges=merges+1 WHERE user_id=%s", (user_id,))
        elif activity == "battle":
            db.execute("UPDATE players SET battles=battles+1 WHERE user_id=%s", (user_id,))


def add_item(user_id, item_key, qty=1):
    with get_db() as db:
        db.execute("""
            INSERT INTO inventory(user_id,item_key,qty) VALUES(%s,%s,%s)
            ON CONFLICT(user_id,item_key) DO UPDATE SET qty=inventory.qty+EXCLUDED.qty
        """, (user_id, item_key, qty))


def item_qty(user_id, item_key):
    row = db_one("SELECT qty FROM inventory WHERE user_id=%s AND item_key=%s", (user_id, item_key))
    return row["qty"] if row else 0


def use_item(user_id, item_key):
    with get_db() as db:
        row = db.execute("SELECT qty FROM inventory WHERE user_id=%s AND item_key=%s FOR UPDATE", (user_id, item_key)).fetchone()
        if not row or row["qty"] <= 0:
            return False
        db.execute("UPDATE inventory SET qty=qty-1 WHERE user_id=%s AND item_key=%s", (user_id, item_key))
        return True


def achievement_progress(user_id):
    row = db_one("SELECT * FROM players WHERE user_id=%s", (user_id,))
    pet_count = db_one("SELECT COUNT(*) AS c FROM pets WHERE user_id=%s", (user_id,))["c"] if row else 0
    has_legendary = db_one("SELECT 1 FROM pets WHERE user_id=%s AND rarity='Legendary' LIMIT 1", (user_id,)) is not None
    return row, int(pet_count), has_legendary


def achievement_met(code, player, pet_count, has_legendary):
    conditions = {
        "first_pet": pet_count >= 1,
        "collector_5": pet_count >= 5,
        "collector_15": pet_count >= 15,
        "summon_10": player["summons"] >= 10,
        "merge_5": player["merges"] >= 5,
        "battle_10": player["wins"] >= 10,
        "battle_50": player["wins"] >= 50,
        "level_10": player["level"] >= 10,
        "legendary": has_legendary,
    }
    return conditions.get(code, False)


def check_achievements(user_id):
    player, count, legendary = achievement_progress(user_id)
    unlocked = []
    with get_db() as db:
        for code, title, desc, reward, kind in ACHIEVEMENTS:
            if achievement_met(code, player, count, legendary):
                exists = db.execute("SELECT 1 FROM achievements WHERE user_id=%s AND code=%s", (user_id, code)).fetchone()
                if not exists:
                    db.execute("INSERT INTO achievements(user_id,code) VALUES(%s,%s)", (user_id, code))
                    db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s", (reward, user_id))
                    unlocked.append((title, reward))
    return unlocked


def daily_status(user_id):
    p = db_one("SELECT daily_date,daily_streak FROM players WHERE user_id=%s", (user_id,))
    if not p:
        return 0, True
    if p["daily_date"] == today_utc():
        return p["daily_streak"], False
    return p["daily_streak"], True


def clan_for_user(user_id):
    return db_one("""
        SELECT c.* FROM clans c JOIN clan_members m ON m.clan_id=c.id WHERE m.user_id=%s
    """, (user_id,))


# ============================================================
# KEYBOARDS
# ============================================================

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐉 Мои существа", callback_data="pets:0")],
        [InlineKeyboardButton(text="✨ Призвать", callback_data="summon"), InlineKeyboardButton(text="🧬 MERGE LAB", callback_data="merge")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="⚔️ В бой", callback_data="battle")],
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory"), InlineKeyboardButton(text="🏪 Магазин", callback_data="market")],
        [InlineKeyboardButton(text="🎁 Ежедневная награда", callback_data="daily"), InlineKeyboardButton(text="📜 Квесты", callback_data="quests")],
        [InlineKeyboardButton(text="🏆 Достижения", callback_data="achievements"), InlineKeyboardButton(text="🏅 Рейтинг", callback_data="top")],
        [InlineKeyboardButton(text="🏰 Клан", callback_data="clan"), InlineKeyboardButton(text="🤝 Рефералы", callback_data="ref")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
    ])


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]])


def pets_kb(pets, page=0):
    buttons = []
    for pet in pets:
        buttons.append([InlineKeyboardButton(text=f"{RARITY_EMOJI[pet['rarity']]} {pet['name']} • ⚡{pet['power']}", callback_data=f"pet:{pet['id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pets:{page-1}"))
    if len(pets) == 8:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pets:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🧬 Merge Lab", callback_data="merge"), InlineKeyboardButton(text="🏠", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
# TEXT VIEWS
# ============================================================

async def edit_or_answer(target, text, reply_markup=None):
    if isinstance(target, Message):
        return await target.answer(text, reply_markup=reply_markup)
    try:
        return await target.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        return await target.message.answer(text, reply_markup=reply_markup)


async def show_home_message(message):
    p = ensure_player(message.from_user.id, message.from_user.username)
    await message.answer(
        "🐉 <b>AetherBeasts</b>\n\n"
        "Добро пожаловать в мир мифических существ!\n\n"
        f"⭐ Уровень: <b>{p['level']}</b> / {MAX_LEVEL}\n"
        f"✨ XP: <b>{p['xp']}</b>\n"
        f"💰 AETHER: <b>{p['aether']}</b>\n\n"
        "Собирай, развивай, объединяй и сражайся.",
        reply_markup=main_kb(),
    )


async def show_profile(message):
    user_id = message.from_user.id
    p = ensure_player(user_id, message.from_user.username)
    stats = db_one("SELECT COUNT(*) AS count, COALESCE(SUM(power),0) AS power FROM pets WHERE user_id=%s", (user_id,))
    clan = clan_for_user(user_id)
    if p["level"] >= MAX_LEVEL:
        xp_text = "MAX"
        progress = 100
    else:
        need = xp_needed(p["level"])
        progress = min(100, int(p["xp"] / need * 100))
        xp_text = f"{p['xp']} / {need}"
    clan_text = clan["name"] if clan else "Нет клана"
    text = (
        "👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🆔 <code>{user_id}</code>\n"
        f"👤 @{escape(p['username']) if p['username'] else 'без_username'}\n"
        f"💰 AETHER: <b>{p['aether']}</b>\n"
        f"⭐ Уровень: <b>{p['level']}</b> / {MAX_LEVEL}\n"
        f"✨ XP: <b>{xp_text}</b>\n"
        f"📊 {progress}%\n\n"
        f"🐉 Существ: <b>{stats['count']}</b>\n"
        f"⚡ Суммарная сила: <b>{stats['power']}</b>\n"
        f"⚔️ Победы/поражения: <b>{p['wins']}</b>/<b>{p['losses']}</b>\n"
        f"🎯 Призывы: <b>{p['summons']}</b>\n"
        f"🧬 Merge: <b>{p['merges']}</b>\n"
        f"🏰 Клан: <b>{escape(clan_text)}</b>"
    )
    await edit_or_answer(message, text, back_kb())


async def show_pets(message, page=0):
    user_id = message.from_user.id
    offset = page * 8
    pets = db_all("SELECT * FROM pets WHERE user_id=%s ORDER BY power DESC, id DESC LIMIT 8 OFFSET %s", (user_id, offset))
    total = db_one("SELECT COUNT(*) AS c FROM pets WHERE user_id=%s", (user_id,))["c"]
    if not pets:
        text = "🐉 <b>МОИ СУЩЕСТВА</b>\n\nУ тебя пока нет существ. Нажми ✨ Призвать."
        await edit_or_answer(message, text, back_kb())
        return
    lines = [f"🐉 <b>МОИ СУЩЕСТВА</b> • {total}\n"]
    for i, pet in enumerate(pets, offset + 1):
        lines.append(
            f"{i}. {RARITY_EMOJI[pet['rarity']]} <b>{escape(pet['name'])}</b>\n"
            f"   {ELEMENTS.get(pet['element'],'✨')} {pet['element']} • ⭐ Lv.{pet['level']} • ⚡ {pet['power']}"
        )
    await edit_or_answer(message, "\n".join(lines), pets_kb(pets, page))


async def show_pet_detail(callback, pet_id):
    pet = db_one("SELECT * FROM pets WHERE id=%s AND user_id=%s", (pet_id, callback.from_user.id))
    if not pet:
        await callback.answer("Существо не найдено", show_alert=True)
        return
    need = min(1000, 100 + pet["level"] * 50)
    text = (
        f"{RARITY_EMOJI[pet['rarity']]} <b>{escape(pet['name'])}</b>\n\n"
        f"⭐ Редкость: <b>{pet['rarity']}</b>\n"
        f"{ELEMENTS.get(pet['element'],'✨')} Элемент: <b>{pet['element']}</b>\n"
        f"⚡ Сила: <b>{pet['power']}</b>\n"
        f"⚔️ Атака: <b>{pet['attack']}</b>\n"
        f"🛡 Защита: <b>{pet['defense']}</b>\n"
        f"❤️ HP: <b>{pet['hp']}</b>\n"
        f"📈 Уровень: <b>{pet['level']}</b> / {MAX_PET_LEVEL}\n"
        f"✨ XP: <b>{pet['xp']}</b> / {need}\n\n"
        "Тренировка: <b>50 AETHER</b> за +25 XP зверю."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬆️ Тренировать (50)", callback_data=f"train:{pet_id}")],
        [InlineKeyboardButton(text="🐉 К коллекции", callback_data="pets:0"), InlineKeyboardButton(text="🏠", callback_data="home")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)


# ============================================================
# START / HELP / MENU
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    user = message.from_user
    existing = db_one("SELECT 1 FROM players WHERE user_id=%s", (user.id,))
    p = ensure_player(user.id, user.username)
    args = message.text.split(maxsplit=1)
    ref = args[1] if len(args) > 1 else ""
    if not existing and ref.startswith("ref_"):
        try:
            referrer_id = int(ref[4:])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user.id and db_one("SELECT 1 FROM players WHERE user_id=%s", (referrer_id,)):
            with get_db() as db:
                db.execute("UPDATE players SET referrer_id=%s WHERE user_id=%s", (referrer_id, user.id))
                db.execute("INSERT INTO referrals(referrer_id,referred_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (referrer_id, user.id))
    count = db_one("SELECT COUNT(*) AS c FROM pets WHERE user_id=%s", (user.id,))["c"]
    if count == 0:
        pet = generate_pet()
        save_pet(user.id, pet)
        fresh = db_one("SELECT referrer_id FROM players WHERE user_id=%s", (user.id,))
        if fresh and fresh["referrer_id"]:
            with get_db() as db:
                db.execute("UPDATE players SET aether=aether+100 WHERE user_id=%s", (fresh["referrer_id"],))
                db.execute("UPDATE referrals SET reward_given=TRUE WHERE referred_id=%s", (user.id,))
        text = (
            "🐉 <b>Добро пожаловать в AetherBeasts!</b>\n\n"
            "🎁 Твоё стартовое существо:\n\n"
            f"{RARITY_EMOJI[pet['rarity']]} <b>{pet['name']}</b>\n"
            f"⭐ {pet['rarity']} • ⚡ {pet['power']} • {ELEMENTS.get(pet['element'],'✨')} {pet['element']}\n\n"
            "Дальше: призывай зверей, объединяй одинаковых, прокачивай их и побеждай на арене."
        )
    else:
        text = (
            "🐉 <b>AetherBeasts</b>\n\n"
            f"С возвращением! ⭐ Уровень <b>{p['level']}</b>\n"
            f"💰 AETHER: <b>{p['aether']}</b>\n\n"
            "Продолжай развивать коллекцию."
        )
    await message.answer(text, reply_markup=main_kb())


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "ℹ️ <b>AetherBeasts — команды</b>\n\n"
        "/start — начать игру\n"
        "/profile — профиль\n"
        "/pets — коллекция\n"
        "/daily — ежедневная награда\n"
        "/quests — квесты\n"
        "/inventory — инвентарь\n"
        "/shop — магазин\n"
        "/top — рейтинг\n"
        "/ref — реферальная ссылка\n"
        "/clan — клан\n"
        "/clan_create Название — создать клан\n"
        "/clan_join ID — вступить в клан\n\n"
        "💡 Всё остальное доступно через кнопки.", reply_markup=main_kb())


# ============================================================
# DAILY / QUESTS / ACHIEVEMENTS
# ============================================================

async def show_daily(target):
    uid = target.from_user.id
    streak, can = daily_status(uid)
    if not can:
        await edit_or_answer(target, f"🎁 <b>ЕЖЕДНЕВНАЯ НАГРАДА</b>\n\n✅ Сегодня уже получена.\n🔥 Серия: <b>{streak}</b> день", back_kb())
        return
    next_streak = streak + 1 if streak < 7 else 1
    reward = DAILY_REWARDS[next_streak - 1]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🎁 Забрать +{reward} AETHER", callback_data="daily_claim")], [InlineKeyboardButton(text="🏠", callback_data="home")]])
    await edit_or_answer(target, f"🎁 <b>ЕЖЕДНЕВНАЯ НАГРАДА</b>\n\n🔥 Текущая серия: <b>{streak}</b>\n🎯 Сегодня: <b>{reward} AETHER</b>\n\nНе пропускай день — серия идёт до 7.", kb)


@dp.callback_query(F.data == "daily")
async def daily_cb(c: CallbackQuery):
    await show_daily(c)
    await c.answer()


@dp.message(Command("daily"))
async def daily_cmd(m: Message):
    await show_daily(m)


@dp.callback_query(F.data == "daily_claim")
async def daily_claim(c: CallbackQuery):
    uid = c.from_user.id
    ensure_player(uid, c.from_user.username)
    today = today_utc()
    with get_db() as db:
        p = db.execute("SELECT daily_date,daily_streak FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if p["daily_date"] == today:
            await c.answer("Сегодня уже получено", show_alert=True)
            return
        if p["daily_date"] == today - timedelta(days=1):
            streak = min(7, p["daily_streak"] + 1)
        else:
            streak = 1
        reward = DAILY_REWARDS[streak - 1]
        db.execute("UPDATE players SET daily_date=%s,daily_streak=%s,aether=aether+%s WHERE user_id=%s", (today, streak, reward, uid))
    await c.message.edit_text(f"🎉 <b>НАГРАДА ПОЛУЧЕНА!</b>\n\n💰 +{reward} AETHER\n🔥 Серия: <b>{streak}</b>/7", reply_markup=main_kb())
    await c.answer("Награда получена! 🎁")


async def show_quests(target):
    p = ensure_player(target.from_user.id, target.from_user.username)
    today = today_utc()
    counters = {"summon": 0, "merge": 0, "battle": 0}
    if p["daily_date"] == today:
        # daily_date is also daily claim date; counters use rolling fields stored in inventory-like meta below.
        pass
    # Use three lightweight daily counters encoded as inventory keys.
    for k in counters:
        counters[k] = item_qty(target.from_user.id, f"daily_{k}_{today.isoformat()}")
    done = [counters["summon"] >= 3, counters["merge"] >= 1, counters["battle"] >= 2]
    claim_keys = [f"daily_claim_s_{today}", f"daily_claim_m_{today}", f"daily_claim_b_{today}"]
    rewards = [100, 150, 125]
    lines = ["📜 <b>ЕЖЕДНЕВНЫЕ КВЕСТЫ</b>\n"]
    buttons = []
    labels = [("✨", "3 призыва", 3), ("🧬", "1 merge", 1), ("⚔️", "2 победы", 2)]
    for i, ((icon, name, goal), ok) in enumerate(zip(labels, done)):
        key = ("summon", "merge", "battle")[i]
        progress = min(counters[key], goal)
        status = "✅" if ok else "⏳"
        lines.append(f"{icon} {name}: <b>{progress}/{goal}</b> — {status} — {rewards[i]} AETHER")
        if ok and item_qty(target.from_user.id, claim_keys[i]) == 0:
            buttons.append([InlineKeyboardButton(text=f"🎁 Забрать {rewards[i]}", callback_data=f"qclaim:{i}")])
    buttons.append([InlineKeyboardButton(text="🏠", callback_data="home")])
    await edit_or_answer(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "quests")
async def quests_cb(c: CallbackQuery):
    await show_quests(c)
    await c.answer()


@dp.message(Command("quests"))
async def quests_cmd(m: Message):
    await show_quests(m)


@dp.callback_query(F.data.startswith("qclaim:"))
async def qclaim_cb(c: CallbackQuery):
    uid = c.from_user.id
    idx = int(c.data.split(":")[1])
    today = today_utc()
    specs = [("summon", 3, 100), ("merge", 1, 150), ("battle", 2, 125)]
    k, goal, reward = specs[idx]
    counter = item_qty(uid, f"daily_{k}_{today.isoformat()}")
    claim_key = f"daily_claim_{['s','m','b'][idx]}_{today.isoformat()}"
    if counter < goal:
        await c.answer("Квест ещё не выполнен", show_alert=True)
        return
    if item_qty(uid, claim_key) > 0:
        await c.answer("Уже получено", show_alert=True)
        return
    add_item(uid, claim_key, 1)
    with get_db() as db:
        db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s", (reward, uid))
    await show_quests(c)
    await c.answer(f"+{reward} AETHER")


async def show_achievements(target):
    p, count, legendary = achievement_progress(target.from_user.id)
    claimed = {r["code"] for r in db_all("SELECT code FROM achievements WHERE user_id=%s", (target.from_user.id,))}
    lines = ["🏆 <b>ДОСТИЖЕНИЯ</b>\n"]
    for code, title, desc, reward, _ in ACHIEVEMENTS:
        status = "✅" if code in claimed else ("🟢" if achievement_met(code, p, count, legendary) else "🔒")
        lines.append(f"{status} <b>{title}</b> — {desc} (+{reward})")
    await edit_or_answer(target, "\n".join(lines), back_kb())


@dp.callback_query(F.data == "achievements")
async def achievements_cb(c: CallbackQuery):
    unlocked = check_achievements(c.from_user.id)
    if unlocked:
        note = "\n\n🎉 Новые достижения:\n" + "\n".join(f"{t} +{r} AETHER" for t, r in unlocked)
    else:
        note = ""
    await show_achievements(c)
    if note:
        await c.answer("Есть новые достижения! 🎉", show_alert=True)
    else:
        await c.answer()


# ============================================================
# SUMMON
# ============================================================

@dp.callback_query(F.data == "summon")
async def summon_cb(c: CallbackQuery):
    uid = c.from_user.id
    ensure_player(uid, c.from_user.username)
    charm = item_qty(uid, "lucky_charm")
    # Lucky Charm is consumed only if a summon succeeds.
    with get_db() as db:
        player = db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if player["aether"] < SUMMON_COST:
            await c.answer(f"Нужно {SUMMON_COST} AETHER. У тебя {player['aether']}.", show_alert=True)
            return
        db.execute("UPDATE players SET aether=aether-%s WHERE user_id=%s", (SUMMON_COST, uid))
    chances = dict(RARITY_CHANCES)
    if charm:
        chances["Legendary"] += 15
        chances["Epic"] += 5
        if not use_item(uid, "lucky_charm"):
            pass
    pet = generate_pet(rarity=random.choices(list(chances), weights=list(chances.values()))[0])
    save_pet(uid, pet)
    reward_activity(uid, "summon")
    # daily counter
    add_item(uid, f"daily_summon_{today_utc().isoformat()}", 1)
    # check first-pet / legendary achievements immediately
    ups = add_player_xp(uid, XP_SUMMON)
    unlocked = check_achievements(uid)
    balance = get_aether(uid)
    extra = ""
    if ups:
        extra += "\n\n🎉 <b>LEVEL UP!</b>\n" + "\n".join(f"⭐ {lvl} • +{reward} AETHER" for lvl, reward in ups)
    if unlocked:
        extra += "\n\n🏆 <b>Достижение!</b>\n" + "\n".join(f"{t} • +{r}" for t, r in unlocked)
    await c.message.edit_text(
        "✨ <b>НОВОЕ СУЩЕСТВО!</b>\n\n"
        f"{RARITY_EMOJI[pet['rarity']]} <b>{pet['name']}</b>\n"
        f"⭐ {pet['rarity']} • {ELEMENTS.get(pet['element'],'✨')} {pet['element']}\n"
        f"⚡ Сила: <b>{pet['power']}</b>\n"
        f"⚔️ {pet['attack']}  🛡 {pet['defense']}  ❤️ {pet['hp']}\n\n"
        f"💸 -{SUMMON_COST} AETHER\n✨ +{XP_SUMMON} XP\n💰 Баланс: <b>{balance}</b>"
        + extra,
        reply_markup=main_kb(),
    )
    await c.answer("Призыв успешен! ✨")


# ============================================================
# MERGE
# ============================================================

def get_merge_options(user_id):
    rows = db_all("""
        SELECT name,rarity,COUNT(*) AS count FROM pets WHERE user_id=%s
        GROUP BY name,rarity HAVING COUNT(*)>=3
    """, (user_id,))
    options = []
    for row in rows:
        rarity = row["rarity"]
        if rarity == "Legendary":
            continue
        idx = RARITY_ORDER.index(rarity)
        next_rarity = RARITY_ORDER[idx + 1]
        next_name = EVOLUTION_CHAINS.get(row["name"], {}).get(next_rarity) or PETS[next_rarity][0]
        options.append((row["name"], rarity, row["count"], next_rarity, next_name))
    options.sort(key=lambda x: RARITY_ORDER.index(x[1]))
    return options


@dp.callback_query(F.data == "merge")
async def merge_menu(c: CallbackQuery):
    opts = get_merge_options(c.from_user.id)
    if not opts:
        await c.message.edit_text("🧬 <b>MERGE LAB</b>\n\nСейчас нет тройки одинаковых существ.\n\nСобери 3 одинаковых зверя, чтобы поднять редкость.", reply_markup=back_kb())
        await c.answer()
        return
    lines = ["🧬 <b>MERGE LAB</b>\n\n3 одинаковых → следующий ранг:\n"]
    buttons = []
    for i, (name, rarity, count, next_rarity, next_name) in enumerate(opts):
        lines.append(f"{RARITY_EMOJI[rarity]} <b>{name}</b> ×{count} → {RARITY_EMOJI[next_rarity]} <b>{next_name}</b>")
        buttons.append([InlineKeyboardButton(text=f"🧬 {name} ×3 → {next_name}", callback_data=f"merge_do:{i}")])
    buttons.append([InlineKeyboardButton(text="🏠", callback_data="home")])
    await c.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await c.answer()


@dp.callback_query(F.data.startswith("merge_do:"))
async def merge_do(c: CallbackQuery):
    uid = c.from_user.id
    idx = int(c.data.split(":")[1])
    opts = get_merge_options(uid)
    if idx >= len(opts):
        await c.answer("Эта эволюция уже недоступна", show_alert=True)
        return
    name, rarity, _, next_rarity, next_name = opts[idx]
    with get_db() as db:
        selected = db.execute("""
            SELECT id,power,attack,defense,hp FROM pets WHERE user_id=%s AND name=%s AND rarity=%s
            ORDER BY power ASC LIMIT 3 FOR UPDATE
        """, (uid, name, rarity)).fetchall()
        if len(selected) < 3:
            await c.answer("Нужно 3 одинаковых существа", show_alert=True)
            return
        ids = [x["id"] for x in selected]
        base = sum(x["power"] for x in selected)
        lo, hi = RARITY_POWER[next_rarity]
        new_power = max(lo, min(hi, int(base * random.uniform(0.75, 1.02))))
        db.execute("DELETE FROM pets WHERE id = ANY(%s)", (ids,))
        pet = generate_pet(next_rarity, next_name, new_power)
        db.execute("""
            INSERT INTO pets(user_id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage)
            VALUES(%s,%s,%s,%s,1,0,%s,%s,%s,%s,1)
        """, (uid, pet["name"], pet["rarity"], pet["power"], pet["attack"], pet["defense"], pet["hp"], pet["element"]))
    reward_activity(uid, "merge")
    add_item(uid, f"daily_merge_{today_utc().isoformat()}", 1)
    ups = add_player_xp(uid, XP_MERGE)
    unlocked = check_achievements(uid)
    extra = ""
    if ups:
        extra += "\n🎉 <b>Уровень повышен!</b> " + ", ".join(str(x[0]) for x in ups)
    if unlocked:
        extra += "\n🏆 Новое достижение!"
    await c.message.edit_text(
        "🧬 <b>ЭВОЛЮЦИЯ ЗАВЕРШЕНА!</b>\n\n"
        f"{RARITY_EMOJI[rarity]} {name} ×3\n⬇️\n"
        f"{RARITY_EMOJI[next_rarity]} <b>{next_name}</b>\n"
        f"⭐ {next_rarity} • ⚡ {new_power}\n\n✨ +{XP_MERGE} XP" + extra,
        reply_markup=main_kb(),
    )
    await c.answer("Эволюция успешна! 🧬")


# ============================================================
# BATTLE
# ============================================================

ENEMIES = [
    ("Forest Slime", 20, 60), ("Cave Beast", 45, 110), ("Storm Wraith", 80, 160),
    ("Void Hunter", 120, 230), ("Ancient Titan", 180, 340), ("Aether Overlord", 260, 500)
]


@dp.callback_query(F.data == "battle")
async def battle(c: CallbackQuery):
    uid = c.from_user.id
    p = ensure_player(uid, c.from_user.username)
    if p["last_battle_at"]:
        delta = (now_utc() - p["last_battle_at"]).total_seconds()
        if delta < BATTLE_COOLDOWN_SECONDS:
            await c.answer(f"Арена перезаряжается: {int(BATTLE_COOLDOWN_SECONDS-delta)} сек.", show_alert=True)
            return
    pet = db_one("SELECT * FROM pets WHERE user_id=%s ORDER BY power DESC LIMIT 1", (uid,))
    if not pet:
        await c.answer("Сначала получи хотя бы одного зверя", show_alert=True)
        return
    level = p["level"]
    low = max(20, level * 25)
    high = max(low + 25, level * 55 + 100)
    enemy_name, _, _ = random.choice(ENEMIES)
    enemy_power = random.randint(low, high)
    if use_item(uid, "battle_elixir"):
        player_power = int(pet["power"] * 1.10 + pet["attack"] * 0.25 + pet["defense"] * 0.15)
        elixir_note = "\n⚔️ Battle Elixir активирован: +10%"
    else:
        player_power = int(pet["power"] + pet["attack"] * 0.20 + pet["defense"] * 0.15)
        elixir_note = ""
    roll = random.randint(-20, 20)
    won = player_power + roll >= enemy_power
    if won:
        reward = random.randint(35 + level * 3, 70 + level * 6)
        xp = XP_BATTLE_WIN
    else:
        reward = random.randint(10, 25 + level)
        xp = XP_BATTLE_LOSS
    with get_db() as db:
        db.execute("""
            UPDATE players SET aether=aether+%s,wins=wins+%s,losses=losses+%s,battles=battles+1,last_battle_at=%s WHERE user_id=%s
        """, (reward, 1 if won else 0, 0 if won else 1, now_utc(), uid))
        db.execute("INSERT INTO battle_logs(user_id,won,player_power,enemy_power,reward) VALUES(%s,%s,%s,%s,%s)", (uid, won, player_power, enemy_power, reward))
    add_item(uid, f"daily_battle_{today_utc().isoformat()}", 1 if won else 0)
    # daily quest counts wins only; add_item with 0 creates no progress effectively
    ups = add_player_xp(uid, xp)
    unlocked = check_achievements(uid)
    if won:
        result = "🏆 <b>ПОБЕДА!</b>"
        icon = "💰"
    else:
        result = "💀 <b>ПОРАЖЕНИЕ</b>"
        icon = "🩹"
    extra = ""
    if ups:
        extra += "\n🎉 Level Up: " + ", ".join(str(x[0]) for x in ups)
    if unlocked:
        extra += "\n🏆 Новое достижение!"
    balance = get_aether(uid)
    await c.message.edit_text(
        f"⚔️ <b>АРЕНА</b>\n\n{result}\n\n"
        f"🐉 {pet['name']} • ⚡ {player_power}\n"
        f"👹 {enemy_name} • ⚡ {enemy_power}\n\n"
        f"{icon} {'+' if won else '+'}{reward} AETHER\n✨ +{xp} XP\n"
        f"💰 Баланс: <b>{balance}</b>" + elixir_note + extra,
        reply_markup=main_kb(),
    )
    await c.answer("Победа! ⚔️" if won else "В следующий раз повезёт!")


# ============================================================
# INVENTORY / SHOP / MARKET
# ============================================================

async def show_inventory(target):
    uid = target.from_user.id
    rows = db_all("SELECT item_key,qty FROM inventory WHERE user_id=%s AND qty>0 ORDER BY item_key", (uid,))
    lines = ["🎒 <b>ИНВЕНТАРЬ</b>\n"]
    buttons = []
    for r in rows:
        info = SHOP_ITEMS.get(r["item_key"])
        if not info:
            continue
        lines.append(f"{info['name']} × <b>{r['qty']}</b>\n<i>{info['description']}</i>")
        if r["item_key"] in ("lucky_charm", "xp_potion", "aether_crystal", "battle_elixir"):
            buttons.append([InlineKeyboardButton(text=f"Использовать {info['name']}", callback_data=f"use:{r['item_key']}")])
    if len(lines) == 1:
        lines.append("Пока пусто. Загляни в 🏪 Магазин.")
    buttons.append([InlineKeyboardButton(text="🏪 Магазин", callback_data="market"), InlineKeyboardButton(text="🏠", callback_data="home")])
    await edit_or_answer(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "inventory")
async def inventory_cb(c: CallbackQuery):
    await show_inventory(c)
    await c.answer()


@dp.message(Command("inventory"))
async def inventory_cmd(m: Message):
    await show_inventory(m)


@dp.callback_query(F.data.startswith("use:"))
async def use_item_cb(c: CallbackQuery):
    uid = c.from_user.id
    key = c.data.split(":", 1)[1]
    if key == "lucky_charm":
        await c.answer("🍀 Подсказка: талисман расходуется автоматически при следующем призыве.", show_alert=True)
        return
    if key == "battle_elixir":
        await c.answer("⚔️ Эликсир расходуется автоматически в следующем бою.", show_alert=True)
        return
    if not use_item(uid, key):
        await c.answer("Предмет закончился", show_alert=True)
        return
    if key == "xp_potion":
        ups = add_player_xp(uid, 100)
        text = "🧪 <b>XP Potion использован!</b>\n✨ +100 XP"
        if ups:
            text += "\n🎉 Уровень повышен!"
    elif key == "aether_crystal":
        with get_db() as db:
            db.execute("UPDATE players SET aether=aether+200 WHERE user_id=%s", (uid,))
        text = "💎 <b>Aether Crystal использован!</b>\n💰 +200 AETHER"
    else:
        text = "Готово!"
    await c.message.edit_text(text, reply_markup=main_kb())
    await c.answer("Использовано!")


async def show_market(target):
    p = ensure_player(target.from_user.id, target.from_user.username)
    lines = [f"🏪 <b>AETHER MARKET</b>\n\n💰 Баланс: <b>{p['aether']}</b>\n"]
    buttons = []
    for key, info in SHOP_ITEMS.items():
        lines.append(f"{info['name']} — <b>{info['price']}</b> AETHER\n{info['description']}\n")
        buttons.append([InlineKeyboardButton(text=f"Купить {info['name']} • {info['price']}", callback_data=f"buy:{key}")])
    buttons.append([InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory"), InlineKeyboardButton(text="🏠", callback_data="home")])
    await edit_or_answer(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data == "market")
async def market_cb(c: CallbackQuery):
    await show_market(c)
    await c.answer()


@dp.message(Command("shop"))
async def shop_cmd(m: Message):
    await show_market(m)


@dp.callback_query(F.data.startswith("buy:"))
async def buy_cb(c: CallbackQuery):
    uid = c.from_user.id
    key = c.data.split(":", 1)[1]
    info = SHOP_ITEMS.get(key)
    if not info:
        await c.answer("Товар не найден", show_alert=True)
        return
    with get_db() as db:
        p = db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if p["aether"] < info["price"]:
            await c.answer("Недостаточно AETHER", show_alert=True)
            return
        db.execute("UPDATE players SET aether=aether-%s WHERE user_id=%s", (info["price"], uid))
        db.execute("""
            INSERT INTO inventory(user_id,item_key,qty) VALUES(%s,%s,1)
            ON CONFLICT(user_id,item_key) DO UPDATE SET qty=inventory.qty+1
        """, (uid, key))
    await show_market(c)
    await c.answer("Покупка совершена! 🛒")


# ============================================================
# RANKING / REFERRALS
# ============================================================

async def show_top(target):
    rows = db_all("SELECT user_id,username,level,wins,aether FROM players ORDER BY level DESC,wins DESC,aether DESC LIMIT 10")
    lines = ["🏅 <b>ТОП ИГРОКОВ</b>\n"]
    for i, r in enumerate(rows, 1):
        name = f"@{escape(r['username'])}" if r["username"] else str(r["user_id"])
        lines.append(f"<b>{i}.</b> {name} — ⭐{r['level']} • 🏆{r['wins']} • 💰{r['aether']}")
    await edit_or_answer(target, "\n".join(lines), back_kb())


@dp.callback_query(F.data == "top")
async def top_cb(c: CallbackQuery):
    await show_top(c)
    await c.answer()


@dp.message(Command("top"))
async def top_cmd(m: Message):
    await show_top(m)


async def show_ref(target):
    uid = target.from_user.id
    ensure_player(uid, target.from_user.username)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{uid}"
    count = db_one("SELECT COUNT(*) AS c FROM referrals WHERE referrer_id=%s", (uid,))["c"]
    rewarded = db_one("SELECT COUNT(*) AS c FROM referrals WHERE referrer_id=%s AND reward_given=TRUE", (uid,))["c"]
    await edit_or_answer(target, f"🤝 <b>РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n\nПриглашай друзей:\n\n<code>{link}</code>\n\n👥 Приглашено: <b>{count}</b>\n🎁 Вознаграждений: <b>{rewarded}</b>\n💰 За нового активного игрока: <b>100 AETHER</b> тебе и бонус новичку.", back_kb())


@dp.callback_query(F.data == "ref")
async def ref_cb(c: CallbackQuery):
    await show_ref(c)
    await c.answer()


@dp.message(Command("ref"))
async def ref_cmd(m: Message):
    await show_ref(m)


# ============================================================
# CLANS
# ============================================================

async def show_clan(target):
    uid = target.from_user.id
    clan = clan_for_user(uid)
    if not clan:
        text = ("🏰 <b>КЛАНЫ</b>\n\nТы пока не состоишь в клане.\n\n"
                "Создай клан командой:\n<code>/clan_create Aether Kings</code>\n\n"
                "Или вступи по ID:\n<code>/clan_join 123</code>\n\nСтоимость создания: <b>500 AETHER</b>.")
        await edit_or_answer(target, text, back_kb())
        return
    members = db_one("SELECT COUNT(*) AS c FROM clan_members WHERE clan_id=%s", (clan["id"],))["c"]
    owner = db_one("SELECT username FROM players WHERE user_id=%s", (clan["owner_id"],))
    await edit_or_answer(target, f"🏰 <b>{escape(clan['name'])}</b>\n\n🆔 ID: <code>{clan['id']}</code>\n⭐ Уровень: <b>{clan['level']}</b>\n✨ XP: <b>{clan['xp']}</b>\n👥 Участников: <b>{members}</b>\n👑 Лидер: @{escape(owner['username']) if owner and owner['username'] else clan['owner_id']}", back_kb())


@dp.callback_query(F.data == "clan")
async def clan_cb(c: CallbackQuery):
    await show_clan(c)
    await c.answer()


@dp.message(Command("clan"))
async def clan_cmd(m: Message):
    await show_clan(m)


@dp.message(Command("clan_create"))
async def clan_create(m: Message):
    uid = m.from_user.id
    ensure_player(uid, m.from_user.username)
    if clan_for_user(uid):
        await m.answer("Ты уже состоишь в клане.", reply_markup=main_kb())
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or len(parts[1].strip()) < 3:
        await m.answer("Формат: /clan_create Aether Kings")
        return
    name = parts[1].strip()[:40]
    with get_db() as db:
        p = db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if p["aether"] < 500:
            await m.answer("Нужно 500 AETHER для создания клана.", reply_markup=main_kb())
            return
        if db.execute("SELECT 1 FROM clans WHERE lower(name)=lower(%s)", (name,)).fetchone():
            await m.answer("Клан с таким названием уже существует.")
            return
        clan_id = db.execute("INSERT INTO clans(name,owner_id) VALUES(%s,%s) RETURNING id", (name, uid)).fetchone()["id"]
        db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)", (clan_id, uid))
        db.execute("UPDATE players SET aether=aether-500 WHERE user_id=%s", (uid,))
    await m.answer(f"🏰 Клан <b>{escape(name)}</b> создан! ID: <code>{clan_id}</code>", reply_markup=main_kb())


@dp.message(Command("clan_join"))
async def clan_join(m: Message):
    uid = m.from_user.id
    ensure_player(uid, m.from_user.username)
    if clan_for_user(uid):
        await m.answer("Ты уже состоишь в клане.")
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Формат: /clan_join 123")
        return
    try:
        clan_id = int(parts[1])
    except ValueError:
        await m.answer("ID клана должен быть числом.")
        return
    with get_db() as db:
        if not db.execute("SELECT 1 FROM clans WHERE id=%s", (clan_id,)).fetchone():
            await m.answer("Клан не найден.")
            return
        db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)", (clan_id, uid))
    await m.answer("✅ Ты вступил в клан!", reply_markup=main_kb())


# ============================================================
# PET TRAINING
# ============================================================

@dp.callback_query(F.data.startswith("pet:"))
async def pet_detail(c: CallbackQuery):
    await show_pet_detail(c, int(c.data.split(":")[1]))
    await c.answer()


@dp.callback_query(F.data.startswith("train:"))
async def train_pet(c: CallbackQuery):
    uid = c.from_user.id
    pet_id = int(c.data.split(":")[1])
    cost = 50
    with get_db() as db:
        p = db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        pet = db.execute("SELECT * FROM pets WHERE id=%s AND user_id=%s FOR UPDATE", (pet_id, uid)).fetchone()
        if not pet:
            await c.answer("Существо не найдено", show_alert=True)
            return
        if p["aether"] < cost:
            await c.answer("Недостаточно AETHER", show_alert=True)
            return
        if pet["level"] >= MAX_PET_LEVEL:
            await c.answer("Существо уже максимального уровня", show_alert=True)
            return
        xp = pet["xp"] + 25
        level = pet["level"]
        while level < MAX_PET_LEVEL and xp >= 100 + level * 50:
            xp -= 100 + level * 50
            level += 1
        multiplier = 1 + (level - 1) * 0.025
        db.execute("""
            UPDATE players SET aether=aether-%s WHERE user_id=%s
        """, (cost, uid))
        db.execute("""
            UPDATE pets SET level=%s,xp=%s,power=%s,attack=%s,defense=%s,hp=%s WHERE id=%s
        """, (level, xp, int(pet["power"] * multiplier), int(pet["attack"] * multiplier), int(pet["defense"] * multiplier), int(pet["hp"] * multiplier), pet_id))
    await show_pet_detail(c, pet_id)
    await c.answer("Существо стало сильнее! ⬆️")


# ============================================================
# HOME / COMMAND ALIASES / HELP CALLBACK
# ============================================================

@dp.callback_query(F.data == "home")
async def home_cb(c: CallbackQuery):
    p = ensure_player(c.from_user.id, c.from_user.username)
    await c.message.edit_text(
        "🐉 <b>AetherBeasts</b>\n\n"
        "Добро пожаловать в мир мифических существ!\n\n"
        f"⭐ Уровень: <b>{p['level']}</b> / {MAX_LEVEL}\n"
        f"✨ XP: <b>{p['xp']}</b>\n"
        f"💰 AETHER: <b>{p['aether']}</b>\n\n"
        "Собирай, развивай, объединяй и сражайся.",
        reply_markup=main_kb(),
    )
    await c.answer()


@dp.callback_query(F.data == "profile")
async def profile_cb(c: CallbackQuery):
    await show_profile(c)
    await c.answer()


@dp.callback_query(F.data.startswith("pets:"))
async def pets_cb(c: CallbackQuery):
    await show_pets(c, int(c.data.split(":")[1]))
    await c.answer()


@dp.callback_query(F.data == "help")
async def help_cb(c: CallbackQuery):
    await c.message.edit_text(
        "ℹ️ <b>AetherBeasts</b>\n\n"
        "✨ Призыв — получай случайных существ за AETHER.\n"
        "🧬 Merge — 3 одинаковых превращаются в следующую форму.\n"
        "⬆️ Тренировка — повышай уровень зверей.\n"
        "⚔️ Арена — PvE-бои с наградами.\n"
        "🎁 Daily — ежедневная серия.\n"
        "📜 Квесты — ежедневные цели.\n"
        "🏆 Достижения — долгосрочные цели.\n"
        "🏪 Магазин — полезные предметы.\n"
        "🏰 Клан — создавай сообщество.\n"
        "🤝 Рефералы — приглашай друзей.\n\n"
        "Все изменения сохраняются в PostgreSQL.",
        reply_markup=back_kb(),
    )
    await c.answer()


@dp.message(Command("profile"))
async def profile_cmd(m: Message):
    await show_profile(m)


@dp.message(Command("pets"))
async def pets_cmd(m: Message):
    await show_pets(m, 0)


# ============================================================
# FASTAPI WEBHOOK
# ============================================================

async def configure_webhook(base_url: str):
    url = base_url.rstrip("/") + "/webhook"
    kwargs = {"url": url, "drop_pending_updates": False}
    if WEBHOOK_SECRET:
        kwargs["secret_token"] = WEBHOOK_SECRET
    await bot.set_webhook(**kwargs)
    if RENDER_EXTERNAL_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎮 Play",
                    web_app=WebAppInfo(url=RENDER_EXTERNAL_URL + "/app")
                )
            )
        except Exception:
            log.exception("Failed to configure Mini App menu button")
    log.info("Webhook configured: %s", url)
    return url


@asynccontextmanager
async def lifespan(app_: FastAPI):
    init_db()
    try:
        if RENDER_EXTERNAL_URL:
            await configure_webhook(RENDER_EXTERNAL_URL)
    except Exception:
        log.exception("Automatic webhook setup failed")
    yield
    try:
        await bot.session.close()
    except Exception:
        pass


app = FastAPI(title="AetherBeasts", lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "online", "bot": "AetherBeasts", "version": "2.0"}


@app.get("/healthz")
async def health():
    db_one("SELECT 1 AS ok")
    return {"status": "healthy"}


@app.get("/set-webhook")
async def set_webhook(request: Request):
    # If WEBHOOK_SECRET is configured, require it for manual configuration too.
    if WEBHOOK_SECRET:
        supplied = request.query_params.get("secret", "")
        if supplied != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")
    base_url = RENDER_EXTERNAL_URL or str(request.base_url).rstrip("/")
    url = await configure_webhook(base_url)
    return {"ok": True, "webhook": url}


@app.post("/webhook")
async def webhook(request: Request):
    if WEBHOOK_SECRET:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if supplied != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")
    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception:
        log.exception("Webhook update failed")
        raise HTTPException(status_code=500, detail="Update processing failed")



# ============================================================
# TELEGRAM MINI APP
# ============================================================

MINIAPP_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=MINIAPP_DIR), name="static")


def validate_init_data(init_data: str):
    if not init_data:
        raise HTTPException(status_code=401, detail="Open AetherBeasts from Telegram")
    try:
        from urllib.parse import parse_qsl
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            raise ValueError("Missing hash")
        check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError("Invalid signature")
        auth_date = int(pairs.get("auth_date", "0"))
        if not auth_date or abs(int(datetime.now(timezone.utc).timestamp()) - auth_date) > 86400:
            raise ValueError("Expired init data")
        user = json.loads(pairs.get("user", "{}"))
        if not user.get("id"):
            raise ValueError("Missing user")
        return user
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Mini App auth failed: %s", e)
        raise HTTPException(status_code=401, detail="Telegram authorization failed")


def mini_user(request: Request):
    return validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))


def pet_emoji(name: str):
    return {"Ember Drake":"🔥","Frost Wolf":"❄️","Thunder Lynx":"⚡","Mystic Serpent":"🐍","Stone Golem":"🪨","Flame Raptor":"🦅","Ice Panther":"🐆","Storm Hawk":"🌪️","Shadow Viper":"🐍","Inferno Dragon":"🐉","Frost Wyvern":"🐲","Thunder Beast":"⚡","Void Serpent":"🌑","Ancient Phoenix":"🔥","Celestial Wolf":"🌟","Shadow Dragon":"🐲","Aether Dragon":"🐉","Eternal Phoenix":"🪽"}.get(name,"🐾")


def serialize_pet(p):
    d=dict(p)
    d["emoji"]=pet_emoji(d.get("name", ""))
    return d


@app.get("/app")
async def miniapp():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(MINIAPP_DIR, "index.html"))


@app.get("/api/state")
async def api_state(request: Request):
    user=mini_user(request)
    p=ensure_player(user["id"], user.get("username"))
    pets=db_all("SELECT id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage,favorite FROM pets WHERE user_id=%s ORDER BY power DESC", (user["id"],))
    return {"player":p,"pets":[serialize_pet(x) for x in pets],"top_pet":serialize_pet(pets[0]) if pets else None,"xp_needed":xp_needed(p["level"]),"daily":daily_status(user["id"]),"energy":refresh_energy(user["id"])}


@app.post("/api/daily")
async def api_daily(request: Request):
    user=mini_user(request); uid=user["id"]; p=ensure_player(uid,user.get("username"))
    today=today_utc()
    with get_db() as db:
        row=db.execute("SELECT daily_date,daily_streak FROM players WHERE user_id=%s FOR UPDATE",(uid,)).fetchone()
        if row["daily_date"]==today:
            raise HTTPException(400,"Daily reward already claimed")
        streak=row["daily_streak"]+1 if row["daily_date"] and (today-row["daily_date"]).days==1 else 1
        reward=min(500,100+streak*25)
        db.execute("UPDATE players SET aether=aether+%s,daily_date=%s,daily_streak=%s,quest_date=%s,quest_summons=0,quest_wins=0,quest_merges=0 WHERE user_id=%s",(reward,today,streak,today,uid))
    return {"reward":reward,"streak":streak}


@app.post("/api/summon")
async def api_summon(request: Request):
    user=mini_user(request); uid=user["id"]; ensure_player(uid,user.get("username")); body=await request.json(); count=int(body.get("count",1))
    if count not in (1,10): raise HTTPException(400,"Invalid summon count")
    cost=25 if count==1 else 225
    results=[]
    with get_db() as db:
        p=db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE",(uid,)).fetchone()
        if p["aether"]<cost: raise HTTPException(400,"Not enough AETHER")
        db.execute("UPDATE players SET aether=aether-%s,summons=summons+%s,quest_summons=CASE WHEN quest_date=%s THEN quest_summons+%s ELSE %s END,quest_date=%s WHERE user_id=%s",(cost,count,today_utc(),count,count,today_utc(),uid))
        for _ in range(count):
            pet=generate_pet(); results.append(pet)
            db.execute("INSERT INTO pets(user_id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage) VALUES(%s,%s,%s,%s,1,0,%s,%s,%s,%s,1)",(uid,pet["name"],pet["rarity"],pet["power"],pet["attack"],pet["defense"],pet["hp"],pet["element"]))
    add_player_xp(uid, XP_SUMMON*count)
    for r in results: r["emoji"]=pet_emoji(r["name"])
    return {"results":results,"cost":cost}


@app.get("/api/quests")
async def api_quests(request: Request):
    user=mini_user(request); uid=user["id"]; p=ensure_player(uid,user.get("username")); today=today_utc()
    if p.get("quest_date") != today:
        with get_db() as db:
            db.execute("UPDATE players SET quest_date=%s,quest_summons=0,quest_wins=0,quest_merges=0 WHERE user_id=%s",(today,uid))
        p=ensure_player(uid,user.get("username"))
    rows=[
        {"code":"summon","title":"Summon 3 beasts","progress":min(3,p.get("quest_summons",0)) ,"target":3,"reward":100},
        {"code":"battle","title":"Win 2 battles","progress":min(2,p.get("quest_wins",0)),"target":2,"reward":150},
        {"code":"merge","title":"Complete 1 evolution","progress":min(1,p.get("quest_merges",0)),"target":1,"reward":200},
    ]
    claimed={r["code"] for r in db_all("SELECT code FROM achievements WHERE user_id=%s AND code LIKE 'daily_quest_%' AND claimed_at::date=%s",(uid,today))}
    return {"date":str(today),"quests":[{**x,"done":x["progress"]>=x["target"],"claimed":f"daily_quest_{x['code']}" in claimed} for x in rows]}

@app.post("/api/quests/claim")
async def api_quest_claim(request: Request):
    user=mini_user(request); uid=user["id"]; body=await request.json(); code=body.get("code"); today=today_utc()
    rewards={"summon":100,"battle":150,"merge":200}; targets={"summon":3,"battle":2,"merge":1}
    if code not in rewards: raise HTTPException(400,"Invalid quest")
    p=ensure_player(uid,user.get("username"))
    progress={"summon":p.get("quest_summons",0),"battle":p.get("quest_wins",0),"merge":p.get("quest_merges",0)}[code]
    if progress < targets[code]: raise HTTPException(400,"Quest not completed")
    with get_db() as db:
        exists=db.execute("SELECT 1 FROM achievements WHERE user_id=%s AND code=%s AND claimed_at::date=%s",(uid,f"daily_quest_{code}",today)).fetchone()
        if exists: raise HTTPException(400,"Quest already claimed")
        db.execute("INSERT INTO achievements(user_id,code) VALUES(%s,%s)",(uid,f"daily_quest_{code}"))
        db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s",(rewards[code],uid))
    return {"reward":rewards[code],"code":code}

@app.get("/api/merge/options")
async def api_merge_options(request: Request):
    user=mini_user(request); opts=get_merge_options(user["id"])
    return {"options":[{"name":x[0],"rarity":x[1],"count":x[2],"next_rarity":x[3],"next_name":x[4],"emoji":pet_emoji(x[0]),"next_emoji":pet_emoji(x[4])} for x in opts]}


@app.post("/api/merge")
async def api_merge(request: Request):
    user=mini_user(request); uid=user["id"]; body=await request.json(); idx=int(body.get("index",-1)); opts=get_merge_options(uid)
    if idx<0 or idx>=len(opts): raise HTTPException(400,"Merge option unavailable")
    name,rarity,count,next_rarity,next_name=opts[idx]
    with get_db() as db:
        selected=db.execute("SELECT id,power FROM pets WHERE user_id=%s AND name=%s AND rarity=%s ORDER BY power ASC LIMIT 3 FOR UPDATE",(uid,name,rarity)).fetchall()
        if len(selected)<3: raise HTTPException(400,"Need three identical beasts")
        ids=[x["id"] for x in selected]; base=sum(x["power"] for x in selected); lo,hi=RARITY_POWER[next_rarity]; power=min(hi,max(lo,int(base*random.uniform(.75,1.05))))
        db.execute("DELETE FROM pets WHERE id=ANY(%s)",(ids,))
        db.execute("INSERT INTO pets(user_id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage) VALUES(%s,%s,%s,%s,1,0,%s,%s,%s,%s,1)",(uid,next_name,next_rarity,power,max(2,int(power*.5)),max(2,int(power*.28)),max(20,power*2),random_element(next_name)))
        db.execute("UPDATE players SET merges=merges+1,quest_merges=CASE WHEN quest_date=%s THEN quest_merges+1 ELSE 1 END,quest_date=%s WHERE user_id=%s",(today_utc(),today_utc(),uid))
    add_player_xp(uid,XP_MERGE); return {"pet":{"name":next_name,"rarity":next_rarity,"power":power,"emoji":pet_emoji(next_name)}}


@app.post("/api/battle")
async def api_battle(request: Request):
    user=mini_user(request); uid=user["id"]; p=ensure_player(uid,user.get("username"));
    if p.get("last_battle_at"):
        elapsed=(datetime.now(timezone.utc)-p["last_battle_at"]).total_seconds()
        if elapsed < BATTLE_COOLDOWN_SECONDS: raise HTTPException(429,f"Battle cooldown: {int(BATTLE_COOLDOWN_SECONDS-elapsed)}s")
    rows=db_all("SELECT power FROM pets WHERE user_id=%s ORDER BY power DESC LIMIT 3",(uid,)); player_power=sum(x["power"] for x in rows)
    if not player_power: raise HTTPException(400,"You need a beast first")
    enemy=random.randint(max(20,player_power//2),max(35,player_power+60)); won=(player_power*random.uniform(.85,1.15))>=enemy; reward=random.randint(35,80) if won else 10; xp=50 if won else 15
    with get_db() as db:
        db.execute("UPDATE players SET aether=aether+%s,battles=battles+1,wins=wins+%s,losses=losses+%s,last_battle_at=NOW(),quest_wins=CASE WHEN quest_date=%s AND %s THEN quest_wins+1 ELSE quest_wins END,quest_date=%s WHERE user_id=%s",(reward,1 if won else 0,0 if won else 1,today_utc(),won,today_utc(),uid)); db.execute("INSERT INTO battle_logs(user_id,won,player_power,enemy_power,reward) VALUES(%s,%s,%s,%s,%s)",(uid,won,player_power,enemy,reward))
    add_player_xp(uid,xp); return {"won":won,"player_power":player_power,"enemy_power":enemy,"reward":reward,"xp":xp}


@app.get("/api/leaderboard")
async def api_leaderboard(request: Request):
    mini_user(request); rows=db_all("SELECT p.username,p.level,COALESCE(SUM(pt.power),0) AS power FROM players p LEFT JOIN pets pt ON pt.user_id=p.user_id GROUP BY p.user_id ORDER BY power DESC,p.level DESC LIMIT 50")
    return {"rows":rows}


# ============================================================
# AETHERBEASTS MINI APP — PRODUCTION EXPANSION
# ============================================================

ENERGY_MAX = 100
ENERGY_REGEN_SECONDS = 5 * 60
TRAIN_ENERGY_COST = 10


def refresh_energy(user_id: int):
    """Regenerate one Energy every 5 minutes, persisted in PostgreSQL."""
    with get_db() as db:
        row = db.execute("SELECT energy,last_energy_at FROM players WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
        if not row:
            return ENERGY_MAX
        energy = int(row.get("energy") if row.get("energy") is not None else ENERGY_MAX)
        last = row.get("last_energy_at")
        now = now_utc()
        if last is None:
            db.execute("UPDATE players SET energy=%s,last_energy_at=%s WHERE user_id=%s", (energy, now, user_id))
            return energy
        elapsed = max(0, int((now - last).total_seconds()))
        gained = elapsed // ENERGY_REGEN_SECONDS
        if gained > 0:
            energy = min(ENERGY_MAX, energy + gained)
            last = now if energy >= ENERGY_MAX else last + timedelta(seconds=gained * ENERGY_REGEN_SECONDS)
            db.execute("UPDATE players SET energy=%s,last_energy_at=%s WHERE user_id=%s", (energy, last, user_id))
        return energy


def spend_energy(user_id: int, amount: int):
    with get_db() as db:
        row = db.execute("SELECT energy FROM players WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
        if not row or int(row["energy"] or 0) < amount:
            return False
        db.execute("UPDATE players SET energy=energy-%s,last_energy_at=COALESCE(last_energy_at,NOW()) WHERE user_id=%s", (amount, user_id))
        return True


def mini_pet_stats(p):
    d = serialize_pet(p)
    d["favorite"] = bool(d.get("favorite", False))
    return d


@app.get("/api/config")
async def api_config(request: Request):
    mini_user(request)
    return {
        "name": "AetherBeasts",
        "version": "3.0.0",
        "max_level": MAX_LEVEL,
        "max_pet_level": MAX_PET_LEVEL,
        "energy_max": ENERGY_MAX,
        "energy_regen_seconds": ENERGY_REGEN_SECONDS,
        "summon_cost": SUMMON_COST,
        "summon_x10_cost": 225,
        "rarities": RARITY_ORDER,
    }


@app.get("/api/economy")
async def api_economy(request: Request):
    user = mini_user(request)
    uid = user["id"]
    ensure_player(uid, user.get("username"))
    return {"energy": refresh_energy(uid), "max_energy": ENERGY_MAX, "regen_seconds": ENERGY_REGEN_SECONDS}


@app.get("/api/pets/{pet_id}")
async def api_pet_detail(request: Request, pet_id: int):
    user = mini_user(request)
    p = db_one("SELECT * FROM pets WHERE id=%s AND user_id=%s", (pet_id, user["id"]))
    if not p:
        raise HTTPException(404, "Beast not found")
    return {"pet": mini_pet_stats(p)}


@app.post("/api/pets/favorite")
async def api_pet_favorite(request: Request):
    user = mini_user(request); uid = user["id"]
    body = await request.json(); pet_id = int(body.get("pet_id", 0))
    with get_db() as db:
        pet = db.execute("SELECT id FROM pets WHERE id=%s AND user_id=%s", (pet_id, uid)).fetchone()
        if not pet: raise HTTPException(404, "Beast not found")
        db.execute("UPDATE pets SET favorite=FALSE WHERE user_id=%s", (uid,))
        db.execute("UPDATE pets SET favorite=TRUE WHERE id=%s AND user_id=%s", (pet_id, uid))
    return {"ok": True, "favorite": pet_id}


@app.post("/api/pets/train")
async def api_pet_train(request: Request):
    user = mini_user(request); uid = user["id"]
    body = await request.json(); pet_id = int(body.get("pet_id", 0))
    ensure_player(uid, user.get("username")); refresh_energy(uid)
    with get_db() as db:
        pet = db.execute("SELECT * FROM pets WHERE id=%s AND user_id=%s FOR UPDATE", (pet_id, uid)).fetchone()
        if not pet: raise HTTPException(404, "Beast not found")
        if pet["level"] >= MAX_PET_LEVEL: raise HTTPException(400, "Beast is max level")
        p = db.execute("SELECT energy FROM players WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if int(p["energy"] or 0) < TRAIN_ENERGY_COST: raise HTTPException(400, "Not enough Energy")
        new_xp = int(pet["xp"] or 0) + 25
        level = int(pet["level"] or 1)
        while level < MAX_PET_LEVEL and new_xp >= 100 + level * 50:
            new_xp -= 100 + level * 50
            level += 1
        mult = 1 + (level - 1) * 0.025
        db.execute("UPDATE players SET energy=energy-%s WHERE user_id=%s", (TRAIN_ENERGY_COST, uid))
        db.execute("UPDATE pets SET level=%s,xp=%s,power=%s,attack=%s,defense=%s,hp=%s WHERE id=%s", (level,new_xp,int(pet["power"]*mult),int(pet["attack"]*mult),int(pet["defense"]*mult),int(pet["hp"]*mult),pet_id))
    updated = db_one("SELECT * FROM pets WHERE id=%s", (pet_id,))
    return {"pet": mini_pet_stats(updated), "energy": refresh_energy(uid)}


@app.get("/api/inventory")
async def api_inventory(request: Request):
    user = mini_user(request); uid = user["id"]
    rows = db_all("SELECT item_key,qty FROM inventory WHERE user_id=%s AND qty>0 ORDER BY item_key", (uid,))
    items=[]
    for r in rows:
        info=SHOP_ITEMS.get(r["item_key"], {"name":r["item_key"],"description":"Item","price":0})
        items.append({"key":r["item_key"],"qty":r["qty"],"name":info["name"],"description":info["description"],"price":info.get("price",0)})
    return {"items":items}


@app.post("/api/inventory/use")
async def api_inventory_use(request: Request):
    user=mini_user(request); uid=user["id"]; body=await request.json(); key=body.get("item_key")
    if key not in SHOP_ITEMS: raise HTTPException(400,"Unknown item")
    if key in ("lucky_charm","battle_elixir"):
        raise HTTPException(400,"This item activates automatically")
    if not use_item(uid,key): raise HTTPException(400,"Item unavailable")
    if key=="xp_potion":
        add_player_xp(uid,100); message="+100 XP"
    elif key=="aether_crystal":
        with get_db() as db: db.execute("UPDATE players SET aether=aether+200 WHERE user_id=%s",(uid,))
        message="+200 AETHER"
    else: message="Used"
    return {"ok":True,"message":message}


@app.get("/api/shop")
async def api_shop(request: Request):
    user=mini_user(request); p=ensure_player(user["id"],user.get("username"))
    return {"aether":p["aether"],"items":[{"key":k,**v,"qty":item_qty(user["id"],k)} for k,v in SHOP_ITEMS.items()]}


@app.post("/api/shop/buy")
async def api_shop_buy(request: Request):
    user=mini_user(request); uid=user["id"]; body=await request.json(); key=body.get("item_key"); qty=max(1,min(20,int(body.get("qty",1))))
    info=SHOP_ITEMS.get(key)
    if not info: raise HTTPException(400,"Item not found")
    total=info["price"]*qty
    with get_db() as db:
        p=db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE",(uid,)).fetchone()
        if p["aether"]<total: raise HTTPException(400,"Not enough AETHER")
        db.execute("UPDATE players SET aether=aether-%s WHERE user_id=%s",(total,uid))
        db.execute("INSERT INTO inventory(user_id,item_key,qty) VALUES(%s,%s,%s) ON CONFLICT(user_id,item_key) DO UPDATE SET qty=inventory.qty+EXCLUDED.qty",(uid,key,qty))
    return {"ok":True,"spent":total,"qty":item_qty(uid,key)}


@app.get("/api/achievements")
async def api_achievements(request: Request):
    user=mini_user(request); uid=user["id"]; ensure_player(uid,user.get("username")); check_achievements(uid)
    player,count,legendary=achievement_progress(uid)
    claimed={r["code"] for r in db_all("SELECT code FROM achievements WHERE user_id=%s",(uid,))}
    rows=[]
    for code,title,desc,reward,kind in ACHIEVEMENTS:
        rows.append({"code":code,"title":title,"description":desc,"reward":reward,"unlocked":achievement_met(code,player,count,legendary),"claimed":code in claimed})
    return {"achievements":rows}


@app.post("/api/achievements/claim")
async def api_achievement_claim(request: Request):
    user=mini_user(request); uid=user["id"]
    unlocked=check_achievements(uid)
    return {"unlocked":[{"title":t,"reward":r} for t,r in unlocked]}


@app.get("/api/profile")
async def api_profile(request: Request):
    user=mini_user(request); uid=user["id"]; p=ensure_player(uid,user.get("username")); refresh_energy(uid)
    power=db_one("SELECT COALESCE(SUM(power),0) AS p FROM pets WHERE user_id=%s",(uid,))["p"]
    count=db_one("SELECT COUNT(*) AS c FROM pets WHERE user_id=%s",(uid,))["c"]
    fav=db_one("SELECT id,name FROM pets WHERE user_id=%s AND favorite=TRUE LIMIT 1",(uid,))
    return {"player":p,"collection":count,"power":power,"favorite":fav,"energy":refresh_energy(uid)}


@app.get("/api/battle/history")
async def api_battle_history(request: Request):
    user=mini_user(request)
    rows=db_all("SELECT won,player_power,enemy_power,reward,created_at FROM battle_logs WHERE user_id=%s ORDER BY id DESC LIMIT 20",(user["id"],))
    return {"rows":[dict(r) for r in rows]}


@app.get("/api/clan")
async def api_clan(request: Request):
    user=mini_user(request); uid=user["id"]; clan=clan_for_user(uid)
    if not clan: return {"clan":None}
    members=db_all("SELECT p.username,p.level,p.user_id FROM clan_members m JOIN players p ON p.user_id=m.user_id WHERE m.clan_id=%s ORDER BY p.level DESC LIMIT 50",(clan["id"],))
    return {"clan":{**dict(clan),"members":[dict(x) for x in members],"member_count":len(members)}}


@app.post("/api/clan/create")
async def api_clan_create(request: Request):
    user=mini_user(request); uid=user["id"]; ensure_player(uid,user.get("username")); body=await request.json(); name=str(body.get("name","")).strip()[:40]
    if len(name)<3: raise HTTPException(400,"Clan name is too short")
    if clan_for_user(uid): raise HTTPException(400,"You are already in a clan")
    with get_db() as db:
        p=db.execute("SELECT aether FROM players WHERE user_id=%s FOR UPDATE",(uid,)).fetchone()
        if p["aether"]<500: raise HTTPException(400,"500 AETHER required")
        if db.execute("SELECT 1 FROM clans WHERE lower(name)=lower(%s)",(name,)).fetchone(): raise HTTPException(400,"Clan name already exists")
        cid=db.execute("INSERT INTO clans(name,owner_id) VALUES(%s,%s) RETURNING id",(name,uid)).fetchone()["id"]
        db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)",(cid,uid))
        db.execute("UPDATE players SET aether=aether-500 WHERE user_id=%s",(uid,))
    return {"ok":True,"clan_id":cid,"name":name}


@app.post("/api/clan/join")
async def api_clan_join(request: Request):
    user=mini_user(request); uid=user["id"]; ensure_player(uid,user.get("username")); body=await request.json(); cid=int(body.get("clan_id",0))
    if clan_for_user(uid): raise HTTPException(400,"You are already in a clan")
    with get_db() as db:
        if not db.execute("SELECT 1 FROM clans WHERE id=%s",(cid,)).fetchone(): raise HTTPException(404,"Clan not found")
        db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)",(cid,uid))
    return {"ok":True}


@app.post("/api/clan/leave")
async def api_clan_leave(request: Request):
    user=mini_user(request); uid=user["id"]; clan=clan_for_user(uid)
    if not clan: raise HTTPException(400,"You are not in a clan")
    if clan["owner_id"]==uid: raise HTTPException(400,"Clan owner cannot leave yet")
    with get_db() as db: db.execute("DELETE FROM clan_members WHERE user_id=%s",(uid,))
    return {"ok":True}


@app.get("/api/catalog")
async def api_catalog(request: Request):
    mini_user(request)
    catalog=[]
    for rarity in RARITY_ORDER:
        for name in PETS[rarity]:
            catalog.append({"name":name,"rarity":rarity,"element":random_element(name),"min_power":RARITY_POWER[rarity][0],"max_power":RARITY_POWER[rarity][1]})
    return {"beasts":catalog}

if __name__ == "__main__":
    # Local development only. Render should use the Start Command with uvicorn.
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
