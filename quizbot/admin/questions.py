"""إدارة الأسئلة: العرض، الإضافة، الاستيراد."""
import json
import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, ConversationHandler

from ..ai_quiz import MAX_AI_INPUT_CHARS, MAX_AI_QUESTIONS, AIQuizError, parse_quiz_text
from ..config import AI_API_KEY, LETTERS, PER_PAGE
from ..db import db, get_setting
from ..questions import (
    MAX_EXPL,
    MAX_OPT,
    MAX_Q,
    get_question,
    import_questions,
    insert_question,
)
from ..ui import BLUE, GREEN, RED, admin_only, btn, esc, rows, safe_send, show
from .common import (
    AI_INPUT,
    AI_REVIEW,
    EDIT,
    IMPORT,
    Q_CORRECT,
    Q_EXPL,
    Q_IMAGE,
    Q_OPTS,
    Q_POINTS,
    Q_TEXT,
    Q_TIME,
)

log = logging.getLogger("quizbot.ai_import")


async def list_questions(update: Update, page=0):
    qs = db.q("SELECT id, text, image FROM questions ORDER BY id")
    if not qs:
        await show(update, "📋 مفيش أسئلة لسه.", rows([btn("➕ إضافة سؤال", "adm:addq", GREEN)], [btn("🔙 اللوحة", "adm:menu")]))
        return
    pages = (len(qs) + PER_PAGE - 1) // PER_PAGE
    page = max(0, min(page, pages - 1))
    kb = [[btn(("🖼 " if r["image"] else "") + f"{r['id']}. {r['text'][:40]}", f"q:view:{r['id']}:{page}", BLUE)] for r in qs[page * PER_PAGE : (page + 1) * PER_PAGE]]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm:list:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("▶️", f"adm:list:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([btn("🔙 اللوحة", "adm:menu")])
    await show(update, f"📋 <b>الأسئلة</b> ({len(qs)}) — صفحة {page + 1}/{pages}", rows(*kb))


async def question_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, p):
    act, qid = p[1], int(p[2])
    page = int(p[3]) if len(p) > 3 else 0
    q = get_question(qid)
    if not q:
        await list_questions(update, 0)
        return
    opts = json.loads(q["options"])
    if act == "view":
        lines = [f"❓ <b>سؤال #{q['id']}</b>", "", esc(q["text"]), ""]
        lines += [("✅ " if i == q["correct"] else "▫️ ") + f"{LETTERS[i]}) {esc(o)}" for i, o in enumerate(opts)]
        lines += ["", f"⏱ الوقت: {q['time_limit'] or get_setting('time') + ' (افتراضي)'} · 🏅 النقاط: {q['points'] or get_setting('points') + ' (افتراضي)'}"]
        if q["explanation"]:
            lines.append(f"📝 {esc(q['explanation'])}")
        top = [btn("🔎 معاينة ككويز", f"q:poll:{qid}:{page}", BLUE)]
        if q["image"]:
            top.append(btn("🖼 الصورة", f"q:img:{qid}:{page}", BLUE))
        await show(
            update, "\n".join(lines),
            rows(top, [btn("✏️ تعديل", f"q:edit:{qid}:{page}", GREEN), btn("🗑 حذف", f"q:del:{qid}:{page}", RED)], [btn("🔙 الأسئلة", f"adm:list:{page}")]),
        )
    elif act == "edit":
        f = lambda key, label: [btn(label, f"qe:{key}:{qid}:{page}", BLUE)]
        await show(
            update, f"✏️ <b>تعديل سؤال #{qid}</b>\nاختار اللي عايز تغيّره (الاختيارات والإجابة الصحيحة: احذف السؤال وأضفه من جديد):",
            rows(f("text", "📝 نص السؤال"), f("expl", "💡 الشرح"), f("time", "⏱ الوقت"), f("points", "🏅 النقاط"), f("image", "🖼 الصورة"),
                 [btn("🔙 رجوع", f"q:view:{qid}:{page}")]),
        )
    elif act == "poll":
        await context.bot.send_poll(
            update.effective_chat.id, question=q["text"][:300], options=opts, type="quiz",
            correct_option_id=q["correct"], explanation=(q["explanation"] or None), is_anonymous=False,
        )
    elif act == "img":
        try:
            await context.bot.send_photo(update.effective_chat.id, q["image"], caption=f"🖼 سؤال #{qid}")
        except TelegramError as e:
            await safe_send(context.bot, update.effective_chat.id, f"⚠️ مقدرتش أعرض الصورة: {esc(str(e))[:150]}")
    elif act == "del":
        await show(update, f"متأكد من حذف السؤال #{qid}؟", rows([btn("✅ تأكيد الحذف", f"q:delok:{qid}:{page}", RED), btn("❌ إلغاء", f"q:view:{qid}:{page}")]))
    elif act == "delok":
        db.x("DELETE FROM questions WHERE id=?", (qid,))
        await list_questions(update, page)


