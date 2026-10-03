import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import run
from telegram.error import NetworkError
from test_handlers import cb_update, context, markup_of

from quizbot import admin as A
from quizbot import app as appmod
from quizbot import engine
from quizbot import handlers_user as U
from quizbot.db import db
from quizbot.questions import insert_question
from quizbot.teams import create_empty_team, create_team, join_team, team_of
from quizbot.ui import fmt_time


def two_member_team():
    for u in (10, 11):
        db.x("INSERT INTO users(id,name) VALUES(?,?)", (u, f"u{u}"))
    t, _ = create_team(10, "الفريق")
    join_team(11, t["code"])
    return t


def test_leader_can_transfer_leadership_and_others_cannot(app):
    two_member_team()
    ctx = context(app)

    upd, q = cb_update("u:team", uid=11)  # العضو العادي ماعندوش زر تغيير القائد
    run(U.user_cb(upd, ctx))
    assert "u:lead" not in [b.callback_data for r in markup_of(q).inline_keyboard for b in r]

    upd, q = cb_update("u:team", uid=10)
    run(U.user_cb(upd, ctx))
    assert "u:lead" in [b.callback_data for r in markup_of(q).inline_keyboard for b in r]

    upd, q = cb_update("u:setlead:11", uid=11)  # محاولة من غير القائد
    run(U.user_cb(upd, ctx))
    assert team_of(10)["leader_id"] == 10

    upd, q = cb_update("u:lead", uid=10)
    run(U.user_cb(upd, ctx))
    assert "u:setlead:11" in [b.callback_data for r in markup_of(q).inline_keyboard for b in r]
    upd, q = cb_update("u:setlead:11", uid=10)
    run(U.user_cb(upd, ctx))
    assert team_of(10)["leader_id"] == 11
    assert any("القائد الجديد" in x for x in app.bot.texts_to(10))

    upd, q = cb_update("u:setlead:999", uid=11)  # مش عضو في الفريق
    run(U.user_cb(upd, ctx))
    assert team_of(10)["leader_id"] == 11


def test_history_lists_and_exports_a_specific_contest(app):
    db.x("INSERT INTO contests(id,status,started_at,qids,idx) VALUES(7,'finished',?, '[1,2]',1)", (time.time(),))
    db.x("INSERT INTO results(contest_id,qid,team_id,team_name,q_text,is_correct,points,bonus,voters,members) "
         "VALUES(7,1,5,'الصقور','س',1,15,5,2,2)")
    ctx = context(app)
    upd, q = cb_update("h:list")
    run(A.admin_cb(upd, ctx))
    labels = [b.text for r in markup_of(q).inline_keyboard for b in r]
    assert any("#7" in x and "الصقور" in x for x in labels)
    upd, q = cb_update("h:view:7")
    run(A.admin_cb(upd, ctx))
    assert "المسابقة #7" in q.edit_message_text.call_args.args[0] and "2 سؤال" in q.edit_message_text.call_args.args[0]
    upd, q = cb_update("h:exp:7")
    run(A.admin_cb(upd, ctx))
    kw = ctx.bot.send_document.call_args.kwargs
    assert kw["filename"] == "contest_7_results.csv" and "الصقور" in kw["document"].decode("utf-8-sig")


def msg(text=None, photo=None):
    return SimpleNamespace(text=text, photo=photo, reply_text=AsyncMock())


def edit(app, field, qid, **m):
    ctx = context(app)
    upd, _q = cb_update(f"qe:{field}:{qid}:0")
    assert run(A.edit_start(upd, ctx)) == A.EDIT
    message = msg(**m)
    res = run(A.edit_value(SimpleNamespace(message=message), ctx))
    return res, message


