import sys
import threading
import types

from quizbot.db import DB, POSTGRES_SCHEMA, _postgres_sql
from quizbot.teams import create_empty_team


class FakeCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return {"id": 47}


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, statement, args=()):
        self.calls.append((statement.strip(), args))
        return FakeCursor()

    def close(self):
        self.closed = True


def test_postgres_placeholder_conversion_is_backend_specific():
    assert _postgres_sql("SELECT * FROM votes WHERE contest_id=? AND user_id=?") == (
        "SELECT * FROM votes WHERE contest_id=%s AND user_id=%s"
    )


def test_postgres_schema_supports_telegram_ids_and_case_insensitive_team_names():
    assert "users(id BIGINT PRIMARY KEY" in POSTGRES_SCHEMA
    assert "user_id BIGINT PRIMARY KEY" in POSTGRES_SCHEMA
    assert "idx_teams_name_lower ON teams (LOWER(name))" in POSTGRES_SCHEMA
    assert "COLLATE NOCASE" not in POSTGRES_SCHEMA


def test_postgres_initialization_uses_psycopg_and_idempotent_schema(monkeypatch):
    connection = FakeConnection()
    captured = {}

    def connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured.update(kwargs)
        return connection

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    database = DB("unused-local-path.db", "postgres://db.example/quiz")

    assert database.backend == "postgres"
    assert captured["dsn"] == "postgresql://db.example/quiz"
    assert captured["autocommit"] is True
    assert captured["connect_timeout"] == 10
    assert any("CREATE TABLE IF NOT EXISTS contests" in sql for sql, _ in connection.calls)
    assert any("ADD COLUMN IF NOT EXISTS completed_idx" in sql for sql, _ in connection.calls)


def test_postgres_insert_returns_generated_id_with_returning():
    connection = FakeConnection()
    database = DB.__new__(DB)
    database.backend = "postgres"
    database.conn = connection
    database._lock = threading.RLock()

    inserted_id = database.x(
        "INSERT INTO questions(text) VALUES(?)",
        ("sample",),
        return_id=True,
    )

    assert inserted_id == 47
    assert connection.calls == [
        ("INSERT INTO questions(text) VALUES(%s) RETURNING id", ("sample",))
    ]


def test_team_names_remain_case_insensitive_with_portable_sql(app):
    first, error = create_empty_team("The Hawks")
    assert error is None and first is not None

    duplicate, error = create_empty_team("the hawks")
    assert duplicate is None
    assert "مستخدم" in error
