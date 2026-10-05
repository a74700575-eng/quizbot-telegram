import asyncio
import time
from types import SimpleNamespace

from apscheduler.jobstores.base import JobLookupError
from conftest import run, vote_update

from quizbot import engine
from quizbot.board import board_rows
from quizbot.db import db
from quizbot.questions import insert_question
from quizbot.state import RT
from quizbot.teams import create_team, join_team


def setup_teams():
    """AA: 3 أعضاء (1,2,3) · BB: عضو واحد (4) · CC: 3 أعضاء (5,6,7)"""
    ids = {}
    for name, uids in (("AA", [1, 2, 3]), ("BB", [4]), ("CC", [5, 6, 7])):
        for u in uids:
            db.x(
                "INSERT INTO users(id,name) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (u, f"user{u}"),
            )
        t, _ = create_team(uids[0], name)
        for u in uids[1:]:
            assert join_team(u, t["code"])[1] is None
        ids[name] = t["id"]
    return ids


async def vote(app, uid, choice):
    c = RT["c"]
    upd, answers = vote_update(uid, c["id"], c["qid"], choice)
    await engine.on_vote(upd, SimpleNamespace(application=app))
    return answers[0]


def ctx(app, job):
    return SimpleNamespace(application=app, bot=app.bot, job=job)


def test_end_callback_tolerates_its_one_shot_job_already_being_removed(app):
    setup_teams()
    qid = insert_question("سؤال؟", ["أ", "ب"], 0, tl=30)

    class CompletedJob:
        def schedule_removal(self):
            raise JobLookupError("No job by the id was found")

    async def go():
        await engine.start_contest(app, [qid])
        c = RT["c"]
        c["job_end"] = CompletedJob()
        await engine._end_job(ctx(app, SimpleNamespace(data=c["id"])))
        assert not RT["c"]["open"]
        assert db.q("SELECT COUNT(*) n FROM results", one=True)["n"] == 3

    run(go())


def test_automatic_next_question_survives_completed_scheduler_job(app):
    setup_teams()
    q1 = insert_question("سؤال أول؟", ["أ", "ب"], 0, tl=30)
    q2 = insert_question("سؤال تاني؟", ["أ", "ب"], 1, tl=30)

    class CompletedJob:
        def schedule_removal(self):
            raise JobLookupError("No job by the id was found")

    async def go():
        await engine.start_contest(app, [q1, q2])
        c = RT["c"]
        await engine.end_question(app, c["id"])
        c["job_next"] = CompletedJob()
        await engine._next_job(ctx(app, SimpleNamespace(data=c["id"])))
        assert RT["c"]["qid"] == q2 and RT["c"]["open"]

    run(go())


def test_full_contest_flow(app):
    setup_teams()
    q1 = insert_question("س1؟", ["أ", "ب", "ج"], 1, "شرح 1", tl=60, pts=10, image="https://x/1.png")
    q2 = insert_question("س2؟", ["أ", "ب"], 0, "", tl=30, pts=10)

    async def go():
        ok, _ = await engine.start_contest(app, [q1, q2])
        assert ok
        c = RT["c"]
        assert c["open"] and len(c["msgs"]) == 7
        assert len(app.bot.photos) == 7  # الصورة وصلت للكل
        # المؤقتات اتجدولت بعد الإرسال
        assert app.job_queue.live("_end_job")
        assert not app.job_queue.live("_warn_job") and not app.job_queue.live("_tick_job")

        # الفريق A إجماع صحيح · B إجابة فردية خاطئة · C عضو واحد صحيح لكن مفيش إجماع
        assert app.bot.photo_captions[0][1] == "س1؟"
        assert all(b.style == "success" for row in app.bot.photo_captions[0][2].inline_keyboard for b in row)
        for uid, ch in ((1, 1), (2, 1), (3, 1), (4, 0), (5, 1)):
            _text, alert = await vote(app, uid, ch)
            assert not alert
        await engine.end_question(app, c["id"])

        res = {r["team_name"]: r for r in db.q("SELECT * FROM results WHERE qid=?", (q1,))}
        assert res["AA"]["is_correct"] == 1 and res["AA"]["points"] > 10 and res["AA"]["bonus"] > 0  # مكافأة سرعة
        assert res["BB"]["points"] == 0
        assert res["CC"]["is_correct"] == 0 and res["CC"]["points"] == 0  # لا يوجد إجماع
        player = db.q("SELECT points,is_correct FROM player_results WHERE user_id=5 AND qid=?", (q1,), one=True)
        assert player["is_correct"] == 1 and player["points"] > 10  # نقاط فردية حتى بدون إجماع الفريق
        assert any("لم يتفق كل أعضاء الفريق" in t for t in app.bot.texts_to(5))
        assert any("شرح 1" in t for t in app.bot.texts_to(1))
        # الجدولة اتعملت بعد إرسال النتايج
        nxt = app.job_queue.live("_next_job")
        assert len(nxt) == 1

        await nxt[0].cb(ctx(app, nxt[0]))
        assert RT["c"]["qid"] == q2 and RT["c"]["open"]
        assert app.bot.texts_to(4)[-1] == "س2؟"
        await vote(app, 4, 0)
        await engine.end_question(app, c["id"])  # آخر سؤال
        nxt = app.job_queue.live("_next_job")
        assert len(nxt) == 1
        await nxt[0].cb(ctx(app, nxt[0]))
        assert "c" not in RT
        assert db.q("SELECT status FROM contests", one=True)["status"] == "finished"
        assert any("الترتيب النهائي" in t for t in app.bot.texts_to(1))
        rows = board_rows(c["id"])
        assert rows[0]["name"] == "AA"

    run(go())