def test_edit_question_fields(app):
    from telegram.ext import ConversationHandler

    qid = insert_question("قديم", ["أ", "ب"], 0, "شرح", tl=30, pts=5, image="https://x/i.png")
    row = lambda: db.q("SELECT * FROM questions WHERE id=?", (qid,), one=True)

    assert edit(app, "text", qid, text="نص جديد")[0] == ConversationHandler.END and row()["text"] == "نص جديد"
    edit(app, "time", qid, text="90")
    assert row()["time_limit"] == 90
    edit(app, "points", qid, text="-")
    assert row()["points"] is None
    edit(app, "expl", qid, text="-")
    assert row()["explanation"] == ""
    edit(app, "image", qid, text="-")
    assert row()["image"] is None
    edit(app, "image", qid, photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="BIG")])
    assert row()["image"] == "BIG"

    res, m = edit(app, "time", qid, text="2")  # قيمة غلط: يفضل في نفس الحالة ومايتغيرش حاجة
    assert res == A.EDIT and row()["time_limit"] == 90 and "5" in m.reply_text.call_args.args[0]
    res, _ = edit(app, "text", qid, text="س" * 400)
    assert res == A.EDIT and row()["text"] == "نص جديد"


def test_edited_question_is_used_by_next_contest(app):
    db.x("INSERT INTO users(id,name) VALUES(1,'a')")
    create_team(1, "فريق")
    qid = insert_question("قديم", ["أ", "ب"], 0, tl=30)
    edit(app, "text", qid, text="بعد التعديل")

    async def go():
        await engine.start_contest(app, [qid])
        assert engine.RT["c"]["qtext"] == "بعد التعديل"

    run(go())


def test_error_alerts_are_throttled_and_ignore_network_blips(app):
    appmod._last_alert.clear()
    bot = app.bot
    bot.sent.clear()

    def ctx(err):
        return SimpleNamespace(error=err, bot=bot)

    async def go():
        await appmod.on_error(None, ctx(NetworkError("x")))
        assert not bot.sent
        await appmod.on_error(None, ctx(ValueError("boom")))
        await appmod.on_error(None, ctx(ValueError("boom again")))
        assert len([1 for c, t, _ in bot.sent if "خطأ في البوت" in t]) == 1  # تنبيه واحد للأدمن
        await appmod.on_error(None, ctx(KeyError("k")))
        assert len([1 for c, t, _ in bot.sent if "خطأ في البوت" in t]) == 2  # نوع خطأ تاني

    run(go())


def test_application_processes_updates_sequentially_for_conversations():
    built = appmod.build_app()
    assert built.update_processor.max_concurrent_updates == 1


def test_start_prompts_unassigned_member_for_code_then_shows_team(app):
    from telegram.ext import ConversationHandler

    user = SimpleNamespace(id=80, first_name="مستخدم", full_name="مستخدم تجربة", username="testuser")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=user, effective_message=message, message=message)
    ctx = context(app)
    assert run(U.cmd_start(update, ctx)) == U.U_CODE
    assert "كود الفريق" in message.reply_text.await_args.args[0]

    team, _ = create_empty_team("الصقور")
    ctx.args = [f"join_{team['code']}"]
    assert run(U.cmd_start(update, ctx)) == ConversationHandler.END
    assert team_of(user.id)["id"] == team["id"]
    assert "الصقور" in message.reply_text.await_args.args[0]


def test_team_discussion_forwards_original_sender_identity(app):
    team, _ = create_team(30, "فريق النقاش")
    join_team(31, team["code"])
    user = SimpleNamespace(id=30, full_name="عضو الفريق", username="team_member")
    message = SimpleNamespace(text="رسالة للفريق", forward=AsyncMock(), set_reaction=AsyncMock())
    update = SimpleNamespace(effective_user=user, effective_message=message)
    ctx = SimpleNamespace(bot=app.bot)

    run(U.relay(update, ctx))
    message.forward.assert_awaited_once_with(chat_id=31)


def test_unassigned_user_relay_prompts_for_team_code_not_team_creation(app):
    user = SimpleNamespace(id=91, full_name="مستخدم بلا فريق", username=None)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=user, effective_message=message)
    run(U.relay(update, SimpleNamespace(bot=app.bot)))
    text = message.reply_text.call_args.args[0]
    assert "/start" in text and "كود الفريق" in text and "اعمل فريق" not in text


def test_top_command_runs_without_crashing_when_there_is_no_contest(app):
    user = SimpleNamespace(id=92, full_name="عضو", username=None)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=user, effective_message=message, callback_query=None)
    run(U.send_top(update, context(app)))
    assert "مفيش مسابقة" in message.reply_text.call_args.args[0]


