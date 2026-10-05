"""توزيع أزرار الأدمن على الوحدات."""

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .. import engine
from ..db import get_setting, set_setting
from ..state import RT
from ..ui import BLUE, RED, admin_only, btn, rows, safe_send, show
from .common import PANEL, admin_menu
from .contest import (
    export_results,
    history_cb,
    init_selection,
    preview_cb,
    selection_cb,
    show_admin_board,
    show_selection,
    show_status,
)
from .questions import clear_ai_review_preview, list_questions, question_cb
from .settings import show_settings
from .teams import list_teams, team_cb


@admin_only
async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    p = q.data.split(":")
    k, app = p[0], context.application
    if k == "pv":
        await preview_cb(update, context, p)
        return
    await q.answer()
    if k == "adm":
        act = p[1]
        if act == "menu":
            await show(update, PANEL, admin_menu())
        elif act == "list":
            await list_questions(update, int(p[2]) if len(p) > 2 else 0)
        elif act == "start":
            init_selection(context)
            await show_selection(update, context)
        elif act == "status":
            await show_status(update)
        elif act == "lb":
            await show_admin_board(update)
        elif act == "teams":
            await list_teams(update)
        elif act == "export":
            await export_results(update, context)
        elif act == "settings":
            await show_settings(update)
        elif act == "resume":
            _ok, msg = await engine.resume_interrupted(app)
            await safe_send(context.bot, update.effective_chat.id, msg, rows([btn("📊 حالة المسابقة", "adm:status", BLUE)]))
    elif k == "h":
        await history_cb(update, context, p)
    elif k == "q":
        await question_cb(update, context, p)
    elif k == "t":
        await team_cb(update, context, p)
    elif k == "sel":
        await selection_cb(update, context, p)
    elif k == "tog":
        if p[1] in ("show_lb", "manual"):
            set_setting(p[1], "0" if get_setting(p[1]) == "1" else "1")
        await show_settings(update)
    elif k == "ctl":
        c = RT.get("c")
        if not c:
            await q.answer("مفيش مسابقة شغالة", show_alert=True)
            return
        act = p[1]
        if act == "skip":
            await engine.end_question(app, c["id"])
        elif act == "next":
            await engine.next_question(app)
        elif act == "pause":
            await engine.pause_contest(app)
            await show_status(update)
        elif act == "resume":
            await engine.resume_contest(app)
            await show_status(update)
        elif act == "manual":
            await engine.toggle_manual(app)
            await show_status(update)
        elif act == "stop":
            await show(update, "⚠️ متأكد إنك عايز توقف المسابقة نهائيًا؟ مش هينفع تكمّلها بعد كده.", rows([btn("⏹ أيوه، أوقفها", "ctl:stopok", RED), btn("🔙 رجوع", "adm:status")]))
        elif act == "stopok":
            await engine.finish_contest(app, "stopped")


async def leave_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clear_ai_review_preview(context)
    for k in ("nq", "setkey", "edit", "ai_import_ready", "ai_import_flagged", "ai_edit_target"):
        context.user_data.pop(k, None)
    await admin_cb(update, context)
    return ConversationHandler.END
