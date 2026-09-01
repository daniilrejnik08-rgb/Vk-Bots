import asyncio
import json
import os
import random
from datetime import datetime, date

import aiosqlite
from dotenv import load_dotenv
from vkbottle import BaseStateGroup, BuiltinStateDispenser, Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import PayloadRule

load_dotenv()

TOKEN = os.getenv("TOKEN", "").strip()
# Главные админы из .env (их нельзя снять через бота)
ENV_ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "854071888").split(",") if x.strip().isdigit()]
if not ENV_ADMINS:
    ENV_ADMINS = [854071888]

DB_NAME = "crmp_bot.db"
PROJECT_NAME = "CRMP"

DAILY_BONUS = 500
WORK_MIN = 150
WORK_MAX = 500
WORK_COOLDOWN_SEC = 300

state_dispenser = BuiltinStateDispenser()
bot = Bot(token=TOKEN, state_dispenser=state_dispenser)


class RegState(BaseStateGroup):
    NICKNAME = 1
    AGE = 2
    GENDER = 3
    CITY = 4
    ABOUT = 5


class IdeaState(BaseStateGroup):
    TEXT = 1


class VoteCreateState(BaseStateGroup):
    QUESTION = 1
    OPTIONS = 2


# -------------------- DB --------------------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                nickname TEXT UNIQUE,
                age INTEGER,
                gender TEXT,
                city TEXT,
                about TEXT,
                status TEXT DEFAULT 'pending',
                registered_at TEXT,
                balance INTEGER DEFAULT 0,
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


async def resolve_player(who: str):
    who = (who or "").strip()
    if not who:
        return None
    if who.isdigit():
        return await get_player(int(who))
    if who.lower().startswith("id") and who[2:].isdigit():
        return await get_player(int(who[2:]))
    return await get_player_by_nick(who)


async def create_player(user_id: int, data: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO players (user_id, nickname, age, gender, city, about, registered_at, balance)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1000)
            """,
            (
                user_id,
                data["nickname"],
                data["age"],
                data["gender"],
                data["city"],
                data["about"],
                datetime.now().strftime("%d.%m.%Y %H:%M"),
            ),
        )
        await db.commit()


async def update_status(user_id: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()


async def set_banned(user_id: int, banned: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET banned = ? WHERE user_id = ?", (banned, user_id))
        await db.commit()


async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id)
        )
        await db.commit()


async def set_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET balance = ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def set_level(user_id: int, level: int, exp: int = 0):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET level = ?, exp = ? WHERE user_id = ?", (level, exp, user_id)
        )
        await db.commit()


async def set_nickname(user_id: int, nick: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE players SET nickname = ? WHERE user_id = ?", (nick, user_id))
        await db.commit()


async def delete_player(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await db.commit()


async def reset_cooldowns(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_daily = NULL, last_work = NULL WHERE user_id = ?",
            (user_id,),
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
            "UPDATE players SET exp = ?, level = ? WHERE user_id = ?",
            (exp, level, user_id),
        )
        await db.commit()
    return leveled


async def set_last_daily(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_daily = ? WHERE user_id = ?",
            (date.today().isoformat(), user_id),
        )
        await db.commit()


async def set_last_work(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_work = ? WHERE user_id = ?",
            (datetime.now().isoformat(), user_id),
        )
        await db.commit()


async def get_pending_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, age, gender, city FROM players WHERE status = 'pending'"
        ) as cur:
            return await cur.fetchall()


async def get_all_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, status, level, balance, banned FROM players ORDER BY balance DESC"
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
            "approved": await cnt("SELECT COUNT(*) FROM players WHERE status = 'approved'"),
            "pending": await cnt("SELECT COUNT(*) FROM players WHERE status = 'pending'"),
            "rejected": await cnt("SELECT COUNT(*) FROM players WHERE status = 'rejected'"),
            "banned": await cnt("SELECT COUNT(*) FROM players WHERE banned = 1"),
            "money": await cnt("SELECT COALESCE(SUM(balance), 0) FROM players"),
            "ideas": await cnt("SELECT COUNT(*) FROM ideas WHERE status = 'new'"),
            "votes": await cnt("SELECT COUNT(*) FROM votes WHERE active = 1"),
        }


async def get_top(limit: int = 10):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT nickname, level, balance FROM players
            WHERE status = 'approved' AND banned = 0
            ORDER BY balance DESC LIMIT ?
            """,
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
            "SELECT item, amount FROM inventory WHERE user_id = ? AND amount > 0",
            (user_id,),
        ) as cur:
            return await cur.fetchall()


async def inv_has(user_id: int, item: str, amount: int = 1) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT amount FROM inventory WHERE user_id = ? AND item = ?",
            (user_id, item),
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0] >= amount)


async def inv_remove(user_id: int, item: str, amount: int = 1) -> bool:
    if not await inv_has(user_id, item, amount):
        return False
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE inventory SET amount = amount - ? WHERE user_id = ? AND item = ?",
            (amount, user_id, item),
        )
        await db.commit()
    return True


