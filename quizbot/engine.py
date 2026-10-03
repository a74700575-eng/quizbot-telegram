"""محرك المسابقة: فتح الأسئلة، التصويت، المؤقتات، النتايج، الإيقاف المؤقت والاستكمال."""
import asyncio
import json
import logging
import math
import random
import time
from collections import Counter, defaultdict

from telegram.error import BadRequest, TelegramError

from .board import board_rows, board_text
from .config import LETTERS
from .db import db, get_int, get_setting
from .questions import get_question
from .scoring import bar, majority, participation_ok, ranked, speed_bonus
from .state import LOCK, RT
from .teams import all_member_ids, team_members, team_of
from .ui import BLUE, GREEN, RED, btn, clip, esc, notify_admins, rows, safe_send

log = logging.getLogger("quizbot")


# ───────────────────────────── أدوات ─────────────────────────────
def _cancel(c, *names):
    for n in names:
        job = c.pop(n, None)
        if job:
            job.schedule_removal()


async def _gather(coros):
    res = await asyncio.gather(*coros, return_exceptions=True)
    for r in res:
        if isinstance(r, Exception) and not isinstance(r, TelegramError):
            log.warning("task failed: %r", r)
    return res


def _left(c):
    if c.get("paused"):
        return math.ceil(c.get("remaining", 0))
    return max(0, math.ceil(c["ends"] - time.time()))


def ctl_markup(c):
    """لوحة تحكم الأدمن في المسابقة."""
    if c.get("paused"):
        first = [btn("▶️ استئناف", "ctl:resume", GREEN)]
    elif c["open"]:
        first = [btn("⏭ إنهاء السؤال الآن", "ctl:skip", GREEN), btn("⏸ إيقاف مؤقت", "ctl:pause", BLUE)]
    else:
        last = c["idx"] + 1 >= len(c["qids"])
        label = "🏁 إنهاء المسابقة" if last else "▶️ السؤال التالي الآن"
        first = [btn(label, "ctl:next", GREEN), btn("⏸ إيقاف مؤقت", "ctl:pause", BLUE)]
    return rows(
        first,
        [btn("🖐 التالي يدوي: " + ("✅" if c.get("manual") else "❌"), "ctl:manual", BLUE), btn("📊 الحالة", "adm:status", BLUE)],
        [btn("⏹ إيقاف المسابقة", "ctl:stop", RED)],
    )


# ───────────────────────────── عرض السؤال ─────────────────────────────
def render_question(c, mem, vote_rows, leader_id=None):
    votes = {r["user_id"]: r["choice"] for r in vote_rows}
    header = f"❓ <b>سؤال {c['idx'] + 1}/{len(c['qids'])}</b>   🏅 {c['points']} نقطة"
    if get_int("speed_bonus") > 0:
        header += f" (+ مكافأة سرعة حتى {get_int('speed_bonus')}%)"
    if c.get("paused"):
        timer = f"⏸ <b>المسابقة متوقفة مؤقتًا</b> — باقي {_left(c)} ثانية"
    else:
        timer = f"⏱ {bar(_left(c), c['tl'])} <b>{_left(c)}</b> ثانية"
    lines = [header, timer, "", esc(c["qtext"]), ""]
    lines += [f"<b>{LETTERS[i]})</b> {esc(o)}" for i, o in enumerate(c["opts"])]
    lines += ["", f"🗳 <b>أصوات فريقك</b> ({len(votes)}/{len(mem)}):"]
    for m in mem:
        v = votes.get(m["id"])
        crown = "👑 " if m["id"] == leader_id else ""
        lines.append(f"• {crown}{esc(m['name'])} ← " + (f"<b>{LETTERS[v]}</b>" if v is not None else "…"))
    if vote_rows:
        lead_choice = votes.get(leader_id)
        lead, _ = majority([(r["choice"], r["ts"]) for r in vote_rows], lead_choice)
        lines.append(f"\n📊 الأغلبية حاليًا: <b>{LETTERS[lead]}</b>")
    min_part = get_int("min_part")
    if min_part:
        lines.append(f"⚠️ لازم {min_part}% من الفريق على الأقل يصوّتوا عشان الإجابة تتحسب.")
    lines.append("\n💬 اكتب أي رسالة هنا وهتوصل لزمايلك للنقاش. تقدر تغيّر اختيارك قبل انتهاء الوقت.")
    return "\n".join(lines)


