import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import DATABASE_URL
from db.models import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _get_missing_columns(conn_sync, table: str, needed: dict[str, str]) -> list[str]:
    existing = {c["name"] for c in inspect(conn_sync).get_columns(table)}
    return [col for col in needed if col not in existing]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.begin() as conn:
        def _migrate(conn_sync):
            missing = _get_missing_columns(
                conn_sync,
                "territories",
                {"proximity_radius": "INTEGER NOT NULL DEFAULT 50"},
            )
            for col in missing:
                ddl = needed.get(col)
                conn_sync.execute(
                    text(f"ALTER TABLE territories ADD COLUMN {col} {ddl}")
                )
                logger.info("Added missing column territories.%s", col)

        needed = {
            "proximity_radius": "INTEGER NOT NULL DEFAULT 50",
            "world": "VARCHAR(100) NOT NULL DEFAULT 'minecraft_overworld'",
            "region_id": "VARCHAR(255)",
            "auto_update": "BOOLEAN NOT NULL DEFAULT 0",
            "last_updated": "DATETIME",
        }
        await conn.run_sync(_migrate)
