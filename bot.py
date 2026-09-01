import asyncio
import hashlib
import json
import os
import random
import re
import secrets
from datetime import datetime, date

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

DB_NAME = "crmp_bot.db"
PROJECT = "CRMP:PRP Games"
PROJECT_SHORT = "PRP Games"

DAILY_BONUS = 500
WORK_MIN = 150
WORK_MAX = 500
WORK_COOLDOWN_SEC = 300

NICK_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

state_dispenser = BuiltinStateDispenser()
bot = Bot(token=TOKEN, state_dispenser=state_dispenser)


class RegState(BaseStateGroup):
    NICKNAME = 1
    EMAIL = 2
    PASSWORD = 3


class IdeaState(BaseStateGroup):
    TEXT = 1


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return digest, salt


def mask_password(password: str) -> str:
    if len(password) <= 2:
        return "*" * len(password)
    return password[:2] + "*" * (len(password) - 2)


async def notify_user(api, user_id: int, text: str, keyboard=None):
    try:
        kwargs = {"user_id": user_id, "message": text, "random_id": 0}
        if keyboard is not None:
            kwargs["keyboard"] = keyboard
        await api.messages.send(**kwargs)
    except Exception:
        pass



# Реакции VK (reaction_id). Часто: 1❤️ 2🔥 3😂 4😮 5😢 6👏 — актуальный набор у VK может меняться
async def react_to(message: Message, reaction_id: int = 1):
    """Ставит реакцию на сообщение пользователя."""
    try:
        cmid = getattr(message, "conversation_message_id", None)
        if not cmid:
            return
        await message.ctx_api.messages.send_reaction(
            peer_id=message.peer_id,
            cmid=int(cmid),
            reaction_id=int(reaction_id),
        )
    except Exception:
        pass


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
                banned INTEGER DEFAULT 0
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
            """
        )
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


async def nick_taken(nick: str) -> bool:
    return await get_player_by_nick(nick) is not None


async def create_player(user_id: int, nickname: str, email: str, password: str):
    pwd_hash, salt = hash_password(password)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO players (
                user_id, nickname, email, password_hash, password_salt,
                status, registered_at, balance
            ) VALUES (?, ?, ?, ?, ?, 'approved', ?, 1000)
            """,
            (user_id, nickname, email, pwd_hash, salt, datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
        await db.commit()


async def unlock_achievement(user_id: int, code: str, title: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO achievements (user_id, code, title, unlocked_at) VALUES (?, ?, ?, ?)",
                (user_id, code, title, datetime.now().strftime("%d.%m.%Y %H:%M")),
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
    return await get_player_by_nick(who)


async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
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
        await db.execute("UPDATE players SET level = ?, exp = ? WHERE user_id = ?", (level, exp, user_id))
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
        await db.execute("UPDATE players SET exp = ?, level = ? WHERE user_id = ?", (exp, level, user_id))
        await db.commit()
    return leveled


async def set_last_daily(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET last_daily = ? WHERE user_id = ?", (date.today().isoformat(), user_id))
        await db.commit()


async def set_last_work(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET last_work = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
        await db.commit()


async def reset_cooldowns(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET last_daily = NULL, last_work = NULL WHERE user_id = ?", (user_id,))
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


async def db_admin_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM bot_admins") as cur:
            return {r[0] for r in await cur.fetchall()}


async def all_admin_ids():
    return set(ENV_ADMINS) | await db_admin_ids()


async def is_admin(uid: int) -> bool:
    return uid in await all_admin_ids()


async def is_owner(uid: int) -> bool:
    return uid in ENV_ADMINS


async def add_bot_admin(user_id: int, by: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO bot_admins (user_id, added_by, added_at) VALUES (?, ?, ?)",
            (user_id, by, datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
        await db.commit()


async def remove_bot_admin(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_bot_admins():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, added_by, added_at FROM bot_admins") as cur:
            return await cur.fetchall()


async def add_idea(user_id: int, nickname: str, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "INSERT INTO ideas (user_id, nickname, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, nickname, text, datetime.now().strftime("%d.%m.%Y %H:%M")),
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
            (question, json.dumps(options, ensure_ascii=False), by, datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
        await db.commit()
        return cur.lastrowid


async def get_active_votes():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, question, options FROM votes WHERE active = 1 ORDER BY id DESC") as cur:
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
    return player


async def main_menu(user_id: int):
    player = await get_player(user_id)
    admin = await is_admin(user_id)
    kb = Keyboard(one_time=False)
    if not player:
        kb.add(Text("📝 Регистрация в игре"), color=KeyboardButtonColor.POSITIVE)
    else:
        kb.add(Text("👤 Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("🎮 Мой аккаунт"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("💼 Работа"), color=KeyboardButtonColor.POSITIVE)
        kb.add(Text("🎁 Ежедневка"), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Text("🛒 Магазин"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("🎒 Инвентарь"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("🏆 Топ"), color=KeyboardButtonColor.SECONDARY)
        kb.add(Text("🏅 Достижения"), color=KeyboardButtonColor.SECONDARY)
        kb.row()
        kb.add(Text("🗳️ Голосования"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("💡 Идея"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("ℹ️ Информация"), color=KeyboardButtonColor.SECONDARY)
    if admin:
        kb.row()
        kb.add(Text("🛠️ Админ-панель"), color=KeyboardButtonColor.NEGATIVE)
    return kb


def cancel_keyboard():
    return Keyboard(one_time=True).add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)


def start_reg_keyboard():
    return (
        Keyboard(one_time=True)
        .add(Text("📝 Начать регистрацию"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    )


def admin_keyboard():
    return (
        Keyboard(one_time=False)
        .add(Text("👥 Игроки"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("📊 Статистика"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("⛔ Баны"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("💡 Идеи"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("🗳️ Голосования админ"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("⭐ Админы бота"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("❓ Админ-помощь"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("🔙 Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


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


@bot.on.message(text=["начать", "Начать", "старт", "Старт", "/start", "меню", "Меню"])
async def start(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await react_to(message, 1)
        await message.answer(
            "🎮 Добро пожаловать на Enhanced CRMP сервер!\n"
            f"Проект: {PROJECT}\n\n"
            "🆕 НОВЫЕ ВОЗМОЖНОСТИ:\n"
            "• 🏆 Система уровней и опыта\n"
            "• 🏅 Достижения за активность\n"
            "• 🎲 Мини-игры с призами\n"
            "• 📊 Детальная статистика\n"
            "• 🗳️ Голосования сообщества\n\n"
            "Выберите действие из меню ниже:",
            keyboard=await main_menu(message.from_id),
        )
        return
    extra = "\nТы администратор бота." if await is_admin(message.from_id) else ""
    await message.answer(f"👋 С возвращением на {PROJECT}!{extra}", keyboard=await main_menu(message.from_id))


@bot.on.message(text=["ℹ️ Информация", "Информация", "инфо", "помощь", "Помощь"])
async def info(message: Message):
    await message.answer(
        f"{PROJECT}\n\n"
        "Регистрация в игре: ник → email → пароль\n"
        "После регистрации получишь логин для входа на сервер.\n\n"
        "Также: ЛК, работа, магазин, достижения, голосования, идеи."
    )


@bot.on.message(text=["📝 Регистрация в игре", "Регистрация в игре", "регистрация", "Регистрация"])
async def reg_menu(message: Message):
    if await get_player(message.from_id):
        await message.answer("✅ У тебя уже есть аккаунт.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        "Регистрация в игре\n\nНажми «Начать регистрацию», чтобы создать игровой аккаунт.",
        keyboard=start_reg_keyboard(),
    )


@bot.on.message(text=["📝 Начать регистрацию", "Начать регистрацию"])
async def reg_start(message: Message):
    if await get_player(message.from_id):
        await message.answer("✅ У тебя уже есть аккаунт.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        "📝 Регистрация нового аккаунта\n\n"
        "Шаг 1/3: Введите желаемый игровой ник\n\n"
        "Требования к нику:\n"
        "• От 3 до 24 символов\n"
        "• Только латинские буквы, цифры и подчеркивания\n"
        "• Ник должен быть уникальным\n\n"
        "Введите ваш игровой ник:",
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
        await message.answer(
            "Ник не подходит.\nНужно: 3–24 символа, только латиница, цифры и _\n"
            "Пример: Artur_Vishnevskiy\n\nВведите ник ещё раз:"
        )
        return
    if await nick_taken(nick):
        await message.answer("Этот ник уже занят. Введите другой:")
        return
    await react_to(message, 1)
    await message.answer(f"✅ Ник '{nick}' доступен!")
    if await unlock_achievement(message.from_id, "first_step", "Первый шаг"):
        await message.answer(
            "🏅 Новое достижение разблокировано!\n\n"
            "🎯 Первый шаг\n📝 Зарегистрировался в игре\n\n"
            "Поздравляем! Продолжайте в том же духе!"
        )
    await state_dispenser.set(message.peer_id, RegState.EMAIL, nickname=nick)
    await message.answer(
        "Шаг 2/3: Введите ваш email\n\n"
        "Email нужен для:\n"
        "• Восстановления доступа к аккаунту\n"
        "• Получения важных уведомлений\n\n"
        "Введите ваш email:",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=RegState.EMAIL)
async def reg_email(message: Message):
    if message.text in ("Отмена", "❌ Отмена"):
        await state_dispenser.delete(message.peer_id)
        await message.answer("❌ Регистрация отменена.", keyboard=await main_menu(message.from_id))
        return
    email = (message.text or "").strip()
    if not EMAIL_RE.match(email):
        await message.answer("Некорректный email. Пример: name@mail.ru\nВведите email ещё раз:")
        return
    await react_to(message, 1)
    await message.answer("✅ Email принят!")
    payload = dict(message.state_peer.payload or {})
    payload["email"] = email
    await state_dispenser.set(message.peer_id, RegState.PASSWORD, **payload)
    await message.answer(
        "Шаг 3/3: Создайте пароль\n\n"
        "Требования к паролю:\n"
        "• Минимум 6 символов\n"
        "• Используйте цифры и буквы для безопасности\n\n"
        "Введите ваш пароль:",
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
        await message.answer("Пароль слишком короткий (минимум 6 символов). Введите другой:")
        return
    if password.isdigit() or password.isalpha():
        await message.answer("Для безопасности используйте и буквы, и цифры.\nВведите пароль ещё раз:")
        return
    payload = dict(message.state_peer.payload or {})
    nick = payload.get("nickname")
    email = payload.get("email")
    if not nick or not email:
        await state_dispenser.delete(message.peer_id)
        await message.answer("Сессия сброшена. Начни регистрацию заново.", keyboard=await main_menu(message.from_id))
        return
    if await nick_taken(nick):
        await state_dispenser.delete(message.peer_id)
        await message.answer("Ник успели занять. Начни регистрацию заново.")
        return
    await create_player(message.from_id, nick, email, password)
    await unlock_achievement(message.from_id, "registered", "Регистрация завершена")
    await state_dispenser.delete(message.peer_id)
    masked = mask_password(password)
    await react_to(message, 1)
    await message.answer(
        "🎉 Регистрация успешно завершена!\n\n"
        "📋 Ваши данные:\n"
        f"• Игровой ник: {nick}\n"
        f"• Email: {email}\n\n"
        "🎮 Для входа в игру используйте:\n"
        f"• Логин: {nick}\n"
        f"• Пароль: {masked}\n\n"
        "⚠️ Сохраните эти данные! После этого сообщения полный пароль больше не будет показан.\n\n"
        f"Удачной игры на нашем CRMP сервере {PROJECT_SHORT}! 🚗",
        keyboard=await main_menu(message.from_id),
    )
    for aid in await all_admin_ids():
        await notify_user(
            message.ctx_api, aid,
            f"Новый аккаунт на {PROJECT}\nVK: [id{message.from_id}|user]\nНик: {nick}\nEmail: {email}",
        )


@bot.on.message(text=["👤 Личный кабинет", "Личный кабинет", "лк", "ЛК", "кабинет"])
async def cabinet(message: Message):
    player = await require_player(message)
    if not player:
        return
    text = (
        f"👤 Личный кабинет {PROJECT_SHORT}\n\n"
        f"Ник: {player['nickname']}\n"
        f"Email: {player.get('email') or '—'}\n"
        f"Уровень: {player['level']} (опыт {player['exp']}/{player['level'] * 1000})\n"
        f"Баланс: {player['balance']}₽\n"
        f"Регистрация: {player['registered_at']}"
    )
    await message.answer(text, keyboard=await main_menu(message.from_id))


@bot.on.message(text=["🎮 Мой аккаунт", "Мой аккаунт", "аккаунт", "данные"])
async def my_account(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(
        "🎮 Данные для входа в игру\n\n"
        f"Логин: {player['nickname']}\n"
        f"Email: {player.get('email') or '—'}\n\n"
        "Пароль хранится в зашифрованном виде и повторно не показывается.\n"
        "Если забыли пароль — напишите администрации."
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
        await message.answer("⏰ Ежедневный бонус уже получен сегодня.")
        return
    await add_balance(message.from_id, DAILY_BONUS)
    await set_last_daily(message.from_id)
    leveled = await add_exp(message.from_id, 50)
    await unlock_achievement(message.from_id, "first_daily", "Первая ежедневка")
    await react_to(message, 2)
    await message.answer(f"+{DAILY_BONUS}₽ (+50 опыта)" + ("\nУровень повышен!" if leveled else ""))


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
                await message.answer(f"Подожди ещё {int(left // 60)}м {int(left % 60)}с")
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
    await message.answer(f"Смена: {random.choice(jobs)}. +{pay}₽ (+30 опыта)" + ("\nУровень повышен!" if leveled else ""))


@bot.on.message(text=["🏆 Топ", "Топ", "топ", "рейтинг"])
async def top(message: Message):
    rows = await get_top(10)
    if not rows:
        await message.answer("Топ пуст.")
        return
    lines = [f"Топ {PROJECT_SHORT}:\n"] + [f"{i}. {n} — {b}₽ (ур. {lv})" for i, (n, lv, b) in enumerate(rows, 1)]
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
        await message.answer(f"Нужно {price}₽")
        return
    await add_balance(message.from_id, -price)
    await inv_add(message.from_id, item, 1)
    await react_to(message, 1)
    await message.answer(f"✅ Куплено: {item}")


@bot.on.message(text=["💸 Перевод", "Перевод", "перевод"])
async def transfer_help(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(f"💸 Формат: перевод Ник 500\nБаланс: {player['balance']}₽")


@bot.on.message(text=["💡 Идея", "Идея", "идея", "предложить"])
async def idea_start(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer("💡 Опиши идею для сервера одним сообщением:", keyboard=cancel_keyboard())
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
            f"Идея #{idea_id} от {player['nickname'] if player else message.from_id}:\n{text}",
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


@bot.on.message(text=["🛠️ Админ-панель", "Админ-панель", "админка", "Админка"])
async def admin_panel(message: Message):
    if not await is_admin(message.from_id):
        await message.answer("⛔ Нет доступа.")
        return
    await message.answer(f"🛠️ Админ-панель {PROJECT_SHORT}", keyboard=admin_keyboard())


@bot.on.message(text=["❓ Админ-помощь", "Админ-помощь", "админпомощь"])
async def admin_help(message: Message):
    if not await is_admin(message.from_id):
        return
    await message.answer(
        "Команды:\n"
        "info Ник | выдать Ник 1000 | забрать Ник 500 | баланс Ник 5000\n"
        "уровень Ник 5 | бан Ник [причина] | разбан Ник\n"
        "пред Ник текст | сказать Ник текст | удалить Ник\n"
        "выдатьвсем 100 | рассылка Текст\n"
        "админдобавить id / админубрать id\n"
        "голосование Вопрос | да | нет | закрытьголос ID"
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
        lines.append(f"[id{user_id}|{nick}] ур.{level} {balance}₽ {email or ''}{' BAN' if banned else ''}")
    await message.answer("\n".join(lines))


@bot.on.message(text=["📊 Статистика", "Статистика"])
async def stats_cmd(message: Message):
    if not await is_admin(message.from_id):
        return
    s = await get_stats()
    await message.answer(
        f"Статистика {PROJECT_SHORT}\n\nАккаунтов: {s['total']}\nБаны: {s['banned']}\n"
        f"Денег: {s['money']}₽\nИдей: {s['ideas']}\nГолосований: {s['votes']}"
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
            f"Идея #{iid} от [id{uid}|{nick}] ({created})\n{text}",
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


@bot.on.message(text=["⭐ Админы бота", "Админы бота", "админы бота"])
async def admins_list(message: Message):
    if not await is_admin(message.from_id):
        return
    lines = ["Главные (.env):\n"] + [f"• [id{a}|id{a}]" for a in ENV_ADMINS]
    extra = await list_bot_admins()
    lines.append("\nДобавленные:")
    lines.append("• нет" if not extra else "")
    for uid, by, at in extra:
        lines.append(f"• [id{uid}|id{uid}] от {by} ({at})")
    await message.answer("\n".join([x for x in lines if x is not None]))


@bot.on.message(text=["🔙 Назад", "Назад"])
async def back(message: Message):
    await message.answer("📋 Меню:", keyboard=await main_menu(message.from_id))


@bot.on.message(PayloadRule({"cmd": "idea_ok"}))
async def idea_ok(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "accepted")
    await message.answer(f"✅ Идея #{iid} принята")
    await notify_user(message.ctx_api, idea["user_id"], f"Идея #{iid} принята!")


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
    await notify_user(message.ctx_api, idea["user_id"], f"Идея #{iid} отклонена.")


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
    await notify_user(message.ctx_api, idea["user_id"], f"Идея #{iid} реализована!")


@bot.on.message()
async def text_commands(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    low = text.lower()
    uid = message.from_id
    api = message.ctx_api

    if low.startswith("перевод "):
        player = await require_player(message)
        if not player:
            return
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit() or int(parts[-1]) <= 0:
            await message.answer("перевод Ник 500")
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

    if not await is_admin(uid):
        return

    if low.startswith("info ") or low.startswith("инфо "):
        p = await resolve_player(text.split(maxsplit=1)[1])
        if not p:
            await message.answer("❌ Не найден")
            return
        await message.answer(
            f"Ник: {p['nickname']}\nEmail: {p.get('email')}\nУр: {p['level']} | Баланс: {p['balance']}₽\n"
            f"Бан: {'да' if p.get('banned') else 'нет'}\nhttps://vk.com/id{p['user_id']}"
        )
        return

    if low.startswith("админдобавить "):
        if not await is_owner(uid):
            await message.answer("Только главный админ")
            return
        who = text.split(maxsplit=1)[1].strip()
        tid = int(who[2:]) if who.lower().startswith("id") and who[2:].isdigit() else (int(who) if who.isdigit() else None)
        if tid is None:
            p = await resolve_player(who)
            tid = p["user_id"] if p else None
        if not tid:
            await message.answer("Укажи id")
            return
        await add_bot_admin(tid, uid)
        await message.answer(f"Админ добавлен: {tid}")
        await notify_user(api, tid, f"Вас назначили админом бота {PROJECT_SHORT}")
        return

    if low.startswith("админубрать "):
        if not await is_owner(uid):
            await message.answer("Только главный админ")
            return
        who = text.split(maxsplit=1)[1].strip()
        tid = int(who[2:]) if who.lower().startswith("id") and who[2:].isdigit() else (int(who) if who.isdigit() else None)
        if tid is None:
            p = await resolve_player(who)
            tid = p["user_id"] if p else None
        if not tid or tid in ENV_ADMINS:
            await message.answer("Нельзя")
            return
        await remove_bot_admin(tid)
        await message.answer(f"Снят: {tid}")
        await notify_user(api, tid, "Права админа бота сняты.")
        return

    if low.startswith("голосование "):
        parts = [p.strip() for p in text[len("голосование "):].split("|")]
        if len(parts) < 3:
            await message.answer("голосование Вопрос | вариант1 | вариант2")
            return
        vid = await create_vote(parts[0], parts[1:], uid)
        await message.answer(f"Голосование #{vid} создано")
        for user_id, nick, status, level, balance, banned, email in await get_all_players():
            if not banned:
                await notify_user(api, user_id, f"Новое голосование #{vid}:\n{parts[0]}\nКнопка «Голосования»")
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
            await notify_user(api, user_id, f"Начисление всем: +{amount}₽")
            ok += 1
        await message.answer(f"Выдано {ok} игрокам")
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
        await message.answer(f"+{amount}₽ → {target['nickname']} (баланс {p2['balance']}₽)")
        await notify_user(api, target["user_id"], f"Вам начислено +{amount}₽\nБаланс: {p2['balance']}₽")
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
        await message.answer(f"Бан {target['nickname']}: {reason}")
        await notify_user(api, target["user_id"], f"Бан в боте.\nПричина: {reason}")
        return

    if low.startswith("разбан "):
        target = await resolve_player(text[7:].strip())
        if not target:
            await message.answer("❌ Не найден")
            return
        await set_banned(target["user_id"], 0)
        await message.answer(f"Разбан {target['nickname']}")
        await notify_user(api, target["user_id"], "Разбан в боте.")
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
        await message.answer(f"Пред → {target['nickname']}")
        await notify_user(api, target["user_id"], f"Предупреждение:\n{parts[2]}")
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
        await notify_user(api, target["user_id"], f"Сообщение администрации:\n{parts[2]}")
        await message.answer(f"Отправлено → {target['nickname']}")
        return

    if low.startswith("сброскулдаун "):
        target = await resolve_player(text.split(maxsplit=1)[1])
        if not target:
            await message.answer("❌ Не найден")
            return
        await reset_cooldowns(target["user_id"])
        await message.answer(f"КД сброшены: {target['nickname']}")
        await notify_user(api, target["user_id"], "Кулдауны сброшены.")
        return

    if low.startswith("удалить "):
        target = await resolve_player(text[8:].strip())
        if not target:
            await message.answer("❌ Не найден")
            return
        tid, tnick = target["user_id"], target["nickname"]
        await delete_player(tid)
        await message.answer(f"Удалён {tnick}")
        await notify_user(api, tid, "Аккаунт удалён администрацией.")
        return

    if low.startswith("рассылка "):
        body = text[9:].strip()
        if not body:
            return
        ok = 0
        for user_id, nick, status, level, balance, banned, email in await get_all_players():
            if not banned:
                await notify_user(api, user_id, f"[Рассылка {PROJECT_SHORT}]\n{body}")
                ok += 1
        await message.answer(f"Рассылка: {ok}")
        return


async def main():
    if not TOKEN:
        raise SystemExit("Укажи TOKEN в .env")
    await init_db()
    print(f"{PROJECT} bot started | admins: {ENV_ADMINS}")
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
