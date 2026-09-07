import os
import json
import random
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, Update, InlineKeyboardMarkup, InlineKeyboardButton, MenuButtonWebApp, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aetherbeasts")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

APP_VERSION = "4.0"
MAX_PLAYER_LEVEL = 50
MAX_BEAST_LEVEL = 30
ENERGY_MAX = 100
ENERGY_REGEN_EVERY = 60
ENERGY_REGEN_AMOUNT = 5
SUMMON_ENERGY = 10
BATTLE_ENERGY = 15
TRAIN_ENERGY = 5
SUMMON_COST_1 = 25
SUMMON_COST_10 = 225
BATTLE_COOLDOWN = 35
XP_SUMMON = 25
XP_BATTLE_WIN = 60
XP_BATTLE_LOSS = 20
XP_MERGE = 100

RARITIES = ["Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic"]
RARITY_WEIGHTS = {"Common": 55.0, "Uncommon": 26.0, "Rare": 12.0, "Epic": 5.0, "Legendary": 1.8, "Mythic": 0.2}
RARITY_POWER = {
    "Common": (35, 80), "Uncommon": (80, 150), "Rare": (150, 280),
    "Epic": (280, 500), "Legendary": (500, 900), "Mythic": (900, 1500)
}
RARITY_COLOR = {"Common":"#9aa7bd","Uncommon":"#43e46b","Rare":"#32b9ff","Epic":"#b86dff","Legendary":"#ffc64a","Mythic":"#ff3e78"}

BEASTS = {
    "Frostpaw": {"rarity":"Common","element":"Wind","art":"frostpaw.png","base":(320,45,30),"next":"Blazehorn"},
    "Blazehorn": {"rarity":"Uncommon","element":"Fire","art":"blazehorn.png","base":(450,70,50),"next":"Solaris"},
    "AquaSerpent": {"rarity":"Rare","element":"Water","art":"aquaserpent.png","base":(650,100,80),"next":"Frostfang"},
    "Stoneguardian": {"rarity":"Epic","element":"Earth","art":"stoneguardian.png","base":(920,140,120),"next":"Stormwing"},
    "Stormwing": {"rarity":"Legendary","element":"Lightning","art":"stormwing.png","base":(1400,220,180),"next":"Voidwyrm"},
    "Skydancer": {"rarity":"Uncommon","element":"Wind","art":"skydancer.png","base":(380,65,40),"next":None},
    "Nightshade": {"rarity":"Uncommon","element":"Dark","art":"nightshade.png","base":(520,85,65),"next":None},
    "Solaris": {"rarity":"Rare","element":"Fire","art":"solaris.png","base":(800,130,100),"next":"Frostfang"},
    "Frostfang": {"rarity":"Epic","element":"Water","art":"frostfang.png","base":(1100,180,140),"next":"Stormwing"},
    "Voidwyrm": {"rarity":"Mythic","element":"Dark","art":"voidwyrm.png","base":(1800,300,240),"next":None},
}
BY_RARITY = {r:[n for n,v in BEASTS.items() if v["rarity"]==r] for r in RARITIES}
ELEMENT_ICONS = {"Fire":"🔥","Water":"💧","Earth":"🌿","Lightning":"⚡","Wind":"🌪️","Dark":"🌑","Light":"☀️"}

ENEMIES = [
    ("Forest Slime", 25, 70), ("Cave Beast", 55, 130), ("Storm Wraith", 100, 210),
    ("Void Hunter", 170, 320), ("Ancient Titan", 280, 520), ("Aether Overlord", 450, 850)
]
SHOP = {
    "xp_potion": {"name":"XP Potion", "price":60, "desc":"+100 player XP"},
    "energy_cell": {"name":"Energy Cell", "price":35, "desc":"+30 Energy"},
    "lucky_charm": {"name":"Lucky Charm", "price":120, "desc":"+35% Legendary/Mythic weight on next summon"},
    "battle_elixir": {"name":"Battle Elixir", "price":100, "desc":"+15% combat power for next battle"},
    "aether_crystal": {"name":"Aether Crystal", "price":140, "desc":"+250 AETHER"},
}
DAILY_REWARDS = [100,125,150,175,225,300,500]
QUESTS = {
    "summon_3": ("Summon 3 Beasts", "summons", 3, 120),
    "win_2": ("Win 2 Arena battles", "wins", 2, 180),
    "train_2": ("Train 2 Beasts", "trains", 2, 150),
    "merge_1": ("Evolve 1 Beast", "merges", 1, 220),
}
ACHIEVEMENTS = [
    ("first_beast","First Bond","Obtain 1 Beast",75,lambda p,c,m:c>=1),
    ("collector_5","Collector","Own 5 Beasts",150,lambda p,c,m:c>=5),
    ("collector_20","Beast Archive","Own 20 Beasts",400,lambda p,c,m:c>=20),
    ("summon_25","Summoner","Make 25 summons",300,lambda p,c,m:p["summons"]>=25),
    ("win_10","Gladiator","Win 10 battles",400,lambda p,c,m:p["wins"]>=10),
    ("win_50","Arena Legend","Win 50 battles",1000,lambda p,c,m:p["wins"]>=50),
    ("merge_5","Evolutionist","Evolve 5 Beasts",500,lambda p,c,m:p["merges"]>=5),
    ("level_10","Veteran","Reach player level 10",300,lambda p,c,m:p["level"]>=10),
    ("legendary","Legendary Bond","Obtain a Legendary or Mythic",900,lambda p,c,m:m),
    ("mythic","Mythic Hunter","Obtain a Mythic",2000,lambda p,c,m:m>=1),
]

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