def vote_markup(c, mine=None):
    btns = []
    for i in range(len(c["opts"])):
        picked = i == mine
        btns.append(btn(("✅ " if picked else "") + LETTERS[i], f"v:{c['id']}:{c['qid']}:{i}", GREEN if picked else BLUE))
    return rows(*[btns[i : i + 5] for i in range(0, len(btns), 5)])


def _team_view(c, tid):
    mem = team_members(tid)
    vote_rows = db.q("SELECT user_id, choice, ts FROM votes WHERE contest_id=? AND qid=? AND team_id=?", (c["id"], c["qid"], tid))
    t = db.q("SELECT leader_id FROM teams WHERE id=?", (tid,), one=True)
    text = clip(render_question(c, mem, vote_rows, t["leader_id"] if t else None))
    return mem, {r["user_id"]: r["choice"] for r in vote_rows}, text


async def _edit(bot, uid, mid, text, markup):
    try:
        await bot.edit_message_text(text, chat_id=uid, message_id=mid, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)
    except TelegramError as e:
        log.warning("edit failed: %s", e)


async def refresh_team(app, c, tid):
    if not c.get("open"):
        return
    mem, votes, text = _team_view(c, tid)
    await _gather(
        [_edit(app.bot, m["id"], c["msgs"][m["id"]], text, vote_markup(c, votes.get(m["id"]))) for m in mem if m["id"] in c["msgs"]]
    )


async def refresh_all(app, c):
    tids = [r["team_id"] for r in db.q("SELECT DISTINCT team_id FROM members")]
    await _gather([refresh_team(app, c, t) for t in tids])


def request_refresh(app, c, tid):
    """تحديث مؤجّل (debounce): كذا صوت قريبين من بعض بيتحدّثوا في تعديل واحد."""
    dirty = c.setdefault("dirty", set())
    if tid in dirty:
        return
    dirty.add(tid)
    app.job_queue.run_once(_refresh_job, 0.7, data=(c["id"], c.get("qid"), tid))


async def _refresh_job(context):
    cid, qid, tid = context.job.data
    c = RT.get("c")
    if not c or c["id"] != cid:
        return
    c.setdefault("dirty", set()).discard(tid)
    if c.get("qid") == qid:
        await refresh_team(context.application, c, tid)


async def sync_membership(app, tid, new_uid=None):
    """بعد انضمام/خروج عضو أثناء سؤال مفتوح."""
    c = RT.get("c")
    if not c or not c.get("open") or not tid:
        return
    if new_uid and new_uid not in c["msgs"]:
        _, _, text = _team_view(c, tid)
        await _deliver(app, c, new_uid, text, vote_markup(c))
    request_refresh(app, c, tid)


async def _deliver(app, c, uid, text, markup):
    if c.get("image"):
        try:
            await app.bot.send_photo(uid, c["image"], caption=f"🖼 سؤال {c['idx'] + 1}")
        except TelegramError as e:
            log.warning("photo failed for %s: %s", uid, e)
    m = await safe_send(app.bot, uid, text, markup)
    if m:
        c["msgs"][uid] = m.message_id


# ───────────────────────────── دورة المسابقة ─────────────────────────────
def _new_runtime(cid, qids, idx=-1, manual=False, shufo=False):
    return {
        "id": cid, "qids": qids, "idx": idx, "open": False, "paused": False,
        "manual": manual, "shufo": shufo, "msgs": {}, "dirty": set(),
    }


