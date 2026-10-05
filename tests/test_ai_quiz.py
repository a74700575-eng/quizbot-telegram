import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import run
from test_handlers import cb_update, context, markup_of

from quizbot import admin as A
from quizbot import ai_quiz
from quizbot.db import db
from quizbot.questions import get_question

RAW_VALID = "ما عاصمة مصر؟ أ) القاهرة ب) الإسكندرية الإجابة: القاهرة"
VALID = {
    "source_order": 1,
    "source_excerpt": RAW_VALID,
    "question": "ما عاصمة مصر؟",
    "options": ["القاهرة", "الإسكندرية"],
    "answer_known": True,
    "correct_index": 0,
    "explanation": "القاهرة هي العاصمة.",
    "issue": "",
}


def test_prepare_questions_never_guesses_missing_or_ambiguous_answers():
    ready, review = ai_quiz.prepare_questions(
        {
            "questions": [
                VALID,
                {
                    **VALID,
                    "source_order": 2,
                    "question": "سؤال بلا إجابة",
                    "answer_known": False,
                    "issue": "الإجابة غير موجودة",
                },
                {
                    **VALID,
                    "source_order": 3,
                    "question": "سؤال ملتبس",
                    "answer_known": False,
                    "issue": "مصدر الإجابة غير واضح",
                },
            ]
        }
    )
    assert len(ready) == 1
    assert ready[0]["correct_index"] == 0
    assert [x["question"] for x in review] == ["سؤال بلا إجابة", "سؤال ملتبس"]
    assert all(x["note"] for x in review)


def test_prepare_questions_flags_invalid_choice_counts_and_indices():
    ready, review = ai_quiz.prepare_questions(
        {
            "questions": [
                {**VALID, "options": ["اختيار واحد"]},
                {**VALID, "source_order": 2, "correct_index": 9},
            ]
        }
    )
    assert not ready
    assert len(review) == 2


def test_prepare_questions_restores_source_order_and_verifies_exact_source_excerpts():
    first_source = "1- الأول؟ أ) نعم ب) لا الإجابة: نعم"
    second_source = "2- الثاني؟ أ) 1 ب) 2 الإجابة: 2"
    first = {
        **VALID,
        "source_order": 1,
        "source_excerpt": first_source,
        "question": "الأول؟",
        "options": ["نعم", "لا"],
        "correct_index": 0,
    }
    second = {
        **VALID,
        "source_order": 2,
        "source_excerpt": second_source,
        "question": "الثاني؟",
        "options": ["1", "2"],
        "correct_index": 1,
    }

    ready, review = ai_quiz.prepare_questions(
        {"questions": [second, first]}, raw=f"{first_source}\n{second_source}"
    )

    assert not review
    assert [item["source_number"] for item in ready] == [1, 2]
    assert [item["question"] for item in ready] == ["الأول؟", "الثاني؟"]


def test_prepare_questions_blocks_the_whole_batch_if_source_order_cannot_be_verified():
    first_source = "1- الأول؟ أ) نعم ب) لا الإجابة: نعم"
    second_source = "2- الثاني؟ أ) 1 ب) 2 الإجابة: 2"
    first = {**VALID, "source_order": 1, "source_excerpt": second_source}
    second = {**VALID, "source_order": 2, "source_excerpt": first_source}

    ready, review = ai_quiz.prepare_questions(
        {"questions": [first, second]}, raw=f"{first_source}\n{second_source}"
    )

    assert not ready
    assert len(review) == 2
    assert all("مقتطف السؤال" in item["note"] for item in review)


def test_prepare_questions_rejects_missing_or_duplicate_source_positions():
    with pytest.raises(ai_quiz.AIQuizError, match="ترتيب"):
        ai_quiz.prepare_questions({"questions": [VALID, {**VALID, "source_order": 1}]})


def test_parse_quiz_text_uses_strict_schema_and_openai_compatible_provider(monkeypatch):
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"questions": [VALID]}, ensure_ascii=False)))]
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self):
            captured["closed"] = True

    monkeypatch.setattr(ai_quiz, "AI_API_KEY", "test-key")
    monkeypatch.setattr(ai_quiz, "AI_PROVIDER", "openai")
    monkeypatch.setattr(ai_quiz, "AsyncOpenAI", FakeClient)
    ready, review = run(ai_quiz.parse_quiz_text(RAW_VALID))
    assert len(ready) == 1 and not review
    assert captured["model"] == ai_quiz.AI_MODEL
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert "source_order" in captured["response_format"]["json_schema"]["schema"]["properties"]["questions"]["items"]["required"]
    assert captured["max_completion_tokens"] > 0 and captured["closed"] is True


