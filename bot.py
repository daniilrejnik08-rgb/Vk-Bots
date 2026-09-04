import asyncio
import hashlib
import json
import os
import random
import re
import secrets
from datetime import datetime, date, timedelta

import aiosqlite
from dotenv import load_dotenv
from vkbottle import BaseStateGroup, BuiltinStateDispenser, Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import PayloadRule

load_dotenv()

TOKEN = os.getenv("TOKEN", "").strip()
ENV_ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "854071888").split(",") if x.strip().isdigit()]
if not ENV_ADMINS:
    ENV_ADMINS = [854071888]

DB_NAME = os.path.join(os.getenv("DATA_DIR", "."), "crmp_bot.db")
PROJECT = "Prp Bot"
PROJECT_SHORT = "Rpr Games"
SERVER_LABEL = "Rpr Games | Основной сервер"

DAILY_BONUS = 500
WORK_MIN = 150
WORK_MAX = 500
WORK_COOLDOWN_SEC = 300
REF_BONUS = 300
SPAM_LIMIT = 6  # сообщений
SPAM_WINDOW = 20  # секунд
# Авто фейк вход/выход: случайный интервал (минуты). 0 = выкл
AUTO_FAKE_MIN_MINUTES = 8
AUTO_FAKE_MAX_MINUTES = 45

NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

FRACTIONS = ["Полиция", "Медики", "Армия", "Мафия", "Правительство", "Такси", "Механики", "ФСБ"]

# Автобан по словам (нижний регистр)
BAN_WORDS = ["свобвк", "бесплатный вк", "накрутка", "продам читы", "сдам сервер"]

FAKE_PLAYERS = {
    "Danya_Nik": {"money": 1_250_000, "donate": 500},
    "Artem_Shpets": {"money": 980_000, "donate": 250},
}

_fake_online: set[str] = set()
_maintenance = False
SITE_URL = os.getenv("SITE_URL", "").strip() or "скоро"
FORUM_URL = os.getenv("FORUM_URL", "").strip() or "скоро"
SERVER_IP_PUBLIC = "скрыт"
FOUNDER_ID = ENV_ADMINS[0] if ENV_ADMINS else 854071888
FRACTION_SALARY = 800
ONLINE_BONUS = 50
ONLINE_BONUS_HOURS = 1
CASHBACK_PERCENT = 5  # % при выдаче Prp Coin
VIP_LEVELS = {
    "bronze": {"days": 7, "price": 50, "label": "Bronze VIP"},
    "gold": {"days": 30, "price": 150, "label": "Gold VIP"},
    "platinum": {"days": 90, "price": 400, "label": "Platinum VIP"},
}
SUPPORT_TEMPLATES = {
    "1": "Здравствуйте! Ваша заявка принята, ожидайте ответа.",
    "2": "Опишите проблему подробнее и приложите скрин, если есть.",
    "3": "Вопрос решён. Если останутся проблемы — создайте новый тикет.",
    "4": "По правилам сервера это запрещено. Подробнее: кнопка «Информация».",
    "5": "Лаунчер ещё в разработке. Следите за новостями проекта.",
}

COIN_SHOP = {
    "VIP 7 дней": (50, "VIP на неделю"),
    "VIP 30 дней": (150, "VIP на месяц"),
    "Скин авто": (80, "Уникальный скин"),
    "Игровые 5000₽": (30, "На баланс бота 5000₽"),
    "Игровые 20000₽": (100, "На баланс бота 20000₽"),
}

# Роли: owner > mod > helper
ROLE_RANK = {"owner": 3, "mod": 2, "helper": 1}
_role_cache: dict[int, tuple[float, str | None]] = {}
ROLE_CACHE_TTL = 30.0

state_dispenser = BuiltinStateDispenser()
bot = Bot(token=TOKEN, state_dispenser=state_dispenser)

# антифлуд: user_id -> list[timestamps]
_flood: dict[int, list[float]] = {}


class RegState(BaseStateGroup):
    NICKNAME = 1
    EMAIL = 2
    PASSWORD = 3
    REF = 4


class IdeaState(BaseStateGroup):
    TEXT = 1


class RecoverState(BaseStateGroup):
    NICK = 1
    EMAIL = 2
    COMMENT = 3


class FracState(BaseStateGroup):
    CHOICE = 1
    MOTIVE = 2


class TicketState(BaseStateGroup):
    TEXT = 1


class ReportState(BaseStateGroup):
    TARGET = 1
    REASON = 2


class PassState(BaseStateGroup):
    OLD = 1
    NEW = 2


class PromoCreateState(BaseStateGroup):
    CODE = 1
    REWARD = 2


class EmailState(BaseStateGroup):
    NEW = 1


class LeaderState(BaseStateGroup):
    FRACTION = 1
    MOTIVE = 2


class MarketState(BaseStateGroup):
    TITLE = 1
    PRICE = 2
    PHOTO = 3


class NewsState(BaseStateGroup):
    TEXT = 1


class PartnerState(BaseStateGroup):
    TEXT = 1


class ClanState(BaseStateGroup):
    NAME = 1
    TAG = 2


class ContestState(BaseStateGroup):
    TITLE = 1


class AppealState(BaseStateGroup):
    TEXT = 1


class TwoFAState(BaseStateGroup):
    CODE = 1


# -------------------- utils --------------------
def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return digest, salt


def check_password(password: str, pwd_hash: str, salt: str) -> bool:
    d, _ = hash_password(password, salt)
    return d == pwd_hash


def mask_password(password: str) -> str:
    if len(password) <= 2:
        return "*" * len(password)
    return password[:2] + "*" * (len(password) - 2)


def now_str() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


async def notify_user(api, user_id: int, text: str, keyboard=None) -> bool:
    try:
        kwargs = {"user_id": int(user_id), "peer_id": int(user_id), "message": text, "random_id": 0}
        if keyboard is not None:
            kwargs["keyboard"] = keyboard
        await api.messages.send(**kwargs)
        return True
    except Exception:
        return False


async def react_to(message: Message, reaction_id: int = 1):
    try:
        cmid = getattr(message, "conversation_message_id", None)
        if not cmid:
            return
        await message.ctx_api.messages.send_reaction(
            peer_id=message.peer_id, cmid=int(cmid), reaction_id=int(reaction_id)
        )
    except Exception:
        pass


def apply_fake_presence(nick: str, action: str) -> None:
    global _fake_online
    if action == "join":
        _fake_online.add(nick)
    else:
        _fake_online.discard(nick)


def server_online_count() -> int:
    return len(_fake_online)


def format_server_status() -> str:
    online = server_online_count()
    players = ", ".join(sorted(_fake_online)) if _fake_online else "—"
    maint = "🔧 ТЕХРАБОТЫ" if _maintenance else "✅ Работает"
    lines = [
        f"📡 Статус сервера {PROJECT_SHORT}",
        "",
        f"Состояние: {maint}",
        f"Онлайн: {online}",
        f"Игроки: {players}",
        f"IP-адрес: {SERVER_IP_PUBLIC}",
        f"Сервер: {SERVER_LABEL}",
    ]
    return chr(10).join(lines)


def format_server_notify(nick: str, action: str, ip: str | None = None) -> str:
    apply_fake_presence(nick, action)
    data = FAKE_PLAYERS.get(nick) or {
        "money": random.randint(50_000, 5_000_000),
        "donate": random.randint(0, 2000),
    }
    money = f"{data['money']:,}".replace(",", " ")
    donate = data["donate"]
    if action == "join":
        head = f"Ваш персонаж {nick} вошёл на сервер"
    else:
        head = f"Ваш персонаж {nick} покинул сервер"
    online = server_online_count()
    lines = [
        head,
        f"IP-адрес: {SERVER_IP_PUBLIC}",
        "",
        f"Деньги: {money} руб.",
        f"Донат: {donate} Prp Coin",
        "",
        f"Онлайн на сервере: {online}",
        f"— Сервер: {SERVER_LABEL}",
    ]
    return chr(10).join(lines)



