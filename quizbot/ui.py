"""أدوات الواجهة: الأزرار الملونة، الإرسال الآمن، حماية الأدمن."""
import functools
import html
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, Update
from telegram import InlineKeyboardMarkup as Markup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from .config import ADMIN_IDS, COLORED_BUTTONS, TIMEZONE

log = logging.getLogger("quizbot")
esc = html.escape

# ألوان الأزرار: أزرق = تنقّل/عرض · أخضر = إضافة/تأكيد/بدء · أحمر = حذف/إيقاف/خروج · بدون لون = محايد/رجوع
BLUE, GREEN, RED = "primary", "success", "danger"


def btn(text, data, style=None):
    return InlineKeyboardButton(text, callback_data=data, style=style if COLORED_BUTTONS else None)


def rows(*lines):
    return Markup([list(line) for line in lines])


def is_admin(uid):
    return uid in ADMIN_IDS


def admin_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        if not is_admin(update.effective_user.id):
            if update.callback_query:
                await update.callback_query.answer("للأدمن فقط ⛔", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("⛔ الأمر ده للأدمن فقط.")
            return ConversationHandler.END
        return await fn(update, context, *a, **k)

    return wrapper


def clip(text, n=3900):
    """يقص النص عند آخر سطر كامل (كل الـ tags عندنا بتفتح وتتقفل في نفس السطر)."""
    if len(text) <= n:
        return text
    cut = text[:n].rsplit("\n", 1)[0]
    return (cut if cut else text[: n - 3]) + "\n…"


async def show(update: Update, text, markup=None):
    """يعدّل الرسالة لو جاية من زر، وإلا يبعت رسالة جديدة."""
    text = clip(text)
    q = update.callback_query
    if q and q.message:
        try:
            await q.edit_message_text(text, reply_markup=markup)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        await q.message.reply_text(text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, reply_markup=markup)


async def safe_send(bot, chat_id, text, markup=None):
    try:
        return await bot.send_message(chat_id, clip(text), reply_markup=markup)
    except TelegramError as e:
        log.debug("send failed to %s: %s", chat_id, e)
        return None


async def notify_admins(bot, text, markup=None):
    for aid in ADMIN_IDS:
        await safe_send(bot, aid, text, markup)


def fmt_time(ts):
    """تاريخ ووقت بتوقيت TIMEZONE (افتراضي القاهرة)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