def test_overlapping_vote_callbacks_are_safe_and_rejected_after_end(app):
    for u in range(100, 160):
        db.x("INSERT INTO users(id,name) VALUES(?,?)", (u, f"u{u}"))
    t, _ = create_team(100, "كبير")
    for u in range(101, 105):
        join_team(u, t["code"])
    for k in range(10):  # 10 فرق صغيرة كمان
        tt, _ = create_team(110 + k * 4, f"ف{k}")
        for u in range(111 + k * 4, 114 + k * 4):
            join_team(u, tt["code"])
    q = insert_question("س؟", ["أ", "ب"], 0, tl=60)

    async def go():
        await engine.start_contest(app, [q])
        c = RT["c"]
        uids = [r["user_id"] for r in db.q("SELECT user_id FROM members")]
        t0 = time.time()
        results = await asyncio.gather(*(vote(app, u, 0) for u in uids))
        assert all(not alert for _, alert in results)
        assert db.q("SELECT COUNT(*) n FROM votes", one=True)["n"] == len(uids)
        assert time.time() - t0 < 2
        # التحديث متجمّع: مهمة واحدة لكل فريق مش لكل صوت
        assert len(app.job_queue.live("_refresh_job")) == 11
        await engine.end_question(app, c["id"])
        text, alert = await vote(app, uids[0], 1)
        assert alert and "انتهى" in text

    run(go())


def test_negative_vote_choice_is_rejected(app):
    setup_teams()
    qid = insert_question("س؟", ["أ", "ب"], 0, tl=30)

    async def go():
        await engine.start_contest(app, [qid])
        c = RT["c"]
        for choice in (-1, len(c["opts"])):
            upd, answers = vote_update(1, c["id"], c["qid"], choice)
            await engine.on_vote(upd, SimpleNamespace(application=app))
            assert answers == [("اختيار غير صالح", True)]
        assert db.q("SELECT COUNT(*) n FROM votes", one=True)["n"] == 0

    run(go())


def test_pause_resume_blocks_votes_and_keeps_remaining_time(app):
    setup_teams()
    q = insert_question("س؟", ["أ", "ب"], 0, tl=60)

    async def go():
        await engine.start_contest(app, [q])
        c = RT["c"]
        assert await engine.pause_contest(app)
        assert c["paused"] and not app.job_queue.live("_end_job")
        assert 55 < c["remaining"] <= 60
        text, alert = await vote(app, 1, 0)
        assert alert and "متوقفة" in text
        assert await engine.resume_contest(app)
        assert not c["paused"] and len(app.job_queue.live("_end_job")) == 1
        assert (await vote(app, 1, 0))[1] is False
        # الإيقاف بين الأسئلة
        await engine.end_question(app, c["id"])
        assert await engine.pause_contest(app)
        assert not app.job_queue.live("_next_job")

    run(go())


def test_manual_mode_waits_for_admin_but_last_question_finishes(app):
    setup_teams()
    q1 = insert_question("س1؟", ["أ", "ب"], 0, tl=30)
    q2 = insert_question("س2؟", ["أ", "ب"], 0, tl=30)

    async def go():
        await engine.start_contest(app, [q1, q2], manual=True)
        c = RT["c"]
        await engine.end_question(app, c["id"])
        assert not app.job_queue.live("_next_job")  # مستني الأدمن
        await engine.next_question(app)
        assert RT["c"]["qid"] == q2
        await engine.end_question(app, c["id"])
        assert len(app.job_queue.live("_next_job")) == 1  # آخر سؤال بينتهي لوحده

    run(go())


def test_shuffled_options_still_score_correctly(app):
    setup_teams()
    q = insert_question("س؟", ["صح", "غلط1", "غلط2", "غلط3"], 0, tl=30, pts=10)

    async def go():
        await engine.start_contest(app, [q], shuffle_options=True)
        c = RT["c"]
        assert sorted(c["opts"]) == sorted(["صح", "غلط1", "غلط2", "غلط3"])
        assert c["opts"][c["correct"]] == "صح"
        for u in (1, 2, 3):
            await vote(app, u, c["correct"])
        await engine.end_question(app, c["id"])
        assert db.q("SELECT is_correct FROM results WHERE team_name='AA'", one=True)["is_correct"] == 1

    run(go())


