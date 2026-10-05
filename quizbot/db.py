"""قاعدة بيانات SQLite محليًا أو PostgreSQL عند ضبط DATABASE_URL."""
import os
import re
import sqlite3
import threading

from .config import DATABASE_URL, DB_PATH

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT);
CREATE TABLE IF NOT EXISTS teams(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE COLLATE NOCASE,
    code TEXT UNIQUE, leader_id INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS members(
    user_id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE, joined_at REAL);
CREATE TABLE IF NOT EXISTS questions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, options TEXT, correct INTEGER,
    explanation TEXT, time_limit INTEGER, points INTEGER, created_at REAL, image TEXT);
CREATE TABLE IF NOT EXISTS contests(
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, started_at REAL, ended_at REAL,
    qids TEXT, idx INTEGER DEFAULT -1, flags TEXT, completed_idx INTEGER DEFAULT -1);
CREATE TABLE IF NOT EXISTS votes(
    contest_id INTEGER, qid INTEGER, user_id INTEGER, team_id INTEGER, choice INTEGER, ts REAL,
    PRIMARY KEY(contest_id, qid, user_id));
CREATE TABLE IF NOT EXISTS results(
    contest_id INTEGER, qid INTEGER, team_id INTEGER, team_name TEXT, q_text TEXT,
    chosen_text TEXT, correct_text TEXT, is_correct INTEGER, points INTEGER,
    bonus INTEGER DEFAULT 0, voters INTEGER DEFAULT 0, members INTEGER DEFAULT 0,
    PRIMARY KEY(contest_id, qid, team_id));
CREATE TABLE IF NOT EXISTS player_results(
    contest_id INTEGER, qid INTEGER, user_id INTEGER, team_id INTEGER, player_name TEXT,
    choice INTEGER, is_correct INTEGER, points INTEGER DEFAULT 0, bonus INTEGER DEFAULT 0,
    PRIMARY KEY(contest_id, qid, user_id));
