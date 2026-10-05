"""تجميع البوت وتشغيله."""
import logging
import time
import warnings

from telegram import BotCommand, BotCommandScopeChat, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    Defaults,
    MessageHandler,
    filters,
)

from . import admin as A
from . import handlers_user as U
from .config import ADMIN_IDS, TOKEN
from .db import db
from .engine import interrupted_contest, on_vote
from .ui import BLUE, GREEN, btn, esc, notify_admins, rows

log = logging.getLogger("quizbot")
# تحذير معروف وغير مؤثر: أزرار جوه ConversationHandler بدون per_message
warnings.filterwarnings("ignore", message=".*per_message=False.*")

ONLY_MSG = filters.UpdateType.MESSAGE  # يتجاهل الرسايل المعدّلة
TEXT = ONLY_MSG & filters.TEXT & ~filters.COMMAND


_last_alert = {}


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    """يسجّل الخطأ، ويبلّغ الأدمن (مرة كل 10 دقايق لكل نوع خطأ) عشان ماتفضلش المشكلة مخفية."""
    log.error("Unhandled error", exc_info=context.error)
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        return  # أخطاء شبكة عابرة
    key = type(err).__name__
    now = time.time()
    if now - _last_alert.get(key, 0) < 600:
        return
    _last_alert[key] = now
    await notify_admins(context.bot, f"⚠️ <b>خطأ في البوت</b>\n<code>{esc(key)}: {esc(str(err))[:300]}</code>\nالتفاصيل في اللوج.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ("nq", "setkey", "edit", "ai_import_ready", "ai_import_flagged", "quiz_preview"):
        context.user_data.pop(k, None)
    await update.effective_message.reply_text("تم الإلغاء ✅")
    return ConversationHandler.END


async def start_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ("nq", "setkey", "edit", "ai_import_ready", "ai_import_flagged", "quiz_preview"):
        context.user_data.pop(k, None)
    return await U.cmd_start(update, context)


def cancel_then(fn):
    """أمر زي /start أو /top جوه محادثة: يلغيها وينفّذ الأمر بدل ما يكتفي بـ"تم الإلغاء"."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        for k in ("nq", "setkey", "edit", "ai_import_ready", "ai_import_flagged", "quiz_preview"):
            context.user_data.pop(k, None)
        await fn(update, context)
        return ConversationHandler.END

    return handler


async def leave_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await U.user_cb(update, context)
    return ConversationHandler.END


async def post_init(app: Application):
    db.x("UPDATE contests SET status='interrupted', ended_at=NULL WHERE status='running'")
    await app.bot.set_my_commands(
        [
            BotCommand("start", "القائمة الرئيسية"), BotCommand("join", "الانضمام بكود"),
            BotCommand("team", "فريقي"), BotCommand("top", "ترتيب المسابقة"),
            BotCommand("arena", "نجم الساحة وترتيب اليوم"), BotCommand("createteam", "إنشاء فريق جديد"),
            BotCommand("leave", "مغادرة الفريق"), BotCommand("help", "مساعدة"),
        ]
    )
    for aid in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(
                [BotCommand("admin", "لوحة الأدمن"), BotCommand("addq", "إضافة سؤال"), BotCommand("aiimport", "تنسيق كويز بالذكاء"), BotCommand("newteam", "إنشاء فريق"), BotCommand("start", "القائمة"),
                 BotCommand("team", "فريقي"), BotCommand("top", "ترتيب المسابقة"),
                 BotCommand("arena", "نجم الساحة اليوم")],
                scope=BotCommandScopeChat(aid),
            )
        except TelegramError:
            pass
    row = interrupted_contest()
    if row:
        await notify_admins(
            app.bot,
            f"⚠️ المسابقة #{row['id']} اتقطعت لما البوت اتعاد تشغيله.\nتقدر تكمّلها من السؤال اللي وقفت عنده.",
            rows([btn("♻️ استكمال المسابقة", "adm:resume", GREEN)], [btn("🎛 اللوحة", "adm:menu", BLUE)]),
        )
    log.info("Bot ready. Admins: %s", sorted(ADMIN_IDS))


def build_app():
    app = (
        Application.builder()
        .token(TOKEN)
        .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
        # ConversationHandler يعتمد على وصول التحديثات بالتتابع؛ التوازي قد يفسد حالة المحادثة.
        .concurrent_updates(False)
        .rate_limiter(AIORateLimiter(overall_max_rate=28, overall_time_period=1, max_retries=3))
        .post_init(post_init)
        .build()
    )
    skip = lambda fn: CommandHandler("skip", fn)
    cmd_fallbacks = [
        CommandHandler("cancel", cancel),
        CommandHandler("start", start_again),
        CommandHandler("help", cancel_then(U.cmd_help)),
        CommandHandler("team", cancel_then(U.cmd_team)),
        CommandHandler("top", cancel_then(U.cmd_top)),
        CommandHandler("arena", cancel_then(U.cmd_arena)),
        CommandHandler("admin", cancel_then(A.cmd_admin)),
    ]
    admin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newteam", A.create_team_start),
            CommandHandler("addq", A.addq_start),
            CommandHandler("aiimport", A.ai_import_start),
            CallbackQueryHandler(A.create_team_start, pattern=r"^adm:teamadd$"),
            CallbackQueryHandler(A.addq_start, pattern=r"^adm:addq$"),
            CallbackQueryHandler(A.ai_import_start, pattern=r"^adm:aiimport$"),
            CallbackQueryHandler(A.import_start, pattern=r"^adm:import$"),
            CallbackQueryHandler(A.bcast_start, pattern=r"^adm:bcast$"),
            CallbackQueryHandler(A.edit_start, pattern=r"^qe:"),
            CallbackQueryHandler(A.set_start, pattern=r"^set:(" + "|".join(A.NUM_SETTINGS) + r")$"),
        ],
        states={
            A.Q_TEXT: [MessageHandler(TEXT, A.q_text)],
            A.Q_IMAGE: [MessageHandler(ONLY_MSG & (filters.PHOTO | (filters.TEXT & ~filters.COMMAND)), A.q_image), skip(A.q_image_skip)],
            A.Q_OPTS: [MessageHandler(TEXT, A.q_opts)],
            A.Q_CORRECT: [CallbackQueryHandler(A.q_correct, pattern=r"^cor:\d+$")],
            A.Q_EXPL: [MessageHandler(TEXT, A.q_expl), skip(A.q_expl_skip)],
            A.Q_TIME: [MessageHandler(TEXT, A.q_time), skip(A.q_time_skip)],
            A.Q_POINTS: [MessageHandler(TEXT, A.q_points), skip(A.q_points_skip)],
            A.S_VALUE: [MessageHandler(TEXT, A.set_value)],
            A.TEAM_NAME: [MessageHandler(TEXT, A.create_team_name)],
            A.IMPORT: [MessageHandler(ONLY_MSG & (filters.Document.ALL | (filters.TEXT & ~filters.COMMAND)), A.import_data)],
            A.AI_INPUT: [MessageHandler(ONLY_MSG & (filters.Document.ALL | (filters.TEXT & ~filters.COMMAND)), A.ai_import_parse)],
            A.AI_REVIEW: [
                MessageHandler(ONLY_MSG & filters.PHOTO, A.ai_import_image),
                CallbackQueryHandler(A.ai_import_review, pattern=r"^ai:(confirm|cancel)$"),
            ],
            A.BCAST: [MessageHandler(TEXT, A.bcast_send)],
            A.EDIT: [MessageHandler(ONLY_MSG & (filters.PHOTO | (filters.TEXT & ~filters.COMMAND)), A.edit_value)],
        },
        fallbacks=cmd_fallbacks
        + [CallbackQueryHandler(A.leave_to_admin, pattern=r"^(adm:(menu|list|start|status|lb|teams|export|settings|resume)|q:|t:|sel:|pv:|ctl:|tog:|h:)")],
        allow_reentry=True,
    )
    user_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", U.cmd_start),
            CommandHandler("join", U.join_start),
            CommandHandler("createteam", U.create_member_team_start),
            CallbackQueryHandler(U.join_start, pattern=r"^u:join$"),
            CallbackQueryHandler(U.create_member_team_start, pattern=r"^u:newteam$"),
        ],
        states={
            U.U_CODE: [MessageHandler(TEXT, U.join_code)],
            U.U_TEAM_NAME: [MessageHandler(TEXT, U.create_member_team_name)],
        },
        fallbacks=cmd_fallbacks + [
            CommandHandler("createteam", U.create_member_team_start),
            CallbackQueryHandler(U.create_member_team_start, pattern=r"^u:newteam$"),
            CallbackQueryHandler(leave_to_user, pattern=r"^u:(team|top|arena|menu|leave|leaveok|lead|setlead)"),
        ],
    )
    app.add_handler(admin_conv)
    app.add_handler(user_conv)
    app.add_handler(CommandHandler("start", U.cmd_start))
    app.add_handler(CommandHandler("help", U.cmd_help))
    app.add_handler(CommandHandler("admin", A.cmd_admin))
    app.add_handler(CommandHandler("team", U.cmd_team))
    app.add_handler(CommandHandler("top", U.cmd_top))
    app.add_handler(CommandHandler("arena", U.cmd_arena))
    app.add_handler(CommandHandler("createteam", U.create_member_team_start))
    app.add_handler(CommandHandler("leave", U.cmd_leave))
    app.add_handler(CallbackQueryHandler(on_vote, pattern=r"^v:"))
    app.add_handler(CallbackQueryHandler(U.user_cb, pattern=r"^u:"))
    app.add_handler(CallbackQueryHandler(A.admin_cb, pattern=r"^(adm|q|t|sel|pv|tog|ctl|h):"))
    app.add_handler(MessageHandler(ONLY_MSG & filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.StatusUpdate.ALL, U.relay))
    app.add_error_handler(on_error)
    return app


def main():
    if not TOKEN:
        raise SystemExit("حط BOT_TOKEN في ملف .env")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS فاضي! محدش هيقدر يدخل لوحة الأدمن.")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
