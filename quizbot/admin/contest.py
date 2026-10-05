"""بدء المسابقة، الحالة، الترتيب، والتصدير."""
import csv
import io
import json
import random

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from .. import engine
from ..board import board_rows, board_text, latest_contest
from ..config import LETTERS, PER_PAGE
from ..db import db, get_setting
from ..questions import get_question
from ..scoring import ranked
from ..state import RT
from ..ui import BLUE, GREEN, RED, btn, esc, fmt_time, rows, safe_send, show
from .common import BACK


def init_selection(context):
    context.user_data["sel"] = {
        "ids": {r["id"] for r in db.q("SELECT id FROM questions")}, "page": 0,
        "shuf": False, "shufo": False, "manual": get_setting("manual") == "1",
    }
    return context.user_data["sel"]


def _make_preview(s):
    ids = sorted(s["ids"])
    if s["shuf"]:
        random.shuffle(ids)
    items = []
    for qid in ids:
        question = get_question(qid)
        if not question:
            continue
        options = json.loads(question["options"])
        correct = question["correct"]
        if s["shufo"]:
            permutation = list(range(len(options)))
            random.shuffle(permutation)
            options = [options[i] for i in permutation]
            correct = permutation.index(correct)
        items.append(
            {
                "id": qid,
                "text": question["text"],
                "image": question["image"],
                "options": options,
                "correct": correct,
            }
        )
    return {"items": items, "index": 0, "selected": None}


def _preview_markup(item, index, total, selected=None):
    choices = [
        btn(
            f"{'✅ ' if choice == selected else ''}{LETTERS[choice]}) {option}"[:64],
            f"pv:a:{item['id']}:{choice}",
            GREEN,
        )
        for choice, option in enumerate(item["options"])
    ]
    navigation = []
    if index > 0:
        navigation.append(btn("⬅️ السابق", f"pv:prev:{index}", BLUE))
    if index + 1 < total:
        navigation.append(btn("التالي ➡️", f"pv:next:{index}", BLUE))
    navigation.append(btn("✅ إنهاء المعاينة", f"pv:close:{index}", RED))
    return rows(*[[choice] for choice in choices], navigation)


async def _send_preview_question(bot, chat_id, preview):
    item = preview["items"][preview["index"]]
    text = esc(item["text"])
    markup = _preview_markup(item, preview["index"], len(preview["items"]), preview.get("selected"))
    if item["image"]:
        try:
            await bot.send_photo(chat_id, item["image"], caption=text, reply_markup=markup)
            return
        except TelegramError:
            pass
    await safe_send(bot, chat_id, text, markup)


async def _disable_preview_controls(query):
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass


async def preview_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    query = update.callback_query
    preview = context.user_data.get("quiz_preview")
    if not preview:
        await query.answer("انتهت المعاينة. ارجع لاختيار الأسئلة وابدأ واحدة جديدة.", show_alert=True)
        return

    action = p[1]
    index = preview["index"]
    item = preview["items"][index]
    if action == "a":
        try:
            qid, choice = int(p[2]), int(p[3])
        except (IndexError, ValueError):
            await query.answer("اختيار غير صالح.", show_alert=True)
            return
        if qid != item["id"] or not 0 <= choice < len(item["options"]):
            await query.answer("هذا الزر من سؤال سابق في المعاينة.", show_alert=True)
            return
        preview["selected"] = choice
        if choice == item["correct"]:
            answer_text = "✅ إجابة صحيحة — معاينة فقط، بدون نقاط أو مسابقة."
        else:
            answer_text = f"❌ إجابة غير صحيحة — الصحيح {LETTERS[item['correct']]}) {item['options'][item['correct']]} (معاينة فقط)."
        await query.answer(answer_text, show_alert=True)
        try:
            await query.edit_message_reply_markup(
                reply_markup=_preview_markup(item, index, len(preview["items"]), choice)
            )
        except TelegramError:
            pass
        return

    try:
        expected_index = int(p[2])
    except (IndexError, ValueError):
        await query.answer("زر معاينة غير صالح.", show_alert=True)
        return
    if expected_index != index:
        await query.answer("هذه بطاقة قديمة؛ استخدم أزرار آخر سؤال.", show_alert=True)
        return
    await query.answer()
    if action == "close":
        await _disable_preview_controls(query)
        context.user_data.pop("quiz_preview", None)
        await show_selection(update, context)
        return
    if action not in ("prev", "next"):
        return
    new_index = index - 1 if action == "prev" else index + 1
    if not 0 <= new_index < len(preview["items"]):
        return
    await _disable_preview_controls(query)
    preview["index"] = new_index
    preview["selected"] = None
    await _send_preview_question(context.bot, query.message.chat_id, preview)


