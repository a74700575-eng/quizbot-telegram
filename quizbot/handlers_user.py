"""أوامر وأزرار المستخدمين: الفرق، الترتيب، ونقاش الفريق."""
import asyncio

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from .board import board_rows, board_text, latest_contest
from .config import MAX_TEAM_SIZE
from .db import get_setting
from .engine import sync_membership
from .teams import (
    join_team,
    leave_team,
    notify_team,
    set_leader,
    team_members,
    team_of,
    team_text,
    touch_user,
)
from .ui import BLUE, GREEN, RED, btn, esc, is_admin, rows, show

U_CODE = 101


def user_menu():
    return rows(
        [btn("🔑 إدخال كود الفريق", "u:join", BLUE)],
        [btn("👥 فريقي", "u:team", BLUE), btn("🏆 الترتيب", "u:top", BLUE)],
    )


HELP = (
    "🎮 <b>بوت المسابقات</b>\n\n"
    "• لو دي أول مرة، ابعت كود الفريق اللي الأدمن ادهولك\n"
    "• /join CODE — الانضمام لفريق بالكود\n"
    "• /team — بيانات فريقك وكود الدعوة\n"
    "• /leave — مغادرة الفريق\n"
    "• /top — ترتيب الفرق\n\n"
    f"الفريق حد أقصى {MAX_TEAM_SIZE} أعضاء. رسائلك هنا بتتوصل للفريق كرسائل مُعاد توجيهها باسم حسابك، حسب إعدادات خصوصيتك.\n"
    "وقت السؤال كل عضو يختار إجابته، وإجابة الفريق النهائية هي <b>الأغلبية</b> "
    "(ولو تعادلوا يرجّح صوت قائد الفريق 👑)."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    touch_user(u)
    if context.args and context.args[0].startswith("join_"):
        if await do_join(update, context, context.args[0][5:]):
            return ConversationHandler.END
        if team_of(u.id):
            await send_team_view(update, context)
            return ConversationHandler.END
    if team_of(u.id):
        await send_team_view(update, context)
        return ConversationHandler.END
    if is_admin(u.id):
        await update.message.reply_text(
            f"أهلاً {esc(u.first_name)} 👋\n\n" + HELP + "\n\n🛠 أنت أدمن: افتح لوحة التحكم بـ /admin",
            reply_markup=user_menu(),
        )
        return ConversationHandler.END
    await update.message.reply_text("أهلاً بك! 🔑 ابعت كود الفريق اللي الأدمن ادهولك عشان تنضم.")
    return U_CODE


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, reply_markup=user_menu())


async def send_team_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = team_of(update.effective_user.id)
    if not t:
        await show(update, "لازم تدخل كود الفريق اللي الأدمن ادهولك عشان تنضم 👇", user_menu())
        return
    line1 = [btn("🏆 الترتيب", "u:top", BLUE)]
    if t["leader_id"] == update.effective_user.id and len(team_members(t["id"])) > 1:
        line1.insert(0, btn("👑 تغيير القائد", "u:lead", BLUE))
    await show(update, team_text(t, context.bot.username), rows(line1, [btn("🚪 مغادرة الفريق", "u:leave", RED)]))


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update.effective_user)
    await send_team_view(update, context)


async def send_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if get_setting("show_lb") != "1" and not is_admin(uid):
        await show(update, "👀 الترتيب مخفي حاليًا بقرار الأدمن.")
        return
    cid = latest_contest()
    if not cid:
        await show(update, "مفيش مسابقة لسه.")
        return
    t = team_of(uid)
    await show(update, board_text(board_rows(cid), highlight=t["id"] if t else None))


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_top(update, context)


async def ask_leave(update: Update):
    if not team_of(update.effective_user.id):
        await show(update, "انت مش في فريق أصلاً.")
        return
    kb = rows([btn("✅ أيوه، اخرج", "u:leaveok", RED), btn("❌ لا", "u:team")])
    await show(update, "متأكد إنك عايز تغادر الفريق؟", kb)


async def cmd_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_leave(update)


