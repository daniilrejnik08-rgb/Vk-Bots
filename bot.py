import asyncio

from vkbottle.bot import Bot, Message

from config import ADMINS, TOKEN
from database import get_player, init_db
from handlers import admin, personal, registration
from keyboards import main_menu
from states import state_dispenser

bot = Bot(token=TOKEN, state_dispenser=state_dispenser)
bot.labeler.load(registration.labeler)
bot.labeler.load(personal.labeler)
bot.labeler.load(admin.labeler)


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


async def main():
    if not TOKEN or TOKEN == "ВСТАВЬ_ТОКЕН_СЮДА":
        raise SystemExit("Укажи TOKEN в файле .env или в config.py")
    await init_db()
    print("Бот Prp Games запущен")
    await bot.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
