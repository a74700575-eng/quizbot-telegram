import json

import pytest

from quizbot.board import board_rows
from quizbot.db import db
from quizbot.questions import import_questions, validate
from quizbot.scoring import bar, majority, participation_ok, ranked, speed_bonus
from quizbot.teams import (
    create_empty_team,
    create_team,
    delete_team,
    join_team,
    team_of,
)


def test_majority_basic_and_none():
    assert majority([]) == (None, None)
    assert majority([(1, 1.0), (1, 2.0), (2, 0.5)]) == (1, 2.0)


def test_majority_tie_leader_wins_then_earliest():
    votes = [(0, 1.0), (1, 2.0)]
    assert majority(votes, leader_choice=1)[0] == 1
    assert majority(votes, leader_choice=0)[0] == 0
    assert majority(votes, leader_choice=None)[0] == 0  # أسرع وصول
    assert majority(votes, leader_choice=2)[0] == 0  # قائد صوّت لاختيار مش من المتعادلين


def test_speed_bonus():
    assert speed_bonus(10, 50, 0, 60) == 5
    assert speed_bonus(10, 50, 30, 60) == 2 or speed_bonus(10, 50, 30, 60) == 3
    assert speed_bonus(10, 50, 60, 60) == 0
    assert speed_bonus(10, 0, 0, 60) == 0
    assert speed_bonus(10, 50, -5, 60) == 5  # صوت وقت الإرسال ماينفعش يدّي أكتر من الحد الأقصى


def test_participation():
    assert participation_ok(1, 2, 50)
    assert not participation_ok(2, 5, 50)
    assert participation_ok(3, 5, 50)
    assert not participation_ok(0, 3, 0)
    assert participation_ok(1, 5, 0)


def test_ranked_ties():
    rows = [{"pts": 10, "ok": 1}, {"pts": 10, "ok": 1}, {"pts": 5, "ok": 1}]
    assert [r for r, _ in ranked(rows)] == [1, 1, 3]


def test_bar():
    assert bar(60, 60) == "▰" * 10
    assert bar(0, 60) == "▱" * 10


def test_validate_errors_are_specific():
    ok = {"question": "س؟", "options": ["أ", "ب"], "answer": 2}
    assert validate(ok)["correct"] == 1
    for bad, msg in [
        ({**ok, "answer": 5}, "answer"),
        ({**ok, "options": ["أ"]}, "عدد الاختيارات"),
        ({**ok, "question": ""}, "فاضي"),
        ({**ok, "time": 2}, "time"),
        ({**ok, "answer": None}, "answer"),
    ]:
        with pytest.raises(ValueError, match=msg):
            validate(bad)


def test_import_json_dedup_and_errors(app):
    raw = json.dumps(
        [
            {"question": "س1؟", "options": ["أ", "ب"], "answer": 1, "image": "https://x/y.png"},
            {"question": "س1؟", "options": ["أ", "ب"], "answer": 1},
            {"question": "س2؟", "options": ["أ", "ب"], "answer": 9},
        ],
        ensure_ascii=False,
    )
    ok, dup, errs = import_questions(raw)
    assert (ok, dup, len(errs)) == (1, 1, 1) and "#3" in errs[0]
    assert db.q("SELECT image FROM questions", one=True)["image"] == "https://x/y.png"
    assert import_questions(raw)[:2] == (0, 2)  # إعادة الاستيراد ماتكررش


def test_import_html(app):
    html = """
    <html><body>
    <div class="q" data-answer="2" data-time="45" data-points="20">
      <h3>ما عاصمة مصر؟</h3><ol><li>أسوان</li><li>القاهرة</li></ol><p class="exp">القاهرة هي العاصمة</p>
    </div>
    <div class="question" data-image="https://x/i.png">
      <p>سؤال بالـ class</p><ul><li>خطأ</li><li class="correct">صح</li><li>خطأ</li></ul>
    </div>
    <div class="q"><h3>سؤال ناقص</h3><ol><li>واحد</li></ol></div>
    </body></html>"""
    ok, _dup, errs = import_questions(html)
    assert ok == 2 and len(errs) == 1
    r = db.q("SELECT * FROM questions ORDER BY id")
    assert r[0]["correct"] == 1 and r[0]["time_limit"] == 45 and r[0]["points"] == 20 and r[0]["explanation"].startswith("القاهرة")
    assert r[1]["correct"] == 1 and r[1]["image"] == "https://x/i.png"


def test_import_reports_wrong_json_types_without_crashing():
    ok, duplicate, errors = import_questions(json.dumps([None, {"question": "سؤال", "options": "ليست قائمة", "answer": 1}]))
    assert ok == duplicate == 0 and len(errors) == 2


def test_html_without_questions_is_an_error(app):
    with pytest.raises(ValueError):
        import_questions("<html><body>مفيش حاجة</body></html>")


def test_deleted_team_keeps_points_on_board(app):
    t, _ = create_team(10, "الصقور")
    db.x(
        "INSERT INTO results(contest_id,qid,team_id,team_name,is_correct,points) VALUES(1,1,?,?,1,15)",
        (t["id"], t["name"]),
    )
    delete_team(t["id"])
    rows = board_rows(1)
    assert len(rows) == 1 and rows[0]["pts"] == 15 and rows[0]["n"] == 0


def test_team_rules(app):
    t, _ = create_team(10, "فريق")
    assert len(t["code"]) == 8
    assert create_team(11, "فريق")[1]  # الاسم مكرر
    for uid in range(11, 15):
        assert join_team(uid, t["code"])[1] is None
    assert "مكتمل" in join_team(99, t["code"])[1]


def test_invalid_invite_codes_are_rate_limited_and_success_clears_attempts(app):
    t, _ = create_team(10, "فريق")
    t2, _ = create_team(20, "فريق ثاني")
    for _ in range(5):
        assert "الكود غلط" in join_team(99, "INVALID1")[1]
    assert "محاولات كثيرة" in join_team(99, t["code"])[1]

    # عضو جديد ينجح مباشرة، والنجاح يمسح سجل محاولاته السابقة.
    for _ in range(4):
        join_team(98, "INVALID1")
    assert join_team(98, t2["code"])[1] is None
    assert db.q("SELECT COUNT(*) n FROM join_attempts WHERE user_id=98", one=True)["n"] == 0


def test_first_member_of_admin_created_team_becomes_leader(app):
    team, error = create_empty_team("النسور")
    assert error is None and team["leader_id"] is None
    joined, error = join_team(77, team["code"])
    assert error is None and joined["leader_id"] == 77
    assert team_of(77)["id"] == team["id"]
