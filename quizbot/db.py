"""قاعدة بيانات SQLite + الإعدادات."""
import os
import sqlite3

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT);
CREATE TABLE IF NOT EXISTS teams(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE COLLATE NOCASE,
    code TEXT UNIQUE, leader_id INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS members(
    user_id INTEGER PRIMARY KEY, team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE, joined_at REAL);
CREATE TABLE IF NOT EXISTS questions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, options TEXT, correct INTEGER,
    explanation TEXT, time_limit INTEGER, points INTEGER, created_at REAL);
CREATE TABLE IF NOT EXISTS contests(id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, started_at REAL, ended_at REAL, qids TEXT, completed_idx INTEGER DEFAULT -1);
CREATE TABLE IF NOT EXISTS votes(
    contest_id INTEGER, qid INTEGER, user_id INTEGER, team_id INTEGER, choice INTEGER, ts REAL,
    PRIMARY KEY(contest_id, qid, user_id));
CREATE TABLE IF NOT EXISTS results(
    contest_id INTEGER, qid INTEGER, team_id INTEGER, team_name TEXT, q_text TEXT,
    chosen_text TEXT, correct_text TEXT, is_correct INTEGER, points INTEGER,
    PRIMARY KEY(contest_id, qid, team_id));
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS join_attempts(user_id INTEGER NOT NULL, attempted_at REAL NOT NULL);
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
    "min_part": "50",      # أقل نسبة مشاركة (%) عشان إجابة الفريق تتحسب
    "speed_bonus": "50",   # أقصى مكافأة سرعة (% من نقاط السؤال) — 0 يوقفها
    "tick": "10",          # تحديث العدّاد الحي كل كام ثانية — 0 يوقفه
    "manual": "0",         # وضع "السؤال التالي يدوي"
}


class DB:
    def __init__(self, path):
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def q(self, sql, args=(), one=False):
        rows = self.conn.execute(sql, args).fetchall()
        self.conn.commit()
        if one:
            return rows[0] if rows else None
        return rows

    def x(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid


db = DB(DB_PATH)


def get_setting(k):
    r = db.q("SELECT v FROM settings WHERE k=?", (k,), one=True)
    return r["v"] if r else DEFAULT_SETTINGS[k]


def get_int(k):
    return int(get_setting(k))


def set_setting(k, v):
    db.x("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