# -------------------- DB --------------------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                nickname TEXT UNIQUE,
                email TEXT,
                password_hash TEXT,
                password_salt TEXT,
                status TEXT DEFAULT 'approved',
                registered_at TEXT,
                balance INTEGER DEFAULT 1000,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                last_daily TEXT,
                last_work TEXT,
                banned INTEGER DEFAULT 0,
                referred_by INTEGER,
                referral_code TEXT UNIQUE,
                fraction TEXT
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item TEXT,
                amount INTEGER DEFAULT 1,
                UNIQUE(user_id, item)
            );
            CREATE TABLE IF NOT EXISTS achievements (
                user_id INTEGER,
                code TEXT,
                title TEXT,
                unlocked_at TEXT,
                PRIMARY KEY (user_id, code)
            );
            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'helper',
                added_by INTEGER,
                added_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nickname TEXT,
                text TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                options TEXT,
                active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS vote_answers (
                vote_id INTEGER,
                user_id INTEGER,
                option_idx INTEGER,
                PRIMARY KEY (vote_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS fraction_apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nickname TEXT,
                fraction TEXT,
                motive TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nickname TEXT,
                text TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT,
                closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS ticket_msgs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                from_admin INTEGER,
                text TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                reward_money INTEGER DEFAULT 0,
                reward_item TEXT,
                max_uses INTEGER DEFAULT 1,
                uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_by INTEGER
            );
            CREATE TABLE IF NOT EXISTS promo_uses (
                code TEXT,
                user_id INTEGER,
                used_at TEXT,
                PRIMARY KEY (code, user_id)
            );
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target TEXT,
                details TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                target_nick TEXT,
                reason TEXT,
                status TEXT DEFAULT 'new',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                event_at TEXT,
                created_by INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            
            CREATE TABLE IF NOT EXISTS market (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER,
                title TEXT,
                price INTEGER,
                photo TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                amount INTEGER,
                meta TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pc_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item TEXT,
                price INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                admin_id INTEGER,
                reason TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS wanted (
                user_id INTEGER PRIMARY KEY,
                nickname TEXT,
                reason TEXT,
                stars INTEGER DEFAULT 1,
                by_admin INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                author_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS event_regs (
                event_id INTEGER,
                user_id INTEGER,
                created_at TEXT,
                PRIMARY KEY (event_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS contests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS contest_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contest_id INTEGER,
                user_id INTEGER,
                photo TEXT,
                votes INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS reputation (
                from_id INTEGER,
                to_id INTEGER,
                value INTEGER,
                created_at TEXT,
                PRIMARY KEY (from_id, to_id)
            );
            CREATE TABLE IF NOT EXISTS badges (
                user_id INTEGER,
                badge TEXT,
                PRIMARY KEY (user_id, badge)
            );
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS clans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                tag TEXT UNIQUE,
                leader_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS clan_members (
                clan_id INTEGER,
                user_id INTEGER,
                role TEXT DEFAULT 'member',
                PRIMARY KEY (clan_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS leader_apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                nickname TEXT,
                fraction TEXT,
                motive TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS activity_log (
                user_id INTEGER,
                day TEXT,
                points INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, day)
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                run_at TEXT,
                done INTEGER DEFAULT 0,
                created_by INTEGER
            );
            """
        )
        # soft migrations for old DBs
        for col, ddl in [
            ("referred_by", "ALTER TABLE players ADD COLUMN referred_by INTEGER"),
            ("referral_code", "ALTER TABLE players ADD COLUMN referral_code TEXT"),
            ("fraction", "ALTER TABLE players ADD COLUMN fraction TEXT"),
            ("prp_coin", "ALTER TABLE players ADD COLUMN prp_coin INTEGER DEFAULT 0"),
            ("sub_until", "ALTER TABLE players ADD COLUMN sub_until TEXT"),
            ("last_seen", "ALTER TABLE players ADD COLUMN last_seen TEXT"),
            ("clan_id", "ALTER TABLE players ADD COLUMN clan_id INTEGER"),
            ("last_frac_salary", "ALTER TABLE players ADD COLUMN last_frac_salary TEXT"),
            ("last_online_bonus", "ALTER TABLE players ADD COLUMN last_online_bonus TEXT"),
            ("vip_until", "ALTER TABLE players ADD COLUMN vip_until TEXT"),
            ("vip_level", "ALTER TABLE players ADD COLUMN vip_level TEXT"),
            ("twofa", "ALTER TABLE players ADD COLUMN twofa INTEGER DEFAULT 0"),
        ]:
            try:
                await db.execute(ddl)
            except Exception:
                pass
        try:
            await db.execute("ALTER TABLE bot_admins ADD COLUMN role TEXT DEFAULT 'helper'")
        except Exception:
            pass
        for extra_sql in (
            "ALTER TABLE tickets ADD COLUMN curator_id INTEGER",
            "ALTER TABLE tickets ADD COLUMN curator_note TEXT",
        ):
            try:
                await db.execute(extra_sql)
            except Exception:
                pass
        await db.commit()


async def get_player(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_by_nick(nick: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE lower(nickname) = lower(?)", (nick,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_by_email(email: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE lower(email) = lower(?)", (email,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_player_by_ref(code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE lower(referral_code) = lower(?)", (code,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def nick_taken(nick: str) -> bool:
    return await get_player_by_nick(nick) is not None


def make_ref_code(nick: str) -> str:
    return (nick[:8] + secrets.token_hex(2)).upper()


async def create_player(user_id: int, nickname: str, email: str, password: str, referred_by: int | None = None):
    pwd_hash, salt = hash_password(password)
    ref = make_ref_code(nickname)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO players (
                user_id, nickname, email, password_hash, password_salt,
                status, registered_at, balance, referred_by, referral_code
            ) VALUES (?, ?, ?, ?, ?, 'approved', ?, 1000, ?, ?)
            """,
            (user_id, nickname, email, pwd_hash, salt, now_str(), referred_by, ref),
        )
        await db.commit()
    return ref


async def set_password(user_id: int, password: str):
    pwd_hash, salt = hash_password(password)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET password_hash = ?, password_salt = ? WHERE user_id = ?",
            (pwd_hash, salt, user_id),
        )
        await db.commit()


async def set_fraction(user_id: int, fraction: str | None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET fraction = ? WHERE user_id = ?", (fraction, user_id))
        await db.commit()


async def unlock_achievement(user_id: int, code: str, title: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO achievements (user_id, code, title, unlocked_at) VALUES (?, ?, ?, ?)",
                (user_id, code, title, now_str()),
            )
            await db.commit()
            return True
        except Exception:
            return False


async def list_achievements(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT title, unlocked_at FROM achievements WHERE user_id = ? ORDER BY unlocked_at",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def resolve_player(who: str):
    who = (who or "").strip()
    if not who:
        return None
    if who.isdigit():
        return await get_player(int(who))
    if who.lower().startswith("id") and who[2:].isdigit():
        return await get_player(int(who[2:]))
    if "@" in who:
        return await get_player_by_email(who)
    return await get_player_by_nick(who)


async def add_balance(user_id: int, amount: int, meta: str = "balance"):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    try:
        await log_tx(user_id, meta, amount)
    except Exception:
        pass


async def add_prp_coin(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET prp_coin = COALESCE(prp_coin, 0) + ? WHERE user_id = ?",
            (amount, user_id),
        )
        await db.commit()


async def set_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET balance = ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def set_banned(user_id: int, banned: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET banned = ? WHERE user_id = ?", (banned, user_id))
        await db.commit()


async def set_level(user_id: int, level: int, exp: int = 0):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET level = ?, exp = ? WHERE user_id = ?", (level, exp, user_id)
        )
        await db.commit()


async def add_exp(user_id: int, amount: int):
    player = await get_player(user_id)
    if not player:
        return False
    exp = player["exp"] + amount
    level = player["level"]
    leveled = False
    while exp >= level * 1000:
        exp -= level * 1000
        level += 1
        leveled = True
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET exp = ?, level = ? WHERE user_id = ?", (exp, level, user_id)
        )
        await db.commit()
    return leveled


async def set_last_daily(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_daily = ? WHERE user_id = ?", (date.today().isoformat(), user_id)
        )
        await db.commit()


async def set_last_work(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_work = ? WHERE user_id = ?",
            (datetime.now().isoformat(), user_id),
        )
        await db.commit()


async def reset_cooldowns(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_daily = NULL, last_work = NULL WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def delete_player(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM achievements WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_all_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, status, level, balance, banned, email FROM players ORDER BY registered_at DESC"
        ) as cur:
            return await cur.fetchall()


async def get_banned_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, nickname FROM players WHERE banned = 1") as cur:
            return await cur.fetchall()


async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async def cnt(sql):
            async with db.execute(sql) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

        return {
            "total": await cnt("SELECT COUNT(*) FROM players"),
            "banned": await cnt("SELECT COUNT(*) FROM players WHERE banned = 1"),
            "money": await cnt("SELECT COALESCE(SUM(balance), 0) FROM players"),
            "ideas": await cnt("SELECT COUNT(*) FROM ideas WHERE status = 'new'"),
            "votes": await cnt("SELECT COUNT(*) FROM votes WHERE active = 1"),
            "tickets": await cnt("SELECT COUNT(*) FROM tickets WHERE status = 'open'"),
            "reports": await cnt("SELECT COUNT(*) FROM reports WHERE status = 'new'"),
            "fracs": await cnt("SELECT COUNT(*) FROM fraction_apps WHERE status = 'pending'"),
        }


async def get_top(limit: int = 10):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT nickname, level, balance FROM players WHERE banned = 0 ORDER BY balance DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def inv_add(user_id: int, item: str, amount: int = 1):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO inventory (user_id, item, amount) VALUES (?, ?, ?)
            ON CONFLICT(user_id, item) DO UPDATE SET amount = amount + ?
            """,
            (user_id, item, amount, amount),
        )
        await db.commit()


async def inv_get(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT item, amount FROM inventory WHERE user_id = ? AND amount > 0", (user_id,)
        ) as cur:
            return await cur.fetchall()


# admins / roles
async def db_admins():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, role FROM bot_admins") as cur:
            return await cur.fetchall()


async def get_role(uid: int) -> str | None:
    import time
    now = time.time()
    cached = _role_cache.get(uid)
    if cached and now - cached[0] < ROLE_CACHE_TTL:
        return cached[1]
    role = None
    if uid in ENV_ADMINS:
        role = "owner"
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT role FROM bot_admins WHERE user_id = ?", (uid,)) as cur:
                row = await cur.fetchone()
                role = row[0] if row else None
    _role_cache[uid] = (now, role)
    return role


async def is_admin(uid: int) -> bool:
    return await get_role(uid) is not None


async def is_owner(uid: int) -> bool:
    return await get_role(uid) == "owner"


async def has_role(uid: int, min_role: str) -> bool:
    role = await get_role(uid)
    if not role:
        return False
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(min_role, 99)


async def add_bot_admin(user_id: int, by: int, role: str = "helper"):
    role = role if role in ROLE_RANK else "helper"
    _role_cache.pop(user_id, None)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO bot_admins (user_id, role, added_by, added_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET role = excluded.role
            """,
            (user_id, role, by, now_str()),
        )
        await db.commit()


async def remove_bot_admin(user_id: int):
    _role_cache.pop(user_id, None)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def all_admin_ids():
    ids = set(ENV_ADMINS)
    for uid, _ in await db_admins():
        ids.add(uid)
    return ids


async def notify_admins(api, text: str) -> int:
    ok = 0
    for aid in await all_admin_ids():
        if await notify_user(api, aid, text):
            ok += 1
    return ok


async def admin_log(admin_id: int, action: str, target: str = "", details: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO admin_logs (admin_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (admin_id, action, target, details, now_str()),
        )
        await db.commit()


async def get_admin_logs(limit: int = 20):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT admin_id, action, target, details, created_at FROM admin_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


# ideas / votes (keep)
async def add_idea(user_id: int, nickname: str, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO ideas (user_id, nickname, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, nickname, text, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def list_ideas(status: str = "new", limit: int = 20):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, nickname, text, status, created_at FROM ideas WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ) as cur:
            return await cur.fetchall()


async def set_idea_status(idea_id: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))
        await db.commit()


async def get_idea(idea_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_vote(question: str, options: list, by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO votes (question, options, created_by, created_at) VALUES (?, ?, ?, ?)",
            (question, json.dumps(options, ensure_ascii=False), by, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_votes():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, question, options FROM votes WHERE active = 1 ORDER BY id DESC"
        ) as cur:
            return await cur.fetchall()


async def get_vote(vote_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM votes WHERE id = ?", (vote_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def close_vote(vote_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE votes SET active = 0 WHERE id = ?", (vote_id,))
        await db.commit()


async def cast_vote(vote_id: int, user_id: int, option_idx: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO vote_answers (vote_id, user_id, option_idx) VALUES (?, ?, ?)
            ON CONFLICT(vote_id, user_id) DO UPDATE SET option_idx = excluded.option_idx
            """,
            (vote_id, user_id, option_idx),
        )
        await db.commit()


async def vote_results(vote_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT option_idx, COUNT(*) FROM vote_answers WHERE vote_id = ? GROUP BY option_idx",
            (vote_id,),
        ) as cur:
            return dict(await cur.fetchall())


async def has_voted(vote_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM vote_answers WHERE vote_id = ? AND user_id = ?", (vote_id, user_id)
        ) as cur:
            return await cur.fetchone() is not None


# fractions
async def add_frac_app(user_id: int, nick: str, fraction: str, motive: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO fraction_apps (user_id, nickname, fraction, motive, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, nick, fraction, motive, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def list_frac_apps(status: str = "pending"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, nickname, fraction, motive, created_at FROM fraction_apps WHERE status = ? ORDER BY id DESC",
            (status,),
        ) as cur:
            return await cur.fetchall()


async def set_frac_app(app_id: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE fraction_apps SET status = ? WHERE id = ?", (status, app_id))
        await db.commit()


async def get_frac_app(app_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM fraction_apps WHERE id = ?", (app_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# tickets
async def create_ticket(user_id: int, nick: str, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO tickets (user_id, nickname, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, nick, text, now_str()),
        )
        tid = cur.lastrowid
        await db.execute(
            "INSERT INTO ticket_msgs (ticket_id, from_admin, text, created_at) VALUES (?, 0, ?, ?)",
            (tid, text, now_str()),
        )
        await db.commit()
        return tid


async def list_open_tickets(limit: int = 20):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, nickname, text, created_at FROM tickets WHERE status = 'open' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def get_ticket(tid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tickets WHERE id = ?", (tid,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def ticket_reply(tid: int, text: str, from_admin: int = 1):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO ticket_msgs (ticket_id, from_admin, text, created_at) VALUES (?, ?, ?, ?)",
            (tid, from_admin, text, now_str()),
        )
        await db.commit()


async def close_ticket(tid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?", (now_str(), tid)
        )
        await db.commit()


# promos
async def create_promo(code: str, money: int, item: str | None, max_uses: int, by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO promos (code, reward_money, reward_item, max_uses, uses, active, created_by)
            VALUES (?, ?, ?, ?, 0, 1, ?)
            """,
            (code.upper(), money, item, max_uses, by),
        )
        await db.commit()


async def get_promo(code: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promos WHERE upper(code) = upper(?)", (code,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def use_promo(code: str, user_id: int) -> tuple[bool, str]:
    promo = await get_promo(code)
    if not promo or not promo["active"]:
        return False, "Промокод не найден или отключён"
    if promo["uses"] >= promo["max_uses"]:
        return False, "Лимит активаций исчерпан"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM promo_uses WHERE upper(code) = upper(?) AND user_id = ?",
            (code, user_id),
        ) as cur:
            if await cur.fetchone():
                return False, "Вы уже активировали этот промокод"
        await db.execute(
            "INSERT INTO promo_uses (code, user_id, used_at) VALUES (?, ?, ?)",
            (code.upper(), user_id, now_str()),
        )
        await db.execute(
            "UPDATE promos SET uses = uses + 1 WHERE upper(code) = upper(?)", (code,)
        )
        await db.commit()
    if promo["reward_money"]:
        await add_balance(user_id, promo["reward_money"])
    if promo.get("reward_item"):
        await inv_add(user_id, promo["reward_item"], 1)
    parts = []
    if promo["reward_money"]:
        parts.append(f"+{promo['reward_money']}₽")
    if promo.get("reward_item"):
        parts.append(f"предмет {promo['reward_item']}")
    return True, ", ".join(parts) or "награда получена"


# reports
async def add_report(reporter_id: int, target_nick: str, reason: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO reports (reporter_id, target_nick, reason, created_at) VALUES (?, ?, ?, ?)",
            (reporter_id, target_nick, reason, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def list_reports(status: str = "new", limit: int = 20):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, reporter_id, target_nick, reason, created_at FROM reports WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ) as cur:
            return await cur.fetchall()


async def set_report_status(rid: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE reports SET status = ? WHERE id = ?", (status, rid))
        await db.commit()


# schedules
async def add_schedule(text: str, run_at: datetime, by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO schedules (text, run_at, created_by) VALUES (?, ?, ?)",
            (text, run_at.isoformat(), by),
        )
        await db.commit()
        return cur.lastrowid


async def due_schedules():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, text FROM schedules WHERE done = 0 AND run_at <= ?",
            (datetime.now().isoformat(),),
        ) as cur:
            return await cur.fetchall()


async def mark_schedule_done(sid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE schedules SET done = 1 WHERE id = ?", (sid,))
        await db.commit()



async def add_event(title: str, description: str, event_at: str, by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO events (title, description, event_at, created_by) VALUES (?, ?, ?, ?)",
            (title, description, event_at, by),
        )
        await db.commit()
        return cur.lastrowid


async def week_events():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, title, description, event_at FROM events ORDER BY event_at LIMIT 30"
        ) as cur:
            return await cur.fetchall()


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()



async def log_tx(user_id: int, kind: str, amount: int, meta: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO transactions (user_id, kind, amount, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, amount, meta, now_str()),
        )
        await db.commit()


async def get_tx(user_id: int, limit: int = 15):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT kind, amount, meta, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            return await cur.fetchall()


async def log_pc_buy(user_id: int, item: str, price: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO pc_purchases (user_id, item, price, created_at) VALUES (?, ?, ?, ?)",
            (user_id, item, price, now_str()),
        )
        await db.commit()


async def get_pc_history(user_id: int, limit: int = 15):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT item, price, created_at FROM pc_purchases WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            return await cur.fetchall()


async def add_activity(user_id: int, points: int = 1):
    day = date.today().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO activity_log (user_id, day, points) VALUES (?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET points = points + ?
            """,
            (user_id, day, points, points),
        )
        await db.execute(
            "UPDATE players SET last_seen = ? WHERE user_id = ?",
            (now_str(), user_id),
        )
        await db.commit()


async def week_activity_top(limit: int = 10):
    since = (date.today() - timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT p.nickname, SUM(a.points) as pts
            FROM activity_log a JOIN players p ON p.user_id = a.user_id
            WHERE a.day >= ?
            GROUP BY a.user_id
            ORDER BY pts DESC LIMIT ?
            """,
            (since, limit),
        ) as cur:
            return await cur.fetchall()


async def set_player_field(user_id: int, field: str, value):
    allowed = {
        "email", "twofa", "vip_level", "vip_until", "last_online_bonus",
        "last_frac_salary", "clan_id", "last_seen", "sub_until", "fraction",
    }
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE players SET {field} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()


async def grant_vip(user_id: int, level: str, days: int | None = None):
    info = VIP_LEVELS.get(level, {"days": days or 7, "label": level})
    d = days if days is not None else info["days"]
    player = await get_player(user_id)
    now = datetime.now()
    start = now
    if player and player.get("vip_until"):
        try:
            prev = datetime.fromisoformat(player["vip_until"])
            if prev > now:
                start = prev
        except Exception:
            pass
    until = start + timedelta(days=d)
    await set_player_field(user_id, "vip_level", level)
    await set_player_field(user_id, "vip_until", until.isoformat())
    await badge_add(user_id, f"VIP:{level}")


async def badge_add(user_id: int, badge: str):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO badges (user_id, badge) VALUES (?, ?)", (user_id, badge)
            )
            await db.commit()
        except Exception:
            pass


async def badges_list(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT badge FROM badges WHERE user_id = ?", (user_id,)) as cur:
            return [r[0] for r in await cur.fetchall()]


async def rep_value(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(value),0) FROM reputation WHERE to_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row[0] if row else 0)


async def rep_set(from_id: int, to_id: int, value: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO reputation (from_id, to_id, value, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(from_id, to_id) DO UPDATE SET value = excluded.value, created_at = excluded.created_at
            """,
            (from_id, to_id, value, now_str()),
        )
        await db.commit()


async def add_warn(user_id: int, admin_id: int, reason: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO warns (user_id, admin_id, reason, created_at) VALUES (?, ?, ?, ?)",
            (user_id, admin_id, reason, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def count_warns(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM warns WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return int(row[0] if row else 0)


async def list_warns(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT reason, created_at FROM warns WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def set_wanted(user_id: int, nick: str, reason: str, stars: int, admin_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO wanted (user_id, nickname, reason, stars, by_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, stars=excluded.stars, created_at=excluded.created_at
            """,
            (user_id, nick, reason, stars, admin_id, now_str()),
        )
        await db.commit()


async def clear_wanted(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM wanted WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_wanted():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, reason, stars FROM wanted ORDER BY stars DESC"
        ) as cur:
            return await cur.fetchall()


async def add_news(text: str, author_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO news (text, author_id, created_at) VALUES (?, ?, ?)",
            (text, author_id, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def last_news(limit: int = 5):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, text, created_at FROM news ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return await cur.fetchall()


async def market_add(seller_id: int, title: str, price: int, photo: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO market (seller_id, title, price, photo, created_at) VALUES (?, ?, ?, ?, ?)",
            (seller_id, title, price, photo, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def market_list(limit: int = 15):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, seller_id, title, price, photo FROM market WHERE active = 1 ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def market_get(mid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM market WHERE id = ?", (mid,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def market_close(mid: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE market SET active = 0 WHERE id = ?", (mid,))
        await db.commit()


async def leader_app_add(user_id: int, nick: str, fraction: str, motive: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO leader_apps (user_id, nickname, fraction, motive, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, nick, fraction, motive, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def leader_apps_list(status: str = "pending"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, nickname, fraction, motive, created_at FROM leader_apps WHERE status = ? ORDER BY id DESC",
            (status,),
        ) as cur:
            return await cur.fetchall()


async def leader_app_set(aid: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE leader_apps SET status = ? WHERE id = ?", (status, aid))
        await db.commit()


async def register_event(event_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO event_regs (event_id, user_id, created_at) VALUES (?, ?, ?)",
                (event_id, user_id, now_str()),
            )
            await db.commit()
            return True
        except Exception:
            return False


async def event_reg_count(event_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM event_regs WHERE event_id = ?", (event_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row[0] if row else 0)


async def partner_add(user_id: int, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO partners (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text, now_str()),
        )
        await db.commit()


async def partners_list(limit: int = 15):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, text, created_at FROM partners WHERE active = 1 ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def clan_create(name: str, tag: str, leader_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO clans (name, tag, leader_id, created_at) VALUES (?, ?, ?, ?)",
            (name, tag, leader_id, now_str()),
        )
        cid = cur.lastrowid
        await db.execute(
            "INSERT INTO clan_members (clan_id, user_id, role) VALUES (?, ?, 'leader')",
            (cid, leader_id),
        )
        await db.execute("UPDATE players SET clan_id = ? WHERE user_id = ?", (cid, leader_id))
        await db.commit()
        return cid


async def clan_by_user(user_id: int):
    player = await get_player(user_id)
    if not player or not player.get("clan_id"):
        return None
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE id = ?", (player["clan_id"],)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_ticket_curator(tid: int, curator_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE tickets SET curator_id = ? WHERE id = ?", (curator_id, tid))
        await db.commit()


async def appeal_add(user_id: int, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO appeals (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def appeals_list(status: str = "open"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, user_id, text, created_at FROM appeals WHERE status = ? ORDER BY id DESC",
            (status,),
        ) as cur:
            return await cur.fetchall()


async def contest_create(title: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO contests (title, created_at) VALUES (?, ?)", (title, now_str())
        )
        await db.commit()
        return cur.lastrowid


async def active_contest():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM contests WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


SHOP = {
    "Аптечка": (300, "HP в RP"),
    "Бронежилет": (800, "Защита"),
    "Телефон": (2500, "Связь"),
    "Маска": (1200, "Для дел"),
    "VIP-карта": (15000, "Статус VIP"),
}


async def require_player(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("📝 Сначала зарегистрируйся в игре.", keyboard=await main_menu(message.from_id))
        return None
    if player.get("banned") and not await is_admin(message.from_id):
        await message.answer("⛔ Аккаунт заблокирован.")
        return None
    try:
        await add_activity(message.from_id, 1)
    except Exception:
        pass
    return player


async def main_menu(user_id: int):
    """Главное меню: макс. 10 рядов (лимит VK)."""
    player = await get_player(user_id)
    admin = await is_admin(user_id)
    kb = Keyboard(one_time=False)
    if not player:
        kb.add(Text("📝 Регистрация в игре"), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Text("🔁 Восстановить аккаунт"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("📡 Статус сервера"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("ℹ️ Информация"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("📞 Связь с основателем"), color=KeyboardButtonColor.PRIMARY)
    else:
        # 8 рядов для игрока + опционально админ
        kb.add(Text("👤 ЛК"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("💼 Работа"), color=KeyboardButtonColor.POSITIVE)
        kb.add(Text("🎁 Ежедневка"), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Text("🛒 Магазин"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("💎 Prp Coin"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("🏪 Биржа"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("🏛️ RP"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("🎮 Сообщество"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("⚙️ Аккаунт"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("🎫 Тикет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("📰 Новости"), color=KeyboardButtonColor.SECONDARY)
        kb.add(Text("📡 Статус"), color=KeyboardButtonColor.PRIMARY)
        kb.row()
        kb.add(Text("ℹ️ Инфо"), color=KeyboardButtonColor.SECONDARY)
        kb.add(Text("📞 Основатель"), color=KeyboardButtonColor.PRIMARY)
        if admin:
            kb.row()
            kb.add(Text("🛠️ Админ-панель"), color=KeyboardButtonColor.NEGATIVE)
    return kb


def rp_menu():
    return (
        Keyboard(one_time=False)
        .add(Text("🏛️ Фракция"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("👑 Лидерство"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🔫 Розыск"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("⚖️ Апелляция"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🚨 Жалоба"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("🛡️ Клан"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def community_menu():
    return (
        Keyboard(one_time=False)
        .add(Text("🗳️ Голосования"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("💡 Идея"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📅 Ивенты"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("📸 Конкурс"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🤝 Напарник"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("🏆 Топ"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("📊 Активность"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("⭐ Репутация"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🎁 Промокод"), color=KeyboardButtonColor.POSITIVE)
        .add(Text("👥 Рефералка"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def account_menu():
    return (
        Keyboard(one_time=False)
        .add(Text("👤 Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("🎮 Мой аккаунт"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🎒 Инвентарь"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("🏅 Достижения"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("📜 История"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("🔑 Сменить пароль"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("📧 Сменить email"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("🔐 2FA"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def cancel_keyboard():
    return Keyboard(one_time=True).add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)


def start_reg_keyboard():
    return (
        Keyboard(one_time=True)
        .add(Text("📝 Начать регистрацию"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    )


def admin_keyboard(show_fake: bool = False):
    kb = (
        Keyboard(one_time=False)
        .add(Text("👥 Игроки"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("📊 Статистика"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("⛔ Баны"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("🎫 Тикеты"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🏛️ Заявки фракций"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("👑 Лидерки"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🚨 Жалобы"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("⚖️ Апелляции"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("📰 Пост новости"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("🔫 Розыск админ"), color=KeyboardButtonColor.NEGATIVE)
        .row()
        .add(Text("💡 Идеи"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("🗳️ Голосования админ"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📋 Лог админов"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("⭐ Админы бота"), color=KeyboardButtonColor.SECONDARY)
    )
    # Фейк вход/выход — только владельцу (ADMINS в .env)
    if show_fake:
        kb.row()
        kb.add(Text("🟢 Фейк вход"), color=KeyboardButtonColor.POSITIVE)
        kb.add(Text("🔴 Фейк выход"), color=KeyboardButtonColor.NEGATIVE)
    kb.row()
    kb.add(Text("❓ Админ-помощь"), color=KeyboardButtonColor.SECONDARY)
    kb.add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    return kb


def shop_keyboard():
    kb = Keyboard(inline=True)
    for name, (price, _) in SHOP.items():
        kb.add(Text(f"{name} — {price}₽", payload={"cmd": "buy", "item": name}))
        kb.row()
    return kb


def idea_keyboard(idea_id: int):
    return (
        Keyboard(inline=True)
        .add(Text("✅ Принять", payload={"cmd": "idea_ok", "id": idea_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("❌ Отклонить", payload={"cmd": "idea_no", "id": idea_id}), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("✔️ Сделано", payload={"cmd": "idea_done", "id": idea_id}), color=KeyboardButtonColor.PRIMARY)
    )


def frac_choice_keyboard():
    kb = Keyboard(one_time=True)
    for i, f in enumerate(FRACTIONS):
        kb.add(Text(f))
        if (i + 1) % 2 == 0:
            kb.row()
    if len(FRACTIONS) % 2 != 0:
        kb.row()
    kb.add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    return kb


def frac_app_keyboard(app_id: int):
    return (
        Keyboard(inline=True)
        .add(Text("✅ В фракцию", payload={"cmd": "frac_ok", "id": app_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("❌ Отказать", payload={"cmd": "frac_no", "id": app_id}), color=KeyboardButtonColor.NEGATIVE)
    )


# -------------------- flood / autoban --------------------
def flood_hit(uid: int) -> bool:
    import time
    now = time.time()
    arr = _flood.get(uid, [])
    arr = [t for t in arr if now - t < SPAM_WINDOW]
    arr.append(now)
    _flood[uid] = arr
    return len(arr) >= SPAM_LIMIT


async def check_autoban(message: Message) -> bool:
    """True если сообщение нужно игнорировать (забанили)."""
    uid = message.from_id
    if await is_admin(uid):
        return False
    text = (message.text or "").lower()
    for w in BAN_WORDS:
        if w in text:
            await set_banned(uid, 1)
            await admin_log(0, "autoban_word", str(uid), w)
            await message.answer("⛔ Автобан: запрещённые слова.")
            await notify_admins(
                message.ctx_api,
                f"⛔ Автобан [id{uid}|user] по слову: {w}\nТекст: {(message.text or '')[:200]}",
            )
            return True
    if flood_hit(uid):
        await set_banned(uid, 1)
        await admin_log(0, "autoban_spam", str(uid), "flood")
        await message.answer("⛔ Автобан: спам.")
        await notify_admins(message.ctx_api, f"⛔ Автобан [id{uid}|user] за спам")
        return True
    return False


# -------------------- handlers --------------------
@bot.on.message(text=["начать", "Начать", "старт", "Старт", "/start", "меню", "Меню"])
async def start(message: Message):
    if await check_autoban(message):
        return
    player = await get_player(message.from_id)
    if not player:
        await react_to(message, 1)
        await message.answer(
            f"🎮 Добро пожаловать в {PROJECT}!\n"
            f"Проект: {PROJECT_SHORT}\n\n"
            "🆕 Возможности:\n"
            "• 🏆 Уровни и опыт\n"
            "• 🏅 Достижения\n"
            "• 🏛️ Фракции\n"
            "• 🎫 Поддержка\n"
            "• 🎁 Промокоды и рефералка\n"
            "• 🗳️ Голосования\n\n"
            "Выберите действие:",
            keyboard=await main_menu(message.from_id),
        )
        return
    extra = "\n🛠️ Ты администратор." if await is_admin(message.from_id) else ""
    await message.answer(f"👋 С возвращением в {PROJECT}!{extra}", keyboard=await main_menu(message.from_id))


@bot.on.message(text=["ℹ️ Информация", "Информация", "инфо", "помощь", "Помощь"])
async def info(message: Message):
    await message.answer(
        f"📘 {PROJECT} | {PROJECT_SHORT}\n\n"
        "Регистрация: ник → email → пароль\n"
        "Промокод: кнопка «Промокод» или «промокод КОД»\n"
        "Рефералка: кнопка «Рефералка»\n"
        "Фракция / Тикет / Жалоба / Смена пароля — в меню\n"
        "Поддержка восстановит доступ, если аккаунт был раньше."
    )


# ----- registration -----
@bot.on.message(text=["📝 Регистрация в игре", "Регистрация в игре", "регистрация", "Регистрация"])
async def reg_menu(message: Message):
    if await get_player(message.from_id):
        await message.answer("✅ У тебя уже есть аккаунт.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        "📝 Регистрация в игре\n\nНажми «Начать регистрацию».",
        keyboard=start_reg_keyboard(),
    )


@bot.on.message(text=["📝 Начать регистрацию", "Начать регистрацию"])
async def reg_start(message: Message):
    if await get_player(message.from_id):
        await message.answer("✅ У тебя уже есть аккаунт.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        "📝 Регистрация нового аккаунта\n\n"
        "Шаг 1/4: игровой ник\n"
        "• 3–24 символа, латиница, цифры, _\n\nВведите ник:",
        keyboard=cancel_keyboard(),
    )
    await state_dispenser.set(message.peer_id, RegState.NICKNAME)


@bot.on.message(state=RegState.NICKNAME)
async def reg_nick(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Регистрация отменена.", keyboard=await main_menu(message.from_id))
        return
    nick = (message.text or "").strip()
    if not NICK_RE.match(nick):
        await message.answer("❌ Ник не подходит. Пример: Artur_Vishnevskiy")
        return
    if await nick_taken(nick):
        await message.answer("❌ Ник занят. Другой:")
        return
    await react_to(message, 1)
    await message.answer(f"✅ Ник '{nick}' доступен!")
    if await unlock_achievement(message.from_id, "first_step", "Первый шаг"):
        await message.answer("🏅 Новое достижение!\n🎯 Первый шаг\n📝 Начал регистрацию")
    await state_dispenser.set(message.peer_id, RegState.EMAIL, nickname=nick)
    await message.answer("Шаг 2/4: email\nВведите email:", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.EMAIL)
async def reg_email(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Регистрация отменена.", keyboard=await main_menu(message.from_id))
        return
    email = (message.text or "").strip()
    if not EMAIL_RE.match(email):
        await message.answer("❌ Некорректный email")
        return
    await react_to(message, 1)
    await message.answer("✅ Email принят!")
    payload = dict(message.state_peer.payload or {})
    payload["email"] = email
    await state_dispenser.set(message.peer_id, RegState.PASSWORD, **payload)
    await message.answer(
        "Шаг 3/4: пароль\n• минимум 6 символов\n• буквы и цифры\n\nВведите пароль:",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=RegState.PASSWORD)
async def reg_password(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Регистрация отменена.", keyboard=await main_menu(message.from_id))
        return
    password = (message.text or "").strip()
    if len(password) < 6:
        await message.answer("❌ Минимум 6 символов")
        return
    if password.isdigit() or password.isalpha():
        await message.answer("❌ Нужны и буквы, и цифры")
        return
    payload = dict(message.state_peer.payload or {})
    payload["password"] = password
    await state_dispenser.set(message.peer_id, RegState.REF, **payload)
    await message.answer(
        "Шаг 4/4: реферальный код друга (или «-» если нет):",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=RegState.REF)
async def reg_finish(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Регистрация отменена.", keyboard=await main_menu(message.from_id))
        return
    payload = dict(message.state_peer.payload or {})
    nick = payload.get("nickname")
    email = payload.get("email")
    password = payload.get("password")
    if not nick or not email or not password:
        await state_dispenser.delete(message.peer_id)
        await message.answer("Сессия сброшена. Начни снова.", keyboard=await main_menu(message.from_id))
        return
    if await nick_taken(nick):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Ник заняли. Начни снова.")
        return
    referred_by = None
    ref_input = (message.text or "").strip()
    if ref_input and ref_input != "-":
        ref_owner = await get_player_by_ref(ref_input)
        if ref_owner and ref_owner["user_id"] != message.from_id:
            referred_by = ref_owner["user_id"]
    ref_code = await create_player(message.from_id, nick, email, password, referred_by)
    if referred_by:
        await add_balance(referred_by, REF_BONUS)
        await add_balance(message.from_id, REF_BONUS)
        await notify_user(
            message.ctx_api, referred_by,
            f"🎁 По вашей рефералке зарегистрировался {nick}! +{REF_BONUS}₽",
        )
    await unlock_achievement(message.from_id, "registered", "Регистрация завершена")
    await state_dispenser.delete(message.peer_id)
    await react_to(message, 1)
    masked = mask_password(password)
    extra = f"\n🎁 Реф.бонус: +{REF_BONUS}₽" if referred_by else ""
    await message.answer(
        "🎉 Регистрация успешно завершена!\n\n"
        "📋 Ваши данные:\n"
        f"• Игровой ник: {nick}\n"
        f"• Email: {email}\n\n"
        "🎮 Для входа в игру:\n"
        f"• Логин: {nick}\n"
        f"• Пароль: {masked}\n\n"
        f"👥 Ваш реферальный код: {ref_code}\n"
        "⚠️ Полный пароль больше не покажем."
        f"{extra}\n\n"
        f"Удачной игры на {PROJECT_SHORT}! 🚗",
        keyboard=await main_menu(message.from_id),
    )
    await notify_admins(
        message.ctx_api,
        f"🆕 Новый аккаунт {PROJECT}\nVK: [id{message.from_id}|user]\nНик: {nick}\nEmail: {email}",
    )


# ----- cabinet -----
@bot.on.message(text=["👤 Личный кабинет", "Личный кабинет", "лк", "ЛК", "кабинет"])
async def cabinet(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(
        f"👤 Личный кабинет {PROJECT_SHORT}\n\n"
        f"Ник: {player['nickname']}\n"
        f"Email: {player.get('email') or '—'}\n"
        f"Фракция: {player.get('fraction') or 'нет'}\n"
        f"Уровень: {player['level']} ({player['exp']}/{player['level'] * 1000})\n"
        f"Баланс: {player['balance']}₽\n"
        f"Prp Coin: {player.get('prp_coin') or 0}\n"
        f"Реф.код: {player.get('referral_code') or '—'}\n"
        f"Регистрация: {player['registered_at']}",
        keyboard=await main_menu(message.from_id),
    )


@bot.on.message(text=["🎮 Мой аккаунт", "Мой аккаунт", "аккаунт", "данные"])
async def my_account(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(
        "🎮 Данные для входа\n\n"
        f"Логин: {player['nickname']}\n"
        f"Email: {player.get('email') or '—'}\n"
        f"VK привязан: id{player['user_id']}\n\n"
        "Пароль хранится в хеше. Сброс — через админа или «Сменить пароль»."
    )


@bot.on.message(text=["🏅 Достижения", "Достижения", "достижения"])
async def achievements_cmd(message: Message):
    player = await require_player(message)
    if not player:
        return
    rows = await list_achievements(message.from_id)
    if not rows:
        await message.answer("📭 Пока нет достижений.")
        return
    lines = ["🏅 Ваши достижения:\n"] + [f"• {t} ({u})" for t, u in rows]
    await message.answer("\n".join(lines))


@bot.on.message(text=["🎁 Ежедневка", "Ежедневка", "ежедневка", "бонус", "daily"])
async def daily(message: Message):
    player = await require_player(message)
    if not player:
        return
    if player.get("last_daily") == date.today().isoformat():
        await message.answer("⏰ Уже получено сегодня.")
        return
    await add_balance(message.from_id, DAILY_BONUS)
    await set_last_daily(message.from_id)
    leveled = await add_exp(message.from_id, 50)
    await unlock_achievement(message.from_id, "first_daily", "Первая ежедневка")
    await react_to(message, 2)
    await message.answer(f"+{DAILY_BONUS}₽ (+50 опыта)" + ("\n🆙 Уровень!" if leveled else ""))


@bot.on.message(text=["💼 Работа", "Работа", "работа", "работать", "work"])
async def work(message: Message):
    player = await require_player(message)
    if not player:
        return
    if player.get("last_work"):
        try:
            last = datetime.fromisoformat(player["last_work"])
            left = WORK_COOLDOWN_SEC - (datetime.now() - last).total_seconds()
            if left > 0:
                await message.answer(f"⏳ Подожди {int(left // 60)}м {int(left % 60)}с")
                return
        except Exception:
            pass
    pay = random.randint(WORK_MIN, WORK_MAX)
    jobs = ["грузоперевозки", "такси", "стройка", "завод", "доставка", "автосервис"]
    await add_balance(message.from_id, pay)
    await set_last_work(message.from_id)
    leveled = await add_exp(message.from_id, 30)
    await unlock_achievement(message.from_id, "first_work", "Первая смена")
    await react_to(message, 2)
    await message.answer(
        f"💼 Смена: {random.choice(jobs)}. +{pay}₽ (+30 опыта)"
        + ("\n🆙 Уровень!" if leveled else "")
    )


@bot.on.message(text=["🏆 Топ", "Топ", "топ", "рейтинг"])
async def top(message: Message):
    rows = await get_top(10)
    if not rows:
        await message.answer("📭 Топ пуст.")
        return
    lines = [f"🏆 Топ {PROJECT_SHORT}:\n"] + [
        f"{i}. {n} — {b}₽ (ур. {lv})" for i, (n, lv, b) in enumerate(rows, 1)
    ]
    await message.answer("\n".join(lines))


@bot.on.message(text=["🎒 Инвентарь", "Инвентарь", "инвентарь", "инв"])
async def inventory(message: Message):
    player = await require_player(message)
    if not player:
        return
    items = await inv_get(message.from_id)
    if not items:
        await message.answer("📭 Инвентарь пуст.")
        return
    await message.answer("🎒 Инвентарь:\n" + "\n".join(f"• {i} × {a}" for i, a in items))


@bot.on.message(text=["🛒 Магазин", "Магазин", "магазин", "шоп"])
async def shop(message: Message):
    player = await require_player(message)
    if not player:
        return
    lines = [f"🛒 Магазин (баланс {player['balance']}₽)\n"]
    for name, (price, desc) in SHOP.items():
        lines.append(f"• {name} — {price}₽ — {desc}")
    await message.answer("\n".join(lines), keyboard=shop_keyboard())


@bot.on.message(PayloadRule({"cmd": "buy"}))
async def buy_item(message: Message):
    player = await require_player(message)
    if not player:
        return
    item = (message.get_payload_json() or {}).get("item")
    if item not in SHOP:
        return
    price, _ = SHOP[item]
    if player["balance"] < price:
        await message.answer(f"❌ Нужно {price}₽")
        return
    await add_balance(message.from_id, -price)
    await inv_add(message.from_id, item, 1)
    await react_to(message, 1)
    await message.answer(f"✅ Куплено: {item}")


@bot.on.message(text=["👥 Рефералка", "Рефералка", "рефералка", "реф"])
async def referral_info(message: Message):
    player = await require_player(message)
    if not player:
        return
    code = player.get("referral_code") or "—"
    await message.answer(
        f"👥 Реферальная программа\n\n"
        f"Ваш код: `{code}`\n"
        f"Друг при регистрации вводит код → оба получают {REF_BONUS}₽\n\n"
        f"Отправь другу: зарегистрируйся в боте и укажи код {code}"
    )


@bot.on.message(text=["🎁 Промокод", "Промокод", "промокод"])
async def promo_help(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("🎁 Введи: промокод ТВОЙКОД")


# ----- fraction -----
@bot.on.message(text=["🏛️ Фракция", "Фракция", "фракция"])
async def frac_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    if player.get("fraction"):
        await message.answer(f"🏛️ Ты уже в фракции: {player['fraction']}")
        return
    await message.answer("🏛️ Выбери фракцию:", keyboard=frac_choice_keyboard())
    await state_dispenser.set(message.peer_id, FracState.CHOICE)


@bot.on.message(state=FracState.CHOICE)
async def frac_choice(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    if message.text not in FRACTIONS:
        await message.answer("Выбери кнопку фракции:", keyboard=frac_choice_keyboard())
        return
    await state_dispenser.set(message.peer_id, FracState.MOTIVE, fraction=message.text)
    await message.answer("Напиши мотивацию (почему хочешь в фракцию):", keyboard=cancel_keyboard())


@bot.on.message(state=FracState.MOTIVE)
async def frac_motive(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    motive = (message.text or "").strip()
    if len(motive) < 5:
        await message.answer("Поподробнее:")
        return
    player = await get_player(message.from_id)
    frac = (message.state_peer.payload or {}).get("fraction")
    app_id = await add_frac_app(message.from_id, player["nickname"], frac, motive)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Заявка #{app_id} во фракцию «{frac}» отправлена.", keyboard=await main_menu(message.from_id))
    await notify_admins(
        message.ctx_api,
        f"🏛️ Заявка во фракцию #{app_id}\n"
        f"[id{message.from_id}|{player['nickname']}] → {frac}\n{motive}",
    )


# ----- tickets -----
@bot.on.message(text=["🎫 Тикет", "Тикет", "тикет", "поддержка"])
async def ticket_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("🎫 Опиши проблему одним сообщением:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, TicketState.TEXT)


@bot.on.message(state=TicketState.TEXT)
async def ticket_save(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Слишком коротко:")
        return
    player = await get_player(message.from_id)
    tid = await create_ticket(message.from_id, player["nickname"], text)
    await state_dispenser.delete(message.peer_id)
    await react_to(message, 1)
    await message.answer(f"✅ Тикет #{tid} создан. Ожидай ответ поддержки.", keyboard=await main_menu(message.from_id))
    await notify_admins(
        message.ctx_api,
        f"🎫 Тикет #{tid} от [id{message.from_id}|{player['nickname']}]\n{text}\n\nОтвет: ответ {tid} текст",
    )


# ----- report -----
@bot.on.message(text=["🚨 Жалоба", "Жалоба", "жалоба", "репорт"])
async def report_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("🚨 Ник игрока, на которого жалоба:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, ReportState.TARGET)


@bot.on.message(state=ReportState.TARGET)
async def report_target(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    nick = (message.text or "").strip()
    await state_dispenser.set(message.peer_id, ReportState.REASON, target=nick)
    await message.answer("Причина жалобы:", keyboard=cancel_keyboard())


@bot.on.message(state=ReportState.REASON)
async def report_reason(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    reason = (message.text or "").strip()
    if len(reason) < 5:
        await message.answer("Опиши подробнее:")
        return
    target = (message.state_peer.payload or {}).get("target", "?")
    rid = await add_report(message.from_id, target, reason)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Жалоба #{rid} отправлена.", keyboard=await main_menu(message.from_id))
    await notify_admins(
        message.ctx_api,
        f"🚨 Жалоба #{rid}\nОт: [id{message.from_id}|user]\nНа: {target}\nПричина: {reason}",
    )


# ----- password change -----
@bot.on.message(text=["🔑 Сменить пароль", "Сменить пароль", "сменить пароль"])
async def pass_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("🔑 Введи текущий пароль:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, PassState.OLD)


@bot.on.message(state=PassState.OLD)
async def pass_old(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    player = await get_player(message.from_id)
    if not player or not check_password(message.text or "", player["password_hash"], player["password_salt"]):
        await message.answer("❌ Неверный пароль. Ещё раз или «Отмена»:")
        return
    await state_dispenser.set(message.peer_id, PassState.NEW)
    await message.answer("Введи новый пароль (6+ символов, буквы и цифры):", keyboard=cancel_keyboard())


@bot.on.message(state=PassState.NEW)
async def pass_new(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    password = (message.text or "").strip()
    if len(password) < 6 or password.isdigit() or password.isalpha():
        await message.answer("❌ Пароль слабый. Другой:")
        return
    await set_password(message.from_id, password)
    await state_dispenser.delete(message.peer_id)
    await message.answer(
        f"✅ Пароль изменён.\nНовый (сохрани): {mask_password(password)}\nПолный больше не покажем.",
        keyboard=await main_menu(message.from_id),
    )


# ----- ideas / votes -----
@bot.on.message(text=["💡 Идея", "Идея", "идея", "предложить"])
async def idea_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("💡 Опиши идею:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, IdeaState.TEXT)


@bot.on.message(state=IdeaState.TEXT)
async def idea_save(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Слишком коротко:")
        return
    player = await get_player(message.from_id)
    idea_id = await add_idea(message.from_id, player["nickname"] if player else "?", text)
    await state_dispenser.delete(message.peer_id)
    await react_to(message, 1)
    await message.answer(f"✅ Идея #{idea_id} отправлена.", keyboard=await main_menu(message.from_id))
    for aid in await all_admin_ids():
        await notify_user(
            message.ctx_api, aid,
            f"💡 Идея #{idea_id} от {player['nickname'] if player else message.from_id}:\n{text}",
            keyboard=idea_keyboard(idea_id),
        )


@bot.on.message(text=["🗳️ Голосования", "Голосования", "голосования", "опрос"])
async def votes_list(message: Message):
    player = await require_player(message)
    if not player:
        return
    votes = await get_active_votes()
    if not votes:
        await message.answer("📭 Активных голосований нет.")
        return
    for vid, question, options_json in votes:
        options = json.loads(options_json)
        kb = Keyboard(inline=True)
        for i, opt in enumerate(options):
            kb.add(Text(opt, payload={"cmd": "vote", "vid": vid, "opt": i}))
            kb.row()
        mark = " (уже голосовал)" if await has_voted(vid, message.from_id) else ""
        await message.answer(f"🗳️ Голосование #{vid}{mark}\n{question}", keyboard=kb)


@bot.on.message(PayloadRule({"cmd": "vote"}))
async def vote_cast(message: Message):
    player = await require_player(message)
    if not player:
        return
    data = message.get_payload_json() or {}
    vid, opt = int(data["vid"]), int(data["opt"])
    vote = await get_vote(vid)
    if not vote or not vote["active"]:
        await message.answer("Голосование закрыто.")
        return
    options = json.loads(vote["options"])
    if opt < 0 or opt >= len(options):
        return
    await cast_vote(vid, message.from_id, opt)
    await react_to(message, 1)
    await message.answer(f"✅ Голос учтён: «{options[opt]}»")


# ----- recovery -----
@bot.on.message(text=["🔁 Восстановить аккаунт", "Восстановить аккаунт", "восстановить", "восстановление"])
async def recover_start(message: Message):
    if await get_player(message.from_id):
        await message.answer("✅ Аккаунт уже есть.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        "🔁 Восстановление\nШаг 1/3: игровой ник:",
        keyboard=cancel_keyboard(),
    )
    await state_dispenser.set(message.peer_id, RecoverState.NICK)


@bot.on.message(state=RecoverState.NICK)
async def recover_nick(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    nick = (message.text or "").strip()
    if len(nick) < 3:
        await message.answer("Короткий ник:")
        return
    await state_dispenser.set(message.peer_id, RecoverState.EMAIL, nickname=nick)
    await message.answer("Шаг 2/3: email (или «-»):", keyboard=cancel_keyboard())


@bot.on.message(state=RecoverState.EMAIL)
async def recover_email(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    payload = dict(message.state_peer.payload or {})
    payload["email"] = (message.text or "").strip()
    await state_dispenser.set(message.peer_id, RecoverState.COMMENT, **payload)
    await message.answer("Шаг 3/3: опиши ситуацию:", keyboard=cancel_keyboard())


@bot.on.message(state=RecoverState.COMMENT)
async def recover_finish(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=await main_menu(message.from_id))
        return
    comment = (message.text or "").strip()
    if len(comment) < 5:
        await message.answer("Подробнее:")
        return
    payload = dict(message.state_peer.payload or {})
    await state_dispenser.delete(message.peer_id)
    await react_to(message, 1)
    await message.answer("✅ Заявка отправлена поддержке.", keyboard=await main_menu(message.from_id))
    await notify_admins(
        message.ctx_api,
        f"🔁 Восстановление\nVK: [id{message.from_id}|user]\n"
        f"Ник: {payload.get('nickname')}\nEmail: {payload.get('email')}\n{comment}",
    )


# ----- admin UI -----

@bot.on.message(text=["📡 Статус сервера", "Статус сервера", "статус", "онлайн", "сервер"])
async def server_status(message: Message):
    await message.answer(format_server_status())


@bot.on.message(text=["💎 Prp Coin", "Prp Coin", "prp coin", "донат магазин"])
async def coin_shop(message: Message):
    player = await require_player(message)
    if not player:
        return
    coins = player.get("prp_coin") or 0
    lines = [f"💎 Магазин Prp Coin\nБаланс: {coins} Prp Coin\n"]
    kb = Keyboard(inline=True)
    for name, (price, desc) in COIN_SHOP.items():
        lines.append(f"• {name} — {price} PC — {desc}")
        kb.add(Text(f"{name} ({price} PC)", payload={"cmd": "buycoin", "item": name}))
        kb.row()
    lines.append("\nPC выдаёт администрация (выдатьcoin).")
    await message.answer("\n".join(lines), keyboard=kb)


@bot.on.message(PayloadRule({"cmd": "buycoin"}))
async def buy_coin_item(message: Message):
    player = await require_player(message)
    if not player:
        return
    item = (message.get_payload_json() or {}).get("item")
    if item not in COIN_SHOP:
        return
    price, desc = COIN_SHOP[item]
    coins = player.get("prp_coin") or 0
    if coins < price:
        await message.answer(f"❌ Нужно {price} Prp Coin (у вас {coins})")
        return
    await add_prp_coin(message.from_id, -price)
    if "5000" in item:
        await add_balance(message.from_id, 5000)
    elif "20000" in item:
        await add_balance(message.from_id, 20000)
    await inv_add(message.from_id, item, 1)
    await log_pc_buy(message.from_id, item, price)
    await message.answer(f"✅ Куплено за {price} Prp Coin: {item}\n{desc}")
    await notify_admins(
        message.ctx_api,
        f"💎 [id{message.from_id}|{player['nickname']}] купил {item} за {price} PC",
    )


@bot.on.message(text=["📅 Ивенты", "Ивенты", "ивенты", "календарь"])
async def events_list(message: Message):
    rows = await week_events()
    if not rows:
        await message.answer("📅 Пока нет ивентов. Следи за новостями!")
        return
    lines = ["📅 Ивенты проекта:\n"]
    for eid, title, desc, event_at in rows:
        lines.append(f"• #{eid} [{event_at}] {title}\n  {desc}")
    await message.answer("\n".join(lines))



@bot.on.message(text=["📞 Связь с основателем", "Связь с основателем", "основатель"])
async def contact_founder(message: Message):
    await message.answer(
        f"📞 Связь с основателем проекта\n"
        f"Напишите: https://vk.com/id{FOUNDER_ID}\n"
        f"Или создайте 🎫 Тикет в боте — куратор ответит."
    )



@bot.on.message(text=["👤 ЛК", "ЛК"])
async def menu_lk_short(message: Message):
    await message.answer("⚙️ Раздел аккаунта:", keyboard=account_menu())


@bot.on.message(text=["🏛️ RP", "RP", "рп"])
async def menu_rp(message: Message):
    await message.answer("🏛️ RP-раздел:", keyboard=rp_menu())


@bot.on.message(text=["🎮 Сообщество", "Сообщество", "сообщество"])
async def menu_community(message: Message):
    await message.answer("🎮 Сообщество:", keyboard=community_menu())


@bot.on.message(text=["⚙️ Аккаунт", "Аккаунт"])
async def menu_account(message: Message):
    await message.answer("⚙️ Аккаунт:", keyboard=account_menu())


@bot.on.message(text=["📡 Статус", "Статус"])
async def menu_status_short(message: Message):
    await message.answer(format_server_status())


@bot.on.message(text=["ℹ️ Инфо", "Инфо"])
async def menu_info_short(message: Message):
    await info(message)


@bot.on.message(text=["📞 Основатель", "Основатель"])
async def menu_founder_short(message: Message):
    await contact_founder(message)


@bot.on.message(text=["📰 Новости", "Новости", "новости"])
async def news_list(message: Message):
    rows = await last_news(8)
    if not rows:
        await message.answer("📰 Новостей пока нет.")
        return
    lines = ["📰 Новости сервера:\n"]
    for nid, body, created in rows:
        lines.append(f"• #{nid} ({created})\n{body}\n")
    await message.answer("\n".join(lines)[:3500])


@bot.on.message(text=["📜 История", "История", "история", "транзакции"])
async def tx_history(message: Message):
    player = await require_player(message)
    if not player:
        return
    rows = await get_tx(message.from_id, 15)
    pc = await get_pc_history(message.from_id, 10)
    lines = ["📜 История операций:\n"]
    if not rows:
        lines.append("• пусто")
    else:
        for kind, amount, meta, created in rows:
            sign = "+" if amount >= 0 else ""
            lines.append(f"• [{created}] {kind}: {sign}{amount} ({meta or '—'})")
    lines.append("\n💎 Покупки Prp Coin:")
    if not pc:
        lines.append("• пусто")
    else:
        for item, price, created in pc:
            lines.append(f"• [{created}] {item} — {price} PC")
    await message.answer("\n".join(lines)[:3500])


@bot.on.message(text=["📊 Активность", "Активность", "активность"])
async def activity_top(message: Message):
    rows = await week_activity_top(10)
    if not rows:
        await message.answer("📊 Пока нет данных за неделю.")
        return
    lines = ["📊 Топ активности за 7 дней:\n"]
    for i, (nick, pts) in enumerate(rows, 1):
        lines.append(f"{i}. {nick} — {pts} очков")
    await message.answer("\n".join(lines))


@bot.on.message(text=["🔫 Розыск", "Розыск", "розыск", "wanted"])
async def wanted_public(message: Message):
    rows = await list_wanted()
    if not rows:
        await message.answer("🔫 Розыск пуст. Город спокоен.")
        return
    lines = ["🔫 Список розыска:\n"]
    for uid, nick, reason, stars in rows:
        lines.append(f"{'⭐' * min(stars, 5)} {nick} — {reason}")
    await message.answer("\n".join(lines))


@bot.on.message(text=["🏪 Биржа", "Биржа", "биржа", "маркет"])
async def market_menu(message: Message):
    player = await require_player(message)
    if not player:
        return
    rows = await market_list(12)
    kb = Keyboard(one_time=False)
    kb.add(Text("➕ Продать на бирже"), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    if not rows:
        await message.answer("🏪 Биржа пуста. Выставь товар кнопкой «Продать».", keyboard=kb)
        return
    await message.answer("🏪 Биржа (лоты):", keyboard=kb)
    for mid, seller_id, title, price, photo in rows:
        body = f"#{mid} {title}\n💰 {price}₽\nПродавец: id{seller_id}"
        kb_buy = Keyboard(inline=True).add(
            Text(f"Купить #{mid}", payload={"cmd": "mbuy", "id": mid}),
            color=KeyboardButtonColor.POSITIVE,
        )
        try:
            if photo:
                await message.answer(body, attachment=photo, keyboard=kb_buy)
            else:
                await message.answer(body, keyboard=kb_buy)
        except Exception:
            await message.answer(body + (f"\n🖼 {photo}" if photo else ""), keyboard=kb_buy)


@bot.on.message(text=["➕ Продать на бирже", "Продать на бирже"])
async def market_sell_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("Название товара:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, MarketState.TITLE)


@bot.on.message(state=MarketState.TITLE)
async def market_title(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    title = (message.text or "").strip()[:80]
    await state_dispenser.set(message.peer_id, MarketState.PRICE, title=title)
    await message.answer("Цена в ₽ (число):", keyboard=cancel_keyboard())


@bot.on.message(state=MarketState.PRICE)
async def market_price(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    if not (message.text or "").isdigit() or int(message.text) <= 0:
        await message.answer("Введи число > 0")
        return
    payload = dict(message.state_peer.payload or {})
    payload["price"] = int(message.text)
    await state_dispenser.set(message.peer_id, MarketState.PHOTO, **payload)
    await message.answer(
        "Пришли фото товара (картинкой) или «-» без фото:",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=MarketState.PHOTO)
async def market_photo(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    payload = dict(message.state_peer.payload or {})
    photo = ""
    # VK attachments
    try:
        atts = message.attachments or []
        for a in atts:
            if getattr(a, "photo", None):
                sizes = a.photo.sizes
                if sizes:
                    photo = sorted(sizes, key=lambda s: s.width * s.height)[-1].url
                # better: use attachment string photo-owner_id_id
                ph = a.photo
                photo = f"photo{ph.owner_id}_{ph.id}"
                if getattr(ph, "access_key", None):
                    photo += f"_{ph.access_key}"
                break
    except Exception:
        pass
    if (message.text or "").strip() == "-":
        photo = ""
    mid = await market_add(message.from_id, payload.get("title", "Товар"), int(payload.get("price", 0)), photo)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Лот #{mid} на бирже!", keyboard=await main_menu(message.from_id))


@bot.on.message(PayloadRule({"cmd": "mbuy"}))
async def market_buy(message: Message):
    player = await require_player(message)
    if not player:
        return
    mid = int((message.get_payload_json() or {}).get("id", 0))
    lot = await market_get(mid)
    if not lot or not lot["active"]:
        await message.answer("Лот недоступен")
        return
    if lot["seller_id"] == message.from_id:
        await message.answer("Нельзя купить свой лот")
        return
    if player["balance"] < lot["price"]:
        await message.answer("Недостаточно ₽")
        return
    await add_balance(message.from_id, -lot["price"], "market_buy")
    await add_balance(lot["seller_id"], lot["price"], "market_sell")
    await market_close(mid)
    await message.answer(f"✅ Куплено: {lot['title']} за {lot['price']}₽")
    await notify_user(
        message.ctx_api, lot["seller_id"],
        f"🏪 Ваш лот #{mid} «{lot['title']}» куплен за {lot['price']}₽",
    )


@bot.on.message(text=["👑 Лидерство", "Лидерство", "лидерство", "лидерка"])
async def leader_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(
        "👑 Заявка на лидерство фракции\nВыбери фракцию:",
        keyboard=frac_choice_keyboard(),
    )
    await state_dispenser.set(message.peer_id, LeaderState.FRACTION)


@bot.on.message(state=LeaderState.FRACTION)
async def leader_frac(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    if message.text not in FRACTIONS:
        await message.answer("Выбери фракцию кнопкой:", keyboard=frac_choice_keyboard())
        return
    await state_dispenser.set(message.peer_id, LeaderState.MOTIVE, fraction=message.text)
    await message.answer(
        "Опыт RP, почему ты, планы на фракцию:",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=LeaderState.MOTIVE)
async def leader_motive(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    motive = (message.text or "").strip()
    if len(motive) < 15:
        await message.answer("Напиши подробнее (от 15 символов):")
        return
    player = await get_player(message.from_id)
    frac = (message.state_peer.payload or {}).get("fraction")
    aid = await leader_app_add(message.from_id, player["nickname"], frac, motive)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Заявка на лидерство #{aid} отправлена!", keyboard=await main_menu(message.from_id))
    await notify_admins(
        message.ctx_api,
        f"👑 Лидерство #{aid}\n[id{message.from_id}|{player['nickname']}] → {frac}\n{motive}",
    )


@bot.on.message(text=["⚖️ Апелляция", "Апелляция", "апелляция", "суд"])
async def appeal_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("⚖️ Опиши ситуацию для апелляции/суда:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, AppealState.TEXT)


@bot.on.message(state=AppealState.TEXT)
async def appeal_save(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    text_a = (message.text or "").strip()
    if len(text_a) < 10:
        await message.answer("Подробнее:")
        return
    aid = await appeal_add(message.from_id, text_a)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Апелляция #{aid} принята.", keyboard=await main_menu(message.from_id))
    await notify_admins(message.ctx_api, f"⚖️ Апелляция #{aid} от [id{message.from_id}|user]\n{text_a}")


@bot.on.message(text=["🤝 Напарник", "Напарник", "напарник", "ищу напарника"])
async def partner_menu(message: Message):
    player = await require_player(message)
    if not player:
        return
    rows = await partners_list(12)
    kb = (
        Keyboard(one_time=False)
        .add(Text("➕ Я ищу напарника"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    )
    lines = ["🤝 Доска «ищу напарника»:\n"]
    if not rows:
        lines.append("Пока пусто. Нажми «Я ищу напарника».")
    else:
        for pid, uid, body, created in rows:
            lines.append(f"• [id{uid}|игрок] ({created}): {body}")
    await message.answer("\n".join(lines)[:3500], keyboard=kb)


@bot.on.message(text=["➕ Я ищу напарника", "Я ищу напарника"])
async def partner_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("Что ищешь? (режим, время, фракция):", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, PartnerState.TEXT)


@bot.on.message(state=PartnerState.TEXT)
async def partner_save(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    body = (message.text or "").strip()
    if len(body) < 5:
        await message.answer("Коротко слишком:")
        return
    await partner_add(message.from_id, body)
    await state_dispenser.delete(message.peer_id)
    await message.answer("✅ Анкета размещена!", keyboard=await main_menu(message.from_id))


@bot.on.message(text=["🛡️ Клан", "Клан", "клан"])
async def clan_menu(message: Message):
    player = await require_player(message)
    if not player:
        return
    clan = await clan_by_user(message.from_id)
    if clan:
        await message.answer(
            f"🛡️ Клан [{clan['tag']}] {clan['name']}\n"
            f"Лидер: id{clan['leader_id']}\n"
            f"Создан: {clan['created_at']}"
        )
        return
    await message.answer(
        "Ты не в клане. Создать: напиши\n"
        "клансоздать Название | ТЕГ\n"
        "Пример: клансоздать Night Wolves | NW"
    )


@bot.on.message(text=["📸 Конкурс", "Конкурс", "конкурс"])
async def contest_menu(message: Message):
    c = await active_contest()
    if not c:
        await message.answer("📸 Активного конкурса скринов нет.")
        return
    await message.answer(
        f"📸 Конкурс: {c['title']}\n"
        f"Пришли фото с подписью: конкурс фото\n"
        f"(или прикрепи картинку и напиши «конкурс фото»)"
    )


@bot.on.message(text=["📅 Запись на ивент", "запись на ивент"])
async def event_reg_help(message: Message):
    await message.answer("Запись: `ивентзапись ID`\nСписок ивентов — кнопка «📅 Ивенты»")


@bot.on.message(text=["🔐 2FA", "2FA", "2fa"])
async def twofa_toggle(message: Message):
    player = await require_player(message)
    if not player:
        return
    cur = int(player.get("twofa") or 0)
    new = 0 if cur else 1
    await set_player_field(message.from_id, "twofa", new)
    await message.answer(
        "🔐 2FA " + ("ВКЛ. Код будет приходить при смене пароля/email." if new else "ВЫКЛ.")
    )


@bot.on.message(text=["📧 Сменить email", "Сменить email", "сменить email"])
async def email_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(f"Текущий: {player.get('email')}\nВведи новый email:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, EmailState.NEW)


@bot.on.message(state=EmailState.NEW)
async def email_save(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    email = (message.text or "").strip()
    if not EMAIL_RE.match(email):
        await message.answer("Некорректный email")
        return
    await set_player_field(message.from_id, "email", email)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Email изменён: {email}", keyboard=await main_menu(message.from_id))


@bot.on.message(text=["⭐ Репутация", "Репутация", "репутация"])
async def rep_self(message: Message):
    player = await require_player(message)
    if not player:
        return
    val = await rep_value(message.from_id)
    badges = await badges_list(message.from_id)
    b = ", ".join(badges) if badges else "—"
    vip = player.get("vip_level") or "нет"
    vu = player.get("vip_until") or "—"
    await message.answer(
        f"⭐ Репутация: {val}\n"
        f"Значки: {b}\n"
        f"VIP: {vip} до {vu}\n"
        f"Поставить игроку: реп Ник +1  или  реп Ник -1"
    )



@bot.on.message(text=["👑 Лидерки", "Лидерки"])
async def leader_apps_admin(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    rows = await leader_apps_list("pending")
    if not rows:
        await message.answer("Нет заявок на лидерство.")
        return
    for aid, uid, nick, frac, motive, created in rows:
        kb = (
            Keyboard(inline=True)
            .add(Text("✅ Лидер", payload={"cmd": "lead_ok", "id": aid}), color=KeyboardButtonColor.POSITIVE)
            .add(Text("❌", payload={"cmd": "lead_no", "id": aid}), color=KeyboardButtonColor.NEGATIVE)
        )
        await message.answer(
            f"👑 #{aid} [id{uid}|{nick}] → {frac}\n{motive}\n({created})",
            keyboard=kb,
        )


@bot.on.message(PayloadRule({"cmd": "lead_ok"}))
async def lead_ok(message: Message):
    if not await has_role(message.from_id, "mod"):
        return
    aid = int((message.get_payload_json() or {})["id"])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leader_apps WHERE id = ?", (aid,)) as cur:
            app = await cur.fetchone()
            app = dict(app) if app else None
    if not app:
        return
    await leader_app_set(aid, "approved")
    await set_fraction(app["user_id"], app["fraction"])
    await badge_add(app["user_id"], f"Лидер:{app['fraction']}")
    await admin_log(message.from_id, "leader_ok", app["nickname"], app["fraction"])
    await message.answer(f"✅ {app['nickname']} — лидер {app['fraction']}")
    await notify_user(
        message.ctx_api, app["user_id"],
        f"👑 Поздравляем! Вы назначены лидером фракции «{app['fraction']}»!",
    )


@bot.on.message(PayloadRule({"cmd": "lead_no"}))
async def lead_no(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    aid = int((message.get_payload_json() or {})["id"])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leader_apps WHERE id = ?", (aid,)) as cur:
            app = await cur.fetchone()
            app = dict(app) if app else None
    if not app:
        return
    await leader_app_set(aid, "rejected")
    await message.answer(f"❌ Отказ #{aid}")
    await notify_user(message.ctx_api, app["user_id"], "👑 Заявка на лидерство отклонена.")


@bot.on.message(text=["⚖️ Апелляции", "Апелляции"])
async def appeals_admin(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    rows = await appeals_list("open")
    if not rows:
        await message.answer("Апелляций нет.")
        return
    for aid, uid, body, created in rows:
        await message.answer(
            f"⚖️ #{aid} [id{uid}|user] ({created})\n{body}\nзакрытьапелляцию {aid}"
        )


@bot.on.message(text=["📰 Пост новости", "Пост новости"])
async def news_post_start(message: Message):
    if not await has_role(message.from_id, "mod"):
        return
    await message.answer("Текст новости:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, NewsState.TEXT)


@bot.on.message(state=NewsState.TEXT)
async def news_post_save(message: Message):
    if not await is_admin(message.from_id):
        return
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=admin_keyboard(await is_owner(message.from_id)))
        return
    body = (message.text or "").strip()
    if len(body) < 5:
        await message.answer("Коротко:")
        return
    nid = await add_news(body, message.from_id)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"✅ Новость #{nid} опубликована")
    # notify players
    for user_id, nick, status, level, balance, banned, email in await get_all_players():
        if not banned:
            await notify_user(message.ctx_api, user_id, f"📰 Новость #{nid}:\n{body}")


@bot.on.message(text=["🔫 Розыск админ", "Розыск админ"])
async def wanted_admin_help(message: Message):
    if not await is_admin(message.from_id):
        return
    await message.answer(
        "розыск Ник причина 3\n"
        "снятьрозыск Ник\n"
        "Список: кнопка «Розыск» у игроков"
    )


@bot.on.message(text=["🛠️ Админ-панель", "Админ-панель", "админка", "Админка"])
async def admin_panel(message: Message):
    if not await is_admin(message.from_id):
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(
        f"🛠️ Админ-панель {PROJECT}",
        keyboard=admin_keyboard(show_fake=await is_owner(message.from_id)),
    )


@bot.on.message(text=["❓ Админ-помощь", "Админ-помощь", "админпомощь"])
async def admin_help(message: Message):
    if not await is_admin(message.from_id):
        return
    await message.answer(
        "🛠️ Команды Prp Bot\n\n"
        "👤 info Ник|email|id\n"
        "💰 выдать / забрать / баланс / уровень\n"
        "⛔ бан / разбан / пред / сказать / удалить\n"
        "🔑 пароль Ник новыйпароль — сброс пароля\n"
        "📢 выдатьвсем / рассылка\n"
        "⭐ админдобавить id [helper|mod]\n"
        "⭐ админубрать id\n"
        "🎁 промосоздать КОД деньги [использований]\n"
        "🏛️ заявки фракций / тикеты / жалобы\n"
        "🎫 ответ ID текст | закрытьтикет ID\n"
        "⏰ ивент 2ч Текст — отложенная рассылка\n"
        "📡 вход / выход [Ник]\n"
        "📋 Лог админов\n"
        "Роли: helper < mod < owner"
    )


@bot.on.message(text=["👥 Игроки", "Игроки"])
async def show_players(message: Message):
    if not await is_admin(message.from_id):
        return
    players = await get_all_players()
    if not players:
        await message.answer("📭 Игроков нет.")
        return
    lines = ["👥 Игроки:\n"]
    for user_id, nick, status, level, balance, banned, email in players[:40]:
        lines.append(
            f"[id{user_id}|{nick}] ур.{level} {balance}₽ {email or ''}{' BAN' if banned else ''}"
        )
    await message.answer("\n".join(lines))


@bot.on.message(text=["📊 Статистика", "Статистика"])
async def stats_cmd(message: Message):
    if not await is_admin(message.from_id):
        return
    s = await get_stats()
    await message.answer(
        f"📊 Статистика {PROJECT_SHORT}\n\n"
        f"Аккаунтов: {s['total']}\nБаны: {s['banned']}\nДенег: {s['money']}₽\n"
        f"Идей: {s['ideas']} | Голосов: {s['votes']}\n"
        f"Тикетов: {s['tickets']} | Жалоб: {s['reports']}\n"
        f"Заявок во фракции: {s['fracs']}"
    )


@bot.on.message(text=["⛔ Баны", "Баны"])
async def bans_list(message: Message):
    if not await is_admin(message.from_id):
        return
    rows = await get_banned_players()
    if not rows:
        await message.answer("✅ Бан-лист пуст.")
        return
    await message.answer("⛔ Баны:\n" + "\n".join(f"[id{u}|{n}]" for u, n in rows))


@bot.on.message(text=["🎫 Тикеты", "Тикеты", "тикеты"])
async def tickets_admin(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    rows = await list_open_tickets()
    if not rows:
        await message.answer("📭 Открытых тикетов нет.")
        return
    for tid, uid, nick, text, created in rows:
        await message.answer(
            f"🎫 #{tid} [id{uid}|{nick}] ({created})\n{text}\n\nответ {tid} текст | закрытьтикет {tid}"
        )


@bot.on.message(text=["🏛️ Заявки фракций", "Заявки фракций"])
async def frac_admin(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    rows = await list_frac_apps("pending")
    if not rows:
        await message.answer("📭 Нет заявок.")
        return
    for app_id, uid, nick, frac, motive, created in rows:
        await message.answer(
            f"🏛️ #{app_id} [id{uid}|{nick}] → {frac}\n{motive}\n({created})",
            keyboard=frac_app_keyboard(app_id),
        )


@bot.on.message(text=["🚨 Жалобы", "Жалобы", "жалобы"])
async def reports_admin(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    rows = await list_reports("new")
    if not rows:
        await message.answer("📭 Новых жалоб нет.")
        return
    for rid, rep, target, reason, created in rows:
        await message.answer(
            f"🚨 #{rid} от [id{rep}|user] на {target}\n{reason}\n({created})\n"
            f"закрытьжалобу {rid}"
        )


@bot.on.message(text=["💡 Идеи", "Идеи", "идеи"])
async def ideas_admin(message: Message):
    if not await is_admin(message.from_id):
        return
    rows = await list_ideas("new")
    if not rows:
        await message.answer("📭 Новых идей нет.")
        return
    for iid, uid, nick, text, status, created in rows:
        await message.answer(
            f"💡 #{iid} от [id{uid}|{nick}] ({created})\n{text}",
            keyboard=idea_keyboard(iid),
        )


@bot.on.message(text=["🗳️ Голосования админ", "Голосования админ", "голосования админ"])
async def votes_admin(message: Message):
    if not await is_admin(message.from_id):
        return
    votes = await get_active_votes()
    if not votes:
        await message.answer("Нет активных.\nСоздать: голосование Вопрос | да | нет")
        return
    for vid, question, options_json in votes:
        options = json.loads(options_json)
        results = await vote_results(vid)
        total = sum(results.values()) or 1
        lines = [f"#{vid} {question}"]
        for i, opt in enumerate(options):
            c = results.get(i, 0)
            lines.append(f"  {opt}: {c} ({c * 100 // total}%)")
        await message.answer("\n".join(lines))


@bot.on.message(text=["📋 Лог админов", "Лог админов", "лог"])
async def logs_admin(message: Message):
    if not await has_role(message.from_id, "mod"):
        await message.answer("Нужна роль mod+")
        return
    rows = await get_admin_logs(25)
    if not rows:
        await message.answer("Лог пуст.")
        return
    lines = ["📋 Последние действия:\n"]
    for admin_id, action, target, details, created in rows:
        lines.append(f"• [{created}] id{admin_id} {action} {target} {details}".strip())
    await message.answer("\n".join(lines[:30]))


@bot.on.message(text=["⭐ Админы бота", "Админы бота", "админы бота"])
async def admins_list(message: Message):
    if not await is_admin(message.from_id):
        return
    lines = ["⭐ Главные (owner из .env):\n"] + [f"• [id{a}|id{a}] owner" for a in ENV_ADMINS]
    extra = await db_admins()
    lines.append("\nДобавленные:")
    if not extra:
        lines.append("• нет")
    else:
        for uid, role in extra:
            lines.append(f"• [id{uid}|id{uid}] {role}")
    await message.answer("\n".join(lines))


@bot.on.message(text=["🔙 Назад", "Назад"])
async def back(message: Message):
    await message.answer("📋 Меню:", keyboard=await main_menu(message.from_id))


@bot.on.message(text=["🟢 Фейк вход", "Фейк вход", "фейк вход"])
async def fake_join_btn(message: Message):
    if not await is_owner(message.from_id):
        return
    nick = random.choice(list(FAKE_PLAYERS.keys()))
    text_n = format_server_notify(nick, "join")
    sent = await notify_admins(message.ctx_api, text_n)
    await admin_log(message.from_id, "fake_join", nick)
    await message.answer(f"✅ В ЛС админам: {sent}\n\n{text_n}")


@bot.on.message(text=["🔴 Фейк выход", "Фейк выход", "фейк выход"])
async def fake_leave_btn(message: Message):
    if not await is_owner(message.from_id):
        return
    nick = random.choice(list(FAKE_PLAYERS.keys()))
    text_n = format_server_notify(nick, "leave")
    sent = await notify_admins(message.ctx_api, text_n)
    await admin_log(message.from_id, "fake_leave", nick)
    await message.answer(f"✅ В ЛС админам: {sent}\n\n{text_n}")


# payloads
@bot.on.message(PayloadRule({"cmd": "idea_ok"}))
async def idea_ok(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "accepted")
    await admin_log(message.from_id, "idea_ok", str(iid))
    await message.answer(f"✅ Идея #{iid} принята")
    await notify_user(message.ctx_api, idea["user_id"], f"✅ Идея #{iid} принята!")


@bot.on.message(PayloadRule({"cmd": "idea_no"}))
async def idea_no(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "rejected")
    await message.answer(f"❌ Идея #{iid} отклонена")
    await notify_user(message.ctx_api, idea["user_id"], f"❌ Идея #{iid} отклонена.")


@bot.on.message(PayloadRule({"cmd": "idea_done"}))
async def idea_done(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "done")
    await message.answer(f"✔️ Идея #{iid} выполнена")
    await notify_user(message.ctx_api, idea["user_id"], f"✔️ Идея #{iid} реализована!")


@bot.on.message(PayloadRule({"cmd": "frac_ok"}))
async def frac_ok(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    app_id = int((message.get_payload_json() or {})["id"])
    app = await get_frac_app(app_id)
    if not app:
        return
    await set_frac_app(app_id, "approved")
    await set_fraction(app["user_id"], app["fraction"])
    await admin_log(message.from_id, "frac_ok", app["nickname"], app["fraction"])
    await message.answer(f"✅ {app['nickname']} принят в {app['fraction']}")
    await notify_user(
        message.ctx_api, app["user_id"],
        f"🏛️ Заявка одобрена! Ты в фракции: {app['fraction']}",
    )


@bot.on.message(PayloadRule({"cmd": "frac_no"}))
async def frac_no(message: Message):
    if not await has_role(message.from_id, "helper"):
        return
    app_id = int((message.get_payload_json() or {})["id"])
    app = await get_frac_app(app_id)
    if not app:
        return
    await set_frac_app(app_id, "rejected")
    await message.answer(f"❌ Отказ по заявке #{app_id}")
    await notify_user(message.ctx_api, app["user_id"], f"🏛️ Заявка во фракцию {app['fraction']} отклонена.")


# ----- text commands -----
@bot.on.message()
async def text_commands(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    if await check_autoban(message):
        return

    global _maintenance
    if _maintenance and not await is_admin(message.from_id):
        t = message.text or ""
        allow = any(x in t for x in (
            "Статус", "статус", "Информация", "инфо", "меню", "Меню",
            "Начать", "начать", "онлайн", "сервер",
        ))
        if not allow:
            await message.answer("🔧 Сервер на технических работах. Зайти сейчас нельзя. Смотри «📡 Статус сервера».")
            return

    low = text.lower()
    uid = message.from_id
    api = message.ctx_api

    # promo for players
    if low.startswith("промокод "):
        player = await require_player(message)
        if not player:
            return
        code = text.split(maxsplit=1)[1].strip()
        ok, msg = await use_promo(code, uid)
        await message.answer(("✅ " if ok else "❌ ") + msg)
        if ok:
            await admin_log(uid, "promo_use", code, msg)
        return

    if low.startswith("перевод "):
        player = await require_player(message)
        if not player:
            return
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit() or int(parts[-1]) <= 0:
            await message.answer("💸 перевод Ник 500")
            return
        amount = int(parts[-1])
        nick = " ".join(parts[1:-1])
        if player["balance"] < amount:
            await message.answer("❌ Недостаточно средств")
            return
        target = await get_player_by_nick(nick)
        if not target or target["user_id"] == uid:
            await message.answer("❌ Получатель недоступен")
            return
        await add_balance(uid, -amount)
        await add_balance(target["user_id"], amount)
        await react_to(message, 1)
        await message.answer(f"✅ Переведено {amount}₽ → {target['nickname']}")
        await notify_user(api, target["user_id"], f"+{amount}₽ от {player['nickname']}")
        return


    if low.startswith("реп "):
        player = await require_player(message)
        if not player:
            return
        parts = text.split()
        if len(parts) < 3 or parts[-1] not in ("+1", "-1", "+1", "1", "-1"):
            await message.answer("реп Ник +1  или  реп Ник -1")
            return
        val = 1 if parts[-1].startswith("+") or parts[-1] == "1" else -1
        if parts[-1] == "-1" or parts[-1].startswith("-"):
            val = -1
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target or target["user_id"] == uid:
            await message.answer("Нельзя")
            return
        await rep_set(uid, target["user_id"], val)
        await message.answer(f"⭐ Репутация {target['nickname']}: {val:+d}")
        return

    if low.startswith("ивентзапись ") and text.split()[-1].isdigit():
        player = await require_player(message)
        if not player:
            return
        eid = int(text.split()[-1])
        ok = await register_event(eid, uid)
        if ok:
            cnt = await event_reg_count(eid)
            await message.answer(f"✅ Запись на ивент #{eid}. Участников: {cnt}")
        else:
            await message.answer("Уже записан или ошибка")
        return

    if low.startswith("клансоздать "):
        player = await require_player(message)
        if not player:
            return
        if player.get("clan_id"):
            await message.answer("Ты уже в клане")
            return
        body = text[len("клансоздать "):]
        if "|" not in body:
            await message.answer("клансоздать Название | ТЕГ")
            return
        name, tag = [x.strip() for x in body.split("|", 1)]
        if len(tag) > 5 or len(name) < 3:
            await message.answer("ТЕГ до 5 символов, название от 3")
            return
        try:
            cid = await clan_create(name, tag.upper(), uid)
            await badge_add(uid, f"Клан:{tag.upper()}")
            await message.answer(f"🛡️ Клан [{tag.upper()}] {name} создан! ID {cid}")
        except Exception:
            await message.answer("Имя или тег заняты")
        return

    if low == "конкурс фото" or low.startswith("конкурс фото"):
        player = await require_player(message)
        if not player:
            return
        c = await active_contest()
        if not c:
            await message.answer("Нет активного конкурса")
            return
        photo = ""
        try:
            for a in (message.attachments or []):
                if getattr(a, "photo", None):
                    ph = a.photo
                    photo = f"photo{ph.owner_id}_{ph.id}"
                    if getattr(ph, "access_key", None):
                        photo += f"_{ph.access_key}"
                    break
        except Exception:
            pass
        if not photo:
            await message.answer("Прикрепи фото к сообщению")
            return
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO contest_entries (contest_id, user_id, photo, created_at) VALUES (?, ?, ?, ?)",
                (c["id"], uid, photo, now_str()),
            )
            await db.commit()
        await message.answer("✅ Работа отправлена на конкурс!")
        return


    if not await is_admin(uid):
        return


    if low.startswith("штраф "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("штраф Ник 500 [причина]")
            return
        amount = int(parts[-1]) if parts[-1].isdigit() else int(parts[2])
        # parse: штраф nick amount reason
        if not parts[-1].isdigit():
            await message.answer("штраф Ник 500 причина")
            return
        # find amount position
        amount = None
        for i, p in enumerate(parts):
            if i > 0 and p.isdigit():
                amount = int(p)
                nick = " ".join(parts[1:i])
                reason = " ".join(parts[i+1:]) or "штраф"
                break
        if amount is None:
            await message.answer("штраф Ник 500 причина")
            return
        target = await resolve_player(nick)
        if not target:
            await message.answer("Не найден")
            return
        await add_balance(target["user_id"], -amount, "fine")
        await admin_log(uid, "fine", target["nickname"], f"{amount} {reason}")
        await message.answer(f"💸 Штраф {amount}₽ → {target['nickname']}")
        await notify_user(api, target["user_id"], f"💸 Штраф {amount}₽\nПричина: {reason}")
        return

    if low.startswith("варн ") or low.startswith("пред "):
        # keep pred existing; enhance warn table on "варн"
        if low.startswith("варн "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                await message.answer("варн Ник причина")
                return
            target = await resolve_player(parts[1])
            if not target:
                await message.answer("Не найден")
                return
            await add_warn(target["user_id"], uid, parts[2])
            n = await count_warns(target["user_id"])
            await admin_log(uid, "warn", target["nickname"], parts[2])
            await message.answer(f"⚠️ Варн #{n} → {target['nickname']}")
            await notify_user(api, target["user_id"], f"⚠️ Варн ({n}): {parts[2]}")
            return

    if low.startswith("уволить "):
        target = await resolve_player(text[8:].strip())
        if not target:
            await message.answer("Не найден")
            return
        await set_fraction(target["user_id"], None)
        await admin_log(uid, "fire", target["nickname"])
        await message.answer(f"Уволен из фракции: {target['nickname']}")
        await notify_user(api, target["user_id"], "Вас уволили из фракции.")
        return

    if low.startswith("розыск "):
        parts = text.split()
        # розыск Nick reason stars
        if len(parts) < 4 or not parts[-1].isdigit():
            await message.answer("розыск Ник причина 3")
            return
        stars = int(parts[-1])
        nick = parts[1]
        reason = " ".join(parts[2:-1])
        target = await resolve_player(nick)
        if not target:
            await message.answer("Не найден")
            return
        await set_wanted(target["user_id"], target["nickname"], reason, stars, uid)
        await admin_log(uid, "wanted", target["nickname"], reason)
        await message.answer(f"🔫 В розыск: {target['nickname']} ({stars}★)")
        return

    if low.startswith("снятьрозыск "):
        target = await resolve_player(text[12:].strip())
        if not target:
            await message.answer("Не найден")
            return
        await clear_wanted(target["user_id"])
        await message.answer(f"Розыск снят: {target['nickname']}")
        return

    if low.startswith("закрытьапелляцию ") and text.split()[-1].isdigit():
        aid = int(text.split()[-1])
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (aid,))
            await db.commit()
        await message.answer(f"Апелляция #{aid} закрыта")
        return

    if low.startswith("куратор "):
        parts = text.split()
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("куратор ID_тикета idVK")
            return
        tid = int(parts[1])
        cid = int(parts[2][2:]) if parts[2].lower().startswith("id") else int(parts[2])
        await set_ticket_curator(tid, cid)
        await message.answer(f"Куратор тикета #{tid}: {cid}")
        await notify_user(api, cid, f"Вам назначен тикет #{tid}")
        return

    if low.startswith("шаблон "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("шаблон N ID_тикета\nШаблоны: " + ", ".join(f"{k}) {v[:40]}" for k,v in SUPPORT_TEMPLATES.items()))
            return
        num, tid = parts[1], int(parts[2])
        tpl = SUPPORT_TEMPLATES.get(num)
        if not tpl:
            await message.answer("Нет такого шаблона 1-5")
            return
        t = await get_ticket(tid)
        if not t:
            await message.answer("Тикет не найден")
            return
        await ticket_reply(tid, tpl, 1)
        await notify_user(api, t["user_id"], f"🎫 Ответ по тикету #{tid}:\n{tpl}")
        await message.answer("Шаблон отправлен")
        return

    if low.startswith("vip "):
        parts = text.split()
        # vip Nick gold
        if len(parts) < 3:
            await message.answer("vip Ник bronze|gold|platinum")
            return
        level = parts[-1].lower()
        if level not in VIP_LEVELS:
            await message.answer("Уровни: bronze gold platinum")
            return
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        await grant_vip(target["user_id"], level)
        await admin_log(uid, "vip", target["nickname"], level)
        await message.answer(f"VIP {level} → {target['nickname']}")
        await notify_user(api, target["user_id"], f"⭐ Вам выдан {VIP_LEVELS[level]['label']}!")
        return

    if low.startswith("подаритьvip "):
        parts = text.split()
        if len(parts) < 3:
            await message.answer("подаритьvip Ник gold")
            return
        level = parts[-1].lower()
        if level not in VIP_LEVELS:
            await message.answer("bronze|gold|platinum")
            return
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        price = VIP_LEVELS[level]["price"]
        giver = await get_player(uid)
        if not giver or (giver.get("prp_coin") or 0) < price:
            await message.answer(f"Нужно {price} PC")
            return
        await add_prp_coin(uid, -price)
        await grant_vip(target["user_id"], level)
        await message.answer(f"Подарен VIP {level} → {target['nickname']}")
        await notify_user(api, target["user_id"], f"🎁 Вам подарили {VIP_LEVELS[level]['label']}!")
        return

    if low.startswith("донатцель "):
        # донатцель 10000 На новый мод
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("донатцель 10000 Описание")
            return
        await set_setting("donate_goal", parts[1])
        await set_setting("donate_goal_title", parts[2])
        await set_setting("donate_now", "0")
        await message.answer(f"Цель: {parts[1]} PC — {parts[2]}")
        return

    if low == "донатцель":
        goal = int(await get_setting("donate_goal", "0") or 0)
        now = int(await get_setting("donate_now", "0") or 0)
        title = await get_setting("donate_goal_title", "Донат")
        if goal <= 0:
            await message.answer("Цель не задана")
            return
        pct = min(100, now * 100 // goal)
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        await message.answer(f"🎯 {title}\n{bar} {pct}%\n{now}/{goal} PC")
        return

    if low.startswith("донатпромо "):
        # донатпромо 20 2026-12-31
        parts = text.split()
        if len(parts) < 3:
            await message.answer("донатпромо 20 2026-12-31")
            return
        await set_setting("donate_promo_pct", parts[1])
        await set_setting("donate_promo_until", parts[2])
        await message.answer(f"Промо -{parts[1]}% до {parts[2]}")
        return

    if low.startswith("конкурссоздать "):
        title = text[len("конкурссоздать "):].strip()
        cid = await contest_create(title)
        await message.answer(f"📸 Конкурс #{cid}: {title}")
        return

    if low.startswith("подписка "):
        # подписка Nick 30
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("подписка Ник 30")
            return
        days = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        until = (datetime.now() + timedelta(days=days)).isoformat()
        await set_player_field(target["user_id"], "sub_until", until)
        await grant_vip(target["user_id"], "gold", days)
        await message.answer(f"Подписка {days}д → {target['nickname']}")
        return


    # fake join/leave

    if low in ("техработы вкл", "техработы on", "техработы 1"):
        if not await is_owner(uid):
            return
        _maintenance = True
        await set_setting("maintenance", "1")
        await admin_log(uid, "maintenance", "on")
        await message.answer("🔧 Техработы ВКЛ")
        await notify_admins(api, "🔧 Техработы включены")
        return

    if low in ("техработы выкл", "техработы off", "техработы 0"):
        if not await is_owner(uid):
            return
        _maintenance = False
        await set_setting("maintenance", "0")
        await admin_log(uid, "maintenance", "off")
        await message.answer("✅ Техработы ВЫКЛ")
        await notify_admins(api, "✅ Техработы выключены")
        return

    if low.startswith("выдатьcoin ") or low.startswith("выдатьpc "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("выдатьcoin Ник 50")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("❌ Не найден")
            return
        await add_prp_coin(target["user_id"], amount)
        extra = ""
        if amount > 0 and CASHBACK_PERCENT > 0:
            cb = max(1, amount * CASHBACK_PERCENT // 100)
            await add_balance(target["user_id"], cb, "cashback")
            extra = f" (+{cb}₽ кэшбэк)"
            try:
                now_pc = int(await get_setting("donate_now", "0") or 0)
                await set_setting("donate_now", str(now_pc + amount))
            except Exception:
                pass
        await admin_log(uid, "give_coin", target["nickname"], str(amount))
        await message.answer(f"💎 +{amount} Prp Coin → {target['nickname']}{extra}")
        await notify_user(api, target["user_id"], f"💎 Вам начислено {amount} Prp Coin{extra}")
        return

    if low.startswith("ивентдобавить "):
        body = text[len("ивентдобавить "):].strip()
        if "|" not in body:
            await message.answer("ивентдобавить 05.09 18:00 Название | Описание")
            return
        left, desc = body.split("|", 1)
        parts = left.strip().split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("ивентдобавить 05.09 18:00 Название | Описание")
            return
        event_at = f"{parts[0]} {parts[1]}"
        title = parts[2]
        eid = await add_event(title, desc.strip(), event_at, uid)
        await admin_log(uid, "event_add", str(eid), title)
        await message.answer(f"📅 Ивент #{eid}: [{event_at}] {title}")
        return

    if low.startswith("вход") or low.startswith("выход"):
        if not await is_owner(uid):
            return
        parts = text.split()
        action = "join" if low.startswith("вход") else "leave"
        nick = parts[1].strip() if len(parts) >= 2 else random.choice(list(FAKE_PLAYERS.keys()))
        for k in FAKE_PLAYERS:
            if k.lower() == nick.lower():
                nick = k
                break
        msg_n = format_server_notify(nick, action)
        sent = await notify_admins(api, msg_n)
        await admin_log(uid, action, nick)
        await message.answer(f"✅ В ЛС админам: {sent}\n\n{msg_n}")
        return

    if low.startswith("info ") or low.startswith("инфо "):
        p = await resolve_player(text.split(maxsplit=1)[1])
        if not p:
            await message.answer("❌ Не найден")
            return
        await message.answer(
            f"👤 {p['nickname']}\nEmail: {p.get('email')}\nФракция: {p.get('fraction') or '—'}\n"
            f"Ур: {p['level']} | Баланс: {p['balance']}₽\n"
            f"Бан: {'да' if p.get('banned') else 'нет'}\n"
            f"Реф: {p.get('referral_code')}\nhttps://vk.com/id{p['user_id']}"
        )
        return

    if low.startswith("пароль "):
        if not await has_role(uid, "mod"):
            await message.answer("Нужна роль mod+")
            return
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("пароль Ник новыйпароль")
            return
        target = await resolve_player(parts[1])
        new_pass = parts[2].strip()
        if not target:
            await message.answer("❌ Не найден")
            return
        if len(new_pass) < 6:
            await message.answer("Пароль короткий")
            return
        await set_password(target["user_id"], new_pass)
        await admin_log(uid, "password_reset", target["nickname"])
        await message.answer(f"✅ Пароль сброшен для {target['nickname']}")
        await notify_user(
            api, target["user_id"],
            f"🔑 Админ сбросил ваш пароль.\nНовый пароль: {new_pass}\nСмените его в боте.",
        )
        return

    if low.startswith("ответ "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            await message.answer("ответ ID текст")
            return
        tid = int(parts[1])
        body = parts[2]
        t = await get_ticket(tid)
        if not t or t["status"] != "open":
            await message.answer("Тикет не найден / закрыт")
            return
        await ticket_reply(tid, body, 1)
        await admin_log(uid, "ticket_reply", str(tid))
        await message.answer(f"✅ Ответ в тикет #{tid} отправлен")
        await notify_user(api, t["user_id"], f"🎫 Ответ по тикету #{tid}:\n{body}")
        return

    if low.startswith("закрытьтикет ") and text.split()[-1].isdigit():
        tid = int(text.split()[-1])
        t = await get_ticket(tid)
        if not t:
            await message.answer("Нет")
            return
        await close_ticket(tid)
        await admin_log(uid, "ticket_close", str(tid))
        await message.answer(f"✅ Тикет #{tid} закрыт")
        await notify_user(api, t["user_id"], f"🎫 Тикет #{tid} закрыт.")
        return

    if low.startswith("закрытьжалобу ") and text.split()[-1].isdigit():
        rid = int(text.split()[-1])
        await set_report_status(rid, "closed")
        await admin_log(uid, "report_close", str(rid))
        await message.answer(f"✅ Жалоба #{rid} закрыта")
        return

    if low.startswith("промосоздать "):
        if not await has_role(uid, "mod"):
            await message.answer("Нужна роль mod+")
            return
        parts = text.split()
        # промосоздать CODE 500 [uses]
        if len(parts) < 3 or not parts[2].isdigit():
            await message.answer("промосоздать КОД 500 [лимит]")
            return
        code = parts[1]
        money = int(parts[2])
        uses = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 100
        try:
            await create_promo(code, money, None, uses, uid)
            await admin_log(uid, "promo_create", code, f"{money}x{uses}")
            await message.answer(f"✅ Промокод {code.upper()}: {money}₽, до {uses} акт.")
        except Exception:
            await message.answer("Ошибка (возможно код уже есть)")
        return

    if low.startswith("ивент "):
        # ивент 2ч Текст...  or ивент 30м Текст
        rest = text[6:].strip()
        m = re.match(r"(\d+)\s*(ч|час|часа|часов|м|мин|минут)\s+(.+)", rest, re.I)
        if not m:
            await message.answer("ивент 2ч Текст рассылки\nивент 30м Текст")
            return
        num = int(m.group(1))
        unit = m.group(2).lower()
        body = m.group(3).strip()
        minutes = num * 60 if unit.startswith("ч") else num
        run_at = datetime.now() + timedelta(minutes=minutes)
        sid = await add_schedule(body, run_at, uid)
        await admin_log(uid, "schedule", str(sid), body[:80])
        await message.answer(
            f"⏰ Рассылка #{sid} запланирована на {run_at.strftime('%d.%m %H:%M')}\n{body}"
        )
        return

    if low.startswith("админдобавить "):
        if not await is_owner(uid):
            await message.answer("Только owner")
            return
        parts = text.split()
        who = parts[1]
        role = parts[2].lower() if len(parts) > 2 else "helper"
        if role not in ROLE_RANK or role == "owner":
            role = "helper"
        tid = int(who[2:]) if who.lower().startswith("id") and who[2:].isdigit() else (
            int(who) if who.isdigit() else None
        )
        if tid is None:
            p = await resolve_player(who)
            tid = p["user_id"] if p else None
        if not tid:
            await message.answer("Укажи id")
            return
        await add_bot_admin(tid, uid, role)
        await admin_log(uid, "admin_add", str(tid), role)
        await message.answer(f"✅ Админ {tid} роль {role}")
        await notify_user(api, tid, f"⭐ Вас назначили {role} в {PROJECT}")
        return

    if low.startswith("админубрать "):
        if not await is_owner(uid):
            await message.answer("Только owner")
            return
        who = text.split(maxsplit=1)[1].strip()
        tid = int(who[2:]) if who.lower().startswith("id") and who[2:].isdigit() else (
            int(who) if who.isdigit() else None
        )
        if tid is None:
            p = await resolve_player(who)
            tid = p["user_id"] if p else None
        if not tid or tid in ENV_ADMINS:
            await message.answer("Нельзя")
            return
        await remove_bot_admin(tid)
        await admin_log(uid, "admin_remove", str(tid))
        await message.answer(f"✅ Снят: {tid}")
        await notify_user(api, tid, "Права админа сняты.")
        return

    if low.startswith("голосование "):
        parts = [p.strip() for p in text[len("голосование "):].split("|")]
        if len(parts) < 3:
            await message.answer("голосование Вопрос | вариант1 | вариант2")
            return
        vid = await create_vote(parts[0], parts[1:], uid)
        await message.answer(f"✅ Голосование #{vid}")
        for user_id, nick, status, level, balance, banned, email in await get_all_players():
            if not banned:
                await notify_user(api, user_id, f"🗳️ Новое голосование #{vid}:\n{parts[0]}")
        return

    if low.startswith("закрытьголос ") and text.split()[-1].isdigit():
        vid = int(text.split()[-1])
        vote = await get_vote(vid)
        if not vote:
            await message.answer("Нет")
            return
        await close_vote(vid)
        results = await vote_results(vid)
        options = json.loads(vote["options"])
        total = sum(results.values()) or 1
        lines = [f"Закрыто #{vid}\n{vote['question']}"]
        for i, opt in enumerate(options):
            c = results.get(i, 0)
            lines.append(f"{opt}: {c} ({c * 100 // total}%)")
        await message.answer("\n".join(lines))
        return

    if low.startswith("выдатьвсем ") and text.split()[1].lstrip("-").isdigit():
        amount = int(text.split()[1])
        ok = 0
        for user_id, nick, status, level, balance, banned, email in await get_all_players():
            if banned:
                continue
            await add_balance(user_id, amount)
            await notify_user(api, user_id, f"💰 Начисление всем: +{amount}₽")
            ok += 1
        await admin_log(uid, "give_all", "", str(amount))
        await message.answer(f"✅ Выдано {ok} игрокам")
        return

    if low.startswith("выдать "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("выдать Ник 1000")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("❌ Не найден")
            return
        await add_balance(target["user_id"], amount)
        p2 = await get_player(target["user_id"])
        await admin_log(uid, "give", target["nickname"], str(amount))
        await message.answer(f"+{amount}₽ → {target['nickname']} (баланс {p2['balance']}₽)")
        await notify_user(api, target["user_id"], f"💰 Вам начислено +{amount}₽\nБаланс: {p2['balance']}₽")
        return

    if low.startswith("забрать "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("забрать Ник 500")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("❌ Не найден")
            return
        await add_balance(target["user_id"], -amount)
        p2 = await get_player(target["user_id"])
        await admin_log(uid, "take", target["nickname"], str(amount))
        await message.answer(f"−{amount}₽ у {target['nickname']}")
        await notify_user(api, target["user_id"], f"Снято −{amount}₽\nБаланс: {p2['balance']}₽")
        return

    if low.startswith("баланс "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("баланс Ник 5000")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("❌ Не найден")
            return
        old = target["balance"]
        await set_balance(target["user_id"], amount)
        await admin_log(uid, "set_balance", target["nickname"], f"{old}->{amount}")
        await message.answer(f"{target['nickname']}: {old} → {amount}")
        await notify_user(api, target["user_id"], f"Баланс изменён: {old}₽ → {amount}₽")
        return

    if low.startswith("уровень "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("уровень Ник 5")
            return
        level = max(1, int(parts[-1]))
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("❌ Не найден")
            return
        await set_level(target["user_id"], level, 0)
        await admin_log(uid, "set_level", target["nickname"], str(level))
        await message.answer(f"Уровень {target['nickname']} = {level}")
        await notify_user(api, target["user_id"], f"Установлен уровень: {level}")
        return

    if low.startswith("бан "):
        rest = text[4:].strip()
        parts = rest.split(maxsplit=1)
        target = await resolve_player(parts[0] if parts else "")
        reason = parts[1] if len(parts) > 1 else "без причины"
        if not target:
            await message.answer("❌ Не найден")
            return
        await set_banned(target["user_id"], 1)
        await admin_log(uid, "ban", target["nickname"], reason)
        await message.answer(f"⛔ Бан {target['nickname']}: {reason}")
        await notify_user(api, target["user_id"], f"⛔ Бан в боте.\nПричина: {reason}")
        return

    if low.startswith("разбан "):
        target = await resolve_player(text[7:].strip())
        if not target:
            await message.answer("❌ Не найден")
            return
        await set_banned(target["user_id"], 0)
        await admin_log(uid, "unban", target["nickname"])
        await message.answer(f"✅ Разбан {target['nickname']}")
        await notify_user(api, target["user_id"], "✅ Разбан в боте.")
        return

    if low.startswith("пред "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("пред Ник Текст")
            return
        target = await resolve_player(parts[1])
        if not target:
            await message.answer("❌ Не найден")
            return
        await admin_log(uid, "warn", target["nickname"], parts[2][:100])
        await message.answer(f"⚠️ Пред → {target['nickname']}")
        await notify_user(api, target["user_id"], f"⚠️ Предупреждение:\n{parts[2]}")
        return

    if low.startswith("сказать "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("сказать Ник Текст")
            return
        target = await resolve_player(parts[1])
        if not target:
            await message.answer("❌ Не найден")
            return
        await notify_user(api, target["user_id"], f"💬 Сообщение администрации:\n{parts[2]}")
        await message.answer(f"✅ Отправлено → {target['nickname']}")
        return

    if low.startswith("сброскулдаун "):
        target = await resolve_player(text.split(maxsplit=1)[1])
        if not target:
            await message.answer("❌ Не найден")
            return
        await reset_cooldowns(target["user_id"])
        await message.answer(f"✅ КД сброшены: {target['nickname']}")
        await notify_user(api, target["user_id"], "Кулдауны сброшены.")
        return

    if low.startswith("удалить "):
        if not await has_role(uid, "mod"):
            await message.answer("Нужна роль mod+")
            return
        target = await resolve_player(text[8:].strip())
        if not target:
            await message.answer("❌ Не найден")
            return
        tid, tnick = target["user_id"], target["nickname"]
        await delete_player(tid)
        await admin_log(uid, "delete", tnick)
        await message.answer(f"🗑 Удалён {tnick}")
        await notify_user(api, tid, "Аккаунт удалён администрацией.")
        return

    if low.startswith("рассылка "):
        body = text[9:].strip()
        if not body:
            return
        ok = 0
        for user_id, nick, status, level, balance, banned, email in await get_all_players():
            if not banned:
                if await notify_user(api, user_id, f"[Рассылка {PROJECT_SHORT}]\n{body}"):
                    ok += 1
        await admin_log(uid, "broadcast", "", body[:80])
        await message.answer(f"✅ Рассылка: {ok}")
        return


async def background_worker():
    """Рассылки, фейк-онлайн, зарплаты, онлайн-бонус, VIP."""
    next_fake_in = random.randint(AUTO_FAKE_MIN_MINUTES, AUTO_FAKE_MAX_MINUTES) * 60
    last_fake = datetime.now()
    last_hourly = datetime.now()
    while True:
        try:
            for sid, body in await due_schedules():
                ok = 0
                for user_id, nick, status, level, balance, banned, email in await get_all_players():
                    if not banned:
                        if await notify_user(bot.api, user_id, "📢 " + body):
                            ok += 1
                await mark_schedule_done(sid)
                await notify_admins(bot.api, "⏰ Рассылка #" + str(sid) + ": " + str(ok) + " чел." + chr(10) + body)

            if AUTO_FAKE_MIN_MINUTES > 0 and AUTO_FAKE_MAX_MINUTES > 0:
                if (datetime.now() - last_fake).total_seconds() >= next_fake_in:
                    nick = random.choice(list(FAKE_PLAYERS.keys()))
                    action = random.choices(["join", "leave"], weights=[55, 45], k=1)[0]
                    await notify_admins(bot.api, format_server_notify(nick, action))
                    last_fake = datetime.now()
                    next_fake_in = random.randint(AUTO_FAKE_MIN_MINUTES, AUTO_FAKE_MAX_MINUTES) * 60

            if (datetime.now() - last_hourly).total_seconds() >= 3600:
                last_hourly = datetime.now()
                today = date.today().isoformat()
                for user_id, nick, status, level, balance, banned, email in await get_all_players():
                    if banned:
                        continue
                    p = await get_player(user_id)
                    if not p:
                        continue
                    try:
                        async with aiosqlite.connect(DB_NAME) as db:
                            async with db.execute(
                                "SELECT points FROM activity_log WHERE user_id = ? AND day = ?",
                                (user_id, today),
                            ) as cur:
                                row = await cur.fetchone()
                        if row and row[0] > 0 and p.get("last_online_bonus") != today:
                            await add_balance(user_id, ONLINE_BONUS, "online_bonus")
                            await set_player_field(user_id, "last_online_bonus", today)
                            await notify_user(bot.api, user_id, "🎁 Премия за активность: +" + str(ONLINE_BONUS) + "₽")
                    except Exception:
                        pass
                    if p.get("fraction") and p.get("last_frac_salary") != today:
                        await add_balance(user_id, FRACTION_SALARY, "frac_salary")
                        await set_player_field(user_id, "last_frac_salary", today)
                        await notify_user(
                            bot.api, user_id,
                            "💼 Зарплата фракции «" + str(p["fraction"]) + "»: +" + str(FRACTION_SALARY) + "₽",
                        )
                    if p.get("vip_until"):
                        try:
                            until = datetime.fromisoformat(p["vip_until"])
                            left = (until - datetime.now()).total_seconds()
                            if 0 < left <= 172800:
                                await notify_user(
                                    bot.api, user_id,
                                    "⭐ VIP (" + str(p.get("vip_level")) + ") скоро закончится: " + until.strftime("%d.%m %H:%M"),
                                )
                            if left <= 0:
                                await set_player_field(user_id, "vip_level", None)
                                await set_player_field(user_id, "vip_until", None)
                                await notify_user(bot.api, user_id, "⭐ Срок VIP истёк.")
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(30)



async def main():
    if not TOKEN:
        raise SystemExit("Укажи TOKEN в .env")
    os.makedirs(os.path.dirname(DB_NAME) or ".", exist_ok=True)
    await init_db()
    global _maintenance
    _maintenance = (await get_setting("maintenance", "0")) == "1"
    # notify restart
    try:
        await notify_admins(
            bot.api,
            f"🟢 {PROJECT} запущен\nСервер: {PROJECT_SHORT}\nАдмины: {ENV_ADMINS}\nВремя: {now_str()}",
        )
    except Exception:
        pass
    print(f"{PROJECT} started | admins: {ENV_ADMINS} | db: {DB_NAME}")
    asyncio.create_task(background_worker())
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
