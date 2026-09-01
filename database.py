import aiosqlite
from datetime import datetime

DB_NAME = "prp_games.db"


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
        async with db.execute(
            "SELECT * FROM players WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
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
        await db.execute(
            "UPDATE players SET status = ? WHERE user_id = ?",
            (status, user_id),
        )
        await db.commit()


async def get_pending_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, age, gender, city FROM players WHERE status = 'pending'"
        ) as cursor:
            return await cursor.fetchall()


async def get_all_players():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, nickname, status, level, balance FROM players ORDER BY registered_at DESC"
        ) as cursor:
            return await cursor.fetchall()
