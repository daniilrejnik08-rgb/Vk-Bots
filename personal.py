from vkbottle.bot import BotLabeler, Message

from config import ADMINS
from database import get_player
from keyboards import main_menu

labeler = BotLabeler()

STATUS_TEXT = {
    "pending": "на рассмотрении",
    "approved": "одобрен",
    "rejected": "отклонён",
}


@labeler.message(text=["Личный кабинет", "лк", "ЛК", "кабинет"])
async def personal_cabinet(message: Message):
    player = await get_player(message.from_id)

    if not player:
        await message.answer(
            "Ты ещё не зарегистрирован. Нажми «Регистрация»",
            keyboard=main_menu(),
        )
        return

    status = STATUS_TEXT.get(player["status"], player["status"])
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


@labeler.message(text=["Мой профиль", "профиль"])
async def profile(message: Message):
    player = await get_player(message.from_id)

    if not player:
        await message.answer("Ты ещё не зарегистрирован.", keyboard=main_menu())
        return

    status = STATUS_TEXT.get(player["status"], player["status"])
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