def test_parse_quiz_text_uses_native_gemini_sdk_and_json_schema(monkeypatch):
    from google import genai

    captured = {"closed": False}

    class FakeModels:
        async def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text=json.dumps({"questions": [VALID]}, ensure_ascii=False))

    class FakeAio:
        def __init__(self):
            self.models = FakeModels()

        async def aclose(self):
            captured["closed"] = True

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.aio = FakeAio()

    monkeypatch.setattr(ai_quiz, "AI_API_KEY", "test-key")
    monkeypatch.setattr(ai_quiz, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(ai_quiz, "AI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(genai, "Client", FakeClient)

    ready, review = run(ai_quiz.parse_quiz_text(RAW_VALID))

    assert len(ready) == 1 and not review
    assert captured["client"]["api_key"] == "test-key"
    assert captured["model"] == "gemini-3.8-flash"
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_schema is ai_quiz._GeminiBatch
    assert captured["closed"] is True


def test_parse_quiz_text_reports_missing_provider_choices(monkeypatch):
    captured = {"closed": False}

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=None)

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self):
            captured["closed"] = True

    monkeypatch.setattr(ai_quiz, "AI_API_KEY", "test-key")
    monkeypatch.setattr(ai_quiz, "AI_PROVIDER", "openai")
    monkeypatch.setattr(ai_quiz, "AsyncOpenAI", FakeClient)
    with pytest.raises(ai_quiz.AIQuizError, match="اختيارًا قابلًا للقراءة"):
        run(ai_quiz.parse_quiz_text("سؤال اصطناعي"))
    assert captured["closed"]


def test_parse_quiz_text_requires_api_key(monkeypatch):
    monkeypatch.setattr(ai_quiz, "AI_API_KEY", "")
    with pytest.raises(ai_quiz.AIQuizError, match="AI_API_KEY"):
        run(ai_quiz.parse_quiz_text("سؤال"))


def test_admin_review_is_required_before_questions_are_inserted(app):
    ctx = context(app)
    ctx.user_data["ai_import_ready"] = [
        {
            "source_number": 1,
            "question": VALID["question"],
            "options": VALID["options"],
            "correct_index": 0,
            "explanation": VALID["explanation"],
            "image": "telegram-photo-file-id",
        }
    ]
    ctx.user_data["ai_import_flagged"] = [{"question": "لا تضف هذا", "note": "إجابته غير واضحة"}]
    assert db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] == 0

    update, query = cb_update("ai:confirm", uid=1)
    run(A.ai_import_review(update, ctx))
    assert db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] == 1
    question_id = db.q("SELECT id FROM questions WHERE text=?", (VALID["question"],), one=True)["id"]
    saved = get_question(question_id)
    assert saved["text"] == VALID["question"] and saved["correct"] == 0
    assert saved["image"] == "telegram-photo-file-id"
    assert "الأسئلة التي ظهرت بعلامة المراجعة" in query.edit_message_text.call_args.args[0]
    assert "ai_import_ready" not in ctx.user_data


def test_ai_import_photo_is_mapped_by_preview_question_number(app):
    ctx = context(app)
    ctx.user_data["ai_import_ready"] = [{
        "source_number": 2,
        "question": "ما عاصمة مصر؟",
        "options": ["القاهرة", "الإسكندرية"],
        "correct_index": 0,
        "explanation": "",
        "image": None,
    }]
    photo = SimpleNamespace(file_id="small-photo"), SimpleNamespace(file_id="largest-photo")
    message = SimpleNamespace(photo=photo, caption="2", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, full_name="Admin"), effective_message=message, message=message
    )

    assert run(A.ai_import_image(update, ctx)) == A.AI_REVIEW
    assert ctx.user_data["ai_import_ready"][0]["image"] == "largest-photo"
    assert "السؤال 2" in message.reply_text.await_args.args[0]


def test_admin_can_repair_flagged_ai_question_before_confirmation(app):
    ctx = context(app)
    flagged = {
        **VALID,
        "source_number": 2,
        "correct_index": None,
        "answer_known": False,
        "note": "الإجابة غير مؤكدة",
        "image": "existing-photo-id",
    }
    ctx.user_data["ai_import_ready"] = []
    ctx.user_data["ai_import_flagged"] = [flagged]

    update, query = cb_update("ai:editmenu")
    assert run(A.ai_import_review(update, ctx)) == A.AI_REVIEW
    assert any(
        button.callback_data == "ai:manage:2"
        for row in markup_of(query).inline_keyboard
        for button in row
    )

    update, query = cb_update("ai:manage:2")
    run(A.ai_import_review(update, ctx))
    assert any(
        button.callback_data == "ai:edit:2"
        for row in markup_of(query).inline_keyboard
        for button in row
    )

    update, _query = cb_update("ai:edit:2")
    assert run(A.ai_import_review(update, ctx)) == A.AI_EDIT
    assert ctx.user_data["ai_edit_target"] == 2

    invalid_message = SimpleNamespace(text="not JSON", chat_id=1, reply_text=AsyncMock())
    invalid_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, full_name="Admin"),
        effective_chat=SimpleNamespace(id=1),
        effective_message=invalid_message,
        message=invalid_message,
    )
    assert run(A.ai_import_edit_text(invalid_update, ctx)) == A.AI_EDIT
    assert ctx.user_data["ai_edit_target"] == 2
    assert ctx.user_data["ai_import_ready"] == []

    message = SimpleNamespace(
        text=json.dumps(
            {
                "question": "ما عاصمة مصر؟",
                "options": ["القاهرة", "الإسكندرية"],
                "answer": 1,
                "explanation": "القاهرة هي العاصمة.",
            },
            ensure_ascii=False,
        ),
        chat_id=1,
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, full_name="Admin"),
        effective_chat=SimpleNamespace(id=1),
        effective_message=message,
        message=message,
    )
    assert run(A.ai_import_edit_text(update, ctx)) == A.AI_REVIEW
    assert ctx.user_data["ai_import_ready"][0]["source_number"] == 2
    assert ctx.user_data["ai_import_ready"][0]["correct_index"] == 0
    assert ctx.user_data["ai_import_ready"][0]["image"] == "existing-photo-id"
    assert ctx.user_data["ai_import_flagged"] == []
    assert "ai_edit_target" not in ctx.user_data
    assert db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] == 0


