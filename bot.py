import asyncio
import os
from datetime import datetime

import aiosqlite
from dotenv import load_dotenv
from vkbottle import BaseStateGroup, BuiltinStateDispenser, Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Bot, Message
from vkbottle.dispatch.rules.base import PayloadRule

load_dotenv()

TOKEN = os.getenv("TOKEN", "").strip()
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]
DB_NAME = "prp_games.db"

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
                nickname TEXT,
                age INTEGER,
                gender TEXT,
                city TEXT,
                about TEXT,
                status TEXT DEFAULT 'pending',
                registered_at TEXT,
                balance INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1
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


async def create_player(user_id: int, data: dict):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO players (user_id, nickname, age, gender, city, about, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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


async def get_pending_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, age, gender, city FROM players WHERE status = 'pending'"
        ) as cur:
            return await cur.fetchall()


async def get_all_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, status, level, balance FROM players ORDER BY registered_at DESC"
        ) as cur:
            return await cur.fetchall()


# -------------------- Keyboards --------------------
def main_menu(is_registered: bool = False, is_admin: bool = False):
    kb = Keyboard(one_time=False)
    if not is_registered:
        kb.add(Text("Регистрация"), color=KeyboardButtonColor.POSITIVE)
    else:
        kb.add(Text("Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("Мой профиль"), color=KeyboardButtonColor.SECONDARY)
    kb.row()
    kb.add(Text("Информация"), color=KeyboardButtonColor.SECONDARY)
    if is_admin:
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
        .add(Text("Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def approve_keyboard(user_id: int):
    return (
        Keyboard(inline=True)
        .add(Text("Одобрить", payload={"cmd": "approve", "user_id": user_id}), color=KeyboardButtonColor.POSITIVE)
        .add(Text("Отклонить", payload={"cmd": "reject", "user_id": user_id}), color=KeyboardButtonColor.NEGATIVE)
    )


# -------------------- Handlers --------------------
@bot.on.message(text=["начать", "Начать", "старт", "Старт", "/start", "меню", "Меню"])
async def start(message: Message):
    player = await get_player(message.from_id)
    await message.answer(
        "Добро пожаловать в бота Prp Games!\n\n"
        "Здесь можно зарегистрироваться в игру и открыть личный кабинет.",
        keyboard=main_menu(player is not None, message.from_id in ADMINS),
    )


@bot.on.message(text=["Информация", "инфо", "помощь"])
async def info(message: Message):
    await message.answer(
        "Prp Games — игровой проект.\n\n"
        "1. Нажми «Регистрация» и заполни анкету.\n"
        "2. Дождись одобрения администратора.\n"
        "3. После одобрения пользуйся личным кабинетом."
    )


@bot.on.message(text=["Регистрация", "регистрация"])
async def start_reg(message: Message):
    player = await get_player(message.from_id)
    if player:
        await message.answer("Ты уже зарегистрирован!", keyboard=main_menu(True, message.from_id in ADMINS))
        return
    await message.answer("Начинаем регистрацию в Prp Games!\n\nВведи свой игровой никнейм:", keyboard=cancel_keyboard())
    await state_dispenser.set(message.peer_id, RegState.NICKNAME)


@bot.on.message(state=RegState.NICKNAME)
async def set_nickname(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Регистрация отменена.", keyboard=main_menu())
        return
    nick = (message.text or "").strip()
    if len(nick) < 2 or len(nick) > 24:
        await message.answer("Ник должен быть от 2 до 24 символов. Попробуй ещё раз:")
        return
    await state_dispenser.set(message.peer_id, RegState.AGE, nickname=nick)
    await message.answer("Отлично! Теперь укажи свой возраст (числом):")


@bot.on.message(state=RegState.AGE)
async def set_age(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Регистрация отменена.", keyboard=main_menu())
        return
    if not (message.text or "").isdigit() or not (10 <= int(message.text) <= 60):
        await message.answer("Возраст должен быть числом от 10 до 60. Попробуй ещё раз:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["age"] = int(message.text)
    await state_dispenser.set(message.peer_id, RegState.GENDER, **payload)
    await message.answer("Выбери пол:", keyboard=gender_keyboard())


@bot.on.message(state=RegState.GENDER)
async def set_gender(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Регистрация отменена.", keyboard=main_menu())
        return
    if message.text not in ("Мужской", "Женский"):
        await message.answer("Выбери пол кнопкой:", keyboard=gender_keyboard())
        return
    payload = dict(message.state_peer.payload or {})
    payload["gender"] = message.text
    await state_dispenser.set(message.peer_id, RegState.CITY, **payload)
    await message.answer("Укажи свой город:", keyboard=cancel_keyboard())


@bot.on.message(state=RegState.CITY)
async def set_city(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Регистрация отменена.", keyboard=main_menu())
        return
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.answer("Укажи город нормально:")
        return
    payload = dict(message.state_peer.payload or {})
    payload["city"] = city
    await state_dispenser.set(message.peer_id, RegState.ABOUT, **payload)
    await message.answer(
        "Расскажи немного о себе / почему хочешь играть (можно пропустить, написав «-»):",
        keyboard=cancel_keyboard(),
    )


@bot.on.message(state=RegState.ABOUT)
async def finish_reg(message: Message):
    if message.text == "Отмена":
        await state_dispenser.delete(message.peer_id)
        await message.answer("Регистрация отменена.", keyboard=main_menu())
        return
    payload = dict(message.state_peer.payload or {})
    payload["about"] = "-" if (message.text or "").strip() == "-" else (message.text or "").strip()
    await create_player(message.from_id, payload)
    await state_dispenser.delete(message.peer_id)
    await message.answer(
        "Заявка на регистрацию отправлена!\n\n"
        f"Ник: {payload.get('nickname')}\n"
        f"Возраст: {payload.get('age')}\n"
        f"Пол: {payload.get('gender')}\n"
        f"Город: {payload.get('city')}\n\n"
        "Статус: на рассмотрении\nАдминистрация скоро рассмотрит заявку.",
        keyboard=main_menu(True, message.from_id in ADMINS),
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
                user_id=admin_id,
                message=text,
                random_id=0,
                keyboard=approve_keyboard(message.from_id),
            )
        except Exception:
            pass


@bot.on.message(text=["Личный кабинет", "лк", "ЛК", "кабинет"])
async def personal_cabinet(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Ты ещё не зарегистрирован. Нажми «Регистрация»", keyboard=main_menu())
        return
    status_map = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}
    status = status_map.get(player["status"], player["status"])
    text = (
        "Личный кабинет Prp Games\n\n"
        f"ID: {player['user_id']}\n"
        f"Ник: {player['nickname']}\n"
        f"Зарегистрирован: {player['registered_at']}\n"
        f"Статус: {status}\n"
        f"Баланс: {player['balance']}\n"
        f"Уровень: {player['level']}"
    )
    await message.answer(text, keyboard=main_menu(True, message.from_id in ADMINS))


@bot.on.message(text=["Мой профиль", "профиль"])
async def profile(message: Message):
    player = await get_player(message.from_id)
    if not player:
        await message.answer("Ты ещё не зарегистрирован.", keyboard=main_menu())
        return
    status_map = {"pending": "на рассмотрении", "approved": "одобрен", "rejected": "отклонён"}
    status = status_map.get(player["status"], player["status"])
    text = (
        "Профиль игрока\n\n"
        f"Ник: {player['nickname']}\n"
        f"Возраст: {player['age']}\n"
        f"Пол: {player['gender']}\n"
        f"Город: {player['city']}\n"
        f"О себе: {player['about']}\n"
        f"Статус: {status}"
    )
    await message.answer(text)


@bot.on.message(text=["Админ-панель", "админка"])
async def admin_panel(message: Message):
    if message.from_id not in ADMINS:
        return
    await message.answer("Админ-панель Prp Games:", keyboard=admin_keyboard())


@bot.on.message(text="Заявки")
async def show_pending(message: Message):
    if message.from_id not in ADMINS:
        return
    pending = await get_pending_players()
    if not pending:
        await message.answer("Нет заявок на рассмотрении.")
        return
    for user_id, nick, age, gender, city in pending:
        await message.answer(
            f"Заявка от [id{user_id}|пользователя]\nНик: {nick}\nВозраст: {age}\nПол: {gender}\nГород: {city}",
            keyboard=approve_keyboard(user_id),
        )


@bot.on.message(text="Игроки")
async def show_players(message: Message):
    if message.from_id not in ADMINS:
        return
    players = await get_all_players()
    if not players:
        await message.answer("Игроков пока нет.")
        return
    lines = ["Список игроков:\n"]
    for user_id, nick, status, level, balance in players[:50]:
        lines.append(f"[id{user_id}|{nick}] — {status}, ур. {level}, баланс {balance}")
    await message.answer("\n".join(lines))


@bot.on.message(PayloadRule({"cmd": "approve"}))
async def approve(message: Message):
    if message.from_id not in ADMINS:
        return
    payload = message.get_payload_json() or {}
    user_id = int(payload["user_id"])
    await update_status(user_id, "approved")
    await message.answer(f"Игрок {user_id} одобрен.")
    try:
        await message.ctx_api.messages.send(
            user_id=user_id,
            message="Поздравляем! Заявка в Prp Games одобрена. Добро пожаловать в игру!",
            random_id=0,
        )
    except Exception:
        pass


@bot.on.message(PayloadRule({"cmd": "reject"}))
async def reject(message: Message):
    if message.from_id not in ADMINS:
        return
    payload = message.get_payload_json() or {}
    user_id = int(payload["user_id"])
    await update_status(user_id, "rejected")
    await message.answer(f"Игрок {user_id} отклонён.")
    try:
        await message.ctx_api.messages.send(
            user_id=user_id,
            message="К сожалению, заявка в Prp Games отклонена.",
            random_id=0,
        )
    except Exception:
        pass


@bot.on.message(text="Назад")
async def back(message: Message):
    player = await get_player(message.from_id)
    await message.answer(
        "Главное меню:",
        keyboard=main_menu(player is not None, message.from_id in ADMINS),
    )


# -------------------- Run --------------------
async def main():
    if not TOKEN:
        raise SystemExit("Укажи TOKEN в переменных окружения или в файле .env")
    await init_db()
    print("Бот Prp Games запущен")
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
