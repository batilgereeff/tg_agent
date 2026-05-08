import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot.db")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
ADMIN_ID: int = int(os.getenv("ADMIN_ID") or "0")
MAX_TOOL_ITERATIONS: int = 5