async def start_contest(app, qids, shuffle_options=False, manual=False):
    async with LOCK:
        if RT.get("c"):
            return False, "⚠️ فيه مسابقة شغالة بالفعل."
        if db.q("SELECT COUNT(*) n FROM members", one=True)["n"] == 0:
            return False, "⚠️ مفيش فرق فيها أعضاء لسه."
        if not qids:
            return False, "⚠️ اختار سؤال واحد على الأقل."
        flags = json.dumps({"manual": manual, "shufo": shuffle_options})
        cid = db.x(
            "INSERT INTO contests(status,started_at,qids,idx,flags) VALUES('running',?,?,-1,?)",
            (time.time(), json.dumps(qids), flags),
        )
        RT["c"] = _new_runtime(cid, qids, manual=manual, shufo=shuffle_options)
    await _announce(app, f"🚀 <b>المسابقة بدأت!</b>\nعدد الأسئلة: {len(qids)}\nجهّزوا نفسكم، أول سؤال بعد ثواني…")
    await asyncio.sleep(3)
    await next_question(app)
    return True, f"✅ المسابقة #{cid} بدأت ({len(qids)} سؤال)."


def interrupted_contest():
    return db.q("SELECT * FROM contests WHERE status='interrupted' ORDER BY id DESC LIMIT 1", one=True)


async def resume_interrupted(app):
    """يكمل مسابقة اتقطعت بسبب إعادة تشغيل البوت (السؤال اللي كان مفتوح بيتعاد من الأول)."""
    async with LOCK:
        if RT.get("c"):
            return False, "⚠️ فيه مسابقة شغالة بالفعل."
        row = interrupted_contest()
        if not row:
            return False, "مفيش مسابقة مقطوعة."
        flags = json.loads(row["flags"] or "{}")
        qids = json.loads(row["qids"])
        persisted_idx = row["idx"] if row["idx"] is not None else -1
        completed_idx = row["completed_idx"] if row["completed_idx"] is not None else -1
        question_completed = persisted_idx >= 0 and completed_idx >= persisted_idx
        idx = persisted_idx if question_completed else max(-1, persisted_idx - 1)
        if not question_completed and idx + 1 < len(qids):
            db.x("DELETE FROM votes WHERE contest_id=? AND qid=?", (row["id"], qids[idx + 1]))
        db.x("UPDATE contests SET status='running', ended_at=NULL WHERE id=?", (row["id"],))
        RT["c"] = _new_runtime(row["id"], qids, idx, flags.get("manual", False), flags.get("shufo", False))
    await _announce(app, "▶️ <b>المسابقة اتكمّلت بعد انقطاع قصير.</b> السؤال الجاي بعد ثواني…")
    await asyncio.sleep(3)
    await next_question(app)
    return True, f"✅ المسابقة #{row['id']} اتكمّلت."


async def _announce(app, text):
    await _gather([safe_send(app.bot, uid, text) for uid in all_member_ids()])


async def next_question(app):
    async with LOCK:
        c = RT.get("c")
        if not c or c["open"] or c["paused"]:
            return
        _cancel(c, "job_next")
        q = None
        while q is None:
            c["idx"] += 1
            if c["idx"] >= len(c["qids"]):
                break
            q = get_question(c["qids"][c["idx"]])
        finished = q is None
        if not finished:
            await open_question(app, c, q)
    if finished:
        await finish_contest(app, "finished")


async def open_question(app, c, q):
    tl = q["time_limit"] or get_int("time")
    opts, correct = json.loads(q["options"]), q["correct"]
    if c["shufo"]:
        perm = list(range(len(opts)))
        random.shuffle(perm)
        opts, correct = [opts[i] for i in perm], perm.index(q["correct"])
    now = time.time()
    c.update(
        qid=q["id"], open=True, paused=False, msgs={}, dirty=set(), tl=tl, qtext=q["text"], opts=opts, correct=correct,
        points=q["points"] or get_int("points"), image=q["image"], started=now, ends=now + tl + 60,
    )
    db.x("UPDATE contests SET idx=? WHERE id=?", (c["idx"], c["id"]))
    tids = [r["team_id"] for r in db.q("SELECT DISTINCT team_id FROM members")]
    jobs = []
    for tid in tids:
        mem, _, text = _team_view(c, tid)
        markup = vote_markup(c)
        jobs += [_deliver(app, c, m["id"], text, markup) for m in mem]
    await _gather(jobs)
    # العدّاد يبدأ بعد ما الإرسال يخلص، عشان كل الفرق تاخد نفس الوقت تقريبًا
    c["started"] = time.time()
    c["ends"] = c["started"] + tl
    _schedule_timers(app, c, tl)
    await notify_admins(
        app.bot,
        f"▶️ سؤال {c['idx'] + 1}/{len(c['qids'])} اتبعت لكل الفرق ({tl} ثانية).\n\n❓ {esc(c['qtext'])}\n"
        f"✅ الصحيحة: <b>{LETTERS[c['correct']]}) {esc(c['opts'][c['correct']])}</b>",
        ctl_markup(c),
    )