@admin_only
async def addq_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data["nq"] = {}
    await update.effective_message.reply_text(f"➕ <b>سؤال جديد</b>\n\nاكتب نص السؤال (حد أقصى {MAX_Q} حرف)\n/cancel للإلغاء")
    return Q_TEXT


async def q_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not t or len(t) > MAX_Q:
        await update.message.reply_text(f"نص السؤال لازم يكون من 1 لـ {MAX_Q} حرف. جرّب تاني.")
        return Q_TEXT
    context.user_data["nq"]["text"] = t
    await update.message.reply_text("🖼 ابعت <b>صورة</b> للسؤال (أو رابط صورة)، أو /skip لو من غير صورة.")
    return Q_IMAGE


async def q_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if m.photo:
        context.user_data["nq"]["image"] = m.photo[-1].file_id
    elif m.text and m.text.strip().startswith(("http://", "https://")) and len(m.text.strip()) <= 500:
        context.user_data["nq"]["image"] = m.text.strip()
    else:
        await m.reply_text("ابعت صورة أو رابط يبدأ بـ http، أو /skip.")
        return Q_IMAGE
    return await ask_opts(update)


async def q_image_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ask_opts(update)


async def ask_opts(update: Update):
    await update.message.reply_text(f"اكتب الاختيارات، <b>كل اختيار في سطر</b> (من 2 لـ 10، وكل اختيار حد أقصى {MAX_OPT} حرف).")
    return Q_OPTS


async def q_opts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    opts = [l.strip() for l in update.message.text.split("\n") if l.strip()]
    if not 2 <= len(opts) <= 10 or any(len(o) > MAX_OPT for o in opts):
        await update.message.reply_text(f"لازم من 2 لـ 10 اختيارات، كل واحد في سطر وحد أقصى {MAX_OPT} حرف. جرّب تاني.")
        return Q_OPTS
    context.user_data["nq"]["opts"] = opts
    kb = rows(*[[btn(f"{LETTERS[i]}) {o[:30]}", f"cor:{i}", BLUE)] for i, o in enumerate(opts)])
    await update.message.reply_text("اختار الإجابة الصحيحة 👇", reply_markup=kb)
    return Q_CORRECT


async def q_correct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    nq = context.user_data.get("nq")
    if not nq or "opts" not in nq:
        return ConversationHandler.END
    i = int(q.data.split(":")[1])
    nq["correct"] = i
    await q.edit_message_text(f"✅ الإجابة الصحيحة: <b>{LETTERS[i]}) {esc(nq['opts'][i])}</b>")
    await q.message.reply_text(f"اكتب شرح/تعليق يظهر بعد انتهاء السؤال (حد أقصى {MAX_EXPL} حرف) أو /skip للتخطي.")
    return Q_EXPL


async def q_expl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if len(t) > MAX_EXPL:
        await update.message.reply_text(f"الشرح أطول من {MAX_EXPL} حرف. اختصره أو /skip.")
        return Q_EXPL
    context.user_data["nq"]["expl"] = t
    return await ask_time(update)


async def q_expl_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nq"]["expl"] = ""
    return await ask_time(update)


async def ask_time(update: Update):
    await update.message.reply_text(f"⏱ وقت السؤال بالثواني (5–600) أو /skip لاستخدام الافتراضي ({get_setting('time')} ث).")
    return Q_TIME


async def q_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not t.isdigit() or not 5 <= int(t) <= 600:
        await update.message.reply_text("اكتب رقم من 5 لـ 600 أو /skip.")
        return Q_TIME
    context.user_data["nq"]["time"] = int(t)
    return await ask_points(update)


async def q_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await ask_points(update)


async def ask_points(update: Update):
    await update.message.reply_text(f"🏅 نقاط السؤال (1–1000) أو /skip لاستخدام الافتراضي ({get_setting('points')}).")
    return Q_POINTS


