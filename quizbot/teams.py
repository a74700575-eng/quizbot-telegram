"""الفرق والأعضاء."""
import secrets
import string
import time

from .config import MAX_TEAM_SIZE
from .db import db
from .state import RT
from .ui import esc, safe_send

JOIN_WINDOW_SECONDS = 600
MAX_FAILED_JOIN_ATTEMPTS = 5
JOIN_CODE_LENGTH = 8


def touch_user(u):
    db.x(
        "INSERT INTO users(id,name,username) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username",
        (u.id, u.full_name, u.username),
    )


def team_of(uid):
    return db.q("SELECT t.* FROM teams t JOIN members m ON m.team_id=t.id WHERE m.user_id=?", (uid,), one=True)


def team_members(tid):
    return db.q(
        "SELECT m.user_id AS id, COALESCE(u.name, CAST(m.user_id AS TEXT)) AS name FROM members m "
        "LEFT JOIN users u ON u.id=m.user_id WHERE m.team_id=? ORDER BY m.joined_at",
        (tid,),
    )


def all_member_ids():
    return [r["user_id"] for r in db.q("SELECT user_id FROM members")]


def drop_votes(uid=None, tid=None):
    """يمسح أصوات عضو/فريق من السؤال المفتوح (لما يخرج أو يتطرد أو الفريق يتحذف)."""
    c = RT.get("c")
    if not c or not c.get("open"):
        return
    if uid:
        db.x("DELETE FROM votes WHERE contest_id=? AND qid=? AND user_id=?", (c["id"], c["qid"], uid))
    if tid:
        db.x("DELETE FROM votes WHERE contest_id=? AND qid=? AND team_id=?", (c["id"], c["qid"], tid))


def gen_code():
    alphabet = "".join(c for c in string.ascii_uppercase if c not in "OIL") + "23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(JOIN_CODE_LENGTH))
        if not db.q("SELECT 1 FROM teams WHERE code=?", (code,), one=True):
            return code


def create_team(uid, name):
    if team_of(uid):
        return None, "انت بالفعل في فريق. اخرج منه الأول بـ /leave"
    name = " ".join(name.split())
    if not 2 <= len(name) <= 30:
        return None, "اسم الفريق لازم يكون من 2 لـ 30 حرف."
    if db.q("SELECT 1 FROM teams WHERE lower(name)=lower(?)", (name,), one=True):
        return None, "الاسم ده مستخدم، اختار اسم تاني."
    tid = db.x("INSERT INTO teams(name,code,leader_id,created_at) VALUES(?,?,?,?)", (name, gen_code(), uid, time.time()), return_id=True)
    db.x("INSERT INTO members(user_id,team_id,joined_at) VALUES(?,?,?)", (uid, tid, time.time()))
    return db.q("SELECT * FROM teams WHERE id=?", (tid,), one=True), None


def create_empty_team(name):
    """ينشئ فريقًا من لوحة الأدمن؛ أول عضو ينضم يصبح القائد تلقائيًا."""
    name = " ".join(name.split())
    if not 2 <= len(name) <= 30:
        return None, "اسم الفريق لازم يكون من 2 لـ 30 حرف."
    if db.q("SELECT 1 FROM teams WHERE lower(name)=lower(?)", (name,), one=True):
        return None, "الاسم ده مستخدم، اختار اسم تاني."
    tid = db.x(
        "INSERT INTO teams(name,code,leader_id,created_at) VALUES(?,?,NULL,?)",
        (name, gen_code(), time.time()),
        return_id=True,
    )
    return db.q("SELECT * FROM teams WHERE id=?", (tid,), one=True), None


def join_team(uid, code):
    if team_of(uid):
        return None, "انت بالفعل في فريق. اخرج منه الأول بـ /leave"
    now = time.time()
    cutoff = now - JOIN_WINDOW_SECONDS
    db.x("DELETE FROM join_attempts WHERE attempted_at < ?", (cutoff,))
    attempts = db.q(
        "SELECT attempted_at FROM join_attempts WHERE user_id=? AND attempted_at>=? ORDER BY attempted_at",
        (uid, cutoff),
    )
    if len(attempts) >= MAX_FAILED_JOIN_ATTEMPTS:
        seconds_left = max(1, int(attempts[0]["attempted_at"] + JOIN_WINDOW_SECONDS - now))
        minutes_left = max(1, (seconds_left + 59) // 60)
        return None, f"🚫 محاولات كثيرة لكود الانضمام. جرّب بعد {minutes_left} دقيقة."
    t = db.q("SELECT * FROM teams WHERE code=?", (code.strip().upper(),), one=True)
    if not t:
        db.x("INSERT INTO join_attempts(user_id, attempted_at) VALUES(?,?)", (uid, now))
        return None, "❌ الكود غلط."
    if len(team_members(t["id"])) >= MAX_TEAM_SIZE:
        return None, f"🚫 الفريق ده مكتمل ({MAX_TEAM_SIZE} أعضاء)."
    db.x("INSERT INTO members(user_id,team_id,joined_at) VALUES(?,?,?)", (uid, t["id"], time.time()))
    db.x("DELETE FROM join_attempts WHERE user_id=?", (uid,))
    if t["leader_id"] is None:
        db.x("UPDATE teams SET leader_id=? WHERE id=? AND leader_id IS NULL", (uid, t["id"]))
        t = db.q("SELECT * FROM teams WHERE id=?", (t["id"],), one=True)
    return t, None


def remove_member(uid, tid):
    """يشيل العضو ويعيّن قائد جديد أو يحذف الفريق الفاضي. يرجع True لو الفريق اتحذف."""
    drop_votes(uid=uid)
    db.x("DELETE FROM members WHERE user_id=? AND team_id=?", (uid, tid))
    left = team_members(tid)
    if not left:
        db.x("DELETE FROM teams WHERE id=?", (tid,))
        return True
    t = db.q("SELECT leader_id FROM teams WHERE id=?", (tid,), one=True)
    if t and t["leader_id"] == uid:
        db.x("UPDATE teams SET leader_id=? WHERE id=?", (left[0]["id"], tid))
    return False


def set_leader(tid, uid):
    db.x("UPDATE teams SET leader_id=? WHERE id=? AND EXISTS (SELECT 1 FROM members WHERE team_id=? AND user_id=?)", (uid, tid, tid, uid))


def leave_team(uid):
    """يرجع (tid، اسم الفريق، اتحذف؟)"""
    t = team_of(uid)
    if not t:
        return None, None, False
    return t["id"], t["name"], remove_member(uid, t["id"])


def delete_team(tid):
    members = [m["id"] for m in team_members(tid)]
    drop_votes(tid=tid)
    db.x("DELETE FROM teams WHERE id=?", (tid,))
    return members


def team_text(t, bot_username):
    from .config import MAX_TEAM_SIZE as MAXN

    mem = team_members(t["id"])
    lines = [
        f"👥 <b>{esc(t['name'])}</b>",
        f"🔑 كود الانضمام: <code>{t['code']}</code>",
        f"🔗 رابط الدعوة: https://t.me/{bot_username}?start=join_{t['code']}",
        "",
        f"الأعضاء ({len(mem)}/{MAXN}):",
    ]
    for m in mem:
        lines.append(("👑 " if m["id"] == t["leader_id"] else "• ") + esc(m["name"]))
    return "\n".join(lines)


async def notify_team(bot, tid, text, exclude=None):
    for m in team_members(tid):
        if m["id"] != exclude:
            await safe_send(bot, m["id"], text)