async def user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    touch_user(q.from_user)
    act = q.data.split(":")[1]
    if act == "menu":
        await show(update, HELP, user_menu())
    elif act == "team":
        await send_team_view(update, context)
    elif act == "top":
        await send_top(update, context)
    elif act == "leave":
        await ask_leave(update)
    elif act in ("lead", "setlead"):
        await change_leader(update, context, q.data.split(":"))
    elif act == "leaveok":
        tid, name, deleted = leave_team(q.from_user.id)
        if not tid:
            await show(update, "انت مش في فريق.")
            return
        await show(update, f"🚪 خرجت من فريق <b>{esc(name)}</b>.", user_menu())
        if not deleted:
            await notify_team(context.bot, tid, f"➖ <b>{esc(q.from_user.full_name)}</b> غادر الفريق.")
        await sync_membership(context.application, tid)


async def change_leader(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    uid = update.effective_user.id
    t = team_of(uid)
    if not t or t["leader_id"] != uid:
        await show(update, "👑 بس قائد الفريق يقدر يغيّر القائد.", user_menu())
        return
    if p[1] == "lead":
        kb = [[btn(m["name"][:30], f"u:setlead:{m['id']}", GREEN)] for m in team_members(t["id"]) if m["id"] != uid]
        kb.append([btn("🔙 رجوع", "u:team")])
        await show(update, "👑 اختار القائد الجديد (صوته بيحسم التعادل في الإجابة):", rows(*kb))
        return
    new = int(p[2])
    if new not in {m["id"] for m in team_members(t["id"])}:
        await show(update, "العضو ده مش في فريقك.", user_menu())
        return
    set_leader(t["id"], new)
    name = next(m["name"] for m in team_members(t["id"]) if m["id"] == new)
    await notify_team(context.bot, t["id"], f"👑 القائد الجديد للفريق: <b>{esc(name)}</b>")
    await sync_membership(context.application, t["id"])
    await send_team_view(update, context)


async def do_join(update: Update, context: ContextTypes.DEFAULT_TYPE, code):
    u = update.effective_user
    t, err = join_team(u.id, code)
    if err:
        await update.effective_message.reply_text(err)
        return False
    await update.effective_message.reply_text(f"✅ اتضممت لفريق <b>{esc(t['name'])}</b>\n\n" + team_text(t, context.bot.username))
    await notify_team(context.bot, t["id"], f"➕ <b>{esc(u.full_name)}</b> انضم للفريق.", exclude=u.id)
    await sync_membership(context.application, t["id"], new_uid=u.id)
    return True


async def join_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    touch_user(update.effective_user)
    if update.callback_query:
        await update.callback_query.answer()
    if context.args:
        await do_join(update, context, context.args[0])
        return ConversationHandler.END
    if team_of(update.effective_user.id):
        await update.effective_message.reply_text("انت بالفعل في فريق. اخرج منه الأول بـ /leave")
        return ConversationHandler.END
    await update.effective_message.reply_text("ابعت كود الفريق (8 حروف/أرقام)\n/cancel للإلغاء")
    return U_CODE


async def join_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = await do_join(update, context, update.message.text)
    return ConversationHandler.END if ok else U_CODE


# ── نقاش الفريق (Relay) ──
async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, u = update.effective_message, update.effective_user
    if not msg:
        return
    touch_user(u)
    t = team_of(u.id)
    if not t:
        hint = "افتح لوحة التحكم بـ /admin" if is_admin(u.id) else "أرسل /start ثم أدخل كود الفريق الذي أعطاك الأدمن إياه."
        await msg.reply_text(hint, reply_markup=None if is_admin(u.id) else user_menu())
        return
    others = [m["id"] for m in team_members(t["id"]) if m["id"] != u.id]
    if not others:
        await msg.reply_text("انت لوحدك في الفريق حاليًا، ابعت كود الفريق لزمايلك 🔑")
        return
    async def send(uid):
        try:
            # Telegram forward preserves native sender attribution instead of impersonating the member.
            await msg.forward(chat_id=uid)
        except TelegramError:
            try:
                name = f"@{u.username}" if u.username else esc(u.full_name)
                header = f"💬 <b>{name}</b>:"
                if msg.text:
                    await context.bot.send_message(uid, f"{header}\n{esc(msg.text)}")
                else:
                    await context.bot.send_message(uid, header)
                    await msg.copy(uid)
            except TelegramError:
                pass

    await asyncio.gather(*(send(uid) for uid in others))
    try:
        await msg.set_reaction("👍")
    except TelegramError:
        pass