def _schedule_timers(app, c, remaining):
    _cancel(c, "job_end", "job_warn", "job_tick")
    c["job_end"] = app.job_queue.run_once(_end_job, remaining, data=c["id"])
    if remaining >= 20:
        c["job_warn"] = app.job_queue.run_once(_warn_job, remaining - 10, data=c["id"])
    tick = get_int("tick")
    if tick:
        tick = max(tick, 5)
        if remaining > tick + 2:
            c["job_tick"] = app.job_queue.run_repeating(_tick_job, interval=tick, first=tick, data=c["id"])


async def _tick_job(context):
    c = RT.get("c")
    if not c or c["id"] != context.job.data or not c["open"] or c["paused"] or c.get("ticking"):
        return
    if _left(c) <= 3:
        return
    c["ticking"] = True
    try:
        await refresh_all(context.application, c)
    finally:
        c["ticking"] = False


async def _warn_job(context):
    c = RT.get("c")
    if not c or c["id"] != context.job.data or not c["open"] or c["paused"]:
        return
    voted = {r["user_id"] for r in db.q("SELECT user_id FROM votes WHERE contest_id=? AND qid=?", (c["id"], c["qid"]))}
    await _gather([safe_send(context.bot, uid, "⏰ باقي 10 ثواني! لسه ماصوّتش.") for uid in c["msgs"] if uid not in voted])


async def _end_job(context):
    await end_question(context.application, context.job.data)


async def _next_job(context):
    c = RT.get("c")
    if not c or c["id"] != context.job.data:
        return
    if c["manual"] and c["idx"] + 1 < len(c["qids"]):
        return
    await next_question(context.application)


def _schedule_next(app, c, delay):
    _cancel(c, "job_next")
    c["job_next"] = app.job_queue.run_once(_next_job, delay, data=c["id"])


# ── التصويت ──
async def on_vote(update, context):
    q = update.callback_query
    try:
        _, cid, qid, ch = q.data.split(":")
        cid, qid, ch = int(cid), int(qid), int(ch)
    except ValueError:
        await q.answer()
        return
    uid = q.from_user.id
    err = None
    async with LOCK:
        c = RT.get("c")
        if not c or not c["open"] or c["id"] != cid or c["qid"] != qid or time.time() > c["ends"] + 0.5:
            err = "⏰ انتهى وقت السؤال"
        elif c["paused"]:
            err = "⏸ المسابقة متوقفة مؤقتًا"
        elif not (t := team_of(uid)):
            err = "لازم تكون في فريق عشان تصوّت."
        elif not 0 <= ch < len(c["opts"]):
            err = "اختيار غير صالح"
        else:
            db.x(
                "INSERT INTO votes(contest_id,qid,user_id,team_id,choice,ts) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(contest_id,qid,user_id) DO UPDATE SET team_id=excluded.team_id, "
                "ts=CASE WHEN votes.choice<>excluded.choice THEN excluded.ts ELSE votes.ts END, choice=excluded.choice",
                (cid, qid, uid, t["id"], ch, time.time()),
            )
    # كل الرد على تليجرام بيحصل بره القفل عشان الأصوات ماتتحجزش ورا بعضها
    if err:
        await q.answer(err, show_alert=True)
        return
    await q.answer(f"تم اختيار {LETTERS[ch]} ✅")
    request_refresh(context.application, c, t["id"])


