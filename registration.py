from vkbottle.bot import BotLabeler, Message

from config import ADMINS
from database import create_player, get_player
from keyboards import (
    approve_keyboard,
    cancel_keyboard,
    gender_keyboard,
    main_menu,
)
from states import RegState, state_dispenser

labeler = BotLabeler()


@labeler.message(text=["Регистрация", "регистрация"])
async def start_reg(message: Message):
    player = await get_player(message.from_id)
    if player:
        await message.answer(
            "Ты уже зарегистрирован!",
            keyboard=main_menu(True, message.from_id in ADMINS),
        )
        return

    await message.answer(
        "Начинаем регистрацию в Prp Games!\n\nВведи свой игровой никнейм:",
        keyboard=cancel_keyboard(),
    )
    await state_dispenser.set(message.peer_id, RegState.NICKNAME)


@labeler.message(state=RegState.NICKNAME)
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


@labeler.message(state=RegState.AGE)
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


@labeler.message(state=RegState.GENDER)
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


@labeler.message(state=RegState.CITY)
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


@labeler.message(state=RegState.ABOUT)
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
        "Статус: на рассмотрении\n"
        "Администрация скоро рассмотрит заявку.",
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
