"""بنية مشتركة: ثوابت اللوحة والقائمة الرئيسية."""

from telegram import Update
from telegram.ext import ContextTypes

from .. import engine
from ..state import RT
from ..teams import touch_user
from ..ui import BLUE, GREEN, admin_only, btn, rows, show

PANEL = "🎛 <b>لوحة تحكم الأدمن</b>"


BACK = rows([btn("🔙 اللوحة", "adm:menu")])


(Q_TEXT, Q_IMAGE, Q_OPTS, Q_CORRECT, Q_EXPL, Q_TIME, Q_POINTS, S_VALUE, IMPORT, BCAST, EDIT) = range(11)
TEAM_NAME = 200
AI_INPUT, AI_REVIEW, AI_EDIT = 300, 301, 302


def admin_menu():
    lines = [
        [btn("➕ إضافة سؤال", "adm:addq", GREEN), btn("📋 الأسئلة", "adm:list:0", BLUE)],
        [btn("🚀 بدء مسابقة", "adm:start", GREEN), btn("📊 حالة المسابقة", "adm:status", BLUE)],
    ]
    if engine.interrupted_contest() and not RT.get("c"):
        lines.append([btn("♻️ استكمال المسابقة المقطوعة", "adm:resume", GREEN)])
    lines += [
        [btn("🏆 الترتيب", "adm:lb", BLUE), btn("👥 الفرق", "adm:teams", BLUE)],
        [btn("➕ إنشاء فريق", "adm:teamadd", GREEN)],
        [btn("🗂 سجل المسابقات", "h:list", BLUE), btn("📤 تصدير آخر نتائج", "adm:export", BLUE)],
        [btn("📥 استيراد أسئلة", "adm:import", BLUE), btn("📢 رسالة للجميع", "adm:bcast", BLUE)],
        [btn("✨ تحويل نص إلى كويز بالذكاء الاصطناعي", "adm:aiimport", GREEN)],
        [btn("⚙️ الإعدادات", "adm:settings")],
    ]
    return rows(*lines)


@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update.effective_user)
    await show(update, PANEL, admin_menu())