# ── إنهاء السؤال ──
async def end_question(app, cid):
    async with LOCK:
        c = RT.get("c")
        if not c or c["id"] != cid or not c["open"]:
            return
        c["open"] = False
        _cancel(c, "job_end", "job_warn", "job_tick", "job_next")
        qid, correct, pts, opts, tl = c["qid"], c["correct"], c["points"], c["opts"], c["tl"]
        q = get_question(qid)
        expl = q["explanation"] if q else ""
        bonus_pct, min_pct = get_int("speed_bonus"), get_int("min_part")
        per = defaultdict(list)
        for v in db.q("SELECT team_id, user_id, choice, ts FROM votes WHERE contest_id=? AND qid=?", (cid, qid)):
            per[v["team_id"]].append(v)
        outcomes = []
        for t in db.q("SELECT id, name, leader_id FROM teams WHERE EXISTS (SELECT 1 FROM members WHERE team_id=teams.id)"):
            vs = per.get(t["id"], [])
            n_mem = len(team_members(t["id"]))
            leader = next((v["choice"] for v in vs if v["user_id"] == t["leader_id"]), None)
            chosen, decided = majority([(v["choice"], v["ts"]) for v in vs], leader)
            enough = participation_ok(len(vs), n_mem, min_pct)
            ok = chosen is not None and enough and chosen == correct
            base = pts if ok else 0
            bonus = speed_bonus(pts, bonus_pct, (decided or c["started"]) - c["started"], tl) if ok else 0
            db.x(
                "INSERT OR REPLACE INTO results(contest_id,qid,team_id,team_name,q_text,chosen_text,correct_text,"
                "is_correct,points,bonus,voters,members) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, qid, t["id"], t["name"], c["qtext"], opts[chosen] if chosen is not None else "", opts[correct],
                 int(ok), base + bonus, bonus, len(vs), n_mem),
            )
            outcomes.append(
                {"team": t, "chosen": chosen, "ok": ok, "enough": enough, "pts": base + bonus,
                 "bonus": bonus, "voters": len(vs), "n": n_mem, "dist": Counter(v["choice"] for v in vs),
                 "mine": {v["user_id"]: v["choice"] for v in vs}}
            )
        # Mark complete only after every team's result is durable; otherwise recovery can safely retry.
        db.x("UPDATE contests SET completed_idx=? WHERE id=?", (c["idx"], cid))
        last = c["idx"] + 1 >= len(c["qids"])
        msgs, idx, total = dict(c["msgs"]), c["idx"] + 1, len(c["qids"])

    rows_ = board_rows(cid)
    rk = {r["id"]: rank for rank, r in ranked(rows_)}
    pts_by = {r["id"]: r["pts"] for r in rows_}
    show_lb = get_setting("show_lb") == "1"
    jobs = []
    for o in outcomes:
        t = o["team"]
        lines = ["⏰ <b>انتهى الوقت!</b>", f"✅ الإجابة الصحيحة: <b>{LETTERS[correct]}) {esc(opts[correct])}</b>"]
        if o["chosen"] is None:
            lines.append("🗳 فريقك ماصوّتش على السؤال ده.")
        else:
            verdict = "✅ صح!" if o["ok"] else "❌ غلط"
            if not o["enough"]:
                verdict = "⚠️ ماتحسبتش (المشاركة قليلة)"
            lines.append(f"🗳 قرار فريقك بالأغلبية: <b>{LETTERS[o['chosen']]}) {esc(opts[o['chosen']])}</b> {verdict}")
            lines.append("📊 توزيع الأصوات: " + " | ".join(f"{LETTERS[k]}: {v}" for k, v in sorted(o["dist"].items())))
            if not o["enough"]:
                lines.append(f"👥 صوّت {o['voters']} من {o['n']} — الحد الأدنى {get_int('min_part')}%.")
        gained = f"+{o['pts']}" if o["pts"] else "0"
        if o["bonus"]:
            gained += f" (منها {o['bonus']} مكافأة سرعة ⚡)"
        lines.append(f"🏅 {gained} نقطة · إجمالي فريقك: {pts_by.get(t['id'], 0)}" + (f" · ترتيبكم #{rk.get(t['id'], '-')}" if show_lb else ""))
        if expl:
            lines.append(f"\n📝 {esc(expl)}")
        base_text = "\n".join(lines)
        for m in team_members(t["id"]):
            jobs.append(_close_for_member(app, m["id"], msgs.get(m["id"]), base_text, o["mine"].get(m["id"])))
    await _gather(jobs)

    summary = [f"⏹ <b>انتهى السؤال {idx}/{total}</b>", ""]
    for o in outcomes:
        ch = "لم يصوّتوا" if o["chosen"] is None else f"{LETTERS[o['chosen']]} {'✅' if o['ok'] else '❌'}"
        summary.append(f"• {esc(o['team']['name'])}: {ch} (+{o['pts']})")
    summary += ["", board_text(rows_, limit=5, title="🏆 <b>أعلى الفرق</b>")]

    async with LOCK:
        c = RT.get("c")
        live = bool(c) and c["id"] == cid and not c["open"]
        if live:
            if last:
                _schedule_next(app, c, 3)  # آخر سؤال: ننهي المسابقة تلقائيًا حتى في الوضع اليدوي
            elif not c["manual"] and not c["paused"]:
                _schedule_next(app, c, int(get_setting("gap")))
    await notify_admins(app.bot, "\n".join(summary), ctl_markup(c) if live else None)