async def show_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = context.user_data.get("sel") or init_selection(context)
    qs = db.q("SELECT id, text FROM questions ORDER BY id")
    if not qs:
        await show(update, "📋 مفيش أسئلة. ضيف أسئلة الأول.", BACK)
        return
    s["ids"] &= {r["id"] for r in qs}
    pages = (len(qs) + PER_PAGE - 1) // PER_PAGE
    s["page"] = max(0, min(s["page"], pages - 1))
    kb = [
        [btn(("✅ " if r["id"] in s["ids"] else "⬜ ") + f"{r['id']}. {r['text'][:35]}", f"sel:t:{r['id']}", BLUE if r["id"] in s["ids"] else None)]
        for r in qs[s["page"] * PER_PAGE : (s["page"] + 1) * PER_PAGE]
    ]
    nav = []
    if s["page"] > 0:
        nav.append(btn("◀️", f"sel:p:{s['page'] - 1}"))
    if s["page"] < pages - 1:
        nav.append(btn("▶️", f"sel:p:{s['page'] + 1}"))
    if nav:
        kb.append(nav)
    on = lambda v: "✅" if v else "❌"
    kb.append([btn("الكل", "sel:all"), btn("لا شيء", "sel:none")])
    kb.append([btn(f"🔀 الأسئلة: {on(s['shuf'])}", "sel:shuf", BLUE), btn(f"🔀 الاختيارات: {on(s['shufo'])}", "sel:shufo", BLUE)])
    kb.append([btn(f"🖐 التالي يدوي: {on(s['manual'])}", "sel:man", BLUE)])
    kb.append([btn(f"▶️ متابعة ({len(s['ids'])} سؤال)", "sel:go", GREEN), btn("🔙", "adm:menu")])
    await show(update, f"🚀 <b>بدء مسابقة</b>\nاختار الأسئلة اللي هتدخل المسابقة (صفحة {s['page'] + 1}/{pages}):", rows(*kb))


async def selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    s = context.user_data.get("sel") or init_selection(context)
    act = p[1]
    if act == "t":
        s["ids"] ^= {int(p[2])}
    elif act == "p":
        s["page"] = int(p[2])
    elif act == "all":
        s["ids"] = {r["id"] for r in db.q("SELECT id FROM questions")}
    elif act == "none":
        s["ids"] = set()
    elif act in ("shuf", "shufo", "man"):
        key = "manual" if act == "man" else act
        s[key] = not s[key]
    elif act == "go":
        if not s["ids"]:
            await update.callback_query.answer("اختار سؤال واحد على الأقل", show_alert=True)
            return
        teams = db.q("SELECT COUNT(DISTINCT team_id) n FROM members", one=True)["n"]
        mem = db.q("SELECT COUNT(*) n FROM members", one=True)["n"]
        opts = [x for x, v in (("ترتيب الأسئلة عشوائي", s["shuf"]), ("ترتيب الاختيارات عشوائي", s["shufo"]), ("التالي يدوي", s["manual"])) if v]
        await show(
            update,
            f"جاهز تبدأ؟\n\n📋 الأسئلة: {len(s['ids'])}\n👥 الفرق: {teams} · الأعضاء: {mem}\n⏱ الوقت الافتراضي: {get_setting('time')} ث"
            + (f"\n⚙️ {' · '.join(opts)}" if opts else ""),
            rows(
                [btn("✅ ابدأ الآن", "sel:launch", GREEN)],
                [btn("🎮 تجربة الأسئلة قبل البدء", "sel:preview", BLUE)],
                [btn("🔙 تعديل الأسئلة", "sel:p:0")],
            ),
        )
        return
    elif act == "preview":
        if not s["ids"]:
            await update.callback_query.answer("اختار سؤالًا واحدًا على الأقل للمعاينة.", show_alert=True)
            return
        preview = _make_preview(s)
        if not preview["items"]:
            await update.callback_query.answer("الأسئلة المختارة لم تعد موجودة.", show_alert=True)
            return
        context.user_data["quiz_preview"] = preview
        await update.callback_query.edit_message_text(
            "🎮 <b>معاينة خاصة للأدمن</b> — الاختيارات لا تبدأ المسابقة ولا تُحتسب نقاطًا.",
            reply_markup=None,
        )
        await _send_preview_question(context.bot, update.effective_chat.id, preview)
        return
    elif act == "launch":
        ids = sorted(s["ids"])
        if s["shuf"]:
            random.shuffle(ids)
        await show(update, "⏳ جاري البدء…")
        _ok, msg = await engine.start_contest(context.application, ids, s["shufo"], s["manual"])
        await safe_send(context.bot, update.effective_chat.id, msg, rows([btn("📊 حالة المسابقة", "adm:status", BLUE)]))
        return
    await show_selection(update, context)


