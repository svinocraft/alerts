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


def _table_exists(conn_sync, table: str) -> bool:
    return inspect(conn_sync).has_table(table)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.begin() as conn:
        def _migrate(conn_sync):
            # ── territories ────────────────────────────────────────────
            if _table_exists(conn_sync, "territories"):
                needed = {
                    "proximity_radius": "INTEGER NOT NULL DEFAULT 50",
                    "world": "VARCHAR(100) NOT NULL DEFAULT 'minecraft_overworld'",
                    "region_id": "VARCHAR(255)",
                    "auto_update": "BOOLEAN NOT NULL DEFAULT 0",
                    "last_updated": "DATETIME",
                }
                missing = _get_missing_columns(conn_sync, "territories", needed)
                for col in missing:
                    ddl = needed[col]
                    conn_sync.execute(
                        text(f"ALTER TABLE territories ADD COLUMN {col} {ddl}")
                    )
                    logger.info("Added missing column territories.%s", col)

            # ── alert_configs ────────────────────────────────────────────
            if _table_exists(conn_sync, "alert_configs"):
                needed = {
                    "enter_enabled": "BOOLEAN NOT NULL DEFAULT 1",
                    "exit_enabled": "BOOLEAN NOT NULL DEFAULT 1",
                    "proximity_enabled": "BOOLEAN NOT NULL DEFAULT 1",
                    "monitor_join_enabled": "BOOLEAN NOT NULL DEFAULT 1",
                    "monitor_leave_enabled": "BOOLEAN NOT NULL DEFAULT 1",
                }
                missing = _get_missing_columns(conn_sync, "alert_configs", needed)
                for col in missing:
                    ddl = needed[col]
                    conn_sync.execute(
                        text(f"ALTER TABLE alert_configs ADD COLUMN {col} {ddl}")
                    )
                    logger.info("Added missing column alert_configs.%s", col)

            # ── whitelist ──────────────────────────────────────────────
            if _table_exists(conn_sync, "whitelist"):
                needed = {"territory_id": "INTEGER REFERENCES territories(id) ON DELETE CASCADE"}
                missing = _get_missing_columns(conn_sync, "whitelist", needed)
                if missing:
                    logger.info("Recreating whitelist table for new constraint...")
                    conn_sync.execute(text("""
                        CREATE TABLE whitelist_new (
                            id INTEGER NOT NULL PRIMARY KEY,
                            chat_id INTEGER NOT NULL,
                            territory_id INTEGER REFERENCES territories(id) ON DELETE CASCADE,
                            player_name VARCHAR(100) NOT NULL,
                            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT uq_whitelist UNIQUE (chat_id, territory_id, player_name)
                        )
                    """))
                    conn_sync.execute(text(
                        "INSERT INTO whitelist_new (id, chat_id, player_name, added_at) "
                        "SELECT id, chat_id, player_name, added_at FROM whitelist"
                    ))
                    conn_sync.execute(text("DROP TABLE whitelist"))
                    conn_sync.execute(text("ALTER TABLE whitelist_new RENAME TO whitelist"))
                    logger.info("Whitelist table recreated with territory_id and new constraint.")

        await conn.run_sync(_migrate)