async def q_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not t.isdigit() or not 1 <= int(t) <= 1000:
        await update.message.reply_text("اكتب رقم من 1 لـ 1000 أو /skip.")
        return Q_POINTS
    context.user_data["nq"]["points"] = int(t)
    return await save_question(update, context)


async def q_points_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await save_question(update, context)


async def save_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nq = context.user_data.pop("nq")
    qid = insert_question(nq["text"], nq["opts"], nq["correct"], nq.get("expl", ""), nq.get("time"), nq.get("points"), nq.get("image"))
    kb = rows(
        [btn("➕ سؤال تاني", "adm:addq", GREEN), btn("🔎 معاينة", f"q:poll:{qid}:0", BLUE)],
        [btn("📋 الأسئلة", "adm:list:0", BLUE), btn("🎛 اللوحة", "adm:menu")],
    )
    await update.message.reply_text(f"✅ تم حفظ السؤال #{qid}", reply_markup=kb)
    return ConversationHandler.END


@admin_only
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    js = '[{"question":"عاصمة مصر؟","options":["القاهرة","الإسكندرية","أسوان"],"answer":1,"explanation":"القاهرة","time":45,"points":10,"image":"https://..."}]'
    ht = '<div class="q" data-answer="1" data-time="45" data-points="10">\n  <h3>عاصمة مصر؟</h3>\n  <ol><li>القاهرة</li><li>أسوان</li></ol>\n  <p class="exp">الشرح</p>\n</div>'
    await update.callback_query.message.reply_text(
        "📥 ابعت ملف <b>.json</b> أو <b>.html</b> (أو الصق النص مباشرة).\n\n<b>JSON:</b>\n"
        f"<pre>{esc(js)}</pre>\n<b>HTML:</b>\n<pre>{esc(ht)}</pre>\n"
        "answer = رقم الإجابة الصحيحة بدءًا من 1 (أو حط class=\"correct\" على الـ li الصحيح في الـ HTML). "
        "explanation/time/points/image اختيارية.\n/cancel للإلغاء"
    )
    return IMPORT


async def import_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    try:
        if msg.document:
            if msg.document.file_size and msg.document.file_size > 1_000_000:
                await msg.reply_text("الملف كبير (الحد 1MB).")
                return IMPORT
            raw = bytes(await (await msg.document.get_file()).download_as_bytearray()).decode("utf-8-sig")
        else:
            raw = msg.text
        ok, dup, errors = import_questions(raw)
    except (ValueError, TelegramError) as e:
        await msg.reply_text(f"❌ مقدرتش أقرأ البيانات: {esc(str(e))[:200]}\nتأكد من الصيغة وجرّب تاني أو /cancel.")
        return IMPORT
    lines = [f"✅ اتضاف {ok} سؤال"]
    if dup:
        lines.append(f"♻️ {dup} سؤال مكرر اتجاهل")
    if errors:
        lines.append(f"⚠️ {len(errors)} سؤال فيهم مشكلة:")
        lines += [esc(e) for e in errors[:8]]
        if len(errors) > 8:
            lines.append(f"… و{len(errors) - 8} كمان")
    await msg.reply_text("\n".join(lines), reply_markup=rows([btn("📋 الأسئلة", "adm:list:0", BLUE)]))
    return ConversationHandler.END


@admin_only
async def ai_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    if not AI_API_KEY:
        await update.effective_message.reply_text(
            "ميزة الذكاء الاصطناعي غير مفعّلة بعد. أضف AI_API_KEY في ملف .env أو إعدادات الاستضافة ثم أعد تشغيل البوت.\n"
            "لا ترسل مفتاح API في المحادثة."
        )
        return ConversationHandler.END
    context.user_data.pop("ai_import_ready", None)
    await update.effective_message.reply_text(
        "✨ <b>تنسيق أسئلة بالذكاء الاصطناعي</b>\n\n"
        f"الصق الأسئلة مع الاختيارات والإجابات الصحيحة، أو أرسل ملف <b>.txt</b> (حتى {MAX_AI_QUESTIONS} سؤالًا و20,000 حرف).\n"
        "سيحوّلها إلى كويز ويعرض معاينة؛ <b>لن تُضاف أي أسئلة قبل تأكيدك</b>. الإجابة غير الواضحة لن يتم تخمينها، وستظهر للمراجعة ولن تُضاف.\n\n"
        "تنبيه: النص المرسل سيُرسل إلى مزود الذكاء الاصطناعي لإتمام التنسيق. راجع الإجابات بنفسك قبل التأكيد.\n"
        "/cancel للإلغاء"
    )
    return AI_INPUT