CREATE INDEX IF NOT EXISTS idx_player_results_user ON player_results(user_id);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS join_attempts(user_id INTEGER NOT NULL, attempted_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_join_attempts_user_time ON join_attempts(user_id, attempted_at);
"""

# BIGINT is required for Telegram user IDs; PostgreSQL INTEGER is only 32-bit.
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id BIGINT PRIMARY KEY, name TEXT, username TEXT);
CREATE TABLE IF NOT EXISTS teams(
    id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, code TEXT UNIQUE, leader_id BIGINT, created_at DOUBLE PRECISION);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_lower ON teams (LOWER(name));
CREATE TABLE IF NOT EXISTS members(
    user_id BIGINT PRIMARY KEY, team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
    joined_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS questions(
    id BIGSERIAL PRIMARY KEY, text TEXT, options TEXT, correct INTEGER,
    explanation TEXT, time_limit INTEGER, points INTEGER, created_at DOUBLE PRECISION, image TEXT);
CREATE TABLE IF NOT EXISTS contests(
    id BIGSERIAL PRIMARY KEY, status TEXT, started_at DOUBLE PRECISION, ended_at DOUBLE PRECISION,
    qids TEXT, idx INTEGER DEFAULT -1, flags TEXT, completed_idx INTEGER DEFAULT -1);
CREATE TABLE IF NOT EXISTS votes(
    contest_id BIGINT, qid BIGINT, user_id BIGINT, team_id BIGINT, choice INTEGER,
    ts DOUBLE PRECISION, PRIMARY KEY(contest_id, qid, user_id));
CREATE TABLE IF NOT EXISTS results(
    contest_id BIGINT, qid BIGINT, team_id BIGINT, team_name TEXT, q_text TEXT,
    chosen_text TEXT, correct_text TEXT, is_correct INTEGER, points INTEGER,
    bonus INTEGER DEFAULT 0, voters INTEGER DEFAULT 0, members INTEGER DEFAULT 0,
    PRIMARY KEY(contest_id, qid, team_id));
CREATE TABLE IF NOT EXISTS player_results(
    contest_id BIGINT, qid BIGINT, user_id BIGINT, team_id BIGINT, player_name TEXT,
    choice INTEGER, is_correct INTEGER, points INTEGER DEFAULT 0, bonus INTEGER DEFAULT 0,
    PRIMARY KEY(contest_id, qid, user_id));
CREATE INDEX IF NOT EXISTS idx_player_results_user ON player_results(user_id);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS join_attempts(user_id BIGINT NOT NULL, attempted_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS idx_join_attempts_user_time ON join_attempts(user_id, attempted_at);
"""

# أعمدة اتضافت بعد النسخة الأولى (ترقية تلقائية لقواعد البيانات القديمة)
MIGRATIONS = [
    ("questions", "image", "TEXT"),
    ("contests", "idx", "INTEGER DEFAULT -1"),
    ("contests", "flags", "TEXT"),
    ("contests", "completed_idx", "INTEGER DEFAULT -1"),
    ("results", "bonus", "INTEGER DEFAULT 0"),
    ("results", "voters", "INTEGER DEFAULT 0"),
    ("results", "members", "INTEGER DEFAULT 0"),
]

DEFAULT_SETTINGS = {
    "time": "60",          # وقت السؤال الافتراضي (ثانية)
    "points": "10",        # نقاط السؤال الافتراضية
    "gap": "5",            # الفاصل بين الأسئلة (ثانية)
    "show_lb": "1",        # عرض الترتيب للفرق
    "speed_bonus": "50",   # أقصى مكافأة سرعة (% من نقاط السؤال) — 0 يوقفها
    "manual": "0",         # وضع "السؤال التالي يدوي"
}


def _postgres_sql(sql):
    """تحويل علامات ? المستخدمة في التطبيق إلى placeholders الخاصة بـ psycopg."""
    return sql.replace("?", "%s")


class DB:
    def __init__(self, path, database_url=None):
        self.path = path
        self.database_url = (database_url or "").strip()
        self.backend = "postgres" if self.database_url else "sqlite"
        self._lock = threading.RLock()
        if self.backend == "postgres":
            self._init_postgres()
        else:
            self._init_sqlite()

    def _init_sqlite(self):
        if self.path != ":memory:":
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SQLITE_SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def _init_postgres(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise RuntimeError("تعذر تحميل مشغل PostgreSQL psycopg.") from None

        try:
            dsn = self.database_url
            if dsn.startswith("postgres://"):
                dsn = "postgresql://" + dsn[len("postgres://"):]
            self.conn = psycopg.connect(
                dsn,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=10,
            )
        except psycopg.Error:
            # لا نضمّن DATABASE_URL في الاستثناء أو السجل لأنه يحتوي بيانات اعتماد.
            raise RuntimeError(
                "تعذر الاتصال بقاعدة PostgreSQL. تحقق من DATABASE_URL وإتاحة قاعدة البيانات."
            ) from None

        for statement in POSTGRES_SCHEMA.split(";"):
            if statement.strip():
                self.conn.execute(statement)
        for table, col, decl in MIGRATIONS:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl}")

    def _execute(self, sql, args=()):
        statement = _postgres_sql(sql) if self.backend == "postgres" else sql
        return self.conn.execute(statement, args)

    def q(self, sql, args=(), one=False):
        with self._lock:
            rows = self._execute(sql, args).fetchall()
            if self.backend == "sqlite":
                self.conn.commit()
        if one:
            return rows[0] if rows else None
        return rows

    def x(self, sql, args=(), return_id=False):
        with self._lock:
            statement = sql
            if self.backend == "postgres" and return_id:
                statement = statement.strip().rstrip(";")
                if not re.match(r"INSERT\b", statement, flags=re.IGNORECASE):
                    raise ValueError("return_id=True is valid only for INSERT statements")
                if not re.search(r"\bRETURNING\b", statement, flags=re.IGNORECASE):
                    statement += " RETURNING id"
            cur = self._execute(statement, args)
            if return_id:
                if self.backend == "postgres":
                    row = cur.fetchone()
                    return row["id"] if row else None
                result = cur.lastrowid
            else:
                result = cur.lastrowid if self.backend == "sqlite" else None
            if self.backend == "sqlite":
                self.conn.commit()
            return result

    def close(self):
        with self._lock:
            self.conn.close()


# DATABASE_URL يشغّل PostgreSQL؛ وإلا نستخدم SQLite (مناسب للتطوير المحلي).
db = DB(DB_PATH, DATABASE_URL)


def get_setting(k):
    r = db.q("SELECT v FROM settings WHERE k=?", (k,), one=True)
    return r["v"] if r else DEFAULT_SETTINGS[k]


def get_int(k):
    return int(get_setting(k))


def set_setting(k, v):
    db.x("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