def test_interrupted_contest_can_be_resumed(app):
    setup_teams()
    q1 = insert_question("س1؟", ["أ", "ب"], 0, tl=30)
    q2 = insert_question("س2؟", ["أ", "ب"], 0, tl=30)

    async def go():
        await engine.start_contest(app, [q1, q2])
        c = RT["c"]
        await vote(app, 1, 0)
        await engine.end_question(app, c["id"])
        nxt = app.job_queue.live("_next_job")[0]
        await nxt.cb(ctx(app, nxt))
        assert RT["c"]["qid"] == q2
        await vote(app, 4, 1)
        # محاكاة إعادة تشغيل البوت: الذاكرة بتتمسح والمسابقة بتتعلّم مقطوعة
        RT.clear()
        db.x("UPDATE contests SET status='interrupted' WHERE status='running'")
        assert engine.interrupted_contest()
        ok, _ = await engine.resume_interrupted(app)
        assert ok and RT["c"]["qid"] == q2 and RT["c"]["id"] == c["id"]
        assert db.q("SELECT COUNT(*) n FROM votes WHERE qid=?", (q2,), one=True)["n"] == 0  # السؤال بيتعاد من الأول
        assert db.q("SELECT COUNT(*) n FROM results WHERE qid=?", (q1,), one=True)["n"] == 3  # نقاط السؤال الأول محفوظة

    run(go())


def test_recovery_in_inter_question_gap_does_not_replay_completed_question(app):
    setup_teams()
    q1 = insert_question("س1؟", ["أ", "ب"], 0, tl=30)
    q2 = insert_question("س2؟", ["أ", "ب"], 0, tl=30)

    async def go():
        await engine.start_contest(app, [q1, q2])
        c = RT["c"]
        await vote(app, 1, 0)
        await engine.end_question(app, c["id"])
        assert db.q("SELECT completed_idx FROM contests WHERE id=?", (c["id"],), one=True)["completed_idx"] == 0

        RT.clear()
        db.x("UPDATE contests SET status='interrupted' WHERE id=?", (c["id"],))
        ok, _ = await engine.resume_interrupted(app)
        assert ok and RT["c"]["qid"] == q2 and RT["c"]["idx"] == 1
        assert db.q("SELECT COUNT(*) n FROM results WHERE contest_id=? AND qid=?", (c["id"], q1), one=True)["n"] == 3

    run(go())


def test_enabling_manual_mode_cancels_pending_auto_advance(app):
    setup_teams()
    q1 = insert_question("س1؟", ["أ", "ب"], 0, tl=30)
    q2 = insert_question("س2؟", ["أ", "ب"], 0, tl=30)

    async def go():
        await engine.start_contest(app, [q1, q2])
        c = RT["c"]
        await engine.end_question(app, c["id"])
        pending = app.job_queue.live("_next_job")[0]
        assert await engine.toggle_manual(app) is True
        assert not app.job_queue.live("_next_job")
        await pending.cb(ctx(app, pending))
        assert RT["c"]["idx"] == 0 and not RT["c"]["open"] and RT["c"]["manual"]

    run(go())


def test_split_team_scores_individuals_but_not_team_consensus(app):
    for u in (20, 21):
        db.x("INSERT INTO users(id,name) VALUES(?,?)", (u, f"u{u}"))
    t, _ = create_team(20, "ثنائي")  # 20 هو القائد
    join_team(21, t["code"])
    q = insert_question("س؟", ["أ", "ب"], 1, tl=30)
    async def go():
        await engine.start_contest(app, [q])
        c = RT["c"]
        await vote(app, 21, 0)  # عضو أجاب خطأ
        await vote(app, 20, 1)  # القائد أجاب صحيحًا، لكن لا يوجد إجماع
        await engine.end_question(app, c["id"])
        team_result = db.q("SELECT is_correct,points FROM results", one=True)
        assert team_result["is_correct"] == 0 and team_result["points"] == 0
        players = {r["user_id"]: r for r in db.q("SELECT * FROM player_results")}
        assert players[20]["is_correct"] == 1 and players[20]["points"] > 0
        assert players[21]["is_correct"] == 0 and players[21]["points"] == 0

    run(go())


def test_leaving_mid_contest_does_not_lose_team_points(app):
    setup_teams()
    q = insert_question("س؟", ["أ", "ب"], 0, tl=30, pts=10)

    async def go():
        await engine.start_contest(app, [q])
        c = RT["c"]
        await vote(app, 4, 0)
        await engine.end_question(app, c["id"])
        from quizbot.teams import leave_team

        leave_team(4)  # العضو الوحيد في B خرج → الفريق اتحذف
        names = [r["name"] for r in board_rows(c["id"])]
        assert "BB" in names

    run(go())