def _ai_preview_chunks(ready, flagged):
    ordered = [(item, False) for item in ready] + [(item, True) for item in flagged]
    ordered.sort(key=lambda pair: pair[0]["source_number"])
    blocks = []
    for item, needs_review in ordered:
        number = item["source_number"]
        title = esc(item["question"] or "(نص السؤال ناقص)")
        badge = "⚠️ يحتاج مراجعة — لن يُضاف" if needs_review else "✅ جاهز للإضافة"
        lines = [f"{badge} · <b>{number}. {title}</b>"]
        source_excerpt = item.get("source_excerpt")
        if isinstance(source_excerpt, str) and source_excerpt.strip():
            lines.append(f"📎 <i>من النص الأصلي: {esc(source_excerpt[:250])}</i>")
        for i, option in enumerate(item["options"]):
            marker = "✅" if item["correct_index"] == i else "▫️"
            lines.append(f"{marker} {esc(option)}")
        if needs_review and item.get("note"):
            lines.append(f"ملاحظة: {esc(item['note'])}")
        blocks.append("\n".join(lines))

    header = f"📋 <b>المعاينة:</b> {len(ready)} سؤال جاهز، {len(flagged)} يحتاج مراجعة.\n\n"
    chunks, current = [], header
    for block in blocks:
        units = len((current + block + "\n\n").encode("utf-16-le")) // 2
        if units > 3500 and current:
            chunks.append(current)
            current = ""
        current += block + "\n\n"
    if current:
        chunks.append(current)
    return chunks or [header]


@admin_only
async def ai_import_parse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    try:
        if msg.document:
            filename = (msg.document.file_name or "").lower()
            if not filename.endswith(".txt"):
                await msg.reply_text("أرسل ملف نصي بامتداد .txt أو الصق النص مباشرة.")
                return AI_INPUT
            if msg.document.file_size and msg.document.file_size > 100_000:
                await msg.reply_text("الملف كبير؛ الحد الأقصى 100KB.")
                return AI_INPUT
            raw = bytes(await (await msg.document.get_file()).download_as_bytearray()).decode("utf-8-sig")
        else:
            raw = msg.text or ""
        if len(raw.strip()) > MAX_AI_INPUT_CHARS:
            await msg.reply_text(f"النص أطول من {MAX_AI_INPUT_CHARS} حرف. قسّمه إلى دفعات أصغر.")
            return AI_INPUT
    except (UnicodeDecodeError, TelegramError, AttributeError):
        await msg.reply_text("ماقدرتش أقرأ الملف. تأكد أنه ملف .txt بترميز UTF-8 أو الصق النص مباشرة.")
        return AI_INPUT

    await msg.reply_text("⏳ جاري ترتيب الأسئلة والتحقق من بنيتها...")
    try:
        ready, flagged = await parse_quiz_text(raw)
    except AIQuizError as exc:
        await msg.reply_text(f"⚠️ {esc(str(exc))}")
        return AI_INPUT
    except Exception as exc:  # noqa: BLE001 - provider SDKs use different transport exception classes
        # لا نسجل نص الأسئلة أو بيانات اتصال مزود الذكاء الاصطناعي في اللوج.
        log.error("AI question parsing failed (%s)", type(exc).__name__)
        await msg.reply_text("تعذر الاتصال بمزود الذكاء الاصطناعي أو قراءة رده. تحقق من إعداداته وحاول مرة أخرى.")
        return AI_INPUT

    context.user_data["ai_import_ready"] = ready
    context.user_data["ai_import_flagged"] = flagged
    keyboard = []
    if ready:
        keyboard.append([btn(f"✅ أضف {len(ready)} سؤال", "ai:confirm", GREEN)])
    keyboard.append([btn("❌ إلغاء", "ai:cancel", RED)])
    chunks = _ai_preview_chunks(ready, flagged)
    for i, chunk in enumerate(chunks):
        markup = rows(*keyboard) if i == len(chunks) - 1 else None
        if i == 0:
            await msg.reply_text(chunk, reply_markup=markup)
        else:
            await context.bot.send_message(msg.chat_id, chunk, reply_markup=markup)
    return AI_REVIEW