# --- admins in DB ---
async def db_admin_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM bot_admins") as cur:
            rows = await cur.fetchall()
            return {r[0] for r in rows}


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
        async with db.execute(
            "SELECT user_id, added_by, added_at FROM bot_admins ORDER BY added_at"
        ) as cur:
            return await cur.fetchall()


# --- ideas ---
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


# --- votes ---
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
            "SELECT 1 FROM vote_answers WHERE vote_id = ? AND user_id = ?",
            (vote_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None


SHOP = {
    "Аптечка": (300, "HP в RP"),
    "Бронежилет": (800, "Защита"),
    "Телефон": (2500, "Связь"),
    "Маска": (1200, "Для дел"),
    "VIP-карта": (15000, "Статус VIP на проекте"),
}


async def notify_user(api, user_id: int, text: str):
    try:
        await api.messages.send(user_id=user_id, message=text, random_id=0)
    except Exception:
        pass


async def require_player(message: Message, need_approved: bool = True):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала регистрация.", keyboard=await main_menu(message.from_id))
        return None
    if player.get("banned") and not await is_admin(message.from_id):
        await message.answer("Ты в бане бота.")
        return None
    if need_approved and player["status"] != "approved" and not await is_admin(message.from_id):
        st = {"pending": "на рассмотрении", "rejected": "отклонена"}.get(player["status"], player["status"])
        await message.answer(f"Заявка не одобрена ({st}).")
        return None
    return player


async def main_menu(user_id: int):
    player = await get_player(user_id)
    admin = await is_admin(user_id)
    approved = bool(player and player["status"] == "approved")
    kb = Keyboard(one_time=False)
    if not player:
        kb.add(Text("Регистрация"), color=KeyboardButtonColor.POSITIVE)
    else:
        kb.add(Text("Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("Профиль"), color=KeyboardButtonColor.SECONDARY)
        if approved or admin:
            kb.row()
            kb.add(Text("Работа"), color=KeyboardButtonColor.POSITIVE)
            kb.add(Text("Ежедневка"), color=KeyboardButtonColor.POSITIVE)
            kb.row()
            kb.add(Text("Магазин"), color=KeyboardButtonColor.PRIMARY)
            kb.add(Text("Инвентарь"), color=KeyboardButtonColor.SECONDARY)
            kb.row()
            kb.add(Text("Топ"), color=KeyboardButtonColor.SECONDARY)
            kb.add(Text("Перевод"), color=KeyboardButtonColor.SECONDARY)
            kb.row()
            kb.add(Text("Голосования"), color=KeyboardButtonColor.PRIMARY)
            kb.add(Text("Идея"), color=KeyboardButtonColor.PRIMARY)
    kb.row()
    kb.add(Text("Информация"), color=KeyboardButtonColor.SECONDARY)
    if admin:
        kb.row()
        kb.add(Text("Админ-панель"), color=KeyboardButtonColor.NEGATIVE)
    return kb


def gender_keyboard():
    return (
        Keyboard(one_time=True)
        .add(Text("Мужской"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Женский"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("Отмена"), color=KeyboardButtonColor.NEGATIVE)
    )


def cancel_keyboard():
    return Keyboard(one_time=True).add(Text("Отмена"), color=KeyboardButtonColor.NEGATIVE)


def admin_keyboard():
    return (
        Keyboard(one_time=False)
        .add(Text("Заявки"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Игроки"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("Статистика"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Баны"), color=KeyboardButtonColor.NEGATIVE)
        .row()
        .add(Text("Идеи"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Голосования админ"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("Админы бота"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("Админ-помощь"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def approve_keyboard(user_id: int):
    return (
        Keyboard(inline=True)
        .add(Text("Одобрить", payload={"cmd": "approve", "user_id": user_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("Отклонить", payload={"cmd": "reject", "user_id": user_id}), color=KeyboardButtonColor.NEGATIVE)
        .row()
        .add(Text("Бан", payload={"cmd": "ban", "user_id": user_id}), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("Карточка", payload={"cmd": "card", "user_id": user_id}), color=KeyboardButtonColor.PRIMARY)
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
        .add(Text("Принять", payload={"cmd": "idea_ok", "id": idea_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("Отклонить", payload={"cmd": "idea_no", "id": idea_id}), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("Сделано", payload={"cmd": "idea_done", "id": idea_id}), color=KeyboardButtonColor.PRIMARY)
    )


def format_player_card(p: dict) -> str:
    status_map = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}
    return (
        f"Карточка игрока {PROJECT_NAME}\n\n"
        f"ID: {p['user_id']}\n"
        f"Ник: {p['nickname']}\n"
        f"Возраст: {p['age']} | Пол: {p['gender']}\n"
        f"Город: {p['city']}\n"
        f"О себе: {p['about']}\n"
        f"Статус: {status_map.get(p['status'], p['status'])}\n"
        f"Уровень: {p['level']} (опыт {p['exp']})\n"
        f"Баланс: {p['balance']}₽\n"
        f"Бан: {'да' if p.get('banned') else 'нет'}\n"
        f"Регистрация: {p['registered_at']}\n"
        f"https://vk.com/id{p['user_id']}"
    )


# -------------------- Handlers --------------------
@bot.on.message(text=["начать", "Начать", "старт", "Старт", "/start", "меню", "Меню"])
async def start(message: Message):
    admin = await is_admin(message.from_id)
    extra = f"\nТы администратор бота {PROJECT_NAME}." if admin else ""
    await message.answer(
        f"Бот проекта {PROJECT_NAME}\n"
        f"Регистрация на сервер, ЛК, экономика, голосования, идеи.{extra}",
        keyboard=await main_menu(message.from_id),
    )


@bot.on.message(text=["Информация", "инфо", "помощь", "Помощь"])
async def info(message: Message):
    await message.answer(
        f"Бот {PROJECT_NAME} (КРМП)\n\n"
        "• Регистрация → одобрение админом\n"
        "• Работа, ежедневка, магазин, перевод, топ\n"
        "• Голосования — участие в опросах проекта\n"
        "• Идея — предложить улучшение серверу\n"
        "• перевод Ник 500\n\n"
        "Админам: Админ-панель / Админ-помощь"
    )


@bot.on.message(text=["Регистрация", "регистрация"])
async def start_reg(message: Message):
    if await get_player(message.from_id):
        await message.answer("Уже зарегистрирован.", keyboard=await main_menu(message.from_id))
        return
    await message.answer(
        f"Регистрация на {PROJECT_NAME}\nВведи игровой ник (как в игре):",
        keyboard=cancel_keyboard(),
    )
    await state_dispenser.set(message.peer_id, RegState.NICKNAME)


@bot.on.message(state=RegState.NICKNAME)
async def set_nickname(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    nick = (message.text or "").strip()
    if len(nick) < 2 or len(nick) > 24:
        await message.answer("Ник 2–24 символа:")
        return
    if await get_player_by_nick(nick):
        await message.answer("Ник занят:")
        return
    await state_dispenser.set(message.peer_id, RegState.AGE, nickname=nick)
    await message.answer("Возраст (числом):")


@bot.on.message(state=RegState.AGE)
async def set_age(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    if not (message.text or "").isdigit() or not (10 <= int(message.text) <= 60):
        await message.answer("Возраст 10–60:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["age"] = int(message.text)
    await state_dispenser.set(message.peer_id, RegState.GENDER, **payload)
    await message.answer("Пол:", keyboard=gender_keyboard())


@bot.on.message(state=RegState.GENDER)
async def set_gender(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    if message.text not in ("Мужской", "Женский"):
        await message.answer("Кнопкой:", keyboard=gender_keyboard())
        return
    payload = dict(message.state_peer.payload or {})
    payload["gender"] = message.text
    await state_dispenser.set(message.peer_id, RegState.CITY, **payload)
    await message.answer("Город (IRL):", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.CITY)
async def set_city(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("Укажи город:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["city"] = city
    await state_dispenser.set(message.peer_id, RegState.ABOUT, **payload)
    await message.answer("Почему хочешь играть на проекте (или «-»):", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.ABOUT)
async def finish_reg(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    payload = dict(message.state_peer.payload or {})
    payload["about"] = "-" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await create_player(message.from_id, payload)
    await state_dispenser.delete(message.peer_id)

    if await is_admin(message.from_id):
        await update_status(message.from_id, "approved")
        await message.answer(
            f"Админ — заявка одобрена.\nНик: {payload.get('nickname')}\nСтарт: 1000₽",
            keyboard=await main_menu(message.from_id),
        )
        return

    await message.answer(
        f"Заявка на {PROJECT_NAME} отправлена.\nНик: {payload.get('nickname')}\nЖди одобрения.",
        keyboard=await main_menu(message.from_id),
    )
    text = (
        f"Новая заявка {PROJECT_NAME}\n\n"
        f"ID: {message.from_id}\n"
        f"Ник: {payload.get('nickname')}\n"
        f"Возраст: {payload.get('age')}\n"
        f"Пол: {payload.get('gender')}\n"
        f"Город: {payload.get('city')}\n"
        f"О себе: {payload.get('about')}"
    )
    for aid in await all_admin_ids():
        try:
            await message.ctx_api.messages.send(
                user_id=aid, message=text, random_id=0, keyboard=approve_keyboard(message.from_id)
            )
        except Exception:
            pass


@bot.on.message(text=["Личный кабинет", "лк", "ЛК", "кабинет"])
async def personal_cabinet(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала регистрация.", keyboard=await main_menu(message.from_id))
        return
    st = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}.get(
        player["status"], player["status"]
    )
    text = (
        f"ЛК {PROJECT_NAME}\n\n"
        f"Ник: {player['nickname']}\n"
        f"Уровень: {player['level']} ({player['exp']}/{player['level'] * 1000} опыта)\n"
        f"Баланс: {player['balance']}₽\n"
        f"Статус: {st}\n"
        f"Регистрация: {player['registered_at']}"
    )
    if player.get("banned"):
        text += "\n⚠ Бан"
    await message.answer(text, keyboard=await main_menu(message.from_id))


@bot.on.message(text=["Профиль", "профиль", "Мой профиль"])
async def profile(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала регистрация.")
        return
    await message.answer(format_player_card(player))


@bot.on.message(text=["Ежедневка", "ежедневка", "бонус", "daily"])
async def daily(message: Message):
    player = await require_player(message)
    if not player:
        return
    if player.get("last_daily") == date.today().isoformat():
        await message.answer("Уже получал сегодня.")
        return
    await add_balance(message.from_id, DAILY_BONUS)
    await set_last_daily(message.from_id)
    leveled = await add_exp(message.from_id, 50)
    await message.answer(f"+{DAILY_BONUS}₽ (+50 опыта)" + ("\nУровень!" if leveled else ""))


@bot.on.message(text=["Работа", "работа", "работать", "work"])
async def work(message: Message):
    player = await require_player(message)
    if not player:
        return
    if player.get("last_work"):
        try:
            last = datetime.fromisoformat(player["last_work"])
            left = WORK_COOLDOWN_SEC - (datetime.now() - last).total_seconds()
            if left > 0:
                await message.answer(f"КД: {int(left // 60)}м {int(left % 60)}с")
                return
        except Exception:
            pass
    pay = random.randint(WORK_MIN, WORK_MAX)
    jobs = ["грузоперевозки", "такси", "стройка", "завод", "доставка", "автосервис"]
    await add_balance(message.from_id, pay)
    await set_last_work(message.from_id)
    leveled = await add_exp(message.from_id, 30)
    await message.answer(
        f"Смена: {random.choice(jobs)}. Заработок {pay}₽ (+30 опыта)"
        + ("\nУровень!" if leveled else "")
    )


@bot.on.message(text=["Топ", "топ", "рейтинг"])
async def top(message: Message):
    rows = await get_top(10)
    if not rows:
        await message.answer("Пусто.")
        return
    lines = [f"Топ {PROJECT_NAME}:\n"]
    for i, (nick, level, balance) in enumerate(rows, 1):
        lines.append(f"{i}. {nick} — {balance}₽ (ур. {level})")
    await message.answer("\n".join(lines))


@bot.on.message(text=["Инвентарь", "инвентарь", "инв"])
async def inventory(message: Message):
    player = await require_player(message)
    if not player:
        return
    items = await inv_get(message.from_id)
    if not items:
        await message.answer("Пусто. Открой «Магазин».")
        return
    await message.answer("Инвентарь:\n" + "\n".join(f"• {i} × {a}" for i, a in items))


@bot.on.message(text=["Магазин", "магазин", "шоп"])
async def shop(message: Message):
    player = await require_player(message)
    if not player:
        return
    lines = [f"Магазин (баланс {player['balance']}₽)\n"]
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
    await message.answer(f"Куплено: {item}")


@bot.on.message(text=["Перевод", "перевод"])
async def transfer_help(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(f"Формат: перевод Ник 500\nБаланс: {player['balance']}₽")


# ----- Ideas -----
@bot.on.message(text=["Идея", "идея", "предложить"])
async def idea_start(message: Message):
    player = await require_player(message, need_approved=False)
    if not player:
        return
    await message.answer(
        f"Опиши идею для {PROJECT_NAME} одним сообщением:",
        keyboard=cancel_keyboard(),
    )
    await state_dispenser.set(message.peer_id, IdeaState.TEXT)


@bot.on.message(state=IdeaState.TEXT)
async def idea_save(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=await main_menu(message.from_id))
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Слишком коротко, напиши подробнее:")
        return
    player = await get_player(message.from_id)
    idea_id = await add_idea(message.from_id, player["nickname"] if player else "?", text)
    await state_dispenser.delete(message.peer_id)
    await message.answer(f"Идея #{idea_id} отправлена администрации.", keyboard=await main_menu(message.from_id))
    note = f"Новая идея #{idea_id} от {player['nickname'] if player else message.from_id}:\n{text}"
    for aid in await all_admin_ids():
        try:
            await message.ctx_api.messages.send(
                user_id=aid, message=note, random_id=0, keyboard=idea_keyboard(idea_id)
            )
        except Exception:
            pass


# ----- Votes player -----
@bot.on.message(text=["Голосования", "голосования", "опрос", "голоса"])
async def votes_list(message: Message):
    player = await require_player(message)
    if not player:
        return
    votes = await get_active_votes()
    if not votes:
        await message.answer("Активных голосований нет.")
        return
    for vid, question, options_json in votes:
        options = json.loads(options_json)
        kb = Keyboard(inline=True)
        for i, opt in enumerate(options):
            kb.add(Text(opt, payload={"cmd": "vote", "vid": vid, "opt": i}))
            kb.row()
        already = " (ты уже голосовал)" if await has_voted(vid, message.from_id) else ""
        await message.answer(f"Голосование #{vid}{already}\n{question}", keyboard=kb)


@bot.on.message(PayloadRule({"cmd": "vote"}))
async def vote_cast(message: Message):
    player = await require_player(message)
    if not player:
        return
    data = message.get_payload_json() or {}
    vid = int(data["vid"])
    opt = int(data["opt"])
    vote = await get_vote(vid)
    if not vote or not vote["active"]:
        await message.answer("Голосование закрыто.")
        return
    options = json.loads(vote["options"])
    if opt < 0 or opt >= len(options):
        return
    await cast_vote(vid, message.from_id, opt)
    await message.answer(f"Голос учтён: «{options[opt]}»")


# ----- Admin UI -----
@bot.on.message(text=["Админ-панель", "админка", "Админка"])
async def admin_panel(message: Message):
    if not await is_admin(message.from_id):
        await message.answer("Нет доступа.")
        return
    await message.answer(f"Админ-панель {PROJECT_NAME}", keyboard=admin_keyboard())


@bot.on.message(text=["Админ-помощь", "админ помощь", "админкоманды"])
async def admin_help(message: Message):
    if not await is_admin(message.from_id):
        return
    await message.answer(
        f"Админ {PROJECT_NAME}\n\n"
        "Игроки: info / выдать / забрать / баланс / уровень / опыт / ник / статус / одобрить / бан / разбан / пред / сказать / удалить / сброскулдаун / предмет\n"
        "Массово: выдатьвсем 100 | одобритьвсех | рассылка Текст\n\n"
        "Админы бота (только главные из .env):\n"
        "• админдобавить id123\n"
        "• админубрать id123\n"
        "• Админы бота — список\n\n"
        "Идеи: кнопка «Идеи» или идеи\n"
        "Голосования:\n"
        "• голосование Вопрос | вариант1 | вариант2 | вариант3\n"
        "• закрытьголос ID\n"
        "• результаты ID\n"
        "• Голосования админ\n\n"
        "Игрок всегда получает уведомление."
    )


@bot.on.message(text="Заявки")
async def show_pending(message: Message):
    if not await is_admin(message.from_id):
        return
    pending = await get_pending_players()
    if not pending:
        await message.answer("Нет заявок.")
        return
    for user_id, nick, age, gender, city in pending:
        await message.answer(
            f"Заявка [id{user_id}|{nick}]\n{age}, {gender}, {city}",
            keyboard=approve_keyboard(user_id),
        )


@bot.on.message(text="Игроки")
async def show_players(message: Message):
    if not await is_admin(message.from_id):
        return
    players = await get_all_players()
    if not players:
        await message.answer("Пусто.")
        return
    lines = ["Игроки:\n"]
    for user_id, nick, status, level, balance, banned in players[:50]:
        lines.append(f"[id{user_id}|{nick}] {status} ур.{level} {balance}₽{' BAN' if banned else ''}")
    await message.answer("\n".join(lines))


@bot.on.message(text="Статистика")
async def stats_cmd(message: Message):
    if not await is_admin(message.from_id):
        return
    s = await get_stats()
    await message.answer(
        f"Статистика {PROJECT_NAME}\n\n"
        f"Игроков: {s['total']}\nОдобрено: {s['approved']}\nОжидают: {s['pending']}\n"
        f"Отклонено: {s['rejected']}\nБаны: {s['banned']}\n"
        f"Денег: {s['money']}₽\nНовых идей: {s['ideas']}\nАктивных голосований: {s['votes']}"
    )


@bot.on.message(text="Баны")
async def bans_list(message: Message):
    if not await is_admin(message.from_id):
        return
    rows = await get_banned_players()
    if not rows:
        await message.answer("Бан-лист пуст.")
        return
    await message.answer("Баны:\n" + "\n".join(f"[id{u}|{n}]" for u, n in rows))


@bot.on.message(text=["Идеи", "идеи"])
async def ideas_admin(message: Message):
    if not await is_admin(message.from_id):
        return
    rows = await list_ideas("new")
    if not rows:
        await message.answer("Новых идей нет.")
        return
    for iid, uid, nick, text, status, created in rows:
        await message.answer(
            f"Идея #{iid} от [id{uid}|{nick}] ({created})\n{text}",
            keyboard=idea_keyboard(iid),
        )


@bot.on.message(text=["Голосования админ", "голосования админ"])
async def votes_admin(message: Message):
    if not await is_admin(message.from_id):
        return
    votes = await get_active_votes()
    if not votes:
        await message.answer(
            "Активных нет.\nСоздать:\nголосование Вопрос | да | нет | воздержусь"
        )
        return
    for vid, question, options_json in votes:
        options = json.loads(options_json)
        results = await vote_results(vid)
        total = sum(results.values()) or 1
        lines = [f"#{vid} {question}"]
        for i, opt in enumerate(options):
            c = results.get(i, 0)
            lines.append(f"  {opt}: {c} ({c * 100 // total}%)")
        lines.append(f"Закрыть: закрытьголос {vid}")
        await message.answer("\n".join(lines))


@bot.on.message(text=["Админы бота", "админы бота"])
async def admins_list(message: Message):
    if not await is_admin(message.from_id):
        return
    lines = ["Главные (из .env, нельзя снять ботом):\n"]
    for a in ENV_ADMINS:
        lines.append(f"• [id{a}|id{a}] OWNER")
    extra = await list_bot_admins()
    lines.append("\nДобавленные через бота:")
    if not extra:
        lines.append("• нет")
    else:
        for uid, by, at in extra:
            lines.append(f"• [id{uid}|id{uid}] добавил {by} ({at})")
    lines.append("\nадминдобавить id123\nадминубрать id123")
    await message.answer("\n".join(lines))


@bot.on.message(text="Назад")
async def back(message: Message):
    await message.answer("Меню:", keyboard=await main_menu(message.from_id))


@bot.on.message(PayloadRule({"cmd": "approve"}))
async def approve(message: Message):
    if not await is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await update_status(user_id, "approved")
    await message.answer(f"Одобрен {user_id}")
    await notify_user(message.ctx_api, user_id, f"Заявка на {PROJECT_NAME} одобрена!")


@bot.on.message(PayloadRule({"cmd": "reject"}))
async def reject(message: Message):
    if not await is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await update_status(user_id, "rejected")
    await message.answer(f"Отклонён {user_id}")
    await notify_user(message.ctx_api, user_id, f"Заявка на {PROJECT_NAME} отклонена.")


@bot.on.message(PayloadRule({"cmd": "ban"}))
async def ban_payload(message: Message):
    if not await is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await set_banned(user_id, 1)
    await message.answer(f"Бан {user_id}")
    await notify_user(message.ctx_api, user_id, "Вы заблокированы в боте.")


@bot.on.message(PayloadRule({"cmd": "card"}))
async def card_payload(message: Message):
    if not await is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    p = await get_player(user_id)
    if not p:
        await message.answer("Нет")
        return
    items = await inv_get(user_id)
    inv = ", ".join(f"{i}×{a}" for i, a in items) if items else "пусто"
    await message.answer(format_player_card(p) + f"\nИнвентарь: {inv}")


@bot.on.message(PayloadRule({"cmd": "idea_ok"}))
async def idea_ok(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "accepted")
    await message.answer(f"Идея #{iid} принята")
    await notify_user(message.ctx_api, idea["user_id"], f"Ваша идея #{iid} принята администрацией!")


@bot.on.message(PayloadRule({"cmd": "idea_no"}))
async def idea_no(message: Message):
    if not await is_admin(message.from_id):
        return
    iid = int((message.get_payload_json() or {})["id"])
    idea = await get_idea(iid)
    if not idea:
        return
    await set_idea_status(iid, "rejected")
    await message.answer(f"Идея #{iid} отклонена")
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
    await message.answer(f"Идея #{iid} отмечена выполненной")
    await notify_user(message.ctx_api, idea["user_id"], f"Идея #{iid} реализована на проекте!")


# ----- Text commands -----
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
            await message.answer("Недостаточно средств")
            return
        target = await get_player_by_nick(nick)
        if not target or target["user_id"] == uid or target["status"] != "approved":
            await message.answer("Получатель недоступен")
            return
        await add_balance(uid, -amount)
        await add_balance(target["user_id"], amount)
        await message.answer(f"Переведено {amount}₽ → {target['nickname']}")
        await notify_user(api, target["user_id"], f"+{amount}₽ от {player['nickname']}")
        return

    if not await is_admin(uid):
        return

    if low.startswith("info ") or low.startswith("инфо "):
        p = await resolve_player(text.split(maxsplit=1)[1])
        if not p:
            await message.answer("Не найден")
            return
        items = await inv_get(p["user_id"])
        inv = ", ".join(f"{i}×{a}" for i, a in items) if items else "пусто"
        await message.answer(format_player_card(p) + f"\nИнвентарь: {inv}")
        return

    if low.startswith("админдобавить "):
        if not await is_owner(uid):
            await message.answer("Только главный админ (.env)")
            return
        who = text.split(maxsplit=1)[1].strip()
        target_id = None
        if who.isdigit():
            target_id = int(who)
        elif who.lower().startswith("id") and who[2:].isdigit():
            target_id = int(who[2:])
        else:
            p = await resolve_player(who)
            target_id = p["user_id"] if p else None
        if not target_id:
            await message.answer("Укажи id")
            return
        if target_id in ENV_ADMINS:
            await message.answer("Уже главный админ")
            return
        await add_bot_admin(target_id, uid)
        await message.answer(f"Добавлен админ бота: {target_id}")
        await notify_user(api, target_id, f"Вас назначили администратором бота {PROJECT_NAME}.")
        return

    if low.startswith("админубрать "):
        if not await is_owner(uid):
            await message.answer("Только главный админ (.env)")
            return
        who = text.split(maxsplit=1)[1].strip()
        target_id = None
        if who.isdigit():
            target_id = int(who)
        elif who.lower().startswith("id") and who[2:].isdigit():
            target_id = int(who[2:])
        else:
            p = await resolve_player(who)
            target_id = p["user_id"] if p else None
        if not target_id:
            await message.answer("Укажи id")
            return
        if target_id in ENV_ADMINS:
            await message.answer("Главного из .env снять нельзя")
            return
        await remove_bot_admin(target_id)
        await message.answer(f"Снят админ: {target_id}")
        await notify_user(api, target_id, "Ваши права администратора бота сняты.")
        return

    if low.startswith("голосование "):
        body = text[len("голосование ") :].strip()
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 3:
            await message.answer("Формат:\nголосование Вопрос | вариант1 | вариант2")
            return
        question, options = parts[0], parts[1:]
        if len(options) > 6:
            await message.answer("Максимум 6 вариантов")
            return
        vid = await create_vote(question, options, uid)
        await message.answer(f"Голосование #{vid} создано.\nИгроки: кнопка «Голосования»")
        # уведомить одобренных кратко
        players = await get_all_players()
        for user_id, nick, status, level, balance, banned in players:
            if status == "approved" and not banned:
                await notify_user(api, user_id, f"Новое голосование #{vid} на {PROJECT_NAME}:\n{question}\nОткрой «Голосования».")
        return

    if low.startswith("закрытьголос "):
        if not text.split()[-1].isdigit():
            await message.answer("закрытьголос ID")
            return
        vid = int(text.split()[-1])
        vote = await get_vote(vid)
        if not vote:
            await message.answer("Нет такого")
            return
        await close_vote(vid)
        results = await vote_results(vid)
        options = json.loads(vote["options"])
        lines = [f"Голосование #{vid} закрыто\n{vote['question']}"]
        total = sum(results.values()) or 1
        for i, opt in enumerate(options):
            c = results.get(i, 0)
            lines.append(f"{opt}: {c} ({c * 100 // total}%)")
        await message.answer("\n".join(lines))
        return

    if low.startswith("результаты "):
        if not text.split()[-1].isdigit():
            await message.answer("результаты ID")
            return
        vid = int(text.split()[-1])
        vote = await get_vote(vid)
        if not vote:
            await message.answer("Нет")
            return
        results = await vote_results(vid)
        options = json.loads(vote["options"])
        total = sum(results.values()) or 1
        lines = [f"#{vid} {'[активно]' if vote['active'] else '[закрыто]'}\n{vote['question']}"]
        for i, opt in enumerate(options):
            c = results.get(i, 0)
            lines.append(f"{opt}: {c} ({c * 100 // total}%)")
        await message.answer("\n".join(lines))
        return

    if low.startswith("выдатьвсем "):
        if not text.split()[1].lstrip("-").isdigit():
            await message.answer("выдатьвсем 100")
            return
        amount = int(text.split()[1])
        ok = 0
        for user_id, nick, status, level, balance, banned in await get_all_players():
            if status != "approved" or banned:
                continue
            await add_balance(user_id, amount)
            await notify_user(api, user_id, f"Начисление всем игрокам {PROJECT_NAME}: +{amount}₽")
            ok += 1
        await message.answer(f"Выдано {amount}₽ → {ok}")
        return

    if low.startswith("выдать "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("выдать Ник 1000")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        await add_balance(target["user_id"], amount)
        p2 = await get_player(target["user_id"])
        await message.answer(f"+{amount}₽ → {target['nickname']} (баланс {p2['balance']}₽)")
        await notify_user(api, target["user_id"], f"Вам начислено +{amount}₽\nБаланс: {p2['balance']}₽")
        return

    if low.startswith("забрать ") and not low.startswith("забратьпредмет"):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("забрать Ник 500")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        await add_balance(target["user_id"], -amount)
        p2 = await get_player(target["user_id"])
        await message.answer(f"−{amount}₽ у {target['nickname']} (баланс {p2['balance']}₽)")
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
            await message.answer("Не найден")
            return
        old = target["balance"]
        await set_balance(target["user_id"], amount)
        await message.answer(f"{target['nickname']}: {old} → {amount}₽")
        await notify_user(api, target["user_id"], f"Баланс изменён админом: {old}₽ → {amount}₽")
        return

    if low.startswith("уровень "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("уровень Ник 5")
            return
        level = max(1, int(parts[-1]))
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        await set_level(target["user_id"], level, 0)
        await message.answer(f"Уровень {target['nickname']} = {level}")
        await notify_user(api, target["user_id"], f"Вам установлен уровень: {level}")
        return

    if low.startswith("опыт "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("опыт Ник 200")
            return
        amount = int(parts[-1])
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        leveled = await add_exp(target["user_id"], amount)
        p2 = await get_player(target["user_id"])
        await message.answer(f"+{amount} опыта → {target['nickname']} ур.{p2['level']}")
        await notify_user(
            api, target["user_id"],
            f"+{amount} опыта" + (" (уровень!)" if leveled else "") + f"\nУровень: {p2['level']}",
        )
        return

    if low.startswith("ник "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("ник Старый Новый")
            return
        target = await resolve_player(parts[1])
        new_nick = parts[2].strip()
        if not target or len(new_nick) < 2 or len(new_nick) > 24 or await get_player_by_nick(new_nick):
            await message.answer("Ошибка ника")
            return
        old = target["nickname"]
        await set_nickname(target["user_id"], new_nick)
        await message.answer(f"{old} → {new_nick}")
        await notify_user(api, target["user_id"], f"Ник изменён: {old} → {new_nick}")
        return

    if low.startswith("статус "):
        parts = text.split()
        if len(parts) < 3 or parts[-1].lower() not in ("approved", "pending", "rejected"):
            await message.answer("статус Ник approved|pending|rejected")
            return
        status = parts[-1].lower()
        target = await resolve_player(" ".join(parts[1:-1]))
        if not target:
            await message.answer("Не найден")
            return
        await update_status(target["user_id"], status)
        ru = {"approved": "одобрен", "pending": "на рассмотрении", "rejected": "отклонён"}[status]
        await message.answer(f"{target['nickname']} → {status}")
        await notify_user(api, target["user_id"], f"Статус заявки: {ru}")
        return

    if low.startswith("одобрить ") and "всех" not in low:
        target = await resolve_player(text.split(maxsplit=1)[1])
        if not target:
            await message.answer("Не найден")
            return
        await update_status(target["user_id"], "approved")
        await message.answer(f"Одобрен {target['nickname']}")
        await notify_user(api, target["user_id"], f"Заявка на {PROJECT_NAME} одобрена!")
        return

    if low.startswith("предмет "):
        parts = text.split()
        if len(parts) < 3:
            await message.answer("предмет Ник Название [N]")
            return
        amount = int(parts[-1]) if parts[-1].isdigit() else 1
        item = " ".join(parts[2:-1]) if parts[-1].isdigit() else " ".join(parts[2:])
        target = await resolve_player(parts[1])
        if not target or not item:
            await message.answer("Ошибка")
            return
        await inv_add(target["user_id"], item, amount)
        await message.answer(f"{item}×{amount} → {target['nickname']}")
        await notify_user(api, target["user_id"], f"Вам выдан предмет: {item} ×{amount}")
        return

    if low.startswith("забратьпредмет "):
        parts = text.split()
        if len(parts) < 3:
            await message.answer("забратьпредмет Ник Название [N]")
            return
        amount = int(parts[-1]) if parts[-1].isdigit() else 1
        item = " ".join(parts[2:-1]) if parts[-1].isdigit() else " ".join(parts[2:])
        target = await resolve_player(parts[1])
        if not target:
            await message.answer("Не найден")
            return
        ok = await inv_remove(target["user_id"], item, amount)
        if ok:
            await message.answer("Снято")
            await notify_user(api, target["user_id"], f"Изъят предмет: {item} ×{amount}")
        else:
            await message.answer("Нет у игрока")
        return

    if low.startswith("сброскулдаун "):
        target = await resolve_player(text.split(maxsplit=1)[1])
        if not target:
            await message.answer("Не найден")
            return
        await reset_cooldowns(target["user_id"])
        await message.answer(f"КД сброшены: {target['nickname']}")
        await notify_user(api, target["user_id"], "Кулдауны работы/ежедневки сброшены.")
        return

    if low.startswith("бан "):
        rest = text[4:].strip()
        parts = rest.split(maxsplit=1)
        target = await resolve_player(parts[0] if parts else "")
        reason = parts[1] if len(parts) > 1 else "без причины"
        if not target:
            await message.answer("Не найден")
            return
        await set_banned(target["user_id"], 1)
        await message.answer(f"Бан {target['nickname']}: {reason}")
        await notify_user(api, target["user_id"], f"Бан в боте.\nПричина: {reason}")
        return

    if low.startswith("разбан "):
        target = await resolve_player(text[7:].strip())
        if not target:
            await message.answer("Не найден")
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
            await message.answer("Не найден")
            return
        await message.answer(f"Пред → {target['nickname']}")
        await notify_user(api, target["user_id"], f"Предупреждение администрации:\n{parts[2]}")
        return

    if low.startswith("сказать "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("сказать Ник Текст")
            return
        target = await resolve_player(parts[1])
        if not target:
            await message.answer("Не найден")
            return
        await notify_user(api, target["user_id"], f"Сообщение администрации:\n{parts[2]}")
        await message.answer(f"Отправлено → {target['nickname']}")
        return

    if low.startswith("удалить "):
        target = await resolve_player(text[8:].strip())
        if not target:
            await message.answer("Не найден")
            return
        tid, tnick = target["user_id"], target["nickname"]
        await delete_player(tid)
        await message.answer(f"Удалён {tnick}")
        await notify_user(api, tid, "Аккаунт в боте удалён.")
        return

    if low in ("одобритьвсех", "одобрить всех"):
        pending = await get_pending_players()
        for user_id, nick, *_ in pending:
            await update_status(user_id, "approved")
            await notify_user(api, user_id, f"Заявка на {PROJECT_NAME} одобрена!")
        await message.answer(f"Одобрено: {len(pending)}")
        return

    if low.startswith("рассылка "):
        body = text[9:].strip()
        if not body:
            return
        ok = 0
        for user_id, nick, status, level, balance, banned in await get_all_players():
            if status == "approved" and not banned:
                await notify_user(api, user_id, f"[Рассылка {PROJECT_NAME}]\n{body}")
                ok += 1
        await message.answer(f"Рассылка: {ok}")
        return


async def main():
    if not TOKEN:
        raise SystemExit("Укажи TOKEN")
    await init_db()
    print(f"Бот {PROJECT_NAME} запущен | OWNER admins: {ENV_ADMINS}")
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
