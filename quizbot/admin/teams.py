"""إدارة الفرق ورسالة البث للجميع."""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .. import engine
from ..config import MAX_TEAM_SIZE
from ..db import db
from ..teams import (
    create_empty_team,
    delete_team,
    remove_member,
    team_members,
    team_text,
)
from ..ui import BLUE, RED, admin_only, btn, esc, rows, safe_send, show
from .common import BCAST, TEAM_NAME


async def list_teams(update: Update):
    ts = db.q("SELECT t.id, t.name, (SELECT COUNT(*) FROM members m WHERE m.team_id=t.id) n FROM teams t ORDER BY t.id")
    if not ts:
        await show(update, "👥 مفيش فرق لسه.", rows([btn("➕ إنشاء فريق", "adm:teamadd", BLUE)], [btn("🔙 اللوحة", "adm:menu")]))
        return
    kb = [[btn(f"{t['name']} ({t['n']}/{MAX_TEAM_SIZE})", f"t:view:{t['id']}", BLUE)] for t in ts[:40]]
    kb.append([btn("➕ إنشاء فريق", "adm:teamadd", BLUE)])
    kb.append([btn("🔙 اللوحة", "adm:menu")])
    await show(update, f"👥 <b>الفرق</b> ({len(ts)})", rows(*kb))


@admin_only
async def create_team_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    await update.effective_message.reply_text("اكتب اسم الفريق (من 2 لـ 30 حرف). أول عضو يدخل بالكود هيبقى قائد الفريق.\n/cancel للإلغاء")
    return TEAM_NAME


@admin_only
async def create_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    team, error = create_empty_team(update.message.text)
    if error:
        await update.message.reply_text(error + "\nجرّب اسم تاني أو /cancel")
        return TEAM_NAME
    await update.message.reply_text(
        "✅ تم إنشاء الفريق. أول عضو ينضم هيبقى القائد تلقائيًا.\n\n" + team_text(team, context.bot.username)
    )
    return ConversationHandler.END


async def team_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    act, tid = p[1], int(p[2])
    t = db.q("SELECT * FROM teams WHERE id=?", (tid,), one=True)
    if not t:
        await list_teams(update)
        return
    app = context.application
    if act == "kick":
        uid = int(p[3])
        deleted = remove_member(uid, tid)
        await safe_send(context.bot, uid, f"🚫 الأدمن شالك من فريق {esc(t['name'])}.")
        await engine.sync_membership(app, tid)
        if deleted:
            await list_teams(update)
            return
        act = "view"
        t = db.q("SELECT * FROM teams WHERE id=?", (tid,), one=True)
    if act == "view":
        kb = [[btn(f"❌ طرد {m['name'][:25]}", f"t:kick:{tid}:{m['id']}", RED)] for m in team_members(tid)]
        kb.append([btn("🗑 حذف الفريق", f"t:del:{tid}", RED), btn("🔙 الفرق", "adm:teams")])
        await show(update, team_text(t, context.bot.username), rows(*kb))
    elif act == "del":
        await show(update, f"متأكد من حذف فريق <b>{esc(t['name'])}</b> بكل أعضائه؟", rows([btn("✅ تأكيد", f"t:delok:{tid}", RED), btn("❌ إلغاء", f"t:view:{tid}")]))
    elif act == "delok":
        for uid in delete_team(tid):
            await safe_send(context.bot, uid, f"🗑 الأدمن حذف فريق {esc(t['name'])}.")
        await list_teams(update)


@admin_only
async def bcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    n = db.q("SELECT COUNT(*) n FROM users", one=True)["n"]
    await update.callback_query.message.reply_text(f"📢 اكتب الرسالة اللي هتتبعت لكل المستخدمين ({n} مستخدم).\n/cancel للإلغاء")
    return BCAST


async def bcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📢 <b>رسالة من الإدارة</b>\n\n" + esc(update.message.text)
    res = await asyncio.gather(*(safe_send(context.bot, r["id"], text) for r in db.q("SELECT id FROM users")))
    ok = sum(1 for r in res if r)
    await update.message.reply_text(f"✅ وصلت لـ {ok} · ❌ فشلت مع {len(res) - ok}")
    return ConversationHandler.END