def test_existing_database_migrates_completed_question_marker(tmp_path):
    import sqlite3

    from quizbot.db import DB

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE contests(id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, started_at REAL, "
            "ended_at REAL, qids TEXT, idx INTEGER, flags TEXT)"
        )
    migrated = DB(str(path))
    columns = {row["name"]: row for row in migrated.q("PRAGMA table_info(contests)")}
    assert columns["completed_idx"]["dflt_value"] == "-1"
    migrated.conn.close()


def test_fmt_time_uses_cairo_timezone():
    assert fmt_time(0) == "1970-01-01 02:00"  # القاهرة UTC+2 في 1970


def test_menu_has_history_button(app):
    data = [b.callback_data for r in A.admin_menu().inline_keyboard for b in r]
    assert "h:list" in data and asyncio.iscoroutinefunction(A.edit_value)


def test_arena_ranks_today_by_points_correctness_and_speed_and_includes_all_teams(app):
    from datetime import datetime, timedelta
    from datetime import time as datetime_time
    from zoneinfo import ZoneInfo

    from quizbot.board import arena_ranked, arena_rows
    from quizbot.config import TIMEZONE

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).date()
    today_start = datetime.combine(today, datetime_time.min, tzinfo=tz).timestamp()
    yesterday_start = datetime.combine(today - timedelta(days=1), datetime_time.min, tzinfo=tz).timestamp()
    fast, _ = create_empty_team("السريع")
    slower, _ = create_empty_team("الأسرع غلط")
    no_score, _ = create_empty_team("بلا نقاط")
    db.x("INSERT INTO contests(id,status,started_at,qids,idx) VALUES(1,'finished',?,'[]',0)", (today_start + 3600,))
    db.x("INSERT INTO contests(id,status,started_at,qids,idx) VALUES(2,'finished',?,'[]',0)", (today_start + 7200,))
    db.x("INSERT INTO contests(id,status,started_at,qids,idx) VALUES(3,'finished',?,'[]',0)", (yesterday_start + 3600,))
    db.x("INSERT INTO results(contest_id,qid,team_id,team_name,is_correct,points,bonus) VALUES(1,1,?,?,1,15,5)", (fast["id"], fast["name"]))
    db.x("INSERT INTO results(contest_id,qid,team_id,team_name,is_correct,points,bonus) VALUES(2,2,?,?,1,12,2)", (fast["id"], fast["name"]))
    db.x("INSERT INTO results(contest_id,qid,team_id,team_name,is_correct,points,bonus) VALUES(1,1,?,?,1,20,0)", (slower["id"], slower["name"]))
    db.x("INSERT INTO results(contest_id,qid,team_id,team_name,is_correct,points,bonus) VALUES(3,3,?,?,1,999,999)", (no_score["id"], no_score["name"]))

    ranked_rows = arena_ranked(arena_rows(today))
    assert [row["id"] for _, row in ranked_rows] == [fast["id"], slower["id"], no_score["id"]]
    assert ranked_rows[0][1]["pts"] == 27 and ranked_rows[0][1]["ok"] == 2 and ranked_rows[0][1]["bonus"] == 7
    assert ranked_rows[1][1]["pts"] == 20
    assert ranked_rows[2][1]["pts"] == 0


def test_arena_shows_ten_per_page_and_allows_paging(app):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quizbot.board import ARENA_PAGE_SIZE, arena_rows, arena_text
    from quizbot.config import TIMEZONE

    for i in range(11):
        create_empty_team(f"فريق الساحة {i:02d}")
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    ranking = arena_rows(today)
    assert len(ranking) == 11
    first_page = arena_text(ranking, today, 0)
    second_page = arena_text(ranking, today, 1)
    assert "إجمالي الفرق: 11" in first_page
    assert len([line for line in first_page.splitlines() if "فريق الساحة" in line]) == ARENA_PAGE_SIZE
    assert len([line for line in second_page.splitlines() if "فريق الساحة" in line]) == 1

    upd, query = cb_update("u:arena:1", uid=100)
    run(U.user_cb(upd, context(app)))
    assert "الصفحة 2/2" in query.edit_message_text.call_args.args[0]
    assert query.answer.await_count == 1
