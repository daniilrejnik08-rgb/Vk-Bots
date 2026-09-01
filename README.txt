Prp Games бот — ОДНА ФАЙЛОВАЯ ВЕРСИЯ
=====================================

Нужны только 2 файла:
- bot.py
- requirements.txt

1) В хостинге / на ПК создай переменные окружения:
   TOKEN = токен сообщества ВК
   ADMINS = твой цифровой ID ВК

   Или создай файл .env рядом с bot.py:
   TOKEN=vk1.a.xxxx
   ADMINS=123456789

2) Установка:
   pip install -r requirements.txt

3) Запуск:
   python bot.py

Если на хостинге (Railway, Render и т.п.):
- загрузи только bot.py и requirements.txt
- в настройках проекта добавь переменные TOKEN и ADMINS
- Start Command: python bot.py
