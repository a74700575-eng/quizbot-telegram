"""الإعدادات."""

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ..db import get_setting, set_setting
from ..ui import BLUE, admin_only, btn, rows, show
from .common import S_VALUE

NUM_SETTINGS = {
    "time": ("وقت السؤال الافتراضي", 5, 600, "ث"),
    "points": ("نقاط السؤال الافتراضية", 1, 1000, ""),
    "gap": ("الفاصل بين الأسئلة", 0, 120, "ث"),
    "speed_bonus": ("أقصى مكافأة سرعة كنسبة من نقاط السؤال (0 = بدون مكافأة)", 0, 200, "%"),
    "tick": ("تحديث العدّاد الحي كل كام ثانية (0 = إيقاف، أو من 5 لـ 60)", 0, 60, "ث"),
}


async def show_settings(update: Update):
    s = get_setting
    on = lambda k: "✅" if s(k) == "1" else "❌"
    num = lambda k, label, unit: [btn(f"{label}: {s(k)}{unit}", f"set:{k}")]
    kb = rows(
        num("time", "⏱ وقت السؤال", " ث"),
        num("points", "🏅 نقاط السؤال", ""),
        num("gap", "⏳ الفاصل بين الأسئلة", " ث"),
        num("speed_bonus", "⚡ مكافأة السرعة", "%"),
        num("tick", "⏲ تحديث العدّاد", " ث"),
        [btn(f"👁 عرض الترتيب: {on('show_lb')}", "tog:show_lb", BLUE)],
        [btn(f"🖐 التالي يدوي (افتراضي): {on('manual')}", "tog:manual", BLUE)],
        [btn("🔙 اللوحة", "adm:menu")],
    )
    await show(update, "⚙️ <b>الإعدادات</b>", kb)


@admin_only
async def set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = q.data.split(":")[1]
    context.user_data["setkey"] = key
    label, lo, hi, _ = NUM_SETTINGS[key]
    await q.message.reply_text(f"اكتب القيمة الجديدة: {label} ({lo}–{hi})\nالحالية: {get_setting(key)}\n/cancel للإلغاء")
    return S_VALUE


async def set_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("setkey")
    t = update.message.text.strip()
    if not key:
        return ConversationHandler.END
    _, lo, hi, _ = NUM_SETTINGS[key]
    bad = not t.isdigit() or not lo <= int(t) <= hi or (key == "tick" and 0 < int(t) < 5)
    if bad:
        await update.message.reply_text(f"اكتب رقم من {lo} لـ {hi}" + (" (أو 0 للإيقاف)" if key == "tick" else "") + ".")
        return S_VALUE
    set_setting(key, int(t))
    context.user_data.pop("setkey", None)
    await update.message.reply_text("✅ تم الحفظ.", reply_markup=rows([btn("⚙️ الإعدادات", "adm:settings", BLUE)]))
    return ConversationHandler.END