async def _close_for_member(app, uid, mid, text, my_choice):
    if mid:
        try:
            await app.bot.edit_message_reply_markup(uid, mid, reply_markup=None)
        except TelegramError:
            pass
    if my_choice is not None:
        text += f"\n🙋 اختيارك أنت: <b>{LETTERS[my_choice]}</b>"
    await safe_send(app.bot, uid, text)


# ── إيقاف مؤقت / استئناف / وضع يدوي ──
async def pause_contest(app):
    async with LOCK:
        c = RT.get("c")
        if not c or c["paused"]:
            return False
        c["paused"] = True
        if c["open"]:
            c["remaining"] = max(1, c["ends"] - time.time())
            _cancel(c, "job_end", "job_warn", "job_tick")
        _cancel(c, "job_next")
    await _announce(app, "⏸ <b>الأدمن أوقف المسابقة مؤقتًا.</b> استنوا إشعار الاستئناف.")
    if c["open"]:
        await refresh_all(app, c)
    return True


async def resume_contest(app):
    async with LOCK:
        c = RT.get("c")
        if not c or not c["paused"]:
            return False
        c["paused"] = False
        if c["open"]:
            c["ends"] = time.time() + c["remaining"]
            _schedule_timers(app, c, c["remaining"])
        elif not c["manual"] or c["idx"] + 1 >= len(c["qids"]):
            _schedule_next(app, c, 3)
    await _announce(app, "▶️ <b>المسابقة كملت!</b>")
    if c["open"]:
        await refresh_all(app, c)
    return True


async def toggle_manual(app):
    c = RT.get("c")
    if not c:
        return None
    c["manual"] = not c["manual"]
    if c["manual"]:
        _cancel(c, "job_next")
    db.x("UPDATE contests SET flags=? WHERE id=?", (json.dumps({"manual": c["manual"], "shufo": c["shufo"]}), c["id"]))
    if not c["manual"] and not c["open"] and not c["paused"] and not c.get("job_next") and c["idx"] >= 0:
        _schedule_next(app, c, int(get_setting("gap")))
    return c["manual"]


# ── الإنهاء ──
async def finish_contest(app, status):
    async with LOCK:
        c = RT.pop("c", None)
        if not c:
            return
        _cancel(c, "job_end", "job_warn", "job_tick", "job_next")
        db.x("UPDATE contests SET status=?, ended_at=? WHERE id=?", (status, time.time(), c["id"]))
    if c.get("open"):
        await _gather([_strip_markup(app, uid, mid) for uid, mid in c["msgs"].items()])
    rows_ = board_rows(c["id"])
    head = "🏁 <b>انتهت المسابقة!</b>" if status == "finished" else "⏹ <b>الأدمن أوقف المسابقة.</b>"
    jobs = []
    for uid in all_member_ids():
        t = team_of(uid)
        jobs.append(safe_send(app.bot, uid, head + "\n\n" + board_text(rows_, highlight=t["id"] if t else None, title="🏆 <b>الترتيب النهائي</b>")))
    await _gather(jobs)
    await notify_admins(app.bot, head + "\n\n" + board_text(rows_, title="🏆 <b>الترتيب النهائي</b>", limit=30, admin=True))


async def _strip_markup(app, uid, mid):
    try:
        await app.bot.edit_message_reply_markup(uid, mid, reply_markup=None)
    except TelegramError:
        pass