def test_ai_review_cleanup_disables_old_preview_buttons(app):
    ctx = context(app)
    ctx.user_data["ai_preview_chat_id"] = 1
    ctx.user_data["ai_preview_message_ids"] = [101, 102]

    run(A.clear_ai_review_preview(ctx))

    assert ctx.bot.edit_message_reply_markup.await_count == 2
    assert ctx.bot.edit_message_reply_markup.await_args_list[0].kwargs == {
        "chat_id": 1,
        "message_id": 101,
        "reply_markup": None,
    }
    assert "ai_preview_message_ids" not in ctx.user_data


def test_admin_can_exclude_a_question_from_ai_batch_without_importing_it(app):
    ctx = context(app)
    ctx.user_data["ai_import_ready"] = [
        {
            "source_number": 1,
            "question": VALID["question"],
            "options": VALID["options"],
            "correct_index": 0,
            "explanation": VALID["explanation"],
            "image": None,
        }
    ]
    ctx.user_data["ai_import_flagged"] = []

    update, query = cb_update("ai:remove:1")
    assert run(A.ai_import_review(update, ctx)) == A.AI_REVIEW
    assert ctx.user_data["ai_import_ready"] == []
    assert ctx.user_data["ai_import_flagged"][0]["note"] == "استبعده الأدمن من دفعة الاستيراد."
    callbacks = [button.callback_data for row in markup_of(query).inline_keyboard for button in row]
    assert "ai:confirm" not in callbacks
    assert "ai:cancel" in callbacks
    assert db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] == 0


def test_ai_import_command_is_admin_only_and_explains_external_processing(app, monkeypatch):
    import quizbot.admin.questions as admin_questions

    monkeypatch.setattr(admin_questions, "AI_API_KEY", "test-key")
    ctx = context(app)
    update, query = cb_update("adm:aiimport", uid=999)
    run(A.ai_import_start(update, ctx))
    query.answer.assert_awaited_with("للأدمن فقط ⛔", show_alert=True)
    assert query.message.reply_text.call_count == 0

    update, query = cb_update("adm:aiimport", uid=1)
    message = update.effective_message
    assert run(A.ai_import_start(update, ctx)) == A.AI_INPUT
    prompt = message.reply_text.call_args.args[0]
    assert "مزود الذكاء الاصطناعي" in prompt and "لن تُضاف" in prompt


def test_ai_import_without_provider_key_does_not_accept_question_text(app, monkeypatch):
    import quizbot.admin.questions as admin_questions

    monkeypatch.setattr(admin_questions, "AI_API_KEY", "")
    ctx = context(app)
    update, _query = cb_update("adm:aiimport", uid=1)
    result = run(admin_questions.ai_import_start(update, ctx))
    assert result == -1
    assert "أضف AI_API_KEY" in update.effective_message.reply_text.call_args.args[0]


def test_ai_parser_preview_marks_ambiguous_items_and_only_offers_valid_items(app, monkeypatch):
    import quizbot.admin.questions as admin_questions

    async def fake_parse(_raw):
        ready, flagged = ai_quiz.prepare_questions(
            {
                "questions": [
                    VALID,
                    {
                        **VALID,
                        "source_order": 2,
                        "question": "سؤال بإجابة مفقودة",
                        "answer_known": False,
                        "issue": "الإجابة مفقودة",
                    },
                ]
            }
        )
        return ready, flagged

    monkeypatch.setattr(admin_questions, "parse_quiz_text", fake_parse)
    ctx = context(app)
    ctx.bot.send_message = app.bot.send_message
    user = SimpleNamespace(id=1, full_name="Admin")
    message = SimpleNamespace(text="نص خام", document=None, chat_id=1, reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=user, message=message)
    result = run(admin_questions.ai_import_parse(update, ctx))
    assert result == A.AI_REVIEW
    assert len(ctx.user_data["ai_import_ready"]) == 1
    preview = message.reply_text.call_args_list[-1].args[0]
    assert "1 سؤال جاهز" in preview and "لن يُضاف" in preview
    assert "من النص الأصلي" in preview
    assert db.q("SELECT COUNT(*) n FROM questions", one=True)["n"] == 0
