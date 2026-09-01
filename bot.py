import asyncio
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
# Можно указать ID прямо здесь, если не используешь .env:
# ADMINS = [123456789]
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]

DB_NAME = "prp_games.db"

DAILY_BONUS = 500
WORK_MIN = 100
WORK_MAX = 400
WORK_COOLDOWN_SEC = 300

state_dispenser = BuiltinStateDispenser()
bot = Bot(token=TOKEN, state_dispenser=state_dispenser)


class RegState(BaseStateGroup):
    NICKNAME = 1
    AGE = 2
    GENDER = 3
    CITY = 4
    ABOUT = 5


# -------------------- DB --------------------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
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
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item TEXT,
                amount INTEGER DEFAULT 1,
                UNIQUE(user_id, item)
            )
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
    """Ник или id123456 или просто число."""
    who = who.strip()
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
            "UPDATE players SET level = ?, exp = ? WHERE user_id = ?",
            (level, exp, user_id),
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
        async with db.execute(
            "SELECT user_id, nickname FROM players WHERE banned = 1"
        ) as cur:
            return await cur.fetchall()


async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async def cnt(sql):
            async with db.execute(sql) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

        total = await cnt("SELECT COUNT(*) FROM players")
        approved = await cnt("SELECT COUNT(*) FROM players WHERE status = 'approved'")
        pending = await cnt("SELECT COUNT(*) FROM players WHERE status = 'pending'")
        rejected = await cnt("SELECT COUNT(*) FROM players WHERE status = 'rejected'")
        banned = await cnt("SELECT COUNT(*) FROM players WHERE banned = 1")
        money = await cnt("SELECT COALESCE(SUM(balance), 0) FROM players")
        return {
            "total": total,
            "approved": approved,
            "pending": pending,
            "rejected": rejected,
            "banned": banned,
            "money": money,
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


SHOP = {
    "Аптечка": (300, "Восстанавливает силы"),
    "Фонарик": (500, "Полезный предмет"),
    "Рюкзак": (1500, "Больше места"),
    "Смартфон": (2500, "Связь и статус"),
    "VIP-карта": (10000, "Статус VIP"),
}


def is_admin(uid: int) -> bool:
    return uid in ADMINS


async def notify_user(api, user_id: int, text: str):
    try:
        await api.messages.send(user_id=user_id, message=text, random_id=0)
    except Exception:
        pass


async def require_player(message: Message, need_approved: bool = True):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала зарегистрируйся: «Регистрация»", keyboard=main_menu())
        return None
    if player.get("banned") and not is_admin(message.from_id):
        await message.answer("Ты заблокирован в боте.")
        return None
    if need_approved and player["status"] != "approved" and not is_admin(message.from_id):
        status = {"pending": "на рассмотрении", "rejected": "отклонена"}.get(
            player["status"], player["status"]
        )
        await message.answer(f"Заявка ещё не одобрена (статус: {status}).")
        return None
    return player


def main_menu(is_registered: bool = False, is_admin_user: bool = False, approved: bool = False):
    kb = Keyboard(one_time=False)
    if not is_registered:
        kb.add(Text("Регистрация"), color=KeyboardButtonColor.POSITIVE)
    else:
        kb.add(Text("Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("Профиль"), color=KeyboardButtonColor.SECONDARY)
        if approved or is_admin_user:
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
    kb.add(Text("Информация"), color=KeyboardButtonColor.SECONDARY)
    if is_admin_user:
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
        .add(Text("Админ-помощь"), color=KeyboardButtonColor.SECONDARY)
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


def format_player_card(p: dict) -> str:
    status_map = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}
    return (
        f"Карточка игрока\n\n"
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
        f"Ссылка: https://vk.com/id{p['user_id']}"
    )


# -------------------- Start / Info --------------------
@bot.on.message(text=["начать", "Начать", "старт", "Старт", "/start", "меню", "Меню"])
async def start(message: Message):
    player = await get_player(message.from_id)
    approved = bool(player and player["status"] == "approved")
    admin = is_admin(message.from_id)
    extra = "\n\nТы администратор бота." if admin else ""
    await message.answer(
        "Добро пожаловать в Prp Games!" + extra,
        keyboard=main_menu(player is not None, admin, approved or admin),
    )


@bot.on.message(text=["Информация", "инфо", "помощь", "Помощь"])
async def info(message: Message):
    await message.answer(
        "Prp Games\n\n"
        "Игроку: регистрация, работа, ежедневка, магазин, инвентарь, перевод, топ\n"
        "Перевод: перевод Ник 500\n\n"
        "Админам: кнопка «Админ-панель» или команда «Админ-помощь»"
    )


# -------------------- Registration --------------------
@bot.on.message(text=["Регистрация", "регистрация"])
async def start_reg(message: Message):
    player = await get_player(message.from_id)
    if player:
        approved = player["status"] == "approved"
        await message.answer(
            "Ты уже зарегистрирован!",
            keyboard=main_menu(True, is_admin(message.from_id), approved),
        )
        return
    await message.answer("Введи игровой никнейм:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, RegState.NICKNAME)


@bot.on.message(state=RegState.NICKNAME)
async def set_nickname(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=main_menu())
        return
    nick = (message.text or "").strip()
    if len(nick) < 2 or len(nick) > 24:
        await message.answer("Ник от 2 до 24 символов:")
        return
    if await get_player_by_nick(nick):
        await message.answer("Ник занят, выбери другой:")
        return
    await state_dispenser.set(message.peer_id, RegState.AGE, nickname=nick)
    await message.answer("Укажи возраст (числом):")


@bot.on.message(state=RegState.AGE)
async def set_age(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=main_menu())
        return
    if not (message.text or "").isdigit() or not (10 <= int(message.text) <= 60):
        await message.answer("Возраст от 10 до 60:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["age"] = int(message.text)
    await state_dispenser.set(message.peer_id, RegState.GENDER, **payload)
    await message.answer("Выбери пол:", keyboard=gender_keyboard())


@bot.on.message(state=RegState.GENDER)
async def set_gender(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=main_menu())
        return
    if message.text not in ("Мужской", "Женский"):
        await message.answer("Выбери кнопкой:", keyboard=gender_keyboard())
        return
    payload = dict(message.state_peer.payload or {})
    payload["gender"] = message.text
    await state_dispenser.set(message.peer_id, RegState.CITY, **payload)
    await message.answer("Укажи город:", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.CITY)
async def set_city(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=main_menu())
        return
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("Укажи город:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["city"] = city
    await state_dispenser.set(message.peer_id, RegState.ABOUT, **payload)
    await message.answer("Коротко о себе (или «-»):", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.ABOUT)
async def finish_reg(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Отменено.", keyboard=main_menu())
        return
    payload = dict(message.state_peer.payload or {})
    payload["about"] = "-" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await create_player(message.from_id, payload)
    await state_dispenser.delete(message.peer_id)

    # Если регистрируется админ — сразу одобряем
    if is_admin(message.from_id):
        await update_status(message.from_id, "approved")
        await message.answer(
            "Ты админ — заявка одобрена автоматически.\n"
            f"Ник: {payload.get('nickname')}\nСтартовый баланс: 1000₽",
            keyboard=main_menu(True, True, True),
        )
        return

    await message.answer(
        "Заявка отправлена!\n"
        f"Ник: {payload.get('nickname')}\nСтатус: на рассмотрении",
        keyboard=main_menu(True, False, False),
    )
    text = (
        "Новая заявка!\n\n"
        f"ID: {message.from_id}\n"
        f"Ник: {payload.get('nickname')}\n"
        f"Возраст: {payload.get('age')}\n"
        f"Пол: {payload.get('gender')}\n"
        f"Город: {payload.get('city')}\n"
        f"О себе: {payload.get('about')}"
    )
    for admin_id in ADMINS:
        try:
            await message.ctx_api.messages.send(
                user_id=admin_id, message=text, random_id=0, keyboard=approve_keyboard(message.from_id)
            )
        except Exception:
            pass


# -------------------- Player features --------------------
@bot.on.message(text=["Личный кабинет", "лк", "ЛК", "кабинет"])
async def personal_cabinet(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала регистрация.", keyboard=main_menu())
        return
    status_map = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}
    text = (
        "Личный кабинет\n\n"
        f"ID: {player['user_id']}\n"
        f"Ник: {player['nickname']}\n"
        f"Уровень: {player['level']} (опыт {player['exp']}/{player['level'] * 1000})\n"
        f"Баланс: {player['balance']}₽\n"
        f"Статус: {status_map.get(player['status'], player['status'])}\n"
        f"Регистрация: {player['registered_at']}"
    )
    if player.get("banned"):
        text += "\n⚠ Заблокирован"
    await message.answer(
        text,
        keyboard=main_menu(True, is_admin(message.from_id), player["status"] == "approved"),
    )


@bot.on.message(text=["Профиль", "профиль", "Мой профиль"])
async def profile(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Сначала регистрация.", keyboard=main_menu())
        return
    await message.answer(format_player_card(player))


@bot.on.message(text=["Ежедневка", "ежедневка", "бонус", "daily"])
async def daily(message: Message):
    player = await require_player(message)
    if not player:
        return
    today = date.today().isoformat()
    if player.get("last_daily") == today:
        await message.answer("Бонус уже получен сегодня.")
        return
    await add_balance(message.from_id, DAILY_BONUS)
    await set_last_daily(message.from_id)
    leveled = await add_exp(message.from_id, 50)
    extra = "\nУровень повышен!" if leveled else ""
    await message.answer(f"+{DAILY_BONUS}₽ (+50 опыта){extra}")


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
                await message.answer(f"Подожди ещё {int(left // 60)}м {int(left % 60)}с")
                return
        except Exception:
            pass
    pay = random.randint(WORK_MIN, WORK_MAX)
    jobs = ["поработал грузчиком", "развёз заказы", "помог на складе", "починил технику", "постоял на смене"]
    await add_balance(message.from_id, pay)
    await set_last_work(message.from_id)
    leveled = await add_exp(message.from_id, 30)
    extra = "\nУровень повышен!" if leveled else ""
    await message.answer(f"Ты {random.choice(jobs)} и заработал {pay}₽ (+30 опыта){extra}")


@bot.on.message(text=["Топ", "топ", "рейтинг"])
async def top(message: Message):
    rows = await get_top(10)
    if not rows:
        await message.answer("Топ пуст.")
        return
    lines = ["Топ по балансу:\n"]
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
        await message.answer("Инвентарь пуст.")
        return
    lines = ["Инвентарь:\n"] + [f"• {item} × {amount}" for item, amount in items]
    await message.answer("\n".join(lines))


@bot.on.message(text=["Магазин", "магазин", "шоп"])
async def shop(message: Message):
    player = await require_player(message)
    if not player:
        return
    lines = [f"Магазин (баланс: {player['balance']}₽)\n"]
    for name, (price, desc) in SHOP.items():
        lines.append(f"• {name} — {price}₽\n  {desc}")
    await message.answer("\n".join(lines), keyboard=shop_keyboard())


@bot.on.message(PayloadRule({"cmd": "buy"}))
async def buy_item(message: Message):
    player = await require_player(message)
    if not player:
        return
    item = (message.get_payload_json() or {}).get("item")
    if item not in SHOP:
        await message.answer("Нет такого товара.")
        return
    price, _ = SHOP[item]
    if player["balance"] < price:
        await message.answer(f"Нужно {price}₽, у тебя {player['balance']}₽")
        return
    await add_balance(message.from_id, -price)
    await inv_add(message.from_id, item, 1)
    await message.answer(f"Куплено: {item} за {price}₽")


@bot.on.message(text=["Перевод", "перевод"])
async def transfer_help(message: Message):
    player = await require_player(message)
    if not player:
        return
    await message.answer(f"Формат: перевод Ник 500\nБаланс: {player['balance']}₽")


# -------------------- Admin UI --------------------
@bot.on.message(text=["Админ-панель", "админка", "Админка"])
async def admin_panel(message: Message):
    if not is_admin(message.from_id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Админ-панель Prp Games:", keyboard=admin_keyboard())


@bot.on.message(text=["Админ-помощь", "админ помощь", "админкоманды"])
async def admin_help(message: Message):
    if not is_admin(message.from_id):
        return
    await message.answer(
        "Команды администратора\n\n"
        "• info Ник — карточка\n"
        "• выдать Ник 1000 / забрать Ник 500\n"
        "• баланс Ник 5000 — точный баланс\n"
        "• выдатьвсем 100 — всем одобренным\n"
        "• уровень Ник 5 / опыт Ник 200\n"
        "• ник Старый Новый\n"
        "• статус Ник approved|pending|rejected\n"
        "• одобрить Ник\n"
        "• предмет Ник Аптечка 1\n"
        "• забратьпредмет Ник Аптечка 1\n"
        "• сброскулдаун Ник\n"
        "• бан Ник [причина] / разбан Ник\n"
        "• пред Ник Текст — предупреждение\n"
        "• сказать Ник Текст — ЛС игроку\n"
        "• удалить Ник\n"
        "• одобритьвсех\n"
        "• рассылка Текст\n\n"
        "Игрок всегда получает уведомление.\n"
        "Вместо ника: id854071888"
    )


@bot.on.message(text="Заявки")
async def show_pending(message: Message):
    if not is_admin(message.from_id):
        return
    pending = await get_pending_players()
    if not pending:
        await message.answer("Нет заявок.")
        return
    for user_id, nick, age, gender, city in pending:
        await message.answer(
            f"Заявка [id{user_id}|{nick}]\nВозраст: {age}\nПол: {gender}\nГород: {city}",
            keyboard=approve_keyboard(user_id),
        )


@bot.on.message(text="Игроки")
async def show_players(message: Message):
    if not is_admin(message.from_id):
        return
    players = await get_all_players()
    if not players:
        await message.answer("Игроков нет.")
        return
    lines = ["Игроки:\n"]
    for user_id, nick, status, level, balance, banned in players[:50]:
        flag = " [BAN]" if banned else ""
        lines.append(f"[id{user_id}|{nick}] {status} ур.{level} {balance}₽{flag}")
    await message.answer("\n".join(lines))


@bot.on.message(text="Статистика")
async def stats_cmd(message: Message):
    if not is_admin(message.from_id):
        return
    s = await get_stats()
    await message.answer(
        "Статистика проекта:\n\n"
        f"Всего игроков: {s['total']}\n"
        f"Одобрено: {s['approved']}\n"
        f"На рассмотрении: {s['pending']}\n"
        f"Отклонено: {s['rejected']}\n"
        f"В бане: {s['banned']}\n"
        f"Денег в экономике: {s['money']}₽"
    )


@bot.on.message(text="Баны")
async def bans_list(message: Message):
    if not is_admin(message.from_id):
        return
    rows = await get_banned_players()
    if not rows:
        await message.answer("Бан-лист пуст.")
        return
    lines = ["Забанены:\n"] + [f"[id{uid}|{nick}]" for uid, nick in rows]
    await message.answer("\n".join(lines))


@bot.on.message(text="Назад")
async def back(message: Message):
    player = await get_player(message.from_id)
    approved = bool(player and player["status"] == "approved")
    await message.answer(
        "Меню:",
        keyboard=main_menu(player is not None, is_admin(message.from_id), approved or is_admin(message.from_id)),
    )


@bot.on.message(PayloadRule({"cmd": "approve"}))
async def approve(message: Message):
    if not is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await update_status(user_id, "approved")
    await message.answer(f"Одобрен {user_id}")
    try:
        await message.ctx_api.messages.send(
            user_id=user_id,
            message="Заявка одобрена! Добро пожаловать. Стартовый баланс 1000₽.",
            random_id=0,
        )
    except Exception:
        pass


@bot.on.message(PayloadRule({"cmd": "reject"}))
async def reject(message: Message):
    if not is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await update_status(user_id, "rejected")
    await message.answer(f"Отклонён {user_id}")
    try:
        await message.ctx_api.messages.send(user_id=user_id, message="Заявка отклонена.", random_id=0)
    except Exception:
        pass


@bot.on.message(PayloadRule({"cmd": "ban"}))
async def ban_payload(message: Message):
    if not is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    await set_banned(user_id, 1)
    await message.answer(f"Забанен {user_id}")


@bot.on.message(PayloadRule({"cmd": "card"}))
async def card_payload(message: Message):
    if not is_admin(message.from_id):
        return
    user_id = int((message.get_payload_json() or {})["user_id"])
    p = await get_player(user_id)
    if not p:
        await message.answer("Игрок не найден")
        return
    items = await inv_get(user_id)
    inv = ", ".join(f"{i}×{a}" for i, a in items) if items else "пусто"
    await message.answer(format_player_card(p) + f"\nИнвентарь: {inv}")


# -------------------- Text commands (transfer + admin) --------------------
@bot.on.message()
async def text_commands(message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    low = text.lower()
    uid = message.from_id

    # ----- перевод -----
    if low.startswith("перевод "):
        player = await require_player(message)
        if not player:
            return
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit() or int(parts[-1]) <= 0:
            await message.answer("Формат: перевод Ник 500")
            return
        amount = int(parts[-1])
        nick = " ".join(parts[1:-1])
        if player["balance"] < amount:
            await message.answer("Недостаточно средств")
            return
        target = await get_player_by_nick(nick)
        if not target:
            await message.answer("Игрок не найден")
            return
        if target["user_id"] == uid:
            await message.answer("Нельзя себе")
            return
        if target["status"] != "approved":
            await message.answer("Получатель не одобрен")
            return
        await add_balance(uid, -amount)
        await add_balance(target["user_id"], amount)
        await message.answer(f"Переведено {amount}₽ → {target['nickname']}")
        try:
            await message.ctx_api.messages.send(
                user_id=target["user_id"],
                message=f"+{amount}₽ от {player['nickname']}",
                random_id=0,
            )
        except Exception:
            pass
        return

    # ----- дальше только админы -----
    if not is_admin(uid):
        return

    async def need_target(prefix_len: int):
        who = text[prefix_len:].strip()
        if not who:
            await message.answer("Укажи ника или id")
            return None
        # для команд с доп. аргументами who может быть "Ник 100"
        return who

    if low.startswith("info ") or low.startswith("инфо "):
        who = text.split(maxsplit=1)[1]
        p = await resolve_player(who)
        if not p:
            await message.answer("Не найден")
            return
        items = await inv_get(p["user_id"])
        inv = ", ".join(f"{i}×{a}" for i, a in items) if items else "пусто"
        await message.answer(format_player_card(p) + f"\nИнвентарь: {inv}")
        return

    if low.startswith("выдатьвсем "):
        parts = text.split()
        if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
            await message.answer("Формат: выдатьвсем 100")
            return
        amount = int(parts[1])
        players = await get_all_players()
        ok = 0
        for user_id, nick, status, level, balance, banned in players:
            if status != "approved" or banned:
                continue
            await add_balance(user_id, amount)
            await notify_user(
                message.ctx_api,
                user_id,
                f"Администрация начислила всем игрокам: {amount}₽",
            )
            ok += 1
        await message.answer(f"Выдано {amount}₽ → {ok} игрокам (с уведомлениями)")
        return

    if low.startswith("выдать "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("Формат: выдать Ник 1000")
            return
        amount = int(parts[-1])
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await add_balance(target["user_id"], amount)
        p2 = await get_player(target["user_id"])
        await message.answer(
            f"Выдано {amount}₽ → {target['nickname']}\nТеперь баланс: {p2['balance']}₽"
        )
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Вам начислено: +{amount}₽\nТекущий баланс: {p2['balance']}₽",
        )
        return

    if low.startswith("забрать ") and not low.startswith("забратьпредмет"):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("Формат: забрать Ник 500")
            return
        amount = int(parts[-1])
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await add_balance(target["user_id"], -amount)
        p2 = await get_player(target["user_id"])
        await message.answer(
            f"Снято {amount}₽ у {target['nickname']}\nТеперь баланс: {p2['balance']}₽"
        )
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"С вашего счёта снято: −{amount}₽\nТекущий баланс: {p2['balance']}₽",
        )
        return

    if low.startswith("баланс "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("Формат: баланс Ник 5000")
            return
        amount = int(parts[-1])
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        old = target["balance"]
        await set_balance(target["user_id"], amount)
        await message.answer(f"Баланс {target['nickname']}: {old}₽ → {amount}₽")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Администратор изменил ваш баланс\nБыло: {old}₽\nСтало: {amount}₽",
        )
        return

    if low.startswith("уровень "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].isdigit():
            await message.answer("Формат: уровень Ник 5")
            return
        level = max(1, int(parts[-1]))
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await set_level(target["user_id"], level, 0)
        await message.answer(f"Уровень {target['nickname']} = {level}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Администратор установил вам уровень: {level}",
        )
        return

    if low.startswith("опыт "):
        parts = text.split()
        if len(parts) < 3 or not parts[-1].lstrip("-").isdigit():
            await message.answer("Формат: опыт Ник 200")
            return
        amount = int(parts[-1])
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        leveled = await add_exp(target["user_id"], amount)
        p2 = await get_player(target["user_id"])
        extra = " (уровень повышен!)" if leveled else ""
        await message.answer(
            f"Опыт {target['nickname']}: +{amount}{extra}\nУр. {p2['level']}, опыт {p2['exp']}"
        )
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Вам начислено опыта: +{amount}{extra}\nУровень: {p2['level']}",
        )
        return

    if low.startswith("ник "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Формат: ник СтарыйНик НовыйНик")
            return
        target = await resolve_player(parts[1])
        new_nick = parts[2].strip()
        if not target:
            await message.answer("Игрок не найден")
            return
        if len(new_nick) < 2 or len(new_nick) > 24:
            await message.answer("Ник 2–24 символа")
            return
        if await get_player_by_nick(new_nick):
            await message.answer("Ник занят")
            return
        old = target["nickname"]
        await set_nickname(target["user_id"], new_nick)
        await message.answer(f"Ник изменён: {old} → {new_nick}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Администратор сменил ваш ник\nБыло: {old}\nСтало: {new_nick}",
        )
        return

    if low.startswith("статус "):
        parts = text.split()
        if len(parts) < 3 or parts[-1].lower() not in ("approved", "pending", "rejected"):
            await message.answer("Формат: статус Ник approved|pending|rejected")
            return
        status = parts[-1].lower()
        who = " ".join(parts[1:-1])
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await update_status(target["user_id"], status)
        status_ru = {"approved": "одобрен", "pending": "на рассмотрении", "rejected": "отклонён"}[status]
        await message.answer(f"Статус {target['nickname']} → {status}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Статус вашей заявки изменён: {status_ru}",
        )
        return

    if low.startswith("одобрить ") and low not in ("одобритьвсех", "одобрить всех"):
        who = text.split(maxsplit=1)[1] if " " in text else ""
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await update_status(target["user_id"], "approved")
        await message.answer(f"Одобрен: {target['nickname']}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            "Ваша заявка одобрена! Добро пожаловать в Prp Games.",
        )
        return

    if low.startswith("предмет "):
        parts = text.split()
        if len(parts) < 3:
            await message.answer("Формат: предмет Ник Название [кол-во]")
            return
        amount = 1
        if parts[-1].isdigit():
            amount = int(parts[-1])
            item = " ".join(parts[2:-1])
            who = parts[1]
        else:
            item = " ".join(parts[2:])
            who = parts[1]
        target = await resolve_player(who)
        if not target:
            await message.answer("Игрок не найден (ник без пробелов удобнее)")
            return
        if not item:
            await message.answer("Укажи предмет")
            return
        await inv_add(target["user_id"], item, amount)
        await message.answer(f"Выдан предмет {item} ×{amount} → {target['nickname']}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Вам выдан предмет: {item} ×{amount}",
        )
        return

    if low.startswith("забратьпредмет "):
        parts = text.split()
        if len(parts) < 3:
            await message.answer("Формат: забратьпредмет Ник Название [кол-во]")
            return
        amount = 1
        if parts[-1].isdigit():
            amount = int(parts[-1])
            item = " ".join(parts[2:-1])
            who = parts[1]
        else:
            item = " ".join(parts[2:])
            who = parts[1]
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        ok = await inv_remove(target["user_id"], item, amount)
        if ok:
            await message.answer(f"Снято {item} ×{amount} у {target['nickname']}")
            await notify_user(
                message.ctx_api,
                target["user_id"],
                f"У вас изъят предмет: {item} ×{amount}",
            )
        else:
            await message.answer("Нет такого количества у игрока")
        return

    if low.startswith("сброскулдаун "):
        who = text.split(maxsplit=1)[1] if " " in text else ""
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await reset_cooldowns(target["user_id"])
        await message.answer(f"Кулдауны сброшены: {target['nickname']}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            "Администратор сбросил ваши кулдауны (работа / ежедневка).",
        )
        return

    if low.startswith("бан "):
        rest = text[4:].strip()
        parts = rest.split(maxsplit=1)
        who = parts[0] if parts else ""
        reason = parts[1] if len(parts) > 1 else "без указания причины"
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await set_banned(target["user_id"], 1)
        await message.answer(f"Бан: {target['nickname']}\nПричина: {reason}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Вы заблокированы в боте Prp Games.\nПричина: {reason}",
        )
        return

    if low.startswith("разбан "):
        who = text[7:].strip()
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        await set_banned(target["user_id"], 0)
        await message.answer(f"Разбан: {target['nickname']}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            "Вы разблокированы в боте Prp Games. Можно снова пользоваться функциями.",
        )
        return

    if low.startswith("пред "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Формат: пред Ник Текст предупреждения")
            return
        target = await resolve_player(parts[1])
        warn = parts[2].strip()
        if not target:
            await message.answer("Не найден")
            return
        await message.answer(f"Предупреждение отправлено → {target['nickname']}")
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Предупреждение от администрации:\n{warn}",
        )
        return

    if low.startswith("сказать "):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Формат: сказать Ник Текст")
            return
        target = await resolve_player(parts[1])
        body = parts[2].strip()
        if not target:
            await message.answer("Не найден")
            return
        await notify_user(
            message.ctx_api,
            target["user_id"],
            f"Сообщение от администрации:\n{body}",
        )
        await message.answer(f"Отправлено → {target['nickname']}")
        return

    if low.startswith("удалить "):
        who = text[8:].strip()
        target = await resolve_player(who)
        if not target:
            await message.answer("Не найден")
            return
        tid, tnick = target["user_id"], target["nickname"]
        await delete_player(tid)
        await message.answer(f"Удалён из базы: {tnick} ({tid})")
        await notify_user(
            message.ctx_api,
            tid,
            "Ваш аккаунт в боте Prp Games удалён администратором.",
        )
        return

    if low in ("одобритьвсех", "одобрить всех"):
        pending = await get_pending_players()
        for user_id, nick, *_ in pending:
            await update_status(user_id, "approved")
            await notify_user(
                message.ctx_api,
                user_id,
                "Ваша заявка одобрена администратором. Добро пожаловать!",
            )
        await message.answer(f"Одобрено заявок: {len(pending)} (уведомления отправлены)")
        return

    if low.startswith("рассылка "):
        body = text[9:].strip()
        if not body:
            await message.answer("Пустой текст")
            return
        players = await get_all_players()
        ok = 0
        for user_id, nick, status, level, balance, banned in players:
            if status != "approved" or banned:
                continue
            await notify_user(message.ctx_api, user_id, f"[Рассылка]\n{body}")
            ok += 1
        await message.answer(f"Рассылка отправлена: {ok} игрокам")
        return


async def main():
    if not TOKEN:
        raise SystemExit("Укажи TOKEN в .env или переменных окружения")
    if not ADMINS:
        print("ВНИМАНИЕ: ADMINS пустой — никто не админ. Укажи свой ID в ADMINS")
    else:
        print(f"Админы: {ADMINS}")
    await init_db()
    print("Бот Prp Games (admin+) запущен")
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
