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
            "evolution_stage": "INTEGER NOT NULL DEFAULT 1",
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
