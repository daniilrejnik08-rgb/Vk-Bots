from vkbottle.bot import BotLabeler, Message
from vkbottle.dispatch.rules.base import PayloadRule

from config import ADMINS
from database import get_all_players, get_pending_players, get_player, update_status
from keyboards import admin_keyboard, approve_keyboard, main_menu

labeler = BotLabeler()


def _is_admin(user_id: int) -> bool:
    return user_id in ADMINS


@labeler.message(text=["Админ-панель", "админка"])
async def admin_panel(message: Message):
    if not _is_admin(message.from_id):
        return
    await message.answer("Админ-панель Prp Games:", keyboard=admin_keyboard())


@labeler.message(text="Заявки")
async def show_pending(message: Message):
    if not _is_admin(message.from_id):
        return

    pending = await get_pending_players()
    if not pending:
        await message.answer("Нет заявок на рассмотрении.")
        return

    for user_id, nick, age, gender, city in pending:
        await message.answer(
            f"Заявка от [id{user_id}|пользователя]\n"
            f"Ник: {nick}\nВозраст: {age}\nПол: {gender}\nГород: {city}",
            keyboard=approve_keyboard(user_id),
        )


@labeler.message(text="Игроки")
async def show_players(message: Message):
    if not _is_admin(message.from_id):
        return

    players = await get_all_players()
    if not players:
        await message.answer("Игроков пока нет.")
        return

    lines = ["Список игроков:\n"]
    for user_id, nick, status, level, balance in players[:50]:
        lines.append(f"[id{user_id}|{nick}] — {status}, ур. {level}, баланс {balance}")
    await message.answer("\n".join(lines))


@labeler.message(PayloadRule({"cmd": "approve"}))
async def approve(message: Message):
    if not _is_admin(message.from_id):
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


@labeler.message(PayloadRule({"cmd": "reject"}))
async def reject(message: Message):
    if not _is_admin(message.from_id):
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


@labeler.message(text="Назад")
async def back(message: Message):
    player = await get_player(message.from_id)
    await message.answer(
        "Главное меню:",
        keyboard=main_menu(player is not None, _is_admin(message.from_id)),
    )
