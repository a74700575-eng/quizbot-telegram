import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["BOT_TOKEN"] = "123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
os.environ["ADMIN_IDS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from quizbot import engine
from quizbot.db import db
from quizbot.state import RT


class Job:
    def __init__(self, cb, when, data, repeating=False):
        self.cb, self.when, self.data, self.repeating, self.removed = cb, when, data, repeating, False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, cb, when, data=None, **_):
        j = Job(cb, when, data)
        self.jobs.append(j)
        return j

    def run_repeating(self, cb, interval, first=None, data=None, **_):
        j = Job(cb, interval, data, True)
        self.jobs.append(j)
        return j

    def live(self, name=None):
        return [j for j in self.jobs if not j.removed and (name is None or j.cb.__name__ == name)]


class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.photos, self.photo_captions, self.mid = [], [], [], [], 0

    async def send_message(self, chat_id, text, reply_markup=None, **_):
        self.mid += 1
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=self.mid)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **_):
        self.mid += 1
        self.photos.append((chat_id, photo))
        self.photo_captions.append((chat_id, caption, reply_markup))
        return SimpleNamespace(message_id=self.mid)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, **_):
        self.edits.append((chat_id, message_id, text, reply_markup))

    async def edit_message_caption(self, caption=None, chat_id=None, message_id=None, reply_markup=None, **_):
        self.edits.append((chat_id, message_id, caption, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None, **_):
        pass

    def texts_to(self, uid):
        return [t for c, t, _ in self.sent if c == uid]


class FakeApp:
    def __init__(self):
        self.bot, self.job_queue = FakeBot(), FakeJobQueue()


def vote_update(uid, cid, qid, choice):
    answers = []

    async def answer(text=None, show_alert=False):
        answers.append((text, show_alert))

    q = SimpleNamespace(data=f"v:{cid}:{qid}:{choice}", from_user=SimpleNamespace(id=uid), answer=answer)
    return SimpleNamespace(callback_query=q), answers


@pytest.fixture
def app(monkeypatch):
    for t in ("votes", "player_results", "results", "contests", "members", "teams", "questions", "users", "settings", "join_attempts"):
        db.x(f"DELETE FROM {t}")
    RT.clear()
    monkeypatch.setattr(engine, "LOCK", asyncio.Lock())

    async def no_sleep(_):
        return None

    monkeypatch.setattr(engine.asyncio, "sleep", no_sleep)
    return FakeApp()


def run(coro):
    return asyncio.run(coro)