def one(sql, params=()):
    with conn() as db:
        return db.execute(sql, params).fetchone()

def all_rows(sql, params=()):
    with conn() as db:
        return db.execute(sql, params).fetchall()

def utcnow():
    return datetime.now(timezone.utc)

def today():
    return utcnow().date()

def xp_needed(level:int)->int:
    return 0 if level>=MAX_PLAYER_LEVEL else 100 + (level-1)*80

def beast_xp_needed(level:int)->int:
    return 0 if level>=MAX_BEAST_LEVEL else 75 + (level-1)*45


def init_db():
    statements = [
        """CREATE TABLE IF NOT EXISTS players(
            user_id BIGINT PRIMARY KEY, username TEXT, aether BIGINT NOT NULL DEFAULT 100,
            level INT NOT NULL DEFAULT 1, xp INT NOT NULL DEFAULT 0,
            wins INT NOT NULL DEFAULT 0, losses INT NOT NULL DEFAULT 0, battles INT NOT NULL DEFAULT 0,
            summons INT NOT NULL DEFAULT 0, merges INT NOT NULL DEFAULT 0, trains INT NOT NULL DEFAULT 0,
            last_battle_at TIMESTAMPTZ, daily_date DATE, daily_streak INT NOT NULL DEFAULT 0,
            quest_date DATE, quest_summons INT NOT NULL DEFAULT 0, quest_wins INT NOT NULL DEFAULT 0,
            quest_trains INT NOT NULL DEFAULT 0, quest_merges INT NOT NULL DEFAULT 0,
            energy INT NOT NULL DEFAULT 100, energy_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            referral_code TEXT UNIQUE, referrer_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS pets(
            id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, rarity TEXT NOT NULL,
            power INT NOT NULL, level INT NOT NULL DEFAULT 1, xp INT NOT NULL DEFAULT 0,
            attack INT NOT NULL DEFAULT 0, defense INT NOT NULL DEFAULT 0, hp INT NOT NULL DEFAULT 0,
            element TEXT NOT NULL DEFAULT 'Aether', evolution_stage INT NOT NULL DEFAULT 1,
            favorite BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS inventory(user_id BIGINT NOT NULL,item_key TEXT NOT NULL,qty INT NOT NULL DEFAULT 0,PRIMARY KEY(user_id,item_key))""",
        """CREATE TABLE IF NOT EXISTS achievements(user_id BIGINT NOT NULL,code TEXT NOT NULL,claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(user_id,code))""",
        """CREATE TABLE IF NOT EXISTS daily_quest_claims(user_id BIGINT NOT NULL,quest_date DATE NOT NULL,code TEXT NOT NULL,claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(user_id,quest_date,code))""",
        """CREATE TABLE IF NOT EXISTS battle_logs(id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL,won BOOLEAN NOT NULL,player_power INT NOT NULL,enemy_power INT NOT NULL,reward BIGINT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS clans(id BIGSERIAL PRIMARY KEY,name TEXT UNIQUE NOT NULL,owner_id BIGINT NOT NULL,level INT NOT NULL DEFAULT 1,xp INT NOT NULL DEFAULT 0,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS clan_members(clan_id BIGINT NOT NULL,user_id BIGINT PRIMARY KEY,joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS referrals(referrer_id BIGINT NOT NULL,referred_id BIGINT PRIMARY KEY,reward_given BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
        "CREATE INDEX IF NOT EXISTS idx_pets_user ON pets(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_battle_user_time ON battle_logs(user_id,created_at DESC)",
    ]
    with conn() as db:
        for s in statements: db.execute(s)
        # Additive migration for old deployments.
        existing_p = {r["column_name"] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='players'").fetchall()}
        for col, typ in {
            "trains":"INT NOT NULL DEFAULT 0", "quest_trains":"INT NOT NULL DEFAULT 0",
            "energy":"INT NOT NULL DEFAULT 100", "energy_updated_at":"TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        }.items():
            if col not in existing_p: db.execute(f"ALTER TABLE players ADD COLUMN {col} {typ}")
        existing_pet = {r["column_name"] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='pets'").fetchall()}
        if "favorite" not in existing_pet: db.execute("ALTER TABLE pets ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT FALSE")
        db.execute("UPDATE players SET referral_code=COALESCE(referral_code,user_id::text)")
        db.execute("UPDATE players SET energy=LEAST(100,GREATEST(0,COALESCE(energy,100)))")
        db.execute("UPDATE players SET energy_updated_at=COALESCE(energy_updated_at,NOW())")
        db.execute("UPDATE pets SET attack=GREATEST(1,power/2) WHERE attack=0")
        db.execute("UPDATE pets SET defense=GREATEST(1,power/4) WHERE defense=0")
        db.execute("UPDATE pets SET hp=GREATEST(20,power*2) WHERE hp=0")


def ensure_player(user_id:int, username:Optional[str]=None):
    with conn() as db:
        p = db.execute("SELECT * FROM players WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()
        if not p:
            db.execute("INSERT INTO players(user_id,username,referral_code) VALUES(%s,%s,%s)", (user_id, username or "", str(user_id)))
            p = db.execute("SELECT * FROM players WHERE user_id=%s", (user_id,)).fetchone()
        elif username and p["username"] != username:
            db.execute("UPDATE players SET username=%s WHERE user_id=%s", (username,user_id)); p["username"] = username
        return sync_energy_row(db,p)


def sync_energy_row(db,p):
    now = utcnow(); current = max(0, int(p.get("energy") or 0)); last = p.get("energy_updated_at") or now
    if current >= ENERGY_MAX:
        db.execute("UPDATE players SET energy_updated_at=%s WHERE user_id=%s",(now,p["user_id"]))
        p["energy"] = ENERGY_MAX; p["energy_updated_at"] = now; return p
    elapsed = max(0,int((now-last).total_seconds()))
    ticks = elapsed // ENERGY_REGEN_EVERY
    if ticks:
        gain = ticks * ENERGY_REGEN_AMOUNT
        new_energy = min(ENERGY_MAX,current+gain)
        used_seconds = ticks*ENERGY_REGEN_EVERY
        new_ts = last + timedelta(seconds=used_seconds)
        db.execute("UPDATE players SET energy=%s,energy_updated_at=%s WHERE user_id=%s",(new_energy,new_ts,p["user_id"]))
        p["energy"]=new_energy; p["energy_updated_at"]=new_ts
    return p


def add_player_xp_tx(db,user_id,amount):
    p=db.execute("SELECT level,xp FROM players WHERE user_id=%s FOR UPDATE",(user_id,)).fetchone()
    if not p: return []
    level,xp=p["level"],p["xp"]+amount; ups=[]
    while level<MAX_PLAYER_LEVEL and xp>=xp_needed(level):
        xp-=xp_needed(level); level+=1
        reward=50+level*15; db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s",(reward,user_id)); ups.append((level,reward))
    if level>=MAX_PLAYER_LEVEL: xp=0
    db.execute("UPDATE players SET level=%s,xp=%s WHERE user_id=%s",(level,xp,user_id))
    return ups


def add_player_xp(user_id,amount):
    with conn() as db: return add_player_xp_tx(db,user_id,amount)


def roll_rarity(lucky=False):
    weights=RARITY_WEIGHTS.copy()
    if lucky:
        weights["Legendary"]*=1.35; weights["Mythic"]*=1.35
        rem=sum(RARITY_WEIGHTS.values())-RARITY_WEIGHTS["Legendary"]-RARITY_WEIGHTS["Mythic"]
        desired=weights["Legendary"]+weights["Mythic"]; scale=(100-desired)/rem
        for r in ["Common","Uncommon","Rare","Epic"]: weights[r]=RARITY_WEIGHTS[r]*scale
    return random.choices(list(weights),weights=list(weights.values()),k=1)[0]


def generate_beast(rarity=None,name=None,lucky=False):
    rarity=rarity or roll_rarity(lucky=lucky)
    name=name or random.choice(BY_RARITY[rarity])
    data=BEASTS[name]
    hp,atk,df=data["base"]
    lo,hi=RARITY_POWER[rarity]
    power=random.randint(lo,hi)
    scale=power/max(1,(hp+atk+df)//3)
    hp2=max(20,int(hp*scale*random.uniform(.92,1.08))); atk2=max(5,int(atk*scale*random.uniform(.90,1.10))); df2=max(4,int(df*scale*random.uniform(.90,1.10)))
    return {"name":name,"rarity":rarity,"element":data["element"],"power":power,"attack":atk2,"defense":df2,"hp":hp2,"art":data["art"]}


def insert_beast(db,user_id,pet):
    return db.execute("""INSERT INTO pets(user_id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage)
        VALUES(%s,%s,%s,%s,1,0,%s,%s,%s,%s,1) RETURNING id""",
        (user_id,pet["name"],pet["rarity"],pet["power"],pet["attack"],pet["defense"],pet["hp"],pet["element"]) ).fetchone()["id"]


def lock_player(db,user_id):
    p=db.execute("SELECT * FROM players WHERE user_id=%s FOR UPDATE",(user_id,)).fetchone()
    if not p:
        db.execute("INSERT INTO players(user_id,referral_code) VALUES(%s,%s)",(user_id,str(user_id)))
        p=db.execute("SELECT * FROM players WHERE user_id=%s FOR UPDATE",(user_id,)).fetchone()
    return sync_energy_row(db,p)


def qreset(db,p):
    if p.get("quest_date") != today():
        db.execute("UPDATE players SET quest_date=%s,quest_summons=0,quest_wins=0,quest_trains=0,quest_merges=0 WHERE user_id=%s",(today(),p["user_id"]))
        p["quest_date"]=today(); p["quest_summons"]=p["quest_wins"]=p["quest_trains"]=p["quest_merges"]=0
    return p


def serialize_pet(p):
    d=dict(p); info=BEASTS.get(d["name"],{})
    d["art"]="/static/assets/beasts/"+info.get("art","frostpaw.png")
    d["rarity_color"]=RARITY_COLOR.get(d.get("rarity"),"#fff")
    d["element_icon"]=ELEMENT_ICONS.get(d.get("element"),"✨")
    d["xp_needed"]=beast_xp_needed(d["level"])
    return d


def achievement_payload(user_id):
    p=one("SELECT * FROM players WHERE user_id=%s",(user_id,))
    c=int(one("SELECT COUNT(*) c FROM pets WHERE user_id=%s",(user_id,))["c"])
    myth=int(one("SELECT COUNT(*) c FROM pets WHERE user_id=%s AND rarity='Mythic'",(user_id,))["c"])
    leg=int(one("SELECT COUNT(*) c FROM pets WHERE user_id=%s AND rarity IN ('Legendary','Mythic')",(user_id,))["c"])
    context={"pet_count":c,"mythic_count":myth,"legendary_or_mythic":leg}
    unlocked={r["code"] for r in all_rows("SELECT code FROM achievements WHERE user_id=%s",(user_id,))}
    rows=[]
    for code,title,desc,reward,fn in ACHIEVEMENTS:
        met=fn(p,c,leg if code=="legendary" else myth)
        rows.append({"code":code,"title":title,"description":desc,"reward":reward,"unlocked":code in unlocked,"ready":bool(met and code not in unlocked)})
    return rows


def maybe_unlock_achievements(db,user_id):
    p=db.execute("SELECT * FROM players WHERE user_id=%s FOR UPDATE",(user_id,)).fetchone()
    count=int(db.execute("SELECT COUNT(*) c FROM pets WHERE user_id=%s",(user_id,)).fetchone()["c"])
    mythic=int(db.execute("SELECT COUNT(*) c FROM pets WHERE user_id=%s AND rarity='Mythic'",(user_id,)).fetchone()["c"])
    legmy=int(db.execute("SELECT COUNT(*) c FROM pets WHERE user_id=%s AND rarity IN ('Legendary','Mythic')",(user_id,)).fetchone()["c"])
    # fn args are player, collection count, extra flag
    for code,title,desc,reward,fn in ACHIEVEMENTS:
        extra=legmy if code=="legendary" else mythic
        if fn(p,count,extra):
            exists=db.execute("SELECT 1 FROM achievements WHERE user_id=%s AND code=%s",(user_id,code)).fetchone()
            if not exists: db.execute("INSERT INTO achievements(user_id,code) VALUES(%s,%s)", (user_id,code)); db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s",(reward,user_id))


def clan_payload(user_id):
    return one("SELECT c.id,c.name,c.owner_id,c.level,c.xp,c.created_at FROM clans c JOIN clan_members m ON m.clan_id=c.id WHERE m.user_id=%s",(user_id,))


def require_amount(body,key,allowed=None,minimum=0):
    try: v=int(body.get(key))
    except: raise HTTPException(400,f"Invalid {key}")
    if v<minimum or (allowed and v not in allowed): raise HTTPException(400,f"Invalid {key}")
    return v

# ---------- Telegram bot shell ----------

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 OPEN AETHERBEASTS",web_app=WebAppInfo(url=RENDER_EXTERNAL_URL+"/app"))]] if RENDER_EXTERNAL_URL else [[InlineKeyboardButton(text="Use the Mini App",callback_data="noop")]])

@dp.message(CommandStart())
async def start(m:Message):
    text="🐲 <b>AetherBeasts</b>\n\nCollect • Evolve • Battle\n\nОткрой игру кнопкой ниже."
    await m.answer(text,reply_markup=main_kb())

@dp.message(Command("help"))
async def help_cmd(m:Message):
    await m.answer("🎮 AetherBeasts — Mini App RPG.\nОткрой игру кнопкой ниже.",reply_markup=main_kb())

@dp.callback_query(F.data=="noop")
async def noop(c:CallbackQuery): await c.answer("Откройте Mini App из меню бота")

# ---------- FastAPI ----------
MINIAPP_DIR=os.path.join(os.path.dirname(__file__),"static")
app=None

async def configure_webhook(base_url:str):
    url=base_url.rstrip("/")+"/webhook"
    kwargs={"url":url,"drop_pending_updates":False}
    if WEBHOOK_SECRET: kwargs["secret_token"]=WEBHOOK_SECRET
    await bot.set_webhook(**kwargs)
    if base_url:
        try: await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🎮 Play",web_app=WebAppInfo(url=base_url.rstrip("/")+"/app")))
        except Exception: log.exception("Menu button setup failed")

@asynccontextmanager
async def lifespan(_):
    init_db()
    if RENDER_EXTERNAL_URL:
        try: await configure_webhook(RENDER_EXTERNAL_URL)
        except Exception: log.exception("Webhook setup failed")
    yield
    try: await bot.session.close()
    except Exception: pass

app=FastAPI(title="AetherBeasts",version=APP_VERSION,lifespan=lifespan)
app.mount("/static",StaticFiles(directory=MINIAPP_DIR),name="static")

@app.get("/")
async def root(): return {"status":"online","game":"AetherBeasts","version":APP_VERSION}

@app.get("/healthz")
async def healthz(): one("SELECT 1"); return {"status":"healthy"}

@app.get("/app")
async def app_page(): return FileResponse(os.path.join(MINIAPP_DIR,"index.html"))

@app.get("/set-webhook")
async def set_webhook(request:Request):
    if WEBHOOK_SECRET and request.query_params.get("secret")!=WEBHOOK_SECRET: raise HTTPException(403,"Forbidden")
    base=RENDER_EXTERNAL_URL or str(request.base_url).rstrip("/"); await configure_webhook(base); return {"ok":True,"webhook":base+"/webhook"}

@app.post("/webhook")
async def webhook(request:Request):
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token")!=WEBHOOK_SECRET: raise HTTPException(403,"Forbidden")
    try:
        upd=Update.model_validate(await request.json(),context={"bot":bot}); await dp.feed_update(bot,upd); return {"ok":True}
    except Exception: log.exception("Webhook update failed"); raise HTTPException(500,"Update processing failed")


def validate_init_data(raw:str):
    if not raw: raise HTTPException(401,"Open AetherBeasts from Telegram")
    try:
        pairs=dict(parse_qsl(raw,keep_blank_values=True)); recv=pairs.pop("hash",None)
        if not recv: raise ValueError("missing hash")
        check="\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,recv): raise ValueError("bad signature")
        auth=int(pairs.get("auth_date","0"))
        if not auth or int(utcnow().timestamp())-auth>86400: raise ValueError("expired")
        user=json.loads(pairs.get("user","{}"));
        if not user.get("id"): raise ValueError("missing user")
        return user
    except HTTPException: raise
    except Exception as e: log.warning("Mini App auth: %s",e); raise HTTPException(401,"Telegram authorization failed")

def mini_user(request:Request): return validate_init_data(request.headers.get("X-Telegram-Init-Data",""))

@app.get("/api/state")
async def api_state(request:Request):
    u=mini_user(request); uid=int(u["id"])
    with conn() as db:
        p=lock_player(db,uid); p=qreset(db,p)
        pets=db.execute("SELECT id,name,rarity,power,level,xp,attack,defense,hp,element,evolution_stage,favorite FROM pets WHERE user_id=%s ORDER BY favorite DESC,power DESC,id DESC",(uid,)).fetchall()
        inv=db.execute("SELECT item_key,qty FROM inventory WHERE user_id=%s AND qty>0 ORDER BY item_key",(uid,)).fetchall()
    p=dict(p); p["energy"]=int(p["energy"])
    return {"player":p,"pets":[serialize_pet(x) for x in pets],"top_pet":serialize_pet(pets[0]) if pets else None,
            "xp_needed":xp_needed(p["level"]),"energy_max":ENERGY_MAX,"rates":RARITY_WEIGHTS,"daily": {"claimed":p.get("daily_date")==today(),"streak":p.get("daily_streak",0)},
            "inventory":inv,"clan":clan_payload(uid),"achievements":achievement_payload(uid)}

@app.post("/api/daily")
async def api_daily(request:Request):
    u=mini_user(request); uid=int(u["id"])
    with conn() as db:
        p=lock_player(db,uid)
        if p["daily_date"]==today(): raise HTTPException(400,"Daily reward already claimed")
        streak=p["daily_streak"]+1 if p["daily_date"] and (today()-p["daily_date"]).days==1 else 1
        reward=DAILY_REWARDS[min(streak-1,len(DAILY_REWARDS)-1)]
        db.execute("UPDATE players SET aether=aether+%s,daily_date=%s,daily_streak=%s,energy=LEAST(100,energy+20) WHERE user_id=%s",(reward,today(),streak,uid))
        db.execute("UPDATE players SET energy_updated_at=%s WHERE user_id=%s",(utcnow(),uid))
        return {"reward":reward,"streak":streak}

@app.post("/api/summon")
async def api_summon(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); count=require_amount(body,"count",{1,10})
    with conn() as db:
        p=lock_player(db,uid); p=qreset(db,p)
        cost=SUMMON_COST_1 if count==1 else SUMMON_COST_10; energy_need=SUMMON_ENERGY*(1 if count==1 else 10)
        if p["aether"]<cost: raise HTTPException(400,"Not enough AETHER")
        if p["energy"]<energy_need: raise HTTPException(400,"Not enough Energy")
        lucky=db.execute("SELECT qty FROM inventory WHERE user_id=%s AND item_key='lucky_charm' FOR UPDATE",(uid,)).fetchone(); lucky=bool(lucky and lucky["qty"]>0)
        if lucky: db.execute("UPDATE inventory SET qty=qty-1 WHERE user_id=%s AND item_key='lucky_charm'",(uid,))
        db.execute("UPDATE players SET aether=aether-%s,energy=energy-%s,energy_updated_at=%s,summons=summons+%s,quest_summons=quest_summons+%s WHERE user_id=%s",(cost,energy_need,utcnow(),count,count,uid))
        results=[]
        for _ in range(count):
            pet=generate_beast(lucky=lucky); pet["id"]=insert_beast(db,uid,pet); results.append(pet)
        add_player_xp_tx(db,uid,XP_SUMMON*count); maybe_unlock_achievements(db,uid)
    return {"results":results,"cost":cost,"lucky":lucky}

@app.get("/api/quests")
async def api_quests(request:Request):
    u=mini_user(request); uid=int(u["id"])
    with conn() as db:
        p=qreset(db,lock_player(db,uid))
        claimed={r["code"] for r in db.execute("SELECT code FROM daily_quest_claims WHERE user_id=%s AND quest_date=%s",(uid,today())).fetchall()}
    vals={"summons":p["quest_summons"],"wins":p["quest_wins"],"trains":p["quest_trains"],"merges":p["quest_merges"]}
    out=[]
    for code,(title,key,target,reward) in QUESTS.items():
        progress=min(target,int(vals[key])); out.append({"code":code,"title":title,"progress":progress,"target":target,"reward":reward,"done":progress>=target,"claimed":code in claimed})
    return {"date":str(today()),"quests":out}

@app.post("/api/quests/claim")
async def api_quest_claim(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); code=str(body.get("code",""))
    if code not in QUESTS: raise HTTPException(400,"Invalid quest")
    title,key,target,reward=QUESTS[code]
    with conn() as db:
        p=qreset(db,lock_player(db,uid)); progress=p[{"summons":"quest_summons","wins":"quest_wins","trains":"quest_trains","merges":"quest_merges"}[key]]
        exists=db.execute("SELECT 1 FROM daily_quest_claims WHERE user_id=%s AND quest_date=%s AND code=%s",(uid,today(),code)).fetchone()
        if exists: raise HTTPException(400,"Quest already claimed")
        if progress<target: raise HTTPException(400,"Quest not completed")
        db.execute("INSERT INTO daily_quest_claims(user_id,quest_date,code) VALUES(%s,%s,%s)",(uid,today(),code)); db.execute("UPDATE players SET aether=aether+%s WHERE user_id=%s",(reward,uid)); add_player_xp_tx(db,uid,reward//2)
    return {"code":code,"reward":reward,"xp":reward//2}

@app.get("/api/achievements")
async def api_achievements(request:Request):
    u=mini_user(request); return {"achievements":achievement_payload(int(u["id"]))}

@app.post("/api/achievements/claim")
async def api_achievement_claim(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); code=str(body.get("code","")); found={x[0]:x for x in ACHIEVEMENTS}.get(code)
    if not found: raise HTTPException(400,"Invalid achievement")
    title,desc,reward=found[1],found[2],found[3]
    with conn() as db:
        maybe_unlock_achievements(db,uid)
        row=db.execute("SELECT 1 FROM achievements WHERE user_id=%s AND code=%s",(uid,code)).fetchone()
        if not row: raise HTTPException(400,"Achievement is not unlocked")
        # achievements are paid automatically when first unlocked; claim endpoint is informational.
    return {"code":code,"reward":reward,"claimed":True}

@app.post("/api/pets/train")
async def api_train(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); pet_id=require_amount(body,"pet_id",minimum=1)
    with conn() as db:
        p=qreset(db,lock_player(db,uid)); pet=db.execute("SELECT * FROM pets WHERE id=%s AND user_id=%s FOR UPDATE",(pet_id,uid)).fetchone()
        if not pet: raise HTTPException(404,"Beast not found")
        if pet["level"]>=MAX_BEAST_LEVEL: raise HTTPException(400,"Beast is max level")
        cost=25+pet["level"]*15
        if p["aether"]<cost: raise HTTPException(400,"Not enough AETHER")
        if p["energy"]<TRAIN_ENERGY: raise HTTPException(400,"Not enough Energy")
        gain=100+pet["level"]*20; new_xp=pet["xp"]+gain; lvl=pet["level"]
        while lvl<MAX_BEAST_LEVEL and new_xp>=beast_xp_needed(lvl): new_xp-=beast_xp_needed(lvl); lvl+=1
        ratio=lvl/pet["level"] if pet["level"] else 1
        power=max(1,int(pet["power"]*ratio**0.65)); atk=max(1,int(pet["attack"]*ratio**0.60)); df=max(1,int(pet["defense"]*ratio**0.60)); hp=max(1,int(pet["hp"]*ratio**0.65))
        db.execute("UPDATE players SET aether=aether-%s,energy=energy-%s,energy_updated_at=%s,trains=trains+1,quest_trains=quest_trains+1 WHERE user_id=%s",(cost,TRAIN_ENERGY,utcnow(),uid))
        db.execute("UPDATE pets SET level=%s,xp=%s,power=%s,attack=%s,defense=%s,hp=%s WHERE id=%s",(lvl,new_xp,power,atk,df,hp,pet_id))
        add_player_xp_tx(db,uid,50); maybe_unlock_achievements(db,uid)
    return {"level":lvl,"xp":new_xp,"power":power,"cost":cost}

@app.post("/api/pets/favorite")
async def api_favorite(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); pet_id=require_amount(body,"pet_id",minimum=1)
    with conn() as db:
        if not db.execute("SELECT 1 FROM pets WHERE id=%s AND user_id=%s",(pet_id,uid)).fetchone(): raise HTTPException(404,"Beast not found")
        db.execute("UPDATE pets SET favorite=NOT favorite WHERE id=%s AND user_id=%s",(pet_id,uid))
    return {"ok":True}

@app.get("/api/merge/options")
async def api_merge_options(request:Request):
    u=mini_user(request); uid=int(u["id"])
    rows=all_rows("""SELECT name,rarity,COUNT(*) count FROM pets WHERE user_id=%s GROUP BY name,rarity HAVING COUNT(*)>=3 ORDER BY COUNT(*) DESC""",(uid,))
    opts=[]
    for r in rows:
        next_name=BEASTS.get(r["name"],{}).get("next")
        if not next_name: continue
        nr=BEASTS[next_name]["rarity"]
        opts.append({"name":r["name"],"rarity":r["rarity"],"count":int(r["count"]),"next_name":next_name,"next_rarity":nr})
    return {"options":opts}

@app.post("/api/merge")
async def api_merge(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); name=str(body.get("name",""))
    if name not in BEASTS or not BEASTS[name].get("next"): raise HTTPException(400,"Merge option unavailable")
    nxt=BEASTS[name]["next"]
    with conn() as db:
        p=qreset(db,lock_player(db,uid)); selected=db.execute("SELECT id,power FROM pets WHERE user_id=%s AND name=%s ORDER BY power ASC LIMIT 3 FOR UPDATE",(uid,name)).fetchall()
        if len(selected)<3: raise HTTPException(400,"Need three identical Beasts")
        ids=[x["id"] for x in selected]; total=sum(x["power"] for x in selected); nr=BEASTS[nxt]["rarity"]; lo,hi=RARITY_POWER[nr]; power=max(lo,min(hi,int(total*random.uniform(.78,1.04))))
        db.execute("DELETE FROM pets WHERE id=ANY(%s)",(ids,)); newpet=generate_beast(nr,nxt); newpet["power"]=power; base=BEASTS[nxt]["base"]; factor=power/max(1,sum(base)/3); newpet["hp"]=max(20,int(base[0]*factor)); newpet["attack"]=max(5,int(base[1]*factor)); newpet["defense"]=max(4,int(base[2]*factor)); newpet["id"]=insert_beast(db,uid,newpet)
        db.execute("UPDATE players SET merges=merges+1,quest_merges=quest_merges+1 WHERE user_id=%s",(uid,)); add_player_xp_tx(db,uid,XP_MERGE); maybe_unlock_achievements(db,uid)
    return {"pet":newpet}

@app.post("/api/battle")
async def api_battle(request:Request):
    u=mini_user(request); uid=int(u["id"])
    with conn() as db:
        p=lock_player(db,uid); p=qreset(db,p)
        last=p.get("last_battle_at")
        if last:
            elapsed=(utcnow()-last).total_seconds()
            if elapsed<BATTLE_COOLDOWN: raise HTTPException(429,f"Arena cooldown: {int(BATTLE_COOLDOWN-elapsed)}s")
        if p["energy"]<BATTLE_ENERGY: raise HTTPException(400,"Not enough Energy")
        pet=db.execute("SELECT * FROM pets WHERE user_id=%s ORDER BY power DESC,favorite DESC LIMIT 1",(uid,)).fetchone()
        if not pet: raise HTTPException(400,"You need a Beast first")
        enemy_name,_,_=random.choice(ENEMIES); enemy_power=random.randint(max(30,p["level"]*28),max(60,p["level"]*70+120))
        elixir=db.execute("SELECT qty FROM inventory WHERE user_id=%s AND item_key='battle_elixir' FOR UPDATE",(uid,)).fetchone(); boosted=bool(elixir and elixir["qty"]>0)
        if boosted: db.execute("UPDATE inventory SET qty=qty-1 WHERE user_id=%s AND item_key='battle_elixir'",(uid,))
        player_power=int((pet["power"]+pet["attack"]*.25+pet["defense"]*.15)*(1.15 if boosted else 1.0))
        won=(player_power+random.randint(-30,30))>=enemy_power
        reward=random.randint(45+p["level"]*2,90+p["level"]*5) if won else random.randint(15,30+p["level"]); xp=XP_BATTLE_WIN if won else XP_BATTLE_LOSS
        db.execute("UPDATE players SET aether=aether+%s,energy=energy-%s,energy_updated_at=%s,battles=battles+1,wins=wins+%s,losses=losses+%s,last_battle_at=%s,quest_wins=quest_wins+%s WHERE user_id=%s",(reward,BATTLE_ENERGY,utcnow(),1 if won else 0,0 if won else 1,utcnow(),1 if won else 0,uid))
        db.execute("INSERT INTO battle_logs(user_id,won,player_power,enemy_power,reward) VALUES(%s,%s,%s,%s,%s)",(uid,won,player_power,enemy_power,reward)); add_player_xp_tx(db,uid,xp); maybe_unlock_achievements(db,uid)
    return {"won":won,"pet":serialize_pet(pet),"enemy":{"name":enemy_name,"power":enemy_power},"player_power":player_power,"reward":reward,"xp":xp,"elixir":boosted}

@app.get("/api/battle/history")
async def api_battle_history(request:Request):
    u=mini_user(request); rows=all_rows("SELECT won,player_power,enemy_power,reward,created_at FROM battle_logs WHERE user_id=%s ORDER BY id DESC LIMIT 20",(int(u["id"]),)); return {"rows":rows}

@app.get("/api/inventory")
async def api_inventory(request:Request):
    u=mini_user(request); uid=int(u["id"]); rows=all_rows("SELECT item_key,qty FROM inventory WHERE user_id=%s AND qty>0 ORDER BY item_key",(uid,)); return {"items":[{**dict(r),**SHOP.get(r["item_key"],{})} for r in rows]}

@app.post("/api/inventory/use")
async def api_inventory_use(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); key=str(body.get("item_key",""))
    if key not in SHOP: raise HTTPException(400,"Invalid item")
    with conn() as db:
        p=lock_player(db,uid); row=db.execute("SELECT qty FROM inventory WHERE user_id=%s AND item_key=%s FOR UPDATE",(uid,key)).fetchone()
        if not row or row["qty"]<=0: raise HTTPException(400,"Item unavailable")
        if key in ("lucky_charm","battle_elixir"): raise HTTPException(400,"This item activates automatically")
        db.execute("UPDATE inventory SET qty=qty-1 WHERE user_id=%s AND item_key=%s",(uid,key))
        if key=="xp_potion": add_player_xp_tx(db,uid,100); result={"xp":100}
        elif key=="energy_cell": db.execute("UPDATE players SET energy=LEAST(100,energy+30),energy_updated_at=%s WHERE user_id=%s",(utcnow(),uid)); result={"energy":30}
        elif key=="aether_crystal": db.execute("UPDATE players SET aether=aether+250 WHERE user_id=%s",(uid,)); result={"aether":250}
        else: result={}
    return {"ok":True,"item_key":key,"result":result}

@app.get("/api/shop")
async def api_shop(request:Request): mini_user(request); return {"items":[{"key":k,**v} for k,v in SHOP.items()]}

@app.post("/api/shop/buy")
async def api_shop_buy(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); key=str(body.get("item_key","")); qty=require_amount(body,"qty",minimum=1)
    if key not in SHOP or qty>20: raise HTTPException(400,"Invalid purchase")
    with conn() as db:
        p=lock_player(db,uid); total=SHOP[key]["price"]*qty
        if p["aether"]<total: raise HTTPException(400,"Not enough AETHER")
        db.execute("UPDATE players SET aether=aether-%s WHERE user_id=%s",(total,uid)); db.execute("INSERT INTO inventory(user_id,item_key,qty) VALUES(%s,%s,%s) ON CONFLICT(user_id,item_key) DO UPDATE SET qty=inventory.qty+EXCLUDED.qty",(uid,key,qty))
    return {"ok":True,"item_key":key,"qty":qty,"spent":total}

@app.get("/api/leaderboard")
async def api_leaderboard(request:Request):
    mini_user(request); rows=all_rows("""SELECT p.user_id,COALESCE(NULLIF(p.username,''),'Player') username,p.level,p.wins,p.summons,p.merges,COALESCE(SUM(pt.power),0)::BIGINT power FROM players p LEFT JOIN pets pt ON pt.user_id=p.user_id GROUP BY p.user_id ORDER BY power DESC,p.level DESC,p.wins DESC LIMIT 50"""); return {"rows":rows}

@app.get("/api/clan")
async def api_clan(request:Request):
    u=mini_user(request); uid=int(u["id"]); clan=clan_payload(uid); members=[]
    if clan: members=all_rows("SELECT p.username,p.level,p.wins FROM clan_members m JOIN players p ON p.user_id=m.user_id WHERE m.clan_id=%s ORDER BY p.level DESC,p.wins DESC LIMIT 50",(clan["id"],))
    return {"clan":clan,"members":members}

@app.post("/api/clan/create")
async def api_clan_create(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); name=str(body.get("name","")).strip()
    if len(name)<3 or len(name)>20 or not all(ch.isalnum() or ch in " _-" for ch in name): raise HTTPException(400,"Clan name must be 3-20 letters/numbers")
    with conn() as db:
        if db.execute("SELECT 1 FROM clan_members WHERE user_id=%s",(uid,)).fetchone(): raise HTTPException(400,"You are already in a clan")
        row=db.execute("INSERT INTO clans(name,owner_id) VALUES(%s,%s) RETURNING id,name,owner_id,level,xp",(name,uid)).fetchone(); db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)",(row["id"],uid))
    return {"clan":row}

@app.post("/api/clan/join")
async def api_clan_join(request:Request):
    u=mini_user(request); uid=int(u["id"]); body=await request.json(); name=str(body.get("name","")).strip()
    with conn() as db:
        if db.execute("SELECT 1 FROM clan_members WHERE user_id=%s",(uid,)).fetchone(): raise HTTPException(400,"You are already in a clan")
        clan=db.execute("SELECT * FROM clans WHERE lower(name)=lower(%s)",(name,)).fetchone()
        if not clan: raise HTTPException(404,"Clan not found")
        db.execute("INSERT INTO clan_members(clan_id,user_id) VALUES(%s,%s)",(clan["id"],uid))
    return {"clan":clan}

@app.post("/api/clan/leave")
async def api_clan_leave(request:Request):
    u=mini_user(request); uid=int(u["id"])
    with conn() as db:
        clan=db.execute("SELECT c.* FROM clans c JOIN clan_members m ON m.clan_id=c.id WHERE m.user_id=%s FOR UPDATE",(uid,)).fetchone()
        if not clan: raise HTTPException(400,"You are not in a clan")
        if clan["owner_id"]==uid:
            members=db.execute("SELECT user_id FROM clan_members WHERE clan_id=%s AND user_id<>%s ORDER BY joined_at LIMIT 1",(clan["id"],uid)).fetchone()
            if members: db.execute("UPDATE clans SET owner_id=%s WHERE id=%s",(members["user_id"],clan["id"]))
            else: db.execute("DELETE FROM clan_members WHERE clan_id=%s",(clan["id"],)); db.execute("DELETE FROM clans WHERE id=%s",(clan["id"],)); return {"ok":True}
        db.execute("DELETE FROM clan_members WHERE user_id=%s",(uid,))
    return {"ok":True}

@app.get("/api/profile")
async def api_profile(request:Request):
    u=mini_user(request); uid=int(u["id"]); p=one("SELECT * FROM players WHERE user_id=%s",(uid,)); pet_count=int(one("SELECT COUNT(*) c FROM pets WHERE user_id=%s",(uid,))["c"]); power=int(one("SELECT COALESCE(SUM(power),0) p FROM pets WHERE user_id=%s",(uid,))["p"]); return {"player":p,"pet_count":pet_count,"power":power}

if __name__=="__main__":
    import uvicorn; uvicorn.run("bot:app",host="0.0.0.0",port=int(os.getenv("PORT","8000")))
