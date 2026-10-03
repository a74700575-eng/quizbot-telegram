"""الإعدادات الثابتة وقراءة متغيرات البيئة."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "data/quizbot.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower() or "openai"
AI_API_BASE = os.getenv("AI_API_BASE", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
# الأزرار الملونة (Bot API 9.4+). اكتب COLORED_BUTTONS=0 لإيقافها.
COLORED_BUTTONS = os.getenv("COLORED_BUTTONS", "1") != "0"

TIMEZONE = os.getenv("TIMEZONE", "Africa/Cairo")

MAX_TEAM_SIZE = 5
PER_PAGE = 8
LETTERS = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
