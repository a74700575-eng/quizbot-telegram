"""لوحات ترتيب المسابقات اليومية والعامة."""
from datetime import datetime, timedelta
from datetime import time as datetime_time
from zoneinfo import ZoneInfo

from .config import MEDALS, TIMEZONE
from .db import db
from .scoring import ranked
from .ui import esc

ARENA_PAGE_SIZE = 10


def latest_contest():
    r = db.q("SELECT id FROM contests ORDER BY (status='running') DESC, id DESC LIMIT 1", one=True)
    return r["id"] if r else None


def board_rows(cid):
    """نقاط كل فريق في المسابقة. الفرق اللي اتحذفت بعد ما أخدت نقاط بتفضل ظاهرة (n=0)."""
    got = {
        r["team_id"]: {"id": r["team_id"], "name": r["name"], "pts": r["pts"], "ok": r["ok"], "n": 0}
        for r in db.q(
            "SELECT team_id, MAX(team_name) name, SUM(points) pts, SUM(is_correct) ok "
            "FROM results WHERE contest_id=? GROUP BY team_id",
            (cid,),
        )
    }
    for t in db.q(
        "SELECT t.id, t.name, (SELECT COUNT(*) FROM members m WHERE m.team_id=t.id) n FROM teams t"
    ):
        if t["n"] == 0:
            continue
        row = got.setdefault(t["id"], {"id": t["id"], "name": t["name"], "pts": 0, "ok": 0, "n": 0})
        row["name"], row["n"] = t["name"], t["n"]
    return sorted(got.values(), key=lambda r: (-r["pts"], -r["ok"], r["name"]))


def arena_rows(day=None):
    """ترتيب فرق اليوم المحلي، مجمّعًا من كل مسابقات اليوم المكتملة جزئيًا أو كليًا.

    النقاط تتضمن مكافأة السرعة، ثم تستخدم الإجابات الصحيحة ومجموع مكافآت السرعة
    لكسر التعادل. تُدرج كل الفرق الحالية حتى لو لم تشارك بعد.
    """
    tz = ZoneInfo(TIMEZONE)
    today = day or datetime.now(tz).date()
    start = datetime.combine(today, datetime_time.min, tzinfo=tz).timestamp()
    end = datetime.combine(today + timedelta(days=1), datetime_time.min, tzinfo=tz).timestamp()

    totals = {
        r["team_id"]: {
            "id": r["team_id"],
            "name": r["name"],
            "pts": int(r["pts"] or 0),
            "ok": int(r["ok"] or 0),
            "bonus": int(r["bonus"] or 0),
            "questions": int(r["questions"] or 0),
        }
        for r in db.q(
            "SELECT r.team_id, MAX(r.team_name) name, SUM(r.points) pts, "
            "SUM(r.is_correct) ok, SUM(r.bonus) bonus, COUNT(*) questions "
            "FROM results r JOIN contests c ON c.id=r.contest_id "
            "WHERE c.started_at>=? AND c.started_at<? GROUP BY r.team_id",
            (start, end),
        )
    }
    for t in db.q("SELECT id, name FROM teams"):
        row = totals.setdefault(
            t["id"], {"id": t["id"], "name": t["name"], "pts": 0, "ok": 0, "bonus": 0, "questions": 0}
        )
        row["name"] = t["name"]
    return sorted(totals.values(), key=lambda r: (-r["pts"], -r["ok"], -r["bonus"], r["name"].casefold()))


def arena_ranked(rows):
    """Ranks ties together; the score key includes correctness and speed bonus."""
    out, rank, prev = [], 0, None
    for i, row in enumerate(rows):
        key = (row["pts"], row["ok"], row["bonus"])
        if key != prev:
            rank, prev = i + 1, key
        out.append((rank, row))
    return out


def arena_text(rows, day, page=0, page_size=ARENA_PAGE_SIZE):
    """يعرض أفضل 10 في الصفحة مع إمكانية تصفح بقية الفرق."""
    title = f"⭐ <b>نجم الساحة — {day.isoformat()}</b>"
    if not rows:
        return title + "\n\nمفيش فرق مسجلة لسه."

    ordered = arena_ranked(rows)
    start = max(0, page) * page_size
    shown = ordered[start:start + page_size]
    stars = [row for rank, row in ordered if rank == 1 and row["ok"] > 0 and row["pts"] > 0]
    out = [title, ""]
    if page == 0:
        if stars:
            names = "، ".join(esc(row["name"]) for row in stars)
            total_pts = stars[0]["pts"]
            out.extend([f"🏅 <b>نجم اليوم: {names}</b> — {total_pts} نقطة", ""])
        else:
            out.extend(["⏳ لسه مفيش فريق جاوب إجابة صحيحة اليوم؛ مفيش نجم مُعلن بعد.", ""])

    for rank, row in shown:
        marker = "⭐" if rank == 1 and row["ok"] > 0 and row["pts"] > 0 else MEDALS.get(rank, f"{rank}.")
        out.append(
            f"{marker} <b>{esc(row['name'])}</b> — {row['pts']} نقطة "
            f"· ✅ {row['ok']} · ⚡ +{row['bonus']}"
        )
    last_page = max(0, (len(ordered) - 1) // page_size)
    out.extend(["", f"الصفحة {min(page, last_page) + 1}/{last_page + 1} · إجمالي الفرق: {len(ordered)}"])
    return "\n".join(out)


def board_text(rows, highlight=None, limit=10, title="🏆 <b>الترتيب</b>", admin=False):
    if not rows:
        return title + "\n\nمفيش فرق لسه."
    out, mine = [title, ""], None
    for i, (rank, r) in enumerate(ranked(rows)):
        medal = MEDALS.get(rank, f"{rank}.")
        extra = (f" · {r['n']} أعضاء" if r["n"] else " · (اتحذف)") if admin else ""
        line = f"{medal} {'👉 ' if r['id'] == highlight else ''}<b>{esc(r['name'])}</b> — {r['pts']} نقطة ({r['ok']}✅){extra}"
        if r["id"] == highlight:
            mine = line
        if i < limit:
            out.append(line)
    if mine and mine not in out:
        out += ["…", mine]
    return "\n".join(out)