@admin_only
async def ai_import_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    ready = context.user_data.get("ai_import_ready")
    if action == "confirm" and not ready:
        await query.answer("انتهت المعاينة أو لا توجد أسئلة صالحة للإضافة.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.pop("ai_import_ready", None)
    context.user_data.pop("ai_import_flagged", None)
    if action == "cancel":
        await query.edit_message_text("تم إلغاء استيراد أسئلة الذكاء الاصطناعي.")
        return ConversationHandler.END

    payload = [
        {
            "question": item["question"],
            "options": item["options"],
            "answer": item["correct_index"] + 1,
            "explanation": item["explanation"],
        }
        for item in ready
    ]
    ok, duplicate, errors = import_questions(json.dumps(payload, ensure_ascii=False))
    lines = [f"✅ تمت إضافة {ok} سؤال."]
    if duplicate:
        lines.append(f"♻️ تم تجاهل {duplicate} سؤال مكرر.")
    if errors:
        lines.append(f"⚠️ تعذر إضافة {len(errors)} سؤال: " + "؛ ".join(errors[:5]))
    lines.append("الأسئلة التي ظهرت بعلامة المراجعة لم تتم إضافتها.")
    await query.edit_message_text("\n".join(lines), reply_markup=rows([btn("📋 الأسئلة", "adm:list:0", BLUE)]))
    return ConversationHandler.END


EDIT_FIELDS = {
    "text": ("نص السؤال", "text"),
    "expl": ("الشرح (أو - لمسحه)", "explanation"),
    "time": ("الوقت بالثواني 5–600 (أو - للافتراضي)", "time_limit"),
    "points": ("النقاط 1–1000 (أو - للافتراضي)", "points"),
    "image": ("صورة جديدة أو رابط (أو - لحذف الصورة)", "image"),
}

EDIT_SQL = {
    "text": "UPDATE questions SET text=? WHERE id=?",
    "expl": "UPDATE questions SET explanation=? WHERE id=?",
    "time": "UPDATE questions SET time_limit=? WHERE id=?",
    "points": "UPDATE questions SET points=? WHERE id=?",
    "image": "UPDATE questions SET image=? WHERE id=?",
}


@admin_only
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, field, qid, page = q.data.split(":")
    if field not in EDIT_FIELDS or not get_question(int(qid)):
        return ConversationHandler.END
    context.user_data["edit"] = {"field": field, "qid": int(qid), "page": int(page)}
    await q.message.reply_text(f"✏️ اكتب القيمة الجديدة: {EDIT_FIELDS[field][0]}\n/cancel للإلغاء")
    return EDIT


def _parse_edit(field, m):
    """يرجّع (القيمة، رسالة خطأ)."""
    t = (m.text or "").strip()
    clear = t == "-"
    if field == "image":
        if m.photo:
            return m.photo[-1].file_id, None
        if clear:
            return None, None
        if t.startswith(("http://", "https://")) and len(t) <= 500:
            return t, None
        return None, "ابعت صورة أو رابط يبدأ بـ http، أو - لحذف الصورة."
    if not t:
        return None, "اكتب نص."
    if field == "text":
        return (t, None) if len(t) <= MAX_Q else (None, f"النص أطول من {MAX_Q} حرف.")
    if field == "expl":
        if clear:
            return "", None
        return (t, None) if len(t) <= MAX_EXPL else (None, f"الشرح أطول من {MAX_EXPL} حرف.")
    lo, hi = (5, 600) if field == "time" else (1, 1000)
    if clear:
        return None, None
    if not t.isdigit() or not lo <= int(t) <= hi:
        return None, f"اكتب رقم من {lo} لـ {hi} (أو - للافتراضي)."
    return int(t), None


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    e = context.user_data.get("edit")
    if not e:
        return ConversationHandler.END
    val, err = _parse_edit(e["field"], update.message)
    if err:
        await update.message.reply_text(err)
        return EDIT
    db.x(EDIT_SQL[e["field"]], (val, e["qid"]))
    context.user_data.pop("edit", None)
    await update.message.reply_text(
        f"✅ اتعدّل سؤال #{e['qid']}", reply_markup=rows([btn("👁 عرض السؤال", f"q:view:{e['qid']}:{e['page']}", BLUE)])
    )
    return ConversationHandler.END
