import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data.db")
API_URL: str = "https://pisunsvinyi.aartzz.pp.ua/tiles"
POLL_INTERVAL: int = 1
AUTO_UPDATE_INTERVAL: int = 300