async def show_status(update: Update):
    c = RT.get("c")
    if not c:
        cid = latest_contest()
        txt = "💤 مفيش مسابقة شغالة."
        if cid:
            txt += "\n\nآخر مسابقة:\n" + board_text(board_rows(cid), admin=True, limit=15)
        extra = [[btn("♻️ استكمال المسابقة المقطوعة", "adm:resume", GREEN)]] if engine.interrupted_contest() else []
        await show(update, txt, rows(*extra, [btn("🔙 اللوحة", "adm:menu")]))
        return
    lines = [f"📊 <b>المسابقة #{c['id']}</b>", f"السؤال {c['idx'] + 1}/{len(c['qids'])}"]
    if c["paused"]:
        lines.append("⏸ متوقفة مؤقتًا")
    elif c["open"]:
        voted = db.q("SELECT COUNT(*) n FROM votes WHERE contest_id=? AND qid=?", (c["id"], c["qid"]), one=True)["n"]
        total = db.q("SELECT COUNT(*) n FROM members", one=True)["n"]
        lines += [f"🟢 مفتوح — باقي {engine._left(c)} ثانية", f"🗳 صوّت {voted} من {total}"]
    else:
        lines.append("⏸ بين الأسئلة")
    lines += ["", board_text(board_rows(c["id"]), admin=True, limit=15)]
    kb = engine.ctl_markup(c)
    await show(update, "\n".join(lines), rows(*kb.inline_keyboard, [btn("🔄 تحديث", "adm:status", BLUE)]))


async def show_admin_board(update: Update):
    cid = latest_contest()
    if not cid:
        await show(update, "مفيش مسابقة لسه.", BACK)
        return
    await show(update, board_text(board_rows(cid), admin=True, limit=40), rows([btn("🔄 تحديث", "adm:lb", BLUE), btn("🔙 اللوحة", "adm:menu")]))


async def export_results(update: Update, context: ContextTypes.DEFAULT_TYPE, cid=None):
    cid = cid or latest_contest()
    if not cid:
        await show(update, "مفيش نتائج لسه.", BACK)
        return
    res = db.q(
        "SELECT qid, q_text, team_name, chosen_text, correct_text, is_correct, points, bonus, voters, members "
        "FROM results WHERE contest_id=? ORDER BY qid, team_name",
        (cid,),
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["question_id", "question", "team", "team_answer", "correct_answer", "is_correct", "points", "speed_bonus", "voters", "members"])
    for r in res:
        w.writerow([r["qid"], r["q_text"], r["team_name"], r["chosen_text"], r["correct_text"], r["is_correct"], r["points"], r["bonus"], r["voters"], r["members"]])
    w.writerow([])
    w.writerow(["ranking", "team", "points", "correct"])
    for rank, r in ranked(board_rows(cid)):
        w.writerow([rank, r["name"], r["pts"], r["ok"]])
    await context.bot.send_document(
        update.effective_chat.id, document=buf.getvalue().encode("utf-8-sig"), filename=f"contest_{cid}_results.csv",
        caption=f"📤 نتائج المسابقة #{cid}",
    )


STATUS_ICON = {"finished": "🏁", "stopped": "⏹", "interrupted": "⚠️", "running": "🟢"}


async def show_history(update: Update):
    cs = db.q("SELECT id, status, started_at FROM contests ORDER BY id DESC LIMIT 10")
    if not cs:
        await show(update, "🗂 مفيش مسابقات لسه.", BACK)
        return
    kb = []
    for c in cs:
        rows_ = board_rows(c["id"])
        top = f" · 🥇 {rows_[0]['name']} ({rows_[0]['pts']})" if rows_ and rows_[0]["pts"] else ""
        kb.append([btn(f"{STATUS_ICON.get(c['status'], '•')} #{c['id']} · {fmt_time(c['started_at'])}{top}"[:60], f"h:view:{c['id']}", BLUE)])
    kb.append([btn("🔙 اللوحة", "adm:menu")])
    await show(update, "🗂 <b>آخر المسابقات</b> (اختار واحدة لعرض ترتيبها أو تصديرها):", rows(*kb))


async def history_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    act = p[1]
    if act == "list":
        await show_history(update)
        return
    cid = int(p[2])
    c = db.q("SELECT * FROM contests WHERE id=?", (cid,), one=True)
    if not c:
        await show_history(update)
        return
    if act == "exp":
        await export_results(update, context, cid)
        return
    n = len(json.loads(c["qids"]))
    head = f"{STATUS_ICON.get(c['status'], '•')} <b>المسابقة #{cid}</b> · {fmt_time(c['started_at'])} · {n} سؤال"
    await show(
        update,
        head + "\n\n" + board_text(board_rows(cid), admin=True, limit=30, title="🏆 <b>الترتيب</b>"),
        rows([btn("📤 تصدير CSV", f"h:exp:{cid}", GREEN)], [btn("🔙 السجل", "h:list")]),
    )
