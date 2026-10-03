"""اختبارات دخان لأزرار اللوحة: كل زرار لازم يشتغل من غير استثناءات."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import run
from telegram import InlineKeyboardMarkup

from quizbot import admin as A
from quizbot import engine
from quizbot import handlers_user as U
from quizbot.db import db
from quizbot.questions import insert_question
from quizbot.teams import create_team


def cb_update(data, uid=1):
    q = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=uid, full_name="x", username=None, first_name="x"), answer=AsyncMock(),
        edit_message_text=AsyncMock(), message=SimpleNamespace(reply_text=AsyncMock(), chat_id=uid),
    )
    msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=uid)
    return SimpleNamespace(
        callback_query=q, effective_user=q.from_user, effective_message=msg, effective_chat=SimpleNamespace(id=uid)
    ), q


def context(app):
    return SimpleNamespace(application=app, bot=SimpleNamespace(username="bot", send_document=AsyncMock(), send_poll=AsyncMock(), send_photo=AsyncMock(), send_message=app.bot.send_message), user_data={}, args=[])


def markup_of(q):
    return q.edit_message_text.call_args.kwargs["reply_markup"]


def test_every_admin_button_renders(app):
    db.x("INSERT INTO users(id,name) VALUES(1,'admin'),(2,'u2')")
    create_team(2, "فريق")
    qid = insert_question("سؤال", ["أ", "ب"], 0, image="https://x/i.png")
    ctx = context(app)
    for data in [
        "adm:menu", "adm:list:0", "adm:start", "adm:status", "adm:lb", "adm:teams", "adm:settings", "adm:export",
        f"q:view:{qid}:0", f"q:del:{qid}:0", f"q:poll:{qid}:0", f"q:img:{qid}:0",
        "sel:all", "sel:none", "sel:shuf", "sel:shufo", "sel:man", "sel:go", "sel:p:0",
        "tog:show_lb", "tog:manual",
        f"t:view:{db.q('SELECT id FROM teams', one=True)['id']}",
    ]:
        upd, q = cb_update(data)
    run(A.admin_cb(upd, ctx))
    assert q.answer.await_count >= 1, data
    assert any(
        b.callback_data == "adm:teamadd"
        for row in A.admin_menu().inline_keyboard
        for b in row
    )
    settings_update, settings_query = cb_update("adm:settings")
    run(A.admin_cb(settings_update, ctx))
    assert "إنشاء فرق" not in settings_query.edit_message_text.call_args.args[0]


def test_non_admin_is_rejected(app):
    upd, q = cb_update("adm:menu", uid=999)
    run(A.admin_cb(upd, context(app)))
    q.answer.assert_awaited_with("للأدمن فقط ⛔", show_alert=True)
    q.edit_message_text.assert_not_awaited()


def test_buttons_are_colored_and_serialise_for_telegram(app):
    kb = A.admin_menu()
    d = kb.to_dict()
    styles = {b["style"] for row in d["inline_keyboard"] for b in row if "style" in b}
    assert styles == {"primary", "success"}
    assert {"primary", "success", "danger"} >= styles
    assert isinstance(U.user_menu(), InlineKeyboardMarkup)
    menu = U.user_menu()
    menu_data = [b.callback_data for r in menu.inline_keyboard for b in r]
    assert "u:join" in menu_data and "u:new" not in menu_data
    # زر الخروج أحمر
    upd, q = cb_update("u:leave", uid=2)
    create_team(2, "فريق2")
    run(U.user_cb(upd, context(app)))
    styles = [b.style for r in markup_of(q).inline_keyboard for b in r]
    assert "danger" in styles


def test_admin_can_create_empty_team_but_non_admin_cannot(app):
    ctx = context(app)
    upd, q = cb_update("adm:teamadd", uid=999)
    run(A.create_team_start(upd, ctx))
    q.answer.assert_awaited_with("للأدمن فقط ⛔", show_alert=True)
    assert db.q("SELECT COUNT(*) n FROM teams", one=True)["n"] == 0

    upd, q = cb_update("adm:teamadd", uid=1)
    assert run(A.create_team_start(upd, ctx)) == A.TEAM_NAME
    message = SimpleNamespace(text="الصقور", reply_text=AsyncMock())
    result = run(A.create_team_name(SimpleNamespace(effective_user=q.from_user, message=message), ctx))
    assert result == -1
    team = db.q("SELECT * FROM teams WHERE name='الصقور'", one=True)
    assert team and team["leader_id"] is None
    assert db.q("SELECT COUNT(*) n FROM members", one=True)["n"] == 0


def test_status_screen_during_contest_has_controls(app):
    db.x("INSERT INTO users(id,name) VALUES(2,'u2')")
    create_team(2, "فريق")
    qid = insert_question("س؟", ["أ", "ب"], 0, tl=30)
    ctx = context(app)

    async def go():
        await engine.start_contest(app, [qid])
        upd, q = cb_update("adm:status")
        await A.admin_cb(upd, ctx)
        data = [b.callback_data for r in markup_of(q).inline_keyboard for b in r]
        assert {"ctl:skip", "ctl:pause", "ctl:manual", "ctl:stop"} <= set(data)
        # الإيقاف النهائي لازم يعدّي على شاشة تأكيد
        upd, q = cb_update("ctl:stop")
        await A.admin_cb(upd, ctx)
        assert "متأكد" in q.edit_message_text.call_args.args[0]
        assert "c" in engine.RT
        upd, q = cb_update("ctl:stopok")
        await A.admin_cb(upd, ctx)
        assert "c" not in engine.RT

    run(go())


def test_colors_can_be_disabled(monkeypatch):
    from quizbot import ui

    monkeypatch.setattr(ui, "COLORED_BUTTONS", False)
    assert ui.btn("x", "y", ui.GREEN).style is None
