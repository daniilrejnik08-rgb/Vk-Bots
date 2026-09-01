import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")

_admins = os.getenv("ADMINS", "")
ADMINS = [int(x.strip()) for x in _admins.split(",") if x.strip().isdigit()]
